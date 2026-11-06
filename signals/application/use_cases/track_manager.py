from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from signals.application.ports.notifier import NotifierPort
from signals.application.ports.sec_gateway import SecGateway
from signals.application.ports.state_repository import StateRepository
from signals.domain.diffing import build_diff_message, diff_positions
from signals.domain.exceptions import InformationTableLookupError, InvalidInformationTableError, SubmissionsFetchError
from signals.domain.filing_sources import (
    all_possible_source_ciks,
    approved_source_ciks,
    composite_accession,
)
from signals.domain.filings import (
    extract_filings,
    filter_by_filing_age,
    is_filing_within_hours,
    parse_iso_date,
    parse_iso_datetime,
)
from signals.domain.formatting import format_report_period, format_subject
from signals.domain.models import Filing, Manager, Position
from signals.domain.parsing import parse_infotable
from signals.domain.quarters import report_quarter_from_iso_date
from signals.domain.timing import now_kyiv
from signals.domain.trends import aggregate_positions_by_instrument

RECENT_NO_CHANGE_NOTIFICATION_HOURS = 24


@dataclass(frozen=True)
class _FilingBatch:
    filing: Filing
    source_filings: tuple[tuple[str, Filing], ...]


def _filing_recency_key(filing: Filing) -> tuple[datetime, datetime, str]:
    acceptance = parse_iso_datetime(filing.acceptance_datetime)
    filing_day = parse_iso_date(filing.filing_date)
    filing_datetime = (
        datetime.combine(filing_day, datetime.min.time(), tzinfo=timezone.utc)
        if filing_day is not None
        else datetime.min.replace(tzinfo=timezone.utc)
    )
    return (
        acceptance or datetime.min.replace(tzinfo=timezone.utc),
        filing_datetime,
        filing.accession,
    )


def _composite_filing(source_filings: tuple[tuple[str, Filing], ...]) -> Filing:
    representative = max((filing for _, filing in source_filings), key=_filing_recency_key)
    return Filing(
        accession=composite_accession(source_filings),
        form="COMPOSITE",
        filing_date=representative.filing_date,
        report_date=representative.report_date,
        acceptance_datetime=representative.acceptance_datetime,
    )


def _collect_filing_batches(
    manager: Manager,
    client: SecGateway,
    *,
    max_filing_age_days: int,
    today: datetime,
    logger: logging.Logger,
) -> list[_FilingBatch]:
    filings_by_source_and_quarter: dict[str, dict[str, list[Filing]]] = defaultdict(
        lambda: defaultdict(list)
    )
    single_source_batches: list[_FilingBatch] = []
    composite_quarters: set[str] = set()

    for source_cik in all_possible_source_ciks(manager):
        try:
            submissions = client.get_submissions(source_cik)
        except SubmissionsFetchError as exc:
            logger.warning(
                "Failed to fetch submissions",
                extra={
                    "manager": manager.name,
                    "cik": manager.cik,
                    "source_cik": source_cik,
                    "error": str(exc),
                },
            )
            continue

        filings = filter_by_filing_age(
            extract_filings(submissions),
            max_filing_age_days,
            today=today.date(),
        )
        for filing in filings:
            report_quarter = report_quarter_from_iso_date(filing.report_date)
            if report_quarter is None:
                continue
            expected_source_ciks = approved_source_ciks(manager, report_quarter)
            if source_cik not in expected_source_ciks:
                continue
            if len(expected_source_ciks) == 1:
                single_source_batches.append(
                    _FilingBatch(filing=filing, source_filings=((source_cik, filing),))
                )
                continue
            filings_by_source_and_quarter[source_cik][report_quarter].append(filing)
            composite_quarters.add(report_quarter)

    composite_batches: list[_FilingBatch] = []
    for report_quarter in composite_quarters:
        selected_filings: list[tuple[str, Filing]] = []
        for source_cik in approved_source_ciks(manager, report_quarter):
            candidates = filings_by_source_and_quarter[source_cik].get(report_quarter, [])
            if not candidates:
                continue
            selected_filings.append((source_cik, max(candidates, key=_filing_recency_key)))
        if len(selected_filings) != len(approved_source_ciks(manager, report_quarter)):
            logger.info(
                "Manager status",
                extra={
                    "manager": manager.name,
                    "cik": manager.cik,
                    "status": "incomplete_composite_sources",
                    "report_quarter": report_quarter,
                },
            )
            continue
        source_filings = tuple(selected_filings)
        composite_batches.append(
            _FilingBatch(
                filing=_composite_filing(source_filings),
                source_filings=source_filings,
            )
        )

    batches = single_source_batches + composite_batches
    batches.sort(key=lambda item: _filing_recency_key(item.filing), reverse=True)
    return batches


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
    filing_batches = _collect_filing_batches(
        manager,
        client,
        max_filing_age_days=max_filing_age_days,
        today=now_fn(),
        logger=app_logger,
    )
    if not filing_batches:
        app_logger.info("Manager status", extra={"manager": manager.name, "cik": manager.cik, "status": "no_filings"})
        return

    state = store.get_state(manager.cik)
    last_accession = state.last_accession if state else None
    last_notified_accession = state.last_notified_accession if state else None
    new_filing_batches: list[_FilingBatch] = []

    if state is None:
        # Initial run: seed baseline from the latest filing only, avoid historical backfill spam.
        new_filing_batches = filing_batches[:1]
    else:
        for filing_batch in filing_batches:
            if last_accession and filing_batch.filing.accession == last_accession:
                break
            new_filing_batches.append(filing_batch)

    if not new_filing_batches:
        app_logger.info("Manager status", extra={"manager": manager.name, "cik": manager.cik, "status": "no_new_filings"})
        if state is not None and last_accession and filing_batches:
            latest_filing = filing_batches[0].filing
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
    for filing_batch in reversed(new_filing_batches):
        filing = filing_batch.filing
        notified_for_filing = False
        raw_positions: list[Position] = []
        failed_source: str | None = None
        for source_cik, source_filing in filing_batch.source_filings:
            try:
                info_url = client.find_information_table_url(source_cik, source_filing.accession)
                raw_positions.extend(parse_infotable_fn(client.get_text(info_url)))
            except (InformationTableLookupError, InvalidInformationTableError) as exc:
                failed_source = source_cik
                app_logger.warning(
                    "Skipping accession due to fetch/parse error",
                    extra={
                        "manager": manager.name,
                        "cik": manager.cik,
                        "source_cik": source_cik,
                        "accession": source_filing.accession,
                        "error": str(exc),
                    },
                )
                break
        if failed_source is not None:
            continue
        positions = (
            raw_positions
            if len(filing_batch.source_filings) == 1
            else aggregate_positions_by_instrument(raw_positions)
        )

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
