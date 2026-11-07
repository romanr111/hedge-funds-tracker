"""Offline CLI tests for market_intel (importlib + monkeypatched deps)."""
from __future__ import annotations

import argparse
import importlib
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from market_intel.config import MarketIntelConfig
from market_intel.domain.exceptions import MarketIntelError, MarketIntelPublishError
from market_intel.domain.models import (
    CollectionResult,
    DashboardSnapshot,
    ModuleGauge,
    PublishResult,
    RunResult,
    SeriesSnapshot,
)

cli_main_module = importlib.import_module("market_intel.interfaces.cli.main")

AS_OF = date(2026, 11, 7)
GENERATED_AT = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)


class _FakeStore:
    def __init__(self) -> None:
        self.settings: dict[str, str] = {}
        self.closed = False

    def get_setting(self, key: str) -> str | None:
        return self.settings.get(key)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value

    def close(self) -> None:
        self.closed = True


class _RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def publish(self, *args: object, **kwargs: object) -> PublishResult:
        self.calls.append({"args": args, **kwargs})
        return PublishResult(status="created", spreadsheet_id="SS_REC", detail="ok")


def _config(tmp_path: Path) -> MarketIntelConfig:
    return MarketIntelConfig(
        db_path=tmp_path / "db.sqlite3",
        sheets_spreadsheet_id="",
        sheets_folder_id="",
        fred_interval_seconds=0.0,
        yahoo_interval_seconds=0.0,
        http_interval_seconds=0.0,
    )


def _module_gauge(module_key: str, score: float = 68.2) -> ModuleGauge:
    return ModuleGauge(
        module_key=module_key,
        title_uk="Валютація" if module_key == "valuation" else module_key,
        score=score,
        state_key="state5",
        chip_label_uk="Дорого",
        chip_color="#E57373",
        verdict_uk="Оцінки дорогі",
        context_forward_uk="Контекст",
        facts_uk=("Факт",),
        members_used=1,
        members_total=1,
        dropped_uk=(),
    )


def _indicator() -> SeriesSnapshot:
    return SeriesSnapshot(
        key="cape",
        title_uk="CAPE",
        module_key="valuation",
        value=38.2,
        obs_date=AS_OF,
        percentile=0.94,
        band=None,
        is_stale=False,
        stale_reason_uk=None,
        meaning_uk="Оцінка",
        source_uk="multpl",
        unit_uk="р.",
    )


def _dashboard() -> DashboardSnapshot:
    return DashboardSnapshot(
        as_of=AS_OF,
        generated_at=GENERATED_AT,
        modules=(_module_gauge("valuation"),),
        indicators=(_indicator(),),
        forward_rows=(),
        system_signal_uk="Сигнал",
        warnings_uk=(),
    )


def _run_result(
    *,
    fetched: tuple[str, ...] = ("fred",),
    skipped: tuple[tuple[str, str], ...] = (),
    errors: tuple[str, ...] = (),
    publish: PublishResult | None = None,
) -> RunResult:
    return RunResult(
        collected=CollectionResult(fetched=fetched, skipped=skipped, errors=errors),
        dashboard=_dashboard(),
        publish=publish,
    )


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "dry_run": False,
        "no_publish": False,
        "show": False,
        "reset_sheet": False,
        "db": None,
        "spreadsheet_id": None,
        "skip_source": [],
        "verbose": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _patch_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    args: argparse.Namespace,
    store: _FakeStore | None = None,
    publisher: _RecordingPublisher | None = None,
    run_result: RunResult | None = None,
    run_side_effect: Exception | None = None,
) -> tuple[_FakeStore, list[dict[str, object]]]:
    store = store or _FakeStore()
    publisher = publisher or _RecordingPublisher()
    recorded: list[dict[str, object]] = []

    monkeypatch.setattr(
        argparse.ArgumentParser, "parse_args", lambda self: args, raising=True
    )
    monkeypatch.setattr(cli_main_module, "load_config", lambda *, db_path: _config(tmp_path))
    monkeypatch.setattr(
        cli_main_module,
        "build_runtime",
        lambda config, *, dry_run: SimpleNamespace(
            store=store, fetchers={}, publisher=publisher
        ),
    )

    def fake_run_market_intel(config, runtime, *, dry_run, no_publish, reset_sheet, skip_sources=()):
        assert runtime.store is store
        recorded.append(
            {
                "dry_run": dry_run,
                "no_publish": no_publish,
                "reset_sheet": reset_sheet,
                "skip_sources": tuple(skip_sources),
            }
        )
        if run_side_effect is not None:
            raise run_side_effect
        return run_result or _run_result(publish=PublishResult("created", "SS123", "8 tabs"))

    monkeypatch.setattr(cli_main_module, "run_market_intel", fake_run_market_intel)
    return store, recorded


def test_cli_success_prints_summary_and_sheet_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store, recorded = _patch_cli(
        monkeypatch,
        tmp_path,
        args=_args(),
        run_result=_run_result(publish=PublishResult("created", "SS123", "8 tabs; 200 cells")),
    )

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert recorded[0]["dry_run"] is False
    assert recorded[0]["no_publish"] is False
    assert recorded[0]["reset_sheet"] is False
    assert store.closed is True
    out = capsys.readouterr().out
    assert "Ринкова розвідка - дані станом на 2026-11-07 (Київ)" in out
    assert "Валютація: Дорого (68.2) - Оцінки дорогі" in out
    assert "Sheet: created https://docs.google.com/spreadsheets/d/SS123" in out


def test_cli_dry_run_forwards_flags_and_has_no_publish_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store, recorded = _patch_cli(
        monkeypatch,
        tmp_path,
        args=_args(dry_run=True),
        run_result=_run_result(publish=None),
    )

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert recorded[0]["dry_run"] is True
    assert recorded[0]["no_publish"] is False
    out = capsys.readouterr().out
    assert "Sheet: публікація не виконувалась" in out
    assert "docs.google.com" not in out
    assert store.closed is True


def test_cli_no_publish_skips_publisher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    publisher = _RecordingPublisher()
    _, recorded = _patch_cli(
        monkeypatch,
        tmp_path,
        args=_args(no_publish=True),
        publisher=publisher,
        run_result=_run_result(publish=None),
    )

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert recorded[0]["no_publish"] is True
    assert publisher.calls == []
    assert "Sheet: публікація не виконувалась" in capsys.readouterr().out


def test_cli_show_forces_no_publish_and_prints_module_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    publisher = _RecordingPublisher()
    _, recorded = _patch_cli(
        monkeypatch,
        tmp_path,
        args=_args(show=True),
        publisher=publisher,
        run_result=_run_result(publish=None),
    )

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert recorded[0]["no_publish"] is True
    assert publisher.calls == []
    out = capsys.readouterr().out
    assert "Модуль: Валютація" in out
    assert "|" in out
    assert "CAPE" in out


def test_cli_spreadsheet_id_override_is_persisted_before_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, _ = _patch_cli(
        monkeypatch,
        tmp_path,
        args=_args(spreadsheet_id="OVERRIDE_ID"),
        run_result=_run_result(),
    )

    assert cli_main_module._main(logging.getLogger("test")) == 0
    assert store.settings["sheets_spreadsheet_id"] == "OVERRIDE_ID"


def test_cli_skip_source_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, recorded = _patch_cli(
        monkeypatch,
        tmp_path,
        args=_args(skip_source=["yahoo", "cnn_fg"]),
        run_result=_run_result(),
    )

    assert cli_main_module._main(logging.getLogger("test")) == 0
    assert recorded[0]["skip_sources"] == ("yahoo", "cnn_fg")


def test_cli_usage_conflict_returns_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_run(*args: object, **kwargs: object) -> RunResult:
        pytest.fail("run_market_intel must not run on conflicting flags")

    _patch_cli(
        monkeypatch,
        tmp_path,
        args=_args(dry_run=True, no_publish=True),
        run_side_effect=None,
    )
    monkeypatch.setattr(cli_main_module, "run_market_intel", fail_run)

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 2
    assert "Конфлікт прапорців" in capsys.readouterr().out


def test_cli_all_sources_failed_returns_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_cli(
        monkeypatch,
        tmp_path,
        args=_args(),
        run_result=_run_result(
            fetched=(), skipped=(("fred", "fetch-failed"),), publish=None
        ),
    )

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 2
    assert "Усі зовнішні джерела недоступні" in capsys.readouterr().out


def test_cli_computed_skips_do_not_count_as_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_cli(
        monkeypatch,
        tmp_path,
        args=_args(),
        run_result=_run_result(fetched=(), skipped=(("derived", "computed"),)),
    )

    assert cli_main_module._main(logging.getLogger("test")) == 0


def test_cli_degraded_publish_is_zero_with_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_cli(
        monkeypatch,
        tmp_path,
        args=_args(),
        run_result=_run_result(
            publish=PublishResult("skipped_no_gog", None, "gog не знайдено в PATH")
        ),
    )

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Sheet: skipped_no_gog" in out
    assert "Увага: gog не знайдено в PATH" in out


def test_cli_degraded_collect_errors_are_zero_with_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_cli(
        monkeypatch,
        tmp_path,
        args=_args(),
        run_result=_run_result(fetched=("fred",), errors=("yahoo: HTTP 503",), publish=None),
    )

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert "Увага: джерело недоступне: yahoo: HTTP 503" in capsys.readouterr().out


def test_main_value_error_returns_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_main_module, "configure_logging", lambda **kwargs: None)
    monkeypatch.setattr(
        cli_main_module, "load_config", lambda *, db_path: (_ for _ in ()).throw(ValueError("bad config"))
    )
    monkeypatch.setattr(
        argparse.ArgumentParser, "parse_args", lambda self: _args(), raising=True
    )

    assert cli_main_module.main() == 1  # type: ignore[no-untyped-call]
    assert "Помилка: bad config" in capsys.readouterr().out


def test_main_market_intel_error_returns_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_main_module, "configure_logging", lambda **kwargs: None)
    monkeypatch.setattr(
        argparse.ArgumentParser, "parse_args", lambda self: _args(), raising=True
    )
    monkeypatch.setattr(cli_main_module, "load_config", lambda *, db_path: _config(tmp_path))
    monkeypatch.setattr(
        cli_main_module,
        "build_runtime",
        lambda config, *, dry_run: (_ for _ in ()).throw(MarketIntelError("no store")),
    )

    assert cli_main_module.main() == 1  # type: ignore[no-untyped-call]


def test_main_publish_error_returns_three(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store, _ = _patch_cli(
        monkeypatch,
        tmp_path,
        args=_args(),
        run_side_effect=MarketIntelPublishError("sheet write failed"),
    )
    monkeypatch.setattr(cli_main_module, "configure_logging", lambda **kwargs: None)

    assert cli_main_module.main() == 3  # type: ignore[no-untyped-call]
    assert "Помилка публікації Sheet" in capsys.readouterr().out
    assert store.closed is True


def test_verbose_sets_debug_logging_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured_levels: list[int] = []

    def fake_configure_logging(*, level: int = logging.INFO) -> None:
        captured_levels.append(level)

    monkeypatch.setattr(cli_main_module, "configure_logging", fake_configure_logging)
    _patch_cli(monkeypatch, tmp_path, args=_args(verbose=True), run_result=_run_result(publish=None))

    # main() configures INFO first, then --verbose reconfigures DEBUG inside _main.
    assert cli_main_module.main() == 0  # type: ignore[no-untyped-call]
    assert captured_levels == [logging.INFO, logging.DEBUG]
