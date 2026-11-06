from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Protocol

from signals.application.ports.sec_gateway import SecGateway
from signals.domain.exceptions import InformationTableLookupError, InvalidInformationTableError, SubmissionsFetchError
from signals.domain.filing_sources import (
    all_possible_source_ciks,
    approved_source_ciks,
    composite_accession,
)
from signals.domain.filings import (
    NOTICE_FORMS,
    extract_13f_notice_other_manager_ciks,
    extract_filings,
    filter_by_filing_age,
    parse_iso_date,
    parse_iso_datetime,
)
from signals.domain.models import Filing, SnapshotSourceFiling
from signals.domain.parsing import parse_infotable
from signals.domain.quarters import quarter_sort_key, report_quarter_from_iso_date
from signals.domain.timing import now_kyiv
from signals.domain.trends import aggregate_positions_by_instrument
from signals.infrastructure.storage.sqlite_state_repository import StateStore


class _ManagerLike(Protocol):
    name: str
    cik: str


def _filing_recency_key(
    acceptance_datetime: str | None,
    filing_date: str | None,
    accession: str,
) -> tuple[datetime, datetime, str]:
    acceptance = parse_iso_datetime(acceptance_datetime)
    filed_day = parse_iso_date(filing_date)
    filing_dt = (
        datetime.combine(filed_day, datetime.min.time(), tzinfo=timezone.utc)
        if filed_day is not None
        else datetime.min.replace(tzinfo=timezone.utc)
    )
    return (
        acceptance or datetime.min.replace(tzinfo=timezone.utc),
        filing_dt,
        accession,
    )


def _notice_candidate_ciks(
    client: SecGateway,
    manager: _ManagerLike,
    notice: Filing,
    *,
    logger: logging.Logger,
) -> tuple[str, ...]:
    try:
        document_url = client.find_primary_document_url(
            manager.cik,
            notice.accession,
            notice.primary_document,
        )
        return extract_13f_notice_other_manager_ciks(client.get_text(document_url))
    except InformationTableLookupError as exc:
        logger.warning(
            "Could not parse 13F notice reporting-manager candidates",
            extra={
                "manager": manager.name,
                "cik": manager.cik,
                "accession": notice.accession,
                "error": str(exc),
            },
        )
        return ()


def sync_quarter_snapshots(
    managers: Sequence[_ManagerLike],
    store: StateStore,
    client: SecGateway,
    *,
    max_quarters: int = 9,
    max_filing_age_days: int = 180,
    dry_run: bool,
    now_fn: Callable[[], datetime] = now_kyiv,
    parse_infotable_fn: Callable[[str], list[dict[str, object]]] = parse_infotable,
    logger: logging.Logger | None = None,
) -> int:
    app_logger = logger or logging.getLogger(__name__)
    if dry_run or not managers:
        return 0

    managers_total = len(managers)
    app_logger.info(
        "Quarter snapshots sync started",
        extra={
            "managers_total": managers_total,
            "max_quarters": max_quarters,
            "max_filing_age_days": max_filing_age_days,
        },
    )

    upserted_rows = 0
    for index, manager in enumerate(managers, start=1):
        app_logger.info(
            "Quarter snapshots sync progress",
            extra={
                "manager": manager.name,
                "cik": manager.cik,
                "manager_index": index,
                "managers_total": managers_total,
                "stage": "fetch_submissions",
            },
        )

        submissions_by_cik: dict[str, dict[str, object]] = {}
        for source_cik in all_possible_source_ciks(manager):
            try:
                submissions_by_cik[source_cik] = client.get_all_submissions(source_cik)
            except SubmissionsFetchError as exc:
                app_logger.warning(
                    "Skipping filing source due to submissions error",
                    extra={
                        "manager": manager.name,
                        "cik": manager.cik,
                        "source_cik": source_cik,
                        "error": str(exc),
                    },
                )
        if not submissions_by_cik:
            continue

        filings_by_source_and_quarter: dict[str, dict[str, list[Filing]]] = defaultdict(
            lambda: defaultdict(list)
        )
        notice_filings_by_quarter: dict[str, list[Filing]] = defaultdict(list)
        candidate_quarters: set[str] = set()
        today = now_fn().date()
        for source_cik, submissions in submissions_by_cik.items():
            filings = filter_by_filing_age(extract_filings(submissions), max_filing_age_days, today=today)
            for filing in filings:
                report_quarter = report_quarter_from_iso_date(filing.report_date)
                if report_quarter is None or source_cik not in approved_source_ciks(manager, report_quarter):
                    continue
                filings_by_source_and_quarter[source_cik][report_quarter].append(filing)
                candidate_quarters.add(report_quarter)

            if source_cik != manager.cik:
                continue
            notices = filter_by_filing_age(
                extract_filings(submissions, allowed_forms=NOTICE_FORMS),
                max_filing_age_days,
                today=today,
            )
            for notice in notices:
                report_quarter = report_quarter_from_iso_date(notice.report_date)
                if report_quarter is None:
                    continue
                notice_filings_by_quarter[report_quarter].append(notice)
                candidate_quarters.add(report_quarter)

        selected_quarters = sorted(candidate_quarters, key=quarter_sort_key, reverse=True)[:max_quarters]
        manager_upserted_rows = 0
        for report_quarter in selected_quarters:
            expected_source_ciks = approved_source_ciks(manager, report_quarter)
            selected_filings: list[tuple[str, Filing]] = []
            for source_cik in expected_source_ciks:
                candidates = filings_by_source_and_quarter[source_cik].get(report_quarter, [])
                if not candidates:
                    continue
                selected_filings.append(
                    (
                        source_cik,
                        max(
                            candidates,
                            key=lambda item: _filing_recency_key(
                                item.acceptance_datetime,
                                item.filing_date,
                                item.accession,
                            ),
                        ),
                    )
                )

            if len(selected_filings) != len(expected_source_ciks):
                notices = notice_filings_by_quarter.get(report_quarter, [])
                if notices:
                    notice = max(
                        notices,
                        key=lambda item: _filing_recency_key(
                            item.acceptance_datetime,
                            item.filing_date,
                            item.accession,
                        ),
                    )
                    store.upsert_reporting_source_review(
                        manager_cik=manager.cik,
                        manager_name=manager.name,
                        report_quarter=report_quarter,
                        notice_accession=notice.accession,
                        notice_filing_date=notice.filing_date,
                        candidate_ciks=_notice_candidate_ciks(
                            client,
                            manager,
                            notice,
                            logger=app_logger,
                        ),
                    )
                continue

            raw_positions: list[dict[str, object]] = []
            source_filings: list[SnapshotSourceFiling] = []
            failed_source: str | None = None
            for source_cik, filing in selected_filings:
                try:
                    info_url = client.find_information_table_url(source_cik, filing.accession)
                    raw_positions.extend(parse_infotable_fn(client.get_text(info_url)))
                except (InformationTableLookupError, InvalidInformationTableError) as exc:
                    failed_source = source_cik
                    app_logger.warning(
                        "Skipping quarter snapshot due to fetch/parse error",
                        extra={
                            "manager": manager.name,
                            "cik": manager.cik,
                            "source_cik": source_cik,
                            "report_quarter": report_quarter,
                            "accession": filing.accession,
                            "error": str(exc),
                        },
                    )
                    break
                source_filings.append(
                    SnapshotSourceFiling(
                        source_cik=source_cik,
                        accession=filing.accession,
                        source_form=filing.form,
                        filing_date=filing.filing_date,
                        acceptance_datetime=filing.acceptance_datetime,
                    )
                )
            if failed_source is not None:
                continue

            positions = aggregate_positions_by_instrument(raw_positions)
            representative_filing = max(
                (filing for _, filing in selected_filings),
                key=lambda item: _filing_recency_key(
                    item.acceptance_datetime,
                    item.filing_date,
                    item.accession,
                ),
            )
            accession = (
                selected_filings[0][1].accession
                if len(selected_filings) == 1
                else composite_accession(selected_filings)
            )
            source_form = selected_filings[0][1].form if len(selected_filings) == 1 else "COMPOSITE"
            aum_value_k = sum(
                position["value"] for position in positions if isinstance(position.get("value"), int)
            )
            store.upsert_manager_quarter_snapshot(
                cik=manager.cik,
                manager_name=manager.name,
                report_quarter=report_quarter,
                report_date=representative_filing.report_date,
                filing_date=representative_filing.filing_date,
                acceptance_datetime=representative_filing.acceptance_datetime,
                accession=accession,
                source_form=source_form,
                positions=positions,
                aum_value_k=aum_value_k,
                source_filings=source_filings,
            )
            store.resolve_reporting_source_review(manager.cik, report_quarter)
            upserted_rows += 1
            manager_upserted_rows += 1

        app_logger.info(
            "Quarter snapshots manager synced",
            extra={
                "manager": manager.name,
                "cik": manager.cik,
                "manager_index": index,
                "managers_total": managers_total,
                "selected_quarters": len(selected_quarters),
                "upserted_rows_for_manager": manager_upserted_rows,
            },
        )

    app_logger.info(
        "Quarter snapshots sync completed",
        extra={"upserted_rows": upserted_rows, "managers_total": managers_total},
    )
    return upserted_rows
