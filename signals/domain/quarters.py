from __future__ import annotations

from datetime import date

from signals.domain.filings import parse_iso_date


def report_quarter_for_day(day: date) -> str:
    quarter = ((day.month - 1) // 3) + 1
    return f"{day.year}Q{quarter}"


def report_quarter_from_iso_date(value: str | None) -> str | None:
    day = parse_iso_date(value)
    if day is None:
        return None
    return report_quarter_for_day(day)


def parse_report_quarter(value: str) -> tuple[int, int] | None:
    if len(value) != 6 or value[4] != "Q":
        return None
    year_raw = value[:4]
    quarter_raw = value[5]
    if not year_raw.isdigit() or quarter_raw not in {"1", "2", "3", "4"}:
        return None
    return (int(year_raw), int(quarter_raw))


def quarter_sort_key(value: str) -> tuple[int, int]:
    parsed = parse_report_quarter(value)
    if parsed is None:
        raise ValueError(f"Invalid report quarter: {value}")
    return parsed
