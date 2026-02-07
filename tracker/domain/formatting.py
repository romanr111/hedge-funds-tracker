from __future__ import annotations


def format_subject(manager_name: str, filing_date: str | None) -> str:
    if filing_date:
        return f"{manager_name} 13F update ({filing_date})"
    return f"{manager_name} 13F update"
