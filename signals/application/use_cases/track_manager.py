from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime

from signals.application.ports.notifier import NotifierPort
from signals.application.ports.sec_gateway import SecGateway
from signals.application.ports.state_repository import StateRepository
from signals.domain.diffing import build_diff_message, diff_positions
from signals.domain.exceptions import InformationTableLookupError, InvalidInformationTableError, SubmissionsFetchError
from signals.domain.filings import extract_filings, filter_by_filing_age, is_filing_within_hours
from signals.domain.formatting import format_report_period, format_subject
from signals.domain.models import Manager, Position
from signals.domain.parsing import parse_infotable
from signals.domain.timing import now_kyiv

RECENT_NO_CHANGE_NOTIFICATION_HOURS = 24


def _send_notifications(notifiers: Sequence[NotifierPort], subject: str, body: str) -> None:
    for notifier in notifiers:
        notifier.send(subject, body)


def _build_no_position_changes_message(report_date: str | None, filing_date: str | None) -> str:
    return (
        f"📅 Period: {format_report_period(report_date)}\n"
        f"Filed {filing_date}.\n\n"
        "No position changes detected in this filing."
    )


def process_manager(
    manager: Manager,
    store: StateRepository,
    client: SecGateway,
    notifiers: Sequence[NotifierPort],
    *,
    notify_initial: bool,
    dry_run: bool,
    max_filing_age_days: int,
    parse_infotable_fn: Callable[[str], list[Position]] = parse_infotable,
    now_fn: Callable[[], datetime] = now_kyiv,
    logger: logging.Logger | None = None,
) -> None:
    app_logger = logger or logging.getLogger(__name__)
    try:
        submissions = client.get_submissions(manager.cik)
    except SubmissionsFetchError as exc:
        app_logger.warning(
            "Failed to fetch submissions",
            extra={"manager": manager.name, "cik": manager.cik, "error": str(exc)},
        )
        return
    filings = extract_filings(submissions)
    if not filings:
        app_logger.info("Manager status", extra={"manager": manager.name, "cik": manager.cik, "status": "no_filings"})
        return
    filings = filter_by_filing_age(filings, max_filing_age_days, today=now_fn().date())
    if not filings:
        app_logger.info(
            "Manager status",
            extra={"manager": manager.name, "cik": manager.cik, "status": "no_recent_filings"},
        )
        return

    state = store.get_state(manager.cik)
    last_accession = state.last_accession if state else None
    last_notified_accession = state.last_notified_accession if state else None
    new_filings = []

    if state is None:
        # Initial run: seed baseline from the latest filing only, avoid historical backfill spam.
        new_filings = filings[:1]
    else:
        for filing in filings:
            if last_accession and filing.accession == last_accession:
                break
            new_filings.append(filing)

    if not new_filings:
        app_logger.info("Manager status", extra={"manager": manager.name, "cik": manager.cik, "status": "no_new_filings"})
        if state is not None and last_accession and filings:
            latest_filing = filings[0]
            should_notify_existing_filing = (
                latest_filing.accession == last_accession
                and last_notified_accession != latest_filing.accession
                and is_filing_within_hours(latest_filing, now=now_fn(), hours=RECENT_NO_CHANGE_NOTIFICATION_HOURS)
            )
            if should_notify_existing_filing and not dry_run and notifiers:
                subject = format_subject(manager.name)
                body = _build_no_position_changes_message(latest_filing.report_date, latest_filing.filing_date)
                _send_notifications(notifiers, subject, body)
                last_notified_accession = latest_filing.accession
                store.upsert_state(
                    cik=state.cik,
                    name=state.name,
                    last_accession=state.last_accession,
                    last_filing_date=state.last_filing_date,
                    last_report_date=state.last_report_date,
                    last_positions=state.last_positions,
                    last_notified_accession=last_notified_accession,
                )
        return

    previous_positions = state.last_positions if state else None

    # Process oldest-to-newest so diffs are deterministic.
    for filing in reversed(new_filings):
        notified_for_filing = False
        try:
            info_url = client.find_information_table_url(manager.cik, filing.accession)
            xml_text = client.get_text(info_url)
            positions = parse_infotable_fn(xml_text)
        except (InformationTableLookupError, InvalidInformationTableError) as exc:
            app_logger.warning(
                "Skipping accession due to fetch/parse error",
                extra={"manager": manager.name, "cik": manager.cik, "accession": filing.accession, "error": str(exc)},
            )
            continue

        if not previous_positions:
            if notify_initial:
                subject = format_subject(manager.name)
                body = (
                    f"Baseline stored for {manager.name} ({manager.cik}).\n\n"
                    f"📅 Period: {format_report_period(filing.report_date)}"
                )
                if not dry_run and notifiers:
                    _send_notifications(notifiers, subject, body)
                    notified_for_filing = True
            else:
                app_logger.info(
                    "Manager status",
                    extra={
                        "manager": manager.name,
                        "cik": manager.cik,
                        "status": "baseline_stored",
                        "accession": filing.accession,
                    },
                )
        else:
            diff = diff_positions(previous_positions, positions)
            if any(
                [
                    diff.new_positions,
                    diff.exited_positions,
                    diff.increased_positions,
                    diff.decreased_positions,
                ]
            ):
                app_logger.info(
                    "Position changes detected",
                    extra={
                        "manager": manager.name,
                        "cik": manager.cik,
                        "accession": filing.accession,
                        "new_positions_count": len(diff.new_positions),
                        "exited_positions_count": len(diff.exited_positions),
                        "increased_positions_count": len(diff.increased_positions),
                        "decreased_positions_count": len(diff.decreased_positions),
                    },
                )
                subject = format_subject(manager.name)
                summary = build_diff_message(diff)
                body = (
                    f"📅 Period: {format_report_period(filing.report_date)}\n"
                    f"Filed {filing.filing_date}.\n\n"
                    f"{summary}"
                )
                if not dry_run and notifiers:
                    _send_notifications(notifiers, subject, body)
                    notified_for_filing = True
            else:
                app_logger.info(
                    "Manager status",
                    extra={
                        "manager": manager.name,
                        "cik": manager.cik,
                        "status": "no_position_changes",
                        "accession": filing.accession,
                    },
                )
                if (
                    is_filing_within_hours(filing, now=now_fn(), hours=RECENT_NO_CHANGE_NOTIFICATION_HOURS)
                    and last_notified_accession != filing.accession
                    and not dry_run
                    and notifiers
                ):
                    subject = format_subject(manager.name)
                    body = _build_no_position_changes_message(filing.report_date, filing.filing_date)
                    _send_notifications(notifiers, subject, body)
                    notified_for_filing = True

        if not dry_run:
            if notified_for_filing:
                last_notified_accession = filing.accession
            store.upsert_state(
                cik=manager.cik,
                name=manager.name,
                last_accession=filing.accession,
                last_filing_date=filing.filing_date,
                last_report_date=filing.report_date,
                last_positions=positions,
                last_notified_accession=last_notified_accession,
            )
        previous_positions = positions
