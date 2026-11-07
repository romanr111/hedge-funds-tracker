"""Offline tests for the gog-backed Google Sheets publisher (fake client)."""
from __future__ import annotations

import importlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import pytest

from market_intel.domain.exceptions import MarketIntelPublishError
from market_intel.domain.models import (
    MODULE_KEYS,
    MODULE_TITLES_UK,
    BandPoint,
    DashboardSnapshot,
    GaugeHistoryPoint,
    ModuleGauge,
    PublishRunRecord,
    PublishResult,
    SeriesSnapshot,
)
from market_intel.infrastructure.export.gog_client import GogClient, GogOutcome
from market_intel.infrastructure.export.gog_sheets_publisher import (
    SETTING_SPREADSHEET_ID,
    GogSheetsPublisher,
)
from market_intel.infrastructure.export.sheet_layout import (
    CHART_TITLE_UK,
    SPREADSHEET_TITLE_UK,
    TAB_HISTORY_UK,
    TAB_NAMES_UK,
    TAB_OVERVIEW_UK,
    build_sheet_document,
)

FAKE_SSID: Final[str] = "SS_TEST_123"
STORED_SSID: Final[str] = "SS_STORED_456"
GENERATED_AT = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)
TAB_NAMES: Final[tuple[str, ...]] = tuple(TAB_NAMES_UK)


# ---------------------------------------------------------------------------
# Fakes


class FakeGogClient:
    """Records argv and synthesizes gog JSON responses (never hits the API)."""

    def __init__(
        self,
        *,
        available: bool = True,
        fail_batch_update: bool = False,
        fail_metadata_for: frozenset[str] = frozenset(),
        existing_tabs: tuple[str, ...] = (),
    ) -> None:
        self.available = available
        self.fail_batch_update = fail_batch_update
        self.fail_metadata_for = fail_metadata_for
        self.calls: list[dict[str, Any]] = []
        self.batches: list[list[dict[str, Any]]] = []
        self.tabs: list[str] = list(existing_tabs)
        self.sheet_ids: dict[str, int] = {
            title: 100 + index for index, title in enumerate(existing_tabs)
        }
        self._next_sheet_id = 200
        self.chart_titles: list[str] = []
        self.chart_specs: list[dict[str, Any]] = []

    def gog_available(self) -> bool:
        return self.available

    def _run(self, args: list[str], *, force: bool = False, retries: int = 3) -> GogOutcome:
        record: dict[str, Any] = {"args": list(args), "force": force, "retries": retries}
        self.calls.append(record)
        if args and args[0] == "sheets" and len(args) > 1:
            return self._dispatch_sheets(args, force=force)
        return GogOutcome(returncode=1, stdout="", stderr="unknown command shape")

    # Dispatch helpers -------------------------------------------------------

    def _dispatch_sheets(self, args: list[str], *, force: bool) -> GogOutcome:
        command = args[1]
        if command == "create":
            self.tabs = ["Sheet1"]
            self.sheet_ids = {"Sheet1": 1}
            doc = {
                "spreadsheetId": FAKE_SSID,
                "properties": {"title": SPREADSHEET_TITLE_UK},
                "sheets": [{"properties": {"title": "Sheet1", "sheetId": 1}}],
            }
            return GogOutcome(0, json.dumps(doc), "")
        if command == "metadata":
            spreadsheet_id = args[2]
            if spreadsheet_id in self.fail_metadata_for:
                return GogOutcome(1, "", "404 Not Found: requested entity was not found")
            doc = {
                "spreadsheetId": spreadsheet_id,
                "sheets": [
                    {"properties": {"title": title, "sheetId": self.sheet_ids.get(title, 100)}}
                    for title in self.tabs
                ],
            }
            return GogOutcome(0, json.dumps(doc), "")
        if command == "add-tab":
            tab_name = args[3]
            self.tabs.append(tab_name)
            self.sheet_ids[tab_name] = self._next_sheet_id
            self._next_sheet_id += 1
            return GogOutcome(0, json.dumps({"status": "ok"}), "")
        if command == "delete-tab":
            tab_name = args[3]
            if tab_name in self.tabs:
                self.tabs.remove(tab_name)
            return GogOutcome(0, json.dumps({"status": "ok"}), "")
        if command == "clear":
            return GogOutcome(0, json.dumps({"clearedRange": args[3]}), "")
        if command == "batch-update":
            if self.fail_batch_update:
                return GogOutcome(1, "", "PERMISSION_DENIED: batch write rejected")
            data_arg = next(arg for arg in args if arg.startswith("--data-json=@"))
            payload = json.loads(
                Path(data_arg.split("=", 1)[1][1:]).read_text(encoding="utf-8")
            )
            self.batches.append(payload)
            return GogOutcome(0, json.dumps({"totalUpdatedCells": 42}), "")
        if command in {"format", "number-format", "freeze", "resize-columns", "update-note"}:
            return GogOutcome(0, json.dumps({"status": "ok"}), "")
        if command == "chart":
            return self._dispatch_chart(args)
        return GogOutcome(1, "", f"unhandled sheets command: {command}")

    def _dispatch_chart(self, args: list[str]) -> GogOutcome:
        action = args[2]
        if action == "list":
            doc = {
                "charts": [
                    {"title": title, "chartId": index}
                    for index, title in enumerate(self.chart_titles)
                ]
            }
            return GogOutcome(0, json.dumps(doc), "")
        if action == "create":
            spec_arg = next(arg for arg in args if arg.startswith("--spec-json"))
            value = spec_arg.split("=", 1)[1] if "=" in spec_arg else args[args.index("--spec-json") + 1]
            path = value[1:] if value.startswith("@") else None
            spec = json.loads(Path(path).read_text(encoding="utf-8")) if path else json.loads(value)
            self.chart_specs.append(spec)
            self.chart_titles.append(spec.get("title", ""))
            return GogOutcome(0, json.dumps({"chartId": 5}), "")
        return GogOutcome(1, "", "unhandled chart action")


class FakeStore:
    """Publisher-facing subset of VintageRepository with recording."""

    def __init__(self, *, stored_ssid: str | None = None) -> None:
        self.settings: dict[str, str] = {}
        if stored_ssid is not None:
            self.settings[SETTING_SPREADSHEET_ID] = stored_ssid
        self.publish_runs: list[dict[str, Any]] = []
        self._last_publish_run: PublishRunRecord | None = None

    # Settings ---------------------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        return self.settings.get(key)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value

    # Publish runs -----------------------------------------------------------

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
        self.publish_runs.append(
            {
                "spreadsheet_id": spreadsheet_id,
                "fingerprint": fingerprint,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "detail": detail,
            }
        )
        self._last_publish_run = PublishRunRecord(
            spreadsheet_id=spreadsheet_id,
            fingerprint=fingerprint,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            detail=detail,
        )

    def last_publish_run(self) -> PublishRunRecord | None:
        return self._last_publish_run

    # Unused port members ----------------------------------------------------

    def append_observation(self, obs: Any) -> bool:  # pragma: no cover - unused
        return True

    def append_observations(self, observations: Any) -> int:  # pragma: no cover
        return 0

    def read_point_in_time(self, series_id: Any, as_of: Any) -> None:  # pragma: no cover
        return None

    def read_history(self, series_id: Any, *, start: Any = None, end: Any = None) -> list:  # pragma: no cover
        return []

    def upsert_gauge_history(self, point: Any) -> None:  # pragma: no cover
        return None

    def read_gauge_history(self) -> list:  # pragma: no cover
        return []

    def close(self) -> None:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# Shared builders


def _modules(scores: dict[str, float] | None = None) -> tuple[ModuleGauge, ...]:
    return tuple(
        ModuleGauge(
            module_key=key,
            title_uk=MODULE_TITLES_UK[key],
            score=(scores or {}).get(key, 50.0),
            state_key="state3",
            chip_label_uk="Нейтрально",
            chip_color="#FFD54F",
            verdict_uk=f"Вердикт {key}",
            context_forward_uk=f"Контекст {key}",
            facts_uk=(f"Факт {key}",),
            members_used=1,
            members_total=2,
            dropped_uk=(),
        )
        for key in MODULE_KEYS
    )


def _history(score: float = 41.0) -> list[GaugeHistoryPoint]:
    rows: list[GaugeHistoryPoint] = []
    for day in (date(2026, 11, 5), date(2026, 11, 6)):
        for key in MODULE_KEYS:
            day_score = score + (0.0 if day == date(2026, 11, 5) else 1.0)
            rows.append(
                GaugeHistoryPoint(
                    run_date=day,
                    module_key=key,
                    score=day_score,
                    state_key="state3",
                    members_used=1,
                    members_total=2,
                    computed_at=GENERATED_AT,
                )
            )
    return rows


def _indicator(module_key: str) -> SeriesSnapshot:
    return SeriesSnapshot(
        key=f"{module_key}-ind",
        title_uk=f"Показник {module_key}",
        module_key=module_key,
        value=12.34,
        obs_date=date(2026, 11, 5),
        percentile=0.62,
        band=BandPoint(
            key="band_mid", label_uk="Нейтрально", color_token="state3", lower=None, upper=None
        ),
        is_stale=False,
        stale_reason_uk=None,
        meaning_uk="Сенс показника",
        source_uk="джерело",
        unit_uk="%",
    )


def _snapshot(scores: dict[str, float] | None = None) -> DashboardSnapshot:
    return DashboardSnapshot(
        as_of=date(2026, 11, 7),
        generated_at=GENERATED_AT,
        modules=_modules(scores),
        indicators=tuple(_indicator(key) for key in MODULE_KEYS),
        forward_rows=(),
        system_signal_uk="Сигнал",
        warnings_uk=(),
    )


def _commands(client: FakeGogClient) -> list[str]:
    """Subcommand verbs in call order (e.g. 'create', 'add-tab', 'batch-update')."""
    verbs: list[str] = []
    for call in client.calls:
        args = call["args"]
        if args[0] == "sheets" and len(args) > 1:
            verbs.append(args[1])
    return verbs


def _batches_for(client: FakeGogClient, tab_name: str) -> list[list[dict[str, Any]]]:
    result = []
    for payload in client.batches:
        first_range = payload[0]["range"] if payload else ""
        if first_range.startswith(f"'{tab_name}'!"):
            result.append(payload)
    return result


def _all_batch_ranges(client: FakeGogClient) -> list[str]:
    return [op["range"] for batch in client.batches for op in batch]


def _publish(
    store: FakeStore,
    client: FakeGogClient,
    *,
    snapshot: DashboardSnapshot | None = None,
    history: list[GaugeHistoryPoint] | None = None,
    reset_sheet: bool = False,
    folder_id: str | None = None,
    title: str = SPREADSHEET_TITLE_UK,
) -> PublishResult:
    publisher = GogSheetsPublisher(
        store, client=client, spreadsheet_title=title, folder_id=folder_id
    )
    return publisher.publish(
        snapshot if snapshot is not None else _snapshot(),
        history if history is not None else _history(),
        reset_sheet=reset_sheet,
    )


# ---------------------------------------------------------------------------
# Tests


def test_first_publish_creates_spreadsheet_and_tabs_in_order() -> None:
    store = FakeStore()
    client = FakeGogClient()

    result = _publish(store, client)

    assert result.status == "created"
    assert result.spreadsheet_id == FAKE_SSID
    # No metadata check happened: there was no stored ssid to verify.
    assert _commands(client)[0] == "create"
    create_args = client.calls[0]["args"]
    assert "--parent" not in create_args  # no folder configured
    # The API-default tab is cleaned up, our tabs are created in fixed order.
    add_tab_args = [call["args"] for call in client.calls if call["args"][1:2] == ["add-tab"]]
    created_tabs = [args[3] for args in add_tab_args]
    assert created_tabs == list(TAB_NAMES)
    # Persisted for the next run.
    assert store.get_setting(SETTING_SPREADSHEET_ID) == FAKE_SSID
    assert client.tabs == list(TAB_NAMES)  # default 'Sheet1' was deleted
    assert _commands(client).count("delete-tab") == 1


def test_first_publish_uses_folder_when_configured() -> None:
    store = FakeStore()
    client = FakeGogClient()

    result = _publish(store, client, folder_id="FOLDER123")

    assert result.status == "created"
    create_args = client.calls[0]["args"]
    assert "--parent" in create_args
    assert create_args[create_args.index("--parent") + 1] == "FOLDER123"


def test_batch_update_runs_once_per_tab_with_raw_input_and_temp_file() -> None:
    store = FakeStore()
    client = FakeGogClient()

    _publish(store, client)

    batch_calls = [call for call in client.calls if call["args"][1] == "batch-update"]
    assert len(batch_calls) == len(TAB_NAMES)
    for call in batch_calls:
        args = call["args"]
        assert "--input" in args and args[args.index("--input") + 1] == "RAW"
        assert any(arg.startswith("--data-json=@") for arg in args)
        data_arg = next(arg for arg in args if arg.startswith("--data-json=@"))
        assert Path(data_arg.split("=", 1)[1]).exists() is False  # temp cleaned up
        assert not data_arg.startswith("--data-json=@/Users/roman/Documents/Development/Signals")  # never inside repo
    # Exactly one batch per tab, each carrying that tab's quoted rows.
    for tab_name in TAB_NAMES:
        assert len(_batches_for(client, tab_name)) == 1


def test_values_written_are_quoted_ranges_with_numeric_cells() -> None:
    store = FakeStore()
    client = FakeGogClient()

    _publish(store, client)

    ranges = _all_batch_ranges(client)
    assert "'Огляд'!A1" in ranges
    overview_payload = _batches_for(client, TAB_OVERVIEW_UK)[0]
    numbers = [
        cell
        for op in overview_payload
        for row in op["values"]
        for cell in row
        if isinstance(cell, (int, float)) and not isinstance(cell, bool)
    ]
    assert numbers
    assert all(isinstance(cell, float) for cell in numbers)


def test_formatting_freeze_notes_and_widths_are_applied() -> None:
    store = FakeStore()
    client = FakeGogClient()

    _publish(store, client)

    commands = _commands(client)
    assert commands.count("format") >= len(TAB_NAMES)
    assert commands.count("number-format") >= len(TAB_NAMES)
    assert commands.count("freeze") == len(TAB_NAMES)
    assert commands.count("resize-columns") >= len(TAB_NAMES)
    assert commands.count("update-note") >= 1
    freeze_args = next(call["args"] for call in client.calls if call["args"][1] == "freeze")
    assert freeze_args[freeze_args.index("--rows") + 1] == "1"
    assert freeze_args[freeze_args.index("--sheet") + 1] == TAB_NAMES[0]
    note_args = next(call["args"] for call in client.calls if call["args"][1] == "update-note")
    assert note_args[note_args.index("--note") + 1]
    resize_args = next(call["args"] for call in client.calls if call["args"][1] == "resize-columns")
    assert resize_args[resize_args.index("--width") + 1].isdigit()


def test_chart_added_once_and_only_on_first_content() -> None:
    store = FakeStore()
    client = FakeGogClient()

    first = _publish(store, client)

    assert first.detail.endswith("chart ok")
    assert client.chart_titles == [CHART_TITLE_UK]
    chart_creates = [call for call in client.calls if call["args"][1:3] == ["chart", "create"]]
    assert len(chart_creates) == 1
    create_args = chart_creates[0]["args"]
    assert create_args[create_args.index("--sheet") + 1] == TAB_HISTORY_UK
    assert create_args[create_args.index("--anchor") + 1] == "I2"
    # The spec is the verified basicChart line chart over the history tab.
    spec = client.chart_specs[0]
    assert spec["title"] == CHART_TITLE_UK
    assert spec["basicChart"]["chartType"] == "LINE"
    assert len(spec["basicChart"]["series"]) == len(MODULE_KEYS)
    assert spec["basicChart"]["domains"][0]["source"]["startColumnIndex"] == 0

    second = _publish(store, client)
    assert second.status == "skipped_unchanged"
    chart_creates = [call for call in client.calls if call["args"][1:3] == ["chart", "create"]]
    assert len(chart_creates) == 1  # still exactly one add


def test_existing_ssid_is_verified_then_updated_without_recreate() -> None:
    store = FakeStore(stored_ssid=STORED_SSID)
    client = FakeGogClient(existing_tabs=TAB_NAMES)

    result = _publish(store, client)

    assert result.status == "updated"
    assert result.spreadsheet_id == STORED_SSID
    commands = _commands(client)
    assert commands[0] == "metadata"  # verification happened first
    assert "create" not in commands
    assert "add-tab" not in commands
    assert commands.count("batch-update") == len(TAB_NAMES)
    assert client.tabs == list(TAB_NAMES)


def test_stale_ssid_falls_back_to_creation() -> None:
    store = FakeStore(stored_ssid=STORED_SSID)
    client = FakeGogClient(fail_metadata_for=frozenset({STORED_SSID}))

    result = _publish(store, client)

    assert result.status == "created"
    commands = _commands(client)
    assert commands[0] == "metadata"
    assert "create" in commands
    assert store.get_setting(SETTING_SPREADSHEET_ID) == FAKE_SSID


def test_same_content_second_publish_is_skipped_without_gog_calls() -> None:
    store = FakeStore()
    client = FakeGogClient()

    first = _publish(store, client)
    assert first.status == "created"
    call_count = len(client.calls)

    second = _publish(store, client)

    assert second.status == "skipped_unchanged"
    assert second.spreadsheet_id == FAKE_SSID
    assert second.detail == "контент не змінився"
    assert len(client.calls) == call_count  # zero new gog invocations
    assert [run["status"] for run in store.publish_runs][-1] == "skipped_unchanged"


def test_changed_content_publishes_update() -> None:
    store = FakeStore(stored_ssid=FAKE_SSID)
    client = FakeGogClient(existing_tabs=TAB_NAMES)

    first = _publish(store, client)
    assert first.status == "updated"

    changed = _publish(store, client, snapshot=_snapshot(scores={"valuation": 71.5}))

    assert changed.status == "updated"
    valuation_batch = _batches_for(client, TAB_OVERVIEW_UK)[-1]
    values = [row[2] for op in valuation_batch for row in op["values"] if len(row) == 6]
    assert 71.5 in values


def test_reset_sheet_deletes_and_recreates_tabs_with_force() -> None:
    store = FakeStore()
    client = FakeGogClient()

    _publish(store, client)
    before_reset = len(client.calls)
    client.tabs = list(TAB_NAMES)  # pretend the tabs exist from the previous run

    result = _publish(store, client, reset_sheet=True)

    assert result.status == "updated"
    delete_calls = [
        call for call in client.calls[before_reset:] if call["args"][1:2] == ["delete-tab"]
    ]
    assert len(delete_calls) >= len(TAB_NAMES)
    assert all(call["force"] is True for call in delete_calls)


def test_gog_unavailable_skips_without_any_calls() -> None:
    store = FakeStore()
    client = FakeGogClient(available=False)

    result = _publish(store, client)

    assert result.status == "skipped_no_gog"
    assert result.spreadsheet_id is None
    assert result.detail == "gog не знайдено в PATH"
    assert client.calls == []
    assert store.publish_runs[-1]["status"] == "skipped_no_gog"


def test_unreachable_stored_ssid_and_create_parse_failure_raise() -> None:
    store = FakeStore(stored_ssid=STORED_SSID)
    client = FakeGogClient(fail_metadata_for=frozenset({STORED_SSID}))
    original_run = client._run

    def break_create(args: list[str], *, force: bool = False, retries: int = 3) -> GogOutcome:
        if args[1:2] == ["create"]:
            return GogOutcome(0, "not json at all", "")
        return original_run(args, force=force, retries=retries)

    client._run = break_create  # type: ignore[method-assign]

    with pytest.raises(MarketIntelPublishError):
        _publish(store, client)


def test_batch_update_failure_on_existing_sheet_raises() -> None:
    store = FakeStore(stored_ssid=STORED_SSID)
    client = FakeGogClient(existing_tabs=TAB_NAMES, fail_batch_update=True)

    with pytest.raises(MarketIntelPublishError) as excinfo:
        _publish(store, client)

    assert "PERMISSION_DENIED" in str(excinfo.value)
    # The failing run is not recorded as a successful publish.
    assert store.publish_runs == []


def test_clear_runs_before_each_tab_batch() -> None:
    store = FakeStore()
    client = FakeGogClient()

    _publish(store, client)

    commands = _commands(client)
    clear_indices = [i for i, command in enumerate(commands) if command == "clear"]
    batch_indices = [i for i, command in enumerate(commands) if command == "batch-update"]
    assert len(clear_indices) == len(TAB_NAMES)
    assert all(c < b for c, b in zip(clear_indices, batch_indices))
    clear_args = next(call["args"] for call in client.calls if call["args"][1] == "clear")
    assert clear_args[3] == f"'{TAB_NAMES[0]}'!A1:AN300"


# ---------------------------------------------------------------------------
# Real GogClient subprocess behavior (subprocess + sleep patched out)


def _patch_gog_runtime(monkeypatch: pytest.MonkeyPatch, responses: list[tuple[int, str]]) -> list[list[str]]:
    gog_client_module = importlib.import_module(
        "market_intel.infrastructure.export.gog_client"
    )
    ran_commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        del kwargs
        ran_commands.append(list(command))
        returncode, output = responses.pop(0)
        return SimpleNamespace(returncode=returncode, stdout=output, stderr="")

    monkeypatch.setattr(gog_client_module.subprocess, "run", fake_run)
    monkeypatch.setattr(gog_client_module.time, "sleep", lambda seconds: None)
    return ran_commands


def test_gog_client_places_no_input_and_force_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOG_ACCOUNT", "me@example.com")
    ran = _patch_gog_runtime(monkeypatch, [(0, "{}")])
    client = GogClient(min_interval_seconds=0.0)

    outcome = client._run(["sheets", "metadata", "SID", "-j"])

    assert outcome.returncode == 0
    assert ran[0] == [
        "gog",
        "sheets",
        "--no-input",
        "metadata",
        "SID",
        "-j",
        "--account",
        "me@example.com",
    ]


def test_gog_client_appends_force_flag_when_desctructive(monkeypatch: pytest.MonkeyPatch) -> None:
    ran = _patch_gog_runtime(monkeypatch, [(0, "{}")])
    client = GogClient(min_interval_seconds=0.0)

    client._run(["sheets", "delete-tab", "SID", "Tab"], force=True)

    assert ran[0][-1] == "-y"


def test_gog_client_retries_on_quota_and_gives_up_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran = _patch_gog_runtime(
        monkeypatch,
        [
            (1, "429 Resource has been exhausted"),
            (1, "429 Resource has been exhausted"),
            (0, '{"ok": true}'),
        ],
    )
    client = GogClient(min_interval_seconds=0.0)

    outcome = client._run(["sheets", "metadata", "SID", "-j"], retries=3)

    assert outcome.returncode == 0
    assert len(ran) == 3  # two quota failures then success


def test_gog_client_returns_failure_without_retry_on_plain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran = _patch_gog_runtime(monkeypatch, [(1, "PERMISSION_DENIED")])
    client = GogClient(min_interval_seconds=0.0)

    outcome = client._run(["sheets", "metadata", "SID", "-j"], retries=3)

    assert outcome.returncode == 1
    assert "PERMISSION_DENIED" in outcome.stderr or "PERMISSION_DENIED" in outcome.stdout
    assert len(ran) == 1


def test_gog_available_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    gog_client_module = importlib.import_module(
        "market_intel.infrastructure.export.gog_client"
    )
    monkeypatch.setattr(gog_client_module.shutil, "which", lambda name: "/usr/local/bin/gog" if name == "gog" else None)
    assert gog_client_module.gog_available() is True
    assert GogClient().gog_available() is True
    monkeypatch.setattr(gog_client_module.shutil, "which", lambda name: None)
    assert gog_client_module.gog_available() is False
    assert GogClient().gog_available() is False
