from __future__ import annotations

import argparse
import importlib
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracker.application.use_cases.run_trend_engine import TrendEngineResult
from tracker.config import AppConfig, ManagerConfig

cli_main_module = importlib.import_module("tracker.interfaces.cli.main")


def test_main_triggers_trend_analysis_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_calls: list[dict[str, object]] = []
    call_order: list[str] = []

    class _FakeStore:
        def close(self) -> None:
            return None

    fake_store = _FakeStore()

    def fake_parse_args(self: argparse.ArgumentParser) -> argparse.Namespace:
        del self
        return argparse.Namespace(
            notify_on_first_start=False,
            clean_state=None,
            test_notification=False,
            dry_run=False,
            force_trend_recompute=False,
            show_trends_detailed=False,
            show_trends_only=False,
            trends_quarter=None,
            trends_min_conf=0.5,
            trends_limit=8,
            trends_show_reversals=True,
            trends_symbols_file="config/cusip_tickers.json",
            trend_live_prices_symbols_file="config/cusip_tickers.json",
            run_quarterly_pipeline=False,
            pipeline_quarter=None,
            pipeline_dry_run_report=False,
            backfill_trend_history=False,
            backfill_from_quarter=None,
            backfill_to_quarter=None,
            backfill_force=False,
            backfill_include_latest=False,
        )

    def fake_load_config(*, notify_initial: bool) -> AppConfig:
        assert notify_initial is False
        return AppConfig(
            sec_user_agent="Tracker/1.0 (test@example.com)",
            sec_rate_limit_per_sec=5.0,
            max_filing_age_days=180,
            db_path=Path("data/test.sqlite3"),
            managers=[ManagerConfig(name="Test Fund", cik="0000000001", weight=1.0)],
            notifiers=["telegram"],
            telegram_bot_token="token",
            telegram_chat_id="chat",
            notify_initial=False,
        )

    def fake_build_runtime(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(client=object(), store=fake_store, notifiers=[object()], history_gateway=object())

    def fake_process_manager(*args: object, **kwargs: object) -> None:
        del args, kwargs

    def fake_sync_quarter_snapshots(*args: object, **kwargs: object) -> int:
        del args, kwargs
        return 0

    def fake_run_trend_engine(*args: object, **kwargs: object) -> TrendEngineResult:
        del args, kwargs
        return TrendEngineResult(status="computed", report_quarter="2025Q4", signals_count=10)

    def fake_notify_trend_analysis_summary(store: object, notifiers: object, **kwargs: object) -> None:
        del store, notifiers
        call_order.append("trend_summary")
        captured_calls.append(dict(kwargs))

    def fake_notify_if_all_reports(*args: object, **kwargs: object) -> None:
        del args, kwargs
        call_order.append("quarterly_completion")

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    monkeypatch.setattr(cli_main_module, "load_config", fake_load_config)
    monkeypatch.setattr(cli_main_module, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli_main_module, "process_manager", fake_process_manager)
    monkeypatch.setattr(cli_main_module, "sync_quarter_snapshots", fake_sync_quarter_snapshots)
    monkeypatch.setattr(cli_main_module, "run_trend_engine_for_latest_completed_quarter", fake_run_trend_engine)
    monkeypatch.setattr(cli_main_module, "notify_trend_analysis_summary", fake_notify_trend_analysis_summary)
    monkeypatch.setattr(
        cli_main_module,
        "notify_if_all_reports_published_for_current_quarter",
        fake_notify_if_all_reports,
    )

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert len(captured_calls) == 1
    assert captured_calls[0]["trend_status"] == "computed"
    assert captured_calls[0]["report_quarter"] == "2025Q4"
    assert captured_calls[0]["manager_ciks"] == ["0000000001"]
    assert captured_calls[0]["show_reversals"] is True
    assert call_order == ["quarterly_completion", "trend_summary"]
