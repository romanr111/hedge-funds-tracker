"""Port for the Google Sheets publisher (implemented by GogSheetsPublisher)."""
from __future__ import annotations

from typing import Protocol, Sequence

from market_intel.domain.models import DashboardSnapshot, GaugeHistoryPoint, PublishResult


class SheetsPublisher(Protocol):
    def publish(
        self,
        snapshot: DashboardSnapshot,
        history_rows: Sequence[GaugeHistoryPoint],
        *,
        reset_sheet: bool = False,
    ) -> PublishResult:
        """Idempotently create-or-update the dashboard spreadsheet."""
        ...
