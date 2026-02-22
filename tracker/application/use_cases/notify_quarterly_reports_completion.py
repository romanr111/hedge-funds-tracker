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


def _quarter_label_from_year_quarter(year: int, quarter: int) -> str:
    return f"Q{quarter} {year}"


def _previous_report_quarter(day: date) -> tuple[int, int]:
    current_quarter = ((day.month - 1) // 3) + 1
    if current_quarter == 1:
        return (day.year - 1, 4)
    return (day.year, current_quarter - 1)


def _is_in_target_quarter(report_date: str | None, *, year: int, quarter: int) -> bool:
    report_day = parse_iso_date(report_date)
    if report_day is None:
        return False
    report_quarter = ((report_day.month - 1) // 3) + 1
    return (
        report_day.year == year
        and report_quarter == quarter
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
    target_year, target_quarter = _previous_report_quarter(today)
    quarter_label = _quarter_label_from_year_quarter(target_year, target_quarter)
    for manager in managers:
        manager_state = store.get_state(manager.cik)
        if manager_state is None or not _is_in_target_quarter(
            manager_state.last_report_date,
            year=target_year,
            quarter=target_quarter,
        ):
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
