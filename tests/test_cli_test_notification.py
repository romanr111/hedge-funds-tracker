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
            notify_on_first_start=False, test_notification=True, dry_run=False, clean_state=None
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
