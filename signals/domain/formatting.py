from __future__ import annotations

from datetime import datetime


def format_subject(manager_name: str) -> str:
    return f"🔥 {manager_name} 13F update"


def format_report_period(report_date: str | None) -> str:
    if not report_date:
        return "Unknown period"

    try:
        report_day = datetime.strptime(report_date, "%Y-%m-%d").date()
    except ValueError:
        return "Unknown period"

    quarter = ((report_day.month - 1) // 3) + 1
    return f"Q{quarter} {report_day.year}"
