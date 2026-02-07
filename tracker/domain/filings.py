from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from tracker.domain.models import Filing


TARGET_FORMS = {"13F-HR", "13F-HR/A"}


def extract_filings(submissions: dict[str, Any]) -> list[Filing]:
    recent = submissions.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber", [])
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    acceptance_datetimes = recent.get("acceptanceDateTime", [])

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
                acceptance_datetime=acceptance_datetimes[idx]
                if idx < len(acceptance_datetimes) and isinstance(acceptance_datetimes[idx], str)
                else None,
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


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(normalized, "%Y%m%d%H%M%S")
        except ValueError:
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def filter_by_filing_age(filings: list[Filing], max_filing_age_days: int, *, today: date) -> list[Filing]:
    cutoff = today - timedelta(days=max_filing_age_days)
    filtered: list[Filing] = []
    for filing in filings:
        filing_day = parse_iso_date(filing.filing_date)
        # Keep unknown dates rather than potentially dropping a valid new filing.
        if filing_day is None or filing_day >= cutoff:
            filtered.append(filing)
    return filtered


def is_filing_within_hours(filing: Filing, *, now: datetime, hours: int) -> bool:
    if hours <= 0:
        return False

    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    filed_at = parse_iso_datetime(filing.acceptance_datetime)
    if filed_at is None:
        # Fallback for legacy payloads without acceptance timestamp (date-only precision).
        filing_day = parse_iso_date(filing.filing_date)
        if filing_day is None:
            return False
        filed_at = datetime.combine(filing_day, time.min, tzinfo=timezone.utc)

    age = now_utc - filed_at
    return timedelta(0) <= age <= timedelta(hours=hours)


def filing_to_dict(filing: Filing) -> dict[str, str | None]:
    return {
        "accession": filing.accession,
        "form": filing.form,
        "filing_date": filing.filing_date,
        "report_date": filing.report_date,
        "acceptance_datetime": filing.acceptance_datetime,
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
        acceptance_datetime = item.get("acceptance_datetime")
        filings.append(
            Filing(
                accession=accession,
                form=form,
                filing_date=filing_date if isinstance(filing_date, str) else None,
                report_date=report_date if isinstance(report_date, str) else None,
                acceptance_datetime=acceptance_datetime if isinstance(acceptance_datetime, str) else None,
            )
        )
    return filings
