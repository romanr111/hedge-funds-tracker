"""End-to-end offline pipeline test: collect -> compute -> store -> publish.

No network, no gog binary: collect/compute are scripted fakes wired into the
real `run_market_intel` use case; publishing goes through the real
GogSheetsPublisher with a fake gog client. Everything runs against an
in-memory store implementing the VintageRepository port.
"""
from __future__ import annotations

import importlib
import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest

from market_intel.config import MarketIntelConfig
from market_intel.domain.models import (
    MODULE_KEYS,
    MODULE_TITLES_UK,
    BandPoint,
    CollectionResult,
    DashboardSnapshot,
    GaugeHistoryPoint,
    ModuleGauge,
    PublishRunRecord,
    SeriesObservation,
    SeriesSnapshot,
    SeriesSpec,
    Skip,
    SKIP_MANUAL,
)
from market_intel.infrastructure.export.gog_client import GogOutcome
from market_intel.infrastructure.export.gog_sheets_publisher import (
    SETTING_SPREADSHEET_ID,
    GogSheetsPublisher,
)
from market_intel.infrastructure.export.sheet_layout import CHART_TITLE_UK

run_mi = importlib.import_module("market_intel.application.use_cases.run_market_intel")

FAKE_SSID: Final[str] = "SS_E2E_123"
HISTORY_DATE: Final[date] = date(2026, 11, 6)
KYIV_NOW: Final[datetime] = datetime(2026, 11, 7, 9, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
SHEET1: Final[str] = "Sheet1"


# ---------------------------------------------------------------------------
# In-memory VintageRepository


class _InMemoryStore:
    """Full offline VintageRepository with dedupe and record keeping."""

    def __init__(self) -> None:
        self.observations: list[SeriesObservation] = []
        self._gauge: dict[tuple[date, str], GaugeHistoryPoint] = {}
        self.settings: dict[str, str] = {}
        self.publish_runs: list[PublishRunRecord] = []
        self.closed = False

    def append_observation(self, obs: SeriesObservation) -> bool:
        key = (obs.series_id, obs.obs_date, obs.value)
        if any((o.series_id, o.obs_date, o.value) == key for o in self.observations):
            return False
        self.observations.append(obs)
        return True

    def append_observations(self, observations: Any) -> int:
        return sum(self.append_observation(obs) for obs in observations)

    def read_point_in_time(self, series_id: str, as_of: date) -> SeriesObservation | None:
        candidates = [
            obs
            for obs in self.observations
            if obs.series_id == series_id and obs.obs_date <= as_of
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda obs: obs.fetched_at)

    def read_history(self, series_id: str, *, start: date | None = None, end: date | None = None) -> list[SeriesObservation]:
        rows = [
            obs
            for obs in self.observations
            if obs.series_id == series_id
            and (start is None or obs.obs_date >= start)
            and (end is None or obs.obs_date <= end)
        ]
        return sorted(rows, key=lambda obs: (obs.obs_date, obs.fetched_at))

    def upsert_gauge_history(self, point: GaugeHistoryPoint) -> None:
        self._gauge[(point.run_date, point.module_key)] = point

    def read_gauge_history(self) -> list[GaugeHistoryPoint]:
        return [self._gauge[key] for key in sorted(self._gauge)]

    def get_setting(self, key: str) -> str | None:
        return self.settings.get(key)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value

    def record_publish_run(
        self,
        *,
        spreadsheet_id: str | None,
        fingerprint: str | None,
        status: str,
        started_at: datetime | None,
        finished_at: datetime | None,
        detail: str,
    ) -> None:
        run = PublishRunRecord(
            spreadsheet_id=spreadsheet_id,
            fingerprint=fingerprint,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            detail=detail,
        )
        self.publish_runs.append(run)

    def last_publish_run(self) -> PublishRunRecord | None:
        return self.publish_runs[-1] if self.publish_runs else None

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Fake gog client (happy-path dispatch; mirrors the gog publisher test fake)


class _FakeGogClient:
    """Records argv and synthesizes gog JSON responses (offline)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.batches: list[list[dict[str, Any]]] = []
        self.tabs: list[str] = [SHEET1]
        self.sheet_ids: dict[str, int] = {SHEET1: 1}
        self._next_sheet_id = 100
        self.chart_titles: list[str] = []

    def gog_available(self) -> bool:
        return True

    def run(self, args: list[str], *, force: bool = False, retries: int = 3) -> GogOutcome:
        self.calls.append({"args": list(args), "force": force})
        if not args or args[0] != "sheets":
            return GogOutcome(1, "", "unhandled command")
        command = args[1]
        if command == "create":
            self.tabs = [SHEET1]
            self.sheet_ids = {SHEET1: 1}
            doc = {
                "spreadsheetId": FAKE_SSID,
                "sheets": [{"properties": {"title": SHEET1, "sheetId": 1}}],
            }
            return GogOutcome(0, json.dumps(doc), "")
        if command == "metadata":
            doc = {
                "spreadsheetId": args[2],
                "sheets": [
                    {"properties": {"title": title, "sheetId": self.sheet_ids.get(title, 99)}}
                    for title in self.tabs
                ],
            }
            return GogOutcome(0, json.dumps(doc), "")
        if command == "add-tab":
            self.tabs.append(args[3])
            self.sheet_ids[args[3]] = self._next_sheet_id
            self._next_sheet_id += 1
            return GogOutcome(0, json.dumps({"status": "ok"}), "")
        if command == "delete-tab":
            if args[3] in self.tabs:
                self.tabs.remove(args[3])
            return GogOutcome(0, json.dumps({"status": "ok"}), "")
        if command == "clear":
            return GogOutcome(0, json.dumps({"clearedRange": args[3]}), "")
        if command == "batch-update":
            data_arg = next(arg for arg in args if arg.startswith("--data-json=@"))
            payload = json.loads(Path(data_arg.split("=", 1)[1][1:]).read_text(encoding="utf-8"))
            self.batches.append(payload)
            return GogOutcome(0, json.dumps({"totalUpdatedCells": 1}), "")
        if command in {"format", "number-format", "freeze", "resize-columns", "update-note"}:
            return GogOutcome(0, json.dumps({"status": "ok"}), "")
        if command == "chart" and args[2] == "list":
            return GogOutcome(0, json.dumps({"charts": []}), "")
        if command == "chart" and args[2] == "create":
            spec_arg = next(arg for arg in args if arg.startswith("--spec-json"))
            value = spec_arg.split("=", 1)[1] if "=" in spec_arg else args[args.index("--spec-json") + 1]
            path = value[1:] if value.startswith("@") else None
            spec = json.loads(Path(path).read_text(encoding="utf-8")) if path else json.loads(value)
            self.chart_titles.append(spec.get("title", ""))
            return GogOutcome(0, json.dumps({"chartId": 7}), "")
        return GogOutcome(1, "", f"unhandled sheets command: {command}")


# ---------------------------------------------------------------------------
# Scripted data / fetchers / specs


SPECS: Final[dict[str, SeriesSpec]] = {
    "cape": SeriesSpec(key="cape", title_uk="CAPE", source="multpl", freq="w", staleness_days=14, module_key="valuation"),
    "vix": SeriesSpec(key="vix", title_uk="VIX", source="yahoo", freq="d", staleness_days=7, module_key="sentiment"),
    "ma_breadth": SeriesSpec(key="ma_breadth", title_uk="Breadth", source="yahoo", freq="d", staleness_days=7, module_key="technique"),
    "t10y": SeriesSpec(key="t10y", title_uk="US10Y", source="fred", freq="d", staleness_days=7, module_key="macro"),
    "hy_oas": SeriesSpec(key="hy_oas", title_uk="HY OAS", source="fred", freq="d", staleness_days=7, module_key="credit"),
}

SERIES_VALUES: Final[dict[str, float]] = {
    "cape": 38.2,
    "vix": 18.5,
    "ma_breadth": 60.0,
    "t10y": 4.25,
    "hy_oas": 3.3,
}
SERIES_UNITS: Final[dict[str, str]] = {
    "cape": "р.",
    "vix": "п.",
    "ma_breadth": "%",
    "t10y": "%",
    "hy_oas": "в.п.",
}


class _ScriptedFetcher:
    """Offline SeriesFetcher: fixed (series_id, obs_date, value) triples."""

    def __init__(self, source: str, keys: tuple[str, ...]) -> None:
        self._source = source
        self._keys = keys

    def fetch(self, spec: SeriesSpec, *, now_utc: datetime) -> list[SeriesObservation]:
        assert spec.key in self._keys
        return [
            SeriesObservation(
                series_id=spec.key,
                obs_date=HISTORY_DATE,
                value=SERIES_VALUES[spec.key],
                source=self._source,
                fetched_at=now_utc,
            )
        ]


def fake_collect_vintages(store: Any, fetchers: Any, series_specs: Any, *, skip_sources: Any = (), now_utc: Any = None) -> CollectionResult:
    fetched: list[str] = []
    skipped: list[Skip] = []
    for spec in series_specs.values():
        if spec.source in skip_sources:
            skipped.append(Skip(key=spec.key, code=SKIP_MANUAL, detail=f"user skip: {spec.source}"))
            continue
        observations = fetchers[spec.source].fetch(spec, now_utc=now_utc)
        store.append_observations(observations)
        if spec.source not in fetched:
            fetched.append(spec.source)
    return CollectionResult(fetched=tuple(fetched), skipped=tuple(skipped), errors=())


def _chip_for(score: float) -> tuple[str, str, str]:
    if score >= 75.0:
        return ("Дорого", "state5", "#E57373")
    if score >= 55.0:
        return ("Тепло", "state4", "#FFB74D")
    if score >= 45.0:
        return ("Нейтрально", "state3", "#FFD54F")
    if score >= 25.0:
        return ("Холодно", "state2", "#90CAF9")
    return ("Дешево", "state1", "#A5D6A7")


def fake_compute_dashboard(store: Any, series_specs: Any, *, as_of: Any, now_kyiv_datetime: Any = None) -> DashboardSnapshot:
    modules: list[ModuleGauge] = []
    indicators: list[SeriesSnapshot] = []
    for module_key in MODULE_KEYS:
        spec_keys = [
            spec.key for spec in series_specs.values() if spec.module_key == module_key
        ]
        points = [
            store.read_point_in_time(key, as_of)
            for key in spec_keys
        ]
        points = [point for point in points if point is not None]
        score: float | None = None
        if points:
            score = round(1.5 * sum(point.value for point in points) / len(points), 1)
            score = min(score, 99.0)
        chip_label_uk, state_key, chip_color = (
            _chip_for(score) if score is not None else ("n/д", "state3", "#FFD54F")
        )
        modules.append(
            ModuleGauge(
                module_key=module_key,
                title_uk=MODULE_TITLES_UK[module_key],
                score=score,
                state_key=state_key,
                chip_label_uk=chip_label_uk,
                chip_color=chip_color,
                verdict_uk=f"Вердикт: {chip_label_uk}",
                context_forward_uk="Контекст форвардної дохідності",
                facts_uk=(f"Факт {module_key}",),
                members_used=len(points),
                members_total=len(spec_keys),
                dropped_uk=(),
            )
        )
        for point in points:
            spec = series_specs[point.series_id]
            indicators.append(
                SeriesSnapshot(
                    key=spec.key,
                    title_uk=spec.title_uk,
                    module_key=module_key,
                    value=point.value,
                    obs_date=point.obs_date,
                    percentile=0.5,
                    band=BandPoint(
                        key="band_mid",
                        label_uk="Нейтрально",
                        color_token="state3",
                        lower=20.0,
                        upper=80.0,
                    ),
                    is_stale=False,
                    stale_reason_uk=None,
                    meaning_uk="Значення",
                    source_uk=spec.source,
                    unit_uk=SERIES_UNITS[spec.key],
                )
            )
    return DashboardSnapshot(
        as_of=as_of,
        generated_at=now_kyiv_datetime,
        modules=tuple(modules),
        indicators=tuple(indicators),
        forward_rows=(),
        system_signal_uk=f"Сигнал: {modules[0].chip_label_uk} / {modules[-1].chip_label_uk}",
        warnings_uk=(),
    )


# ---------------------------------------------------------------------------
# Wiring helpers


class _RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def publish(self, snapshot: Any, history_rows: Any, *, reset_sheet: bool = False) -> dict[str, Any]:
        self.calls.append(
            {
                "snapshot": snapshot,
                "history_rows": list(history_rows),
                "reset_sheet": reset_sheet,
            }
        )
        return {"status": "created"}


def _config(tmp_path: Path) -> MarketIntelConfig:
    return MarketIntelConfig(
        db_path=tmp_path / "db.sqlite3",
        sheets_spreadsheet_id="",
        sheets_folder_id="",
        fred_interval_seconds=0.0,
        yahoo_interval_seconds=0.0,
        http_interval_seconds=0.0,
    )


def _seed_prior_day_history(store: _InMemoryStore) -> None:
    """A second history date so the layout emits the chart spec."""
    for module_key in MODULE_KEYS:
        store.upsert_gauge_history(
            GaugeHistoryPoint(
                run_date=HISTORY_DATE,
                module_key=module_key,
                score=50.0,
                state_key="state3",
                members_used=1,
                members_total=1,
                computed_at=KYIV_NOW,
            )
        )


def _wire_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Any, _InMemoryStore]:
    store = _InMemoryStore()
    _seed_prior_day_history(store)
    monkeypatch.setattr(run_mi, "collect_vintages", fake_collect_vintages)
    monkeypatch.setattr(run_mi, "compute_dashboard", fake_compute_dashboard)
    monkeypatch.setattr(run_mi, "SERIES_SPECS", SPECS)
    monkeypatch.setattr(run_mi, "now_kyiv", lambda: KYIV_NOW)
    return run_mi, store


def _fetchers() -> dict[str, _ScriptedFetcher]:
    return {
        "fred": _ScriptedFetcher("fred", ("t10y", "hy_oas")),
        "multpl": _ScriptedFetcher("multpl", ("cape",)),
        "yahoo": _ScriptedFetcher("yahoo", ("vix", "ma_breadth")),
    }


def _run(run_module: Any, store: Any, publisher: Any, *, reset_sheet: bool = False) -> Any:
    config = _config(Path("."))  # not used by the use case (deleted body)
    runtime = SimpleNamespace(store=store, fetchers=_fetchers(), publisher=publisher)
    return run_module.run_market_intel(
        config,
        runtime,
        dry_run=False,
        no_publish=False,
        reset_sheet=reset_sheet,
        skip_sources=(),
    )


# ---------------------------------------------------------------------------
# Tests


def test_first_run_collects_computes_persists_and_publishes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_module, store = _wire_run(monkeypatch, Path("."))
    publisher = _RecordingPublisher()

    result = _run(run_module, store, publisher)

    assert set(result.collected.fetched) == {"fred", "multpl", "yahoo"}
    assert result.collected.errors == ()
    assert result.collected.skipped == ()

    modules = result.dashboard.modules
    assert [module.module_key for module in modules] == list(MODULE_KEYS)
    assert all(module.score is not None for module in modules)
    assert result.dashboard.as_of == KYIV_NOW.date()

    history = store.read_gauge_history()
    assert {point.module_key for point in history} == set(MODULE_KEYS)
    today_points = [point for point in history if point.run_date == KYIV_NOW.date()]
    assert len(today_points) == 5  # one gauge point per module

    assert len(publisher.calls) == 1
    call = publisher.calls[0]
    snapshot, history_rows = call["snapshot"], call["history_rows"]
    assert call["reset_sheet"] is False
    assert snapshot.as_of == KYIV_NOW.date()
    assert history_rows == history

    # The scripted fetcher output is deduped by the store on a plain re-collect.
    fetcher = _ScriptedFetcher("fred", ("t10y", "hy_oas"))
    obs = fetcher.fetch(SPECS["t10y"], now_utc=KYIV_NOW)[0]
    assert store.append_observation(obs) is False  # duplicate (id, date, value)
    assert store.append_observation(
        SeriesObservation("t10y", HISTORY_DATE, 4.3, "fred", KYIV_NOW)
    ) is True


def test_second_identical_run_skips_publish_without_gog_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_module, store = _wire_run(monkeypatch, Path("."))
    client = _FakeGogClient()
    publisher = GogSheetsPublisher(store, client=client)

    first = _run(run_module, store, publisher, reset_sheet=False)
    assert first.publish is not None
    assert first.publish.status in {"created", "updated"}
    assert first.publish.spreadsheet_id == FAKE_SSID
    first_calls = list(client.calls)

    # First publish: no spec-tab rebuild (reset off), default tab only, chart added.
    delete_targets = [
        call["args"][3] for call in first_calls if call["args"][1] == "delete-tab"
    ]
    assert set(delete_targets) == {SHEET1}
    assert client.chart_titles == [CHART_TITLE_UK]
    assert len(client.batches) == 8  # one batch-update payload per tab

    assert store.settings[SETTING_SPREADSHEET_ID] == FAKE_SSID
    last_run = store.last_publish_run()
    assert last_run is not None and last_run.fingerprint

    second = _run(run_module, store, publisher, reset_sheet=False)
    assert second.publish is not None
    assert second.publish.status == "skipped_unchanged"
    assert client.calls == first_calls  # zero additional gog calls


def test_dry_run_and_no_publish_return_publish_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_module, store = _wire_run(monkeypatch, Path("."))
    publisher = _RecordingPublisher()

    config = _config(Path("."))
    runtime = SimpleNamespace(store=store, fetchers=_fetchers(), publisher=publisher)
    dry = run_module.run_market_intel(
        config, runtime, dry_run=True, no_publish=False, reset_sheet=False
    )
    skipped = run_module.run_market_intel(
        config, runtime, dry_run=False, no_publish=True, reset_sheet=False
    )

    assert dry.publish is None
    assert skipped.publish is None
    assert publisher.calls == []
