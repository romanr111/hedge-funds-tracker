from __future__ import annotations

from dataclasses import dataclass
from typing import Any


Position = dict[str, Any]


@dataclass(frozen=True)
class Manager:
    name: str
    cik: str


@dataclass(frozen=True)
class Filing:
    accession: str
    form: str
    filing_date: str | None
    report_date: str | None


@dataclass(frozen=True)
class ManagerState:
    cik: str
    name: str
    last_accession: str | None
    last_filing_date: str | None
    last_report_date: str | None
    last_positions: list[Position] | None


@dataclass(frozen=True)
class DiffResult:
    new_positions: list[Position]
    exited_positions: list[Position]
    increased_positions: list[Position]
    decreased_positions: list[Position]
