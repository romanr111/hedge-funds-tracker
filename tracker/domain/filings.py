from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from tracker.domain.models import Filing


TARGET_FORMS = {"13F-HR", "13F-HR/A"}


def extract_filings(submissions: dict[str, Any]) -> list[Filing]:
    recent = submissions.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber", [])
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])

    count = min(len(accessions), len(forms), len(filing_dates), len(report_dates))
    filings: list[Filing] = []
    for idx in range(count):
        if forms[idx] not in TARGET_FORMS:
            continue
        filings.append(
            Filing(
                accession=accessions[idx],
                form=forms[idx],
                filing_date=filing_dates[idx],
                report_date=report_dates[idx],
            )
        )

    filings.sort(key=lambda item: item.filing_date or "", reverse=True)
    return filings


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def filter_by_filing_age(filings: list[Filing], max_filing_age_days: int, *, today: date) -> list[Filing]:
    cutoff = today - timedelta(days=max_filing_age_days)
    filtered: list[Filing] = []
    for filing in filings:
        filing_day = parse_iso_date(filing.filing_date)
        # Keep unknown dates rather than potentially dropping a valid new filing.
        if filing_day is None or filing_day >= cutoff:
            filtered.append(filing)
    return filtered


def filing_to_dict(filing: Filing) -> dict[str, str | None]:
    return {
        "accession": filing.accession,
        "form": filing.form,
        "filing_date": filing.filing_date,
        "report_date": filing.report_date,
    }


def filings_from_dicts(items: list[dict[str, Any]]) -> list[Filing]:
    filings: list[Filing] = []
    for item in items:
        accession = item.get("accession")
        if not isinstance(accession, str):
            continue
        form_raw = item.get("form")
        form = form_raw if isinstance(form_raw, str) else ""
        filing_date = item.get("filing_date")
        report_date = item.get("report_date")
        filings.append(
            Filing(
                accession=accession,
                form=form,
                filing_date=filing_date if isinstance(filing_date, str) else None,
                report_date=report_date if isinstance(report_date, str) else None,
            )
        )
    return filings
