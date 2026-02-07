from __future__ import annotations

from typing import Protocol

from tracker.domain.models import ManagerState, Position


class StateRepository(Protocol):
    def get_state(self, cik: str) -> ManagerState | None:
        ...

    def upsert_state(
        self,
        *,
        cik: str,
        name: str,
        last_accession: str | None,
        last_filing_date: str | None,
        last_report_date: str | None,
        last_positions: list[Position] | None,
    ) -> None:
        ...

    def close(self) -> None:
        ...
