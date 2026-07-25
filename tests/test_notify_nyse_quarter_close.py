from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from signals.application.use_cases.notify_nyse_quarter_close import (
    NYSE_QUARTER_CLOSE_STATE_CIK,
    notify_on_nyse_quarter_close,
)
from signals.domain.models import ManagerState


class _CapturingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.sent.append((subject, body))


class _TradingCalendar:
    def __init__(self, trading_days: set[str]) -> None:
        self._trading_days = trading_days

    def is_trading_day(self, day: date) -> bool:
        return day.isoformat() in self._trading_days


class _Store:
    def __init__(self) -> None:
        self.states: dict[str, ManagerState] = {}

    def get_state(self, cik: str) -> ManagerState | None:
        return self.states.get(cik)

    def upsert_state(self, **kwargs: object) -> None:
        state = ManagerState(**kwargs)
        self.states[state.cik] = state


def _kyiv_datetime(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=ZoneInfo("Europe/Kyiv"))


@pytest.mark.parametrize(
    ("day", "quarter_label"),
    [
        (date(2024, 3, 28), "Q1 2024"),
        (date(2024, 6, 28), "Q2 2024"),
        (date(2024, 9, 30), "Q3 2024"),
        (date(2024, 12, 31), "Q4 2024"),
    ],
)
def test_morning_of_last_nyse_trading_day_sends_once_per_quarter(day: date, quarter_label: str) -> None:
    store = _Store()
    notifier = _CapturingNotifier()
    calendar = _TradingCalendar({day.isoformat()})

    notify_on_nyse_quarter_close(
        store,
        [notifier],
        calendar,
        dry_run=False,
        now_fn=lambda: _kyiv_datetime(day, 7),
    )
    notify_on_nyse_quarter_close(
        store,
        [notifier],
        calendar,
        dry_run=False,
        now_fn=lambda: _kyiv_datetime(day, 7),
    )

    assert notifier.sent == [
        (
            f"📅 NYSE quarter-end: {quarter_label}",
            f"Today's NYSE session is the final trading session of {quarter_label}.",
        )
    ]
    assert store.get_state(NYSE_QUARTER_CLOSE_STATE_CIK) is not None


def test_evening_run_does_not_send_quarter_close_notification() -> None:
    store = _Store()
    notifier = _CapturingNotifier()
    day = date(2024, 3, 28)
    calendar = _TradingCalendar({day.isoformat()})

    notify_on_nyse_quarter_close(
        store,
        [notifier],
        calendar,
        dry_run=False,
        now_fn=lambda: _kyiv_datetime(day, 19),
    )

    assert notifier.sent == []
    assert store.get_state(NYSE_QUARTER_CLOSE_STATE_CIK) is None
