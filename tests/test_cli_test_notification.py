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


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.sent.append((subject, body))


def _build_config() -> AppConfig:
    return AppConfig(
        sec_user_agent="Tracker/1.0 (test@example.com)",
        sec_rate_limit_per_sec=5.0,
        max_filing_age_days=180,
        db_path=Path("data/test.sqlite3"),
        managers=[],
        notifiers=["telegram"],
        telegram_bot_token="token",
        telegram_chat_id="chat",
        notify_initial=False,
    )


def test_test_notification_skips_runtime_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_notifier = _FakeNotifier()

    def fake_parse_args(self: argparse.ArgumentParser) -> argparse.Namespace:
        del self
        return argparse.Namespace(
            notify_on_first_start=False,
            test_notification=True,
            dry_run=False,
            clean_state=None,
            force_trend_recompute=False,
            show_trends_detailed=False,
            trends_quarter=None,
            trends_min_conf=0.5,
            trends_limit=15,
            trends_show_reversals=False,
            trends_symbols_file="config/cusip_tickers.json",
            trend_live_prices_symbols_file="config/cusip_tickers.json",
        )

    def fake_load_config(*, notify_initial: bool) -> AppConfig:
        assert notify_initial is False
        return _build_config()

    def fake_build_notifier_list(
        config: AppConfig, *, dry_run: bool, test_notification: bool
    ) -> list[_FakeNotifier]:
        assert config.notifiers == ["telegram"]
        assert dry_run is False
        assert test_notification is True
        return [fake_notifier]

    def fail_build_runtime(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("build_runtime should not be called for --test-notification")

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    monkeypatch.setattr(cli_main_module, "load_config", fake_load_config)
    monkeypatch.setattr(cli_main_module, "build_notifier_list", fake_build_notifier_list)
    monkeypatch.setattr(cli_main_module, "build_runtime", fail_build_runtime)

    exit_code = cli_main_module._main(logging.getLogger("test"))
    assert exit_code == 0
    assert len(fake_notifier.sent) == 1


def test_clean_state_clears_store_before_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _FakeStore:
        def clear_state(self) -> int:
            calls.append("clear")
            return 2

        def close(self) -> None:
            calls.append("close")

    fake_store = _FakeStore()

    def fake_parse_args(self: argparse.ArgumentParser) -> argparse.Namespace:
        del self
        return argparse.Namespace(
            notify_on_first_start=True,
            clean_state="clean_state",
            test_notification=False,
            dry_run=False,
            force_trend_recompute=False,
            show_trends_detailed=False,
            trends_quarter=None,
            trends_min_conf=0.5,
            trends_limit=15,
            trends_show_reversals=False,
            trends_symbols_file="config/cusip_tickers.json",
            trend_live_prices_symbols_file="config/cusip_tickers.json",
        )

    def fake_load_config(*, notify_initial: bool) -> AppConfig:
        assert notify_initial is True
        return AppConfig(
            sec_user_agent="Tracker/1.0 (test@example.com)",
            sec_rate_limit_per_sec=5.0,
            max_filing_age_days=180,
            db_path=Path("data/test.sqlite3"),
            managers=[ManagerConfig(name="Test Fund", cik="0000000001", weight=1.0)],
            notifiers=[],
            telegram_bot_token=None,
            telegram_chat_id=None,
            notify_initial=True,
        )

    def fake_build_runtime(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(client=object(), store=fake_store, notifiers=[])

    def fake_process_manager(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls.append("process")

    def fake_sync_quarter_snapshots(*args: object, **kwargs: object) -> int:
        del args, kwargs
        calls.append("sync")
        return 1

    def fake_run_trend_engine(*args: object, **kwargs: object) -> TrendEngineResult:
        del args, kwargs
        calls.append("trend")
        return TrendEngineResult(status="computed", report_quarter="2025Q4", signals_count=10)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    monkeypatch.setattr(cli_main_module, "load_config", fake_load_config)
    monkeypatch.setattr(cli_main_module, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli_main_module, "process_manager", fake_process_manager)
    monkeypatch.setattr(cli_main_module, "sync_quarter_snapshots", fake_sync_quarter_snapshots)
    monkeypatch.setattr(cli_main_module, "run_trend_engine_for_latest_completed_quarter", fake_run_trend_engine)

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert calls == ["clear", "process", "sync", "trend", "close"]


def test_force_trend_recompute_flag_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_force_flag: list[bool] = []

    class _FakeStore:
        def clear_state(self) -> int:
            return 0

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
            force_trend_recompute=True,
            show_trends_detailed=False,
            trends_quarter=None,
            trends_min_conf=0.5,
            trends_limit=15,
            trends_show_reversals=False,
            trends_symbols_file="config/cusip_tickers.json",
            trend_live_prices_symbols_file="config/cusip_tickers.json",
        )

    def fake_load_config(*, notify_initial: bool) -> AppConfig:
        assert notify_initial is False
        return AppConfig(
            sec_user_agent="Tracker/1.0 (test@example.com)",
            sec_rate_limit_per_sec=5.0,
            max_filing_age_days=180,
            db_path=Path("data/test.sqlite3"),
            managers=[ManagerConfig(name="Test Fund", cik="0000000001", weight=1.0)],
            notifiers=[],
            telegram_bot_token=None,
            telegram_chat_id=None,
            notify_initial=False,
        )

    def fake_build_runtime(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(client=object(), store=fake_store, notifiers=[])

    def fake_process_manager(*args: object, **kwargs: object) -> None:
        del args, kwargs

    def fake_sync_quarter_snapshots(*args: object, **kwargs: object) -> int:
        del args, kwargs
        return 0

    def fake_run_trend_engine(*args: object, **kwargs: object) -> TrendEngineResult:
        captured_force_flag.append(bool(kwargs.get("force_recompute")))
        return TrendEngineResult(status="computed", report_quarter="2025Q4", signals_count=10)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    monkeypatch.setattr(cli_main_module, "load_config", fake_load_config)
    monkeypatch.setattr(cli_main_module, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli_main_module, "process_manager", fake_process_manager)
    monkeypatch.setattr(cli_main_module, "sync_quarter_snapshots", fake_sync_quarter_snapshots)
    monkeypatch.setattr(cli_main_module, "run_trend_engine_for_latest_completed_quarter", fake_run_trend_engine)

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert captured_force_flag == [True]


def test_prints_detailed_trend_table_when_ready_and_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    printed_quarters: list[str] = []

    class _FakeStore:
        def clear_state(self) -> int:
            return 0

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
            trends_quarter=None,
            trends_min_conf=0.5,
            trends_limit=15,
            trends_show_reversals=False,
            trends_symbols_file="config/cusip_tickers.json",
            trend_live_prices_symbols_file="config/cusip_tickers.json",
        )

    def fake_load_config(*, notify_initial: bool) -> AppConfig:
        assert notify_initial is False
        return AppConfig(
            sec_user_agent="Tracker/1.0 (test@example.com)",
            sec_rate_limit_per_sec=5.0,
            max_filing_age_days=180,
            db_path=Path("data/test.sqlite3"),
            managers=[ManagerConfig(name="Test Fund", cik="0000000001", weight=1.0)],
            notifiers=[],
            telegram_bot_token=None,
            telegram_chat_id=None,
            notify_initial=False,
        )

    def fake_build_runtime(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(client=object(), store=fake_store, notifiers=[])

    def fake_process_manager(*args: object, **kwargs: object) -> None:
        del args, kwargs

    def fake_sync_quarter_snapshots(*args: object, **kwargs: object) -> int:
        del args, kwargs
        return 0

    def fake_run_trend_engine(*args: object, **kwargs: object) -> TrendEngineResult:
        del args, kwargs
        return TrendEngineResult(status="computed", report_quarter="2025Q4", signals_count=10)

    def fake_print_detailed_table(
        store: object,
        report_quarter: str,
        *,
        min_conf: float = 0.5,
        limit: int = 15,
        show_reversals: bool = False,
        symbols_file: str = "config/cusip_tickers.json",
        manager_ciks: list[str] | None = None,
        view: str = "shortlist",
        explain: str | None = None,
    ) -> None:
        del store, min_conf, limit, show_reversals, symbols_file, manager_ciks, view, explain
        printed_quarters.append(report_quarter)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    monkeypatch.setattr(cli_main_module, "load_config", fake_load_config)
    monkeypatch.setattr(cli_main_module, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli_main_module, "process_manager", fake_process_manager)
    monkeypatch.setattr(cli_main_module, "sync_quarter_snapshots", fake_sync_quarter_snapshots)
    monkeypatch.setattr(cli_main_module, "run_trend_engine_for_latest_completed_quarter", fake_run_trend_engine)
    monkeypatch.setattr(cli_main_module, "_print_detailed_trend_table", fake_print_detailed_table)
    monkeypatch.setattr(cli_main_module.sys.stdout, "isatty", lambda: True)

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert printed_quarters == ["2025Q4"]


def test_show_trends_detailed_flag_prints_detailed_table(monkeypatch: pytest.MonkeyPatch) -> None:
    detailed_quarters: list[str] = []

    class _FakeStore:
        def clear_state(self) -> int:
            return 0

        def close(self) -> None:
            return None

        def get_latest_trend_quarter(self) -> str | None:
            return "2025Q4"

    fake_store = _FakeStore()

    def fake_parse_args(self: argparse.ArgumentParser) -> argparse.Namespace:
        del self
        return argparse.Namespace(
            notify_on_first_start=False,
            clean_state=None,
            test_notification=False,
            dry_run=False,
            force_trend_recompute=False,
            show_trends_detailed=True,
            trends_quarter=None,
            trends_min_conf=0.5,
            trends_limit=15,
            trends_show_reversals=False,
            trends_symbols_file="config/cusip_tickers.json",
            trend_live_prices_symbols_file="config/cusip_tickers.json",
        )

    def fake_load_config(*, notify_initial: bool) -> AppConfig:
        assert notify_initial is False
        return AppConfig(
            sec_user_agent="Tracker/1.0 (test@example.com)",
            sec_rate_limit_per_sec=5.0,
            max_filing_age_days=180,
            db_path=Path("data/test.sqlite3"),
            managers=[ManagerConfig(name="Test Fund", cik="0000000001", weight=1.0)],
            notifiers=[],
            telegram_bot_token=None,
            telegram_chat_id=None,
            notify_initial=False,
        )

    def fake_build_runtime(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(client=object(), store=fake_store, notifiers=[])

    def fake_process_manager(*args: object, **kwargs: object) -> None:
        del args, kwargs

    def fake_sync_quarter_snapshots(*args: object, **kwargs: object) -> int:
        del args, kwargs
        return 0

    def fake_run_trend_engine(*args: object, **kwargs: object) -> TrendEngineResult:
        del args, kwargs
        return TrendEngineResult(status="computed", report_quarter="2025Q4", signals_count=10)

    def fake_print_detailed_table(
        store: object,
        report_quarter: str,
        *,
        min_conf: float = 0.5,
        limit: int = 15,
        show_reversals: bool = False,
        symbols_file: str = "config/cusip_tickers.json",
        manager_ciks: list[str] | None = None,
        view: str = "shortlist",
        explain: str | None = None,
    ) -> None:
        del store, min_conf, limit, show_reversals, symbols_file, manager_ciks, view, explain
        detailed_quarters.append(report_quarter)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    monkeypatch.setattr(cli_main_module, "load_config", fake_load_config)
    monkeypatch.setattr(cli_main_module, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli_main_module, "process_manager", fake_process_manager)
    monkeypatch.setattr(cli_main_module, "sync_quarter_snapshots", fake_sync_quarter_snapshots)
    monkeypatch.setattr(cli_main_module, "run_trend_engine_for_latest_completed_quarter", fake_run_trend_engine)
    monkeypatch.setattr(cli_main_module, "_print_detailed_trend_table", fake_print_detailed_table)
    monkeypatch.setattr(cli_main_module.sys.stdout, "isatty", lambda: False)

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert detailed_quarters == ["2025Q4"]


def test_show_trends_only_uses_existing_db_and_skips_daily_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    detailed_quarters: list[str] = []

    class _FakeStore:
        def close(self) -> None:
            calls.append("close")

        def get_latest_trend_quarter(self) -> str | None:
            return "2025Q4"

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
            show_trends_only=True,
            trends_quarter="2024Q4",
            trends_min_conf=0.5,
            trends_limit=15,
            trends_show_reversals=False,
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
            managers=[ManagerConfig(name="Test Fund", cik="0000000001", weight=1.0)],
            notifiers=[],
            telegram_bot_token=None,
            telegram_chat_id=None,
            notify_initial=False,
        )

    def fake_build_runtime(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(client=object(), store=fake_store, notifiers=[], history_gateway=object())

    def fail_daily(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Daily flow should not run in --show-trends-only mode")

    def fake_print_detailed_table(
        store: object,
        report_quarter: str,
        *,
        min_conf: float = 0.5,
        limit: int = 15,
        show_reversals: bool = False,
        symbols_file: str = "config/cusip_tickers.json",
        manager_ciks: list[str] | None = None,
        view: str = "shortlist",
        explain: str | None = None,
    ) -> None:
        del store, min_conf, limit, show_reversals, symbols_file, manager_ciks, view, explain
        detailed_quarters.append(report_quarter)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    monkeypatch.setattr(cli_main_module, "load_config", fake_load_config)
    monkeypatch.setattr(cli_main_module, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli_main_module, "process_manager", fail_daily)
    monkeypatch.setattr(cli_main_module, "sync_quarter_snapshots", fail_daily)
    monkeypatch.setattr(cli_main_module, "run_trend_engine_for_latest_completed_quarter", fail_daily)
    monkeypatch.setattr(cli_main_module, "notify_if_all_reports_published_for_current_quarter", fail_daily)
    monkeypatch.setattr(cli_main_module, "run_backfill_trend_history", fail_daily)
    monkeypatch.setattr(cli_main_module, "_print_detailed_trend_table", fake_print_detailed_table)

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert detailed_quarters == ["2024Q4"]
    assert calls == ["close"]


def test_live_prices_are_loaded_and_passed_to_trend_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_latest_prices: list[dict[str, float] | None] = []

    class _FakeStore:
        def clear_state(self) -> int:
            return 0

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
            trends_quarter=None,
            trends_min_conf=0.5,
            trends_limit=15,
            trends_show_reversals=False,
            trends_symbols_file="config/cusip_tickers.json",
            trend_live_prices_symbols_file="config/cusip_tickers.json",
        )

    def fake_load_config(*, notify_initial: bool) -> AppConfig:
        assert notify_initial is False
        return AppConfig(
            sec_user_agent="Tracker/1.0 (test@example.com)",
            sec_rate_limit_per_sec=5.0,
            max_filing_age_days=180,
            db_path=Path("data/test.sqlite3"),
            managers=[ManagerConfig(name="Test Fund", cik="0000000001", weight=1.0)],
            notifiers=[],
            telegram_bot_token=None,
            telegram_chat_id=None,
            notify_initial=False,
        )

    def fake_build_runtime(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(client=object(), store=fake_store, notifiers=[])

    def fake_process_manager(*args: object, **kwargs: object) -> None:
        del args, kwargs

    def fake_sync_quarter_snapshots(*args: object, **kwargs: object) -> int:
        del args, kwargs
        return 0

    def fake_run_trend_engine(*args: object, **kwargs: object) -> TrendEngineResult:
        captured_latest_prices.append(kwargs.get("latest_prices"))
        return TrendEngineResult(status="computed", report_quarter="2025Q4", signals_count=10)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    monkeypatch.setattr(cli_main_module, "load_config", fake_load_config)
    monkeypatch.setattr(cli_main_module, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli_main_module, "process_manager", fake_process_manager)
    monkeypatch.setattr(cli_main_module, "sync_quarter_snapshots", fake_sync_quarter_snapshots)
    monkeypatch.setattr(cli_main_module, "run_trend_engine_for_latest_completed_quarter", fake_run_trend_engine)
    monkeypatch.setattr(cli_main_module, "_load_live_latest_prices", lambda **kwargs: {"02079K107": 314.9})

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert captured_latest_prices == [{"02079K107": 314.9}]


def test_backfill_mode_skips_daily_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _FakeStore:
        def close(self) -> None:
            calls.append("close")

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
            trends_quarter=None,
            trends_min_conf=0.5,
            trends_limit=15,
            trends_show_reversals=False,
            trends_symbols_file="config/cusip_tickers.json",
            trend_live_prices_symbols_file="config/cusip_tickers.json",
            backfill_trend_history=True,
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
            notifiers=[],
            telegram_bot_token=None,
            telegram_chat_id=None,
            notify_initial=False,
        )

    def fake_build_runtime(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(client=object(), store=fake_store, notifiers=[], history_gateway=object())

    def fail_daily(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Daily flow should not run in backfill mode")

    def fake_backfill(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        calls.append("backfill")
        return SimpleNamespace(
            status="completed",
            batch_id="batch-test",
            quarters_requested=3,
            computed=2,
            skipped_existing=1,
            failed=0,
            details=[],
        )

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    monkeypatch.setattr(cli_main_module, "load_config", fake_load_config)
    monkeypatch.setattr(cli_main_module, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli_main_module, "process_manager", fail_daily)
    monkeypatch.setattr(cli_main_module, "sync_quarter_snapshots", fail_daily)
    monkeypatch.setattr(cli_main_module, "run_trend_engine_for_latest_completed_quarter", fail_daily)
    monkeypatch.setattr(cli_main_module, "notify_if_all_reports_published_for_current_quarter", fail_daily)
    monkeypatch.setattr(cli_main_module, "run_backfill_trend_history", fake_backfill)
    monkeypatch.setattr(cli_main_module, "_load_live_latest_prices", lambda **kwargs: {"02079K107": 314.9})

    exit_code = cli_main_module._main(logging.getLogger("test"))

    assert exit_code == 0
    assert calls == ["backfill", "close"]
