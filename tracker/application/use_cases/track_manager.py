from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime

from tracker.application.ports.notifier import NotifierPort
from tracker.application.ports.sec_gateway import SecGateway
from tracker.application.ports.state_repository import StateRepository
from tracker.domain.diffing import build_diff_message, diff_positions
from tracker.domain.exceptions import InformationTableLookupError, InvalidInformationTableError, SubmissionsFetchError
from tracker.domain.filings import extract_filings, filter_by_filing_age
from tracker.domain.formatting import format_report_period, format_subject
from tracker.domain.models import Manager, Position
from tracker.domain.parsing import parse_infotable
from tracker.domain.timing import now_kyiv


def _send_notifications(notifiers: Sequence[NotifierPort], subject: str, body: str) -> None:
    for notifier in notifiers:
        notifier.send(subject, body)


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
        return

    previous_positions = state.last_positions if state else None

    # Process oldest-to-newest so diffs are deterministic.
    for filing in reversed(new_filings):
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
                if not dry_run:
                    _send_notifications(notifiers, subject, body)
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
                if not dry_run:
                    _send_notifications(notifiers, subject, body)
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

        if not dry_run:
            store.upsert_state(
                cik=manager.cik,
                name=manager.name,
                last_accession=filing.accession,
                last_filing_date=filing.filing_date,
                last_report_date=filing.report_date,
                last_positions=positions,
            )
        previous_positions = positions
