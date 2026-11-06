from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol

from signals.domain.models import Filing


class _ManagerLike(Protocol):
    """Structural type covering Manager and ManagerConfig for source resolution."""

    name: str
    cik: str


def approved_source_ciks(manager: _ManagerLike, report_quarter: str) -> tuple[str, ...]:
    """Resolve the explicitly approved source CIKs for a report quarter."""

    resolver = getattr(manager, "source_ciks_for_quarter", None)
    if callable(resolver):
        source_ciks = resolver(report_quarter)
        if isinstance(source_ciks, tuple) and source_ciks:
            return source_ciks
    return (manager.cik,)


def all_possible_source_ciks(manager: _ManagerLike) -> tuple[str, ...]:
    """Every source CIK the canonical manager can ever file through."""

    source_ciks = [manager.cik]
    for period in getattr(manager, "filing_source_periods", ()):
        source_ciks.extend(getattr(period, "source_ciks", ()))
    return tuple(dict.fromkeys(source_ciks))


def composite_accession(source_filings: Sequence[tuple[str, Filing]]) -> str:
    """Stable composite identifier shared by tracking accessions and snapshot accessions."""

    payload = "|".join(
        f"{source_cik}:{filing.accession}"
        for source_cik, filing in sorted(source_filings, key=lambda item: item[0])
    )
    return f"composite:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"
