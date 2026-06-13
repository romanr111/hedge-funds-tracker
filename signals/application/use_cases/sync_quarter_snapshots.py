from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from signals.application.ports.sec_gateway import SecGateway
from signals.domain.exceptions import InformationTableLookupError, InvalidInformationTableError, SubmissionsFetchError
from signals.domain.filings import extract_filings, filter_by_filing_age, parse_iso_date, parse_iso_datetime
from signals.domain.models import Manager
from signals.domain.parsing import parse_infotable
from signals.domain.quarters import quarter_sort_key, report_quarter_from_iso_date
from signals.domain.timing import now_kyiv
from signals.domain.trends import aggregate_positions_by_instrument
from signals.infrastructure.storage.sqlite_state_repository import StateStore


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


def sync_quarter_snapshots(
    managers: Sequence[Manager],
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

        manager_upserted_rows = 0
        selected_quarters_count = 0
        try:
            submissions = client.get_all_submissions(manager.cik)
        except SubmissionsFetchError as exc:
            app_logger.warning(
                "Skipping snapshot sync due to submissions error",
                extra={"manager": manager.name, "cik": manager.cik, "error": str(exc)},
            )
            continue

        filings = extract_filings(submissions)
        filings = filter_by_filing_age(filings, max_filing_age_days, today=now_fn().date())
        filings_by_quarter: dict[str, list[object]] = defaultdict(list)
        for filing in filings:
            report_quarter = report_quarter_from_iso_date(filing.report_date)
            if report_quarter is None:
                continue
            filings_by_quarter[report_quarter].append(filing)

        selected_quarters = sorted(filings_by_quarter.keys(), key=quarter_sort_key, reverse=True)[:max_quarters]
        selected_quarters_count = len(selected_quarters)
        for report_quarter in selected_quarters:
            filing = max(
                filings_by_quarter[report_quarter],
                key=lambda item: _filing_recency_key(item.acceptance_datetime, item.filing_date, item.accession),
            )
            try:
                info_url = client.find_information_table_url(manager.cik, filing.accession)
                xml_text = client.get_text(info_url)
                raw_positions = parse_infotable_fn(xml_text)
            except (InformationTableLookupError, InvalidInformationTableError) as exc:
                app_logger.warning(
                    "Skipping quarter snapshot due to fetch/parse error",
                    extra={
                        "manager": manager.name,
                        "cik": manager.cik,
                        "report_quarter": report_quarter,
                        "accession": filing.accession,
                        "error": str(exc),
                    },
                )
                continue

            positions = aggregate_positions_by_instrument(raw_positions)
            aum_value_k = sum(
                position["value"] for position in positions if isinstance(position.get("value"), int)
            )
            store.upsert_manager_quarter_snapshot(
                cik=manager.cik,
                manager_name=manager.name,
                report_quarter=report_quarter,
                report_date=filing.report_date,
                filing_date=filing.filing_date,
                acceptance_datetime=filing.acceptance_datetime,
                accession=filing.accession,
                source_form=filing.form,
                positions=positions,
                aum_value_k=aum_value_k,
            )
            upserted_rows += 1
            manager_upserted_rows += 1

        app_logger.info(
            "Quarter snapshots manager synced",
            extra={
                "manager": manager.name,
                "cik": manager.cik,
                "manager_index": index,
                "managers_total": managers_total,
                "selected_quarters": selected_quarters_count,
                "upserted_rows_for_manager": manager_upserted_rows,
            },
        )

    app_logger.info(
        "Quarter snapshots sync completed",
        extra={"upserted_rows": upserted_rows, "managers_total": managers_total},
    )
    return upserted_rows
