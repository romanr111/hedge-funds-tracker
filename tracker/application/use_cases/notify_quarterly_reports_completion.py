from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime

from tracker.application.ports.notifier import NotifierPort
from tracker.application.ports.state_repository import StateRepository
from tracker.domain.filings import parse_iso_date
from tracker.domain.models import Manager
from tracker.domain.timing import now_kyiv

QUARTERLY_COMPLETION_STATE_CIK = "__quarterly_reports_completion__"
QUARTERLY_COMPLETION_STATE_NAME = "Quarterly reports completion"


def _send_notifications(notifiers: Sequence[NotifierPort], subject: str, body: str) -> None:
    for notifier in notifiers:
        notifier.send(subject, body)


def _quarter_label(day: date) -> str:
    quarter = ((day.month - 1) // 3) + 1
    return f"Q{quarter} {day.year}"


def _is_in_same_quarter(report_date: str | None, *, day: date) -> bool:
    report_day = parse_iso_date(report_date)
    if report_day is None:
        return False
    return (
        report_day.year == day.year
        and ((report_day.month - 1) // 3) == ((day.month - 1) // 3)
    )


def notify_if_all_reports_published_for_current_quarter(
    managers: Sequence[Manager],
    store: StateRepository,
    notifiers: Sequence[NotifierPort],
    *,
    dry_run: bool,
    now_fn: Callable[[], datetime] = now_kyiv,
    logger: logging.Logger | None = None,
) -> None:
    app_logger = logger or logging.getLogger(__name__)
    if not managers or dry_run or not notifiers:
        return

    today = now_fn().date()
    quarter_label = _quarter_label(today)
    for manager in managers:
        manager_state = store.get_state(manager.cik)
        if manager_state is None or not _is_in_same_quarter(manager_state.last_report_date, day=today):
            app_logger.info(
                "Quarterly reports completion status",
                extra={
                    "status": "pending",
                    "quarter": quarter_label,
                    "missing_manager": manager.name,
                    "missing_manager_cik": manager.cik,
                },
            )
            return

    marker_state = store.get_state(QUARTERLY_COMPLETION_STATE_CIK)
    if marker_state and marker_state.last_notified_accession == quarter_label:
        app_logger.info(
            "Quarterly reports completion status",
            extra={"status": "already_notified", "quarter": quarter_label},
        )
        return

    subject = f"✅ All tracked funds reported for {quarter_label}"
    body = (
        f"All tracked funds ({len(managers)}) have published 13F reports "
        f"for {quarter_label}."
    )
    _send_notifications(notifiers, subject, body)
    store.upsert_state(
        cik=QUARTERLY_COMPLETION_STATE_CIK,
        name=QUARTERLY_COMPLETION_STATE_NAME,
        last_accession=f"all-reports-{quarter_label}",
        last_filing_date=today.isoformat(),
        last_report_date=today.isoformat(),
        last_positions=None,
        last_notified_accession=quarter_label,
    )
