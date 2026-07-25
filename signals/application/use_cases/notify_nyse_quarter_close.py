from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta

from signals.application.ports.notifier import NotifierPort
from signals.application.ports.state_repository import StateRepository
from signals.application.ports.trading_calendar import TradingCalendarPort
from signals.domain.timing import now_kyiv

NYSE_QUARTER_CLOSE_STATE_CIK = "__nyse_quarter_close__"
NYSE_QUARTER_CLOSE_STATE_NAME = "NYSE quarter close"
MORNING_END_HOUR = 12


def _quarter_label(day: date) -> str:
    return f"Q{((day.month - 1) // 3) + 1} {day.year}"


def _quarter_end(day: date) -> date:
    quarter_end_month = (((day.month - 1) // 3) + 1) * 3
    if quarter_end_month == 12:
        return date(day.year, 12, 31)
    return date(day.year, quarter_end_month + 1, 1) - timedelta(days=1)


def _last_trading_day_of_quarter(day: date, calendar: TradingCalendarPort) -> date:
    candidate = _quarter_end(day)
    while not calendar.is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def _send_notifications(notifiers: Sequence[NotifierPort], subject: str, body: str) -> None:
    for notifier in notifiers:
        notifier.send(subject, body)


def notify_on_nyse_quarter_close(
    store: StateRepository,
    notifiers: Sequence[NotifierPort],
    calendar: TradingCalendarPort,
    *,
    dry_run: bool,
    now_fn: Callable[[], datetime] = now_kyiv,
    logger: logging.Logger | None = None,
) -> None:
    app_logger = logger or logging.getLogger(__name__)
    if dry_run or not notifiers:
        return

    now = now_fn()
    if now.hour >= MORNING_END_HOUR:
        return

    today = now.date()
    if today != _last_trading_day_of_quarter(today, calendar):
        return

    quarter_label = _quarter_label(today)
    marker_state = store.get_state(NYSE_QUARTER_CLOSE_STATE_CIK)
    if marker_state and marker_state.last_notified_accession == quarter_label:
        app_logger.info(
            "NYSE quarter-close notification skipped",
            extra={"status": "already_notified", "quarter": quarter_label},
        )
        return

    subject = f"📅 NYSE quarter-end: {quarter_label}"
    body = f"Today's NYSE session is the final trading session of {quarter_label}."
    _send_notifications(notifiers, subject, body)
    today_iso = today.isoformat()
    store.upsert_state(
        cik=NYSE_QUARTER_CLOSE_STATE_CIK,
        name=NYSE_QUARTER_CLOSE_STATE_NAME,
        last_accession=f"nyse-quarter-close-{quarter_label}",
        last_filing_date=today_iso,
        last_report_date=today_iso,
        last_positions=None,
        last_notified_accession=quarter_label,
    )
