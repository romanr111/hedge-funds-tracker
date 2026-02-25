from __future__ import annotations

import argparse
import importlib
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracker.config import AppConfig, ManagerConfig

cli_main_module = importlib.import_module("tracker.interfaces.cli.main")


def test_send_trend_summary_from_db_skips_daily_collection_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    call_order: list[str] = []
    captured_summary_kwargs: list[dict[str, object]] = []

    class _FakeStore:
        def get_latest_trend_quarter(self) -> str | None:
            return "2025Q4"

        def close(self) -> None:
            call_order.append("close")

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
            send_trend_summary_from_db=True,
            send_trend_summary_force=True,
            trends_quarter=None,
            trends_min_conf=0.5,
            trends_limit=8,
            trends_show_reversals=True,
            trends_symbols_file="config/cusip_tickers.json",
            trend_live_prices_symbols_file="config/cusip_tickers.json",
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
            managers=[ManagerConfig(name="Fund A", cik="0000000001", weight=1.0)],
            notifiers=["telegram"],
            telegram_bot_token="token",
            telegram_chat_id="chat",
            notify_initial=False,
        )

    def fake_build_runtime(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(client=object(), store=fake_store, notifiers=[object()], history_gateway=object())

    def fail_daily(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Daily collection flow should not run in --send-trend-summary-from-db mode")

    def fake_notify_completion(*args: object, **kwargs: object) -> None:
        del args, kwargs
        call_order.append("completion")

    def fake_notify_summary(*args: object, **kwargs: object) -> None:
        call_order.append("summary")
        captured_summary_kwargs.append(dict(kwargs))

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    monkeypatch.setattr(cli_main_module, "load_config", fake_load_config)
    monkeypatch.setattr(cli_main_module, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli_main_module, "process_manager", fail_daily)
    monkeypatch.setattr(cli_main_module, "sync_quarter_snapshots", fail_daily)
    monkeypatch.setattr(cli_main_module, "run_trend_engine_for_latest_completed_quarter", fail_daily)
    monkeypatch.setattr(
        cli_main_module,
        "notify_if_all_reports_published_for_current_quarter",
        fake_notify_completion,
    )
    monkeypatch.setattr(cli_main_module, "notify_trend_analysis_summary", fake_notify_summary)

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert call_order == ["completion", "summary", "close"]
    assert len(captured_summary_kwargs) == 1
    kwargs = captured_summary_kwargs[0]
    assert kwargs["trend_status"] == "from_db"
    assert kwargs["report_quarter"] == "2025Q4"
    assert kwargs["force_send"] is True
