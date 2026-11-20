"""Port for the append-only vintage store (implemented by VintageStore)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, Sequence

from market_intel.domain.models import (
    GaugeHistoryPoint,
    PublishRunRecord,
    SeriesObservation,
)


class VintageRepository(Protocol):
    def append_observation(self, obs: SeriesObservation) -> bool:
        """Insert if (series_id, obs_date, value) is new; return True when inserted."""
        ...

    def append_observations(self, observations: Sequence[SeriesObservation]) -> int:
        """Bulk insert; return the number of rows actually inserted."""
        ...

    def read_point_in_time(self, series_id: str, as_of: date) -> SeriesObservation | None:
        """Latest raw observation with obs_date <= as_of by first print (min fetched_at)."""
        ...

    def read_history(self, series_id: str, *, start: date | None = None, end: date | None = None) -> list[SeriesObservation]:
        """All stored observations in [start, end], ordered by obs_date."""
        ...

    def upsert_gauge_history(self, point: GaugeHistoryPoint) -> None: ...
    def read_gauge_history(self) -> list[GaugeHistoryPoint]: ...

    def get_setting(self, key: str) -> str | None: ...
    def set_setting(self, key: str, value: str) -> None: ...

    def record_publish_run(
        self,
        *,
        spreadsheet_id: str | None,
        fingerprint: str | None,
        status: str,
        started_at: datetime | None,
        finished_at: datetime | None,
        detail: str,
    ) -> None: ...

    def last_publish_run(self) -> PublishRunRecord | None: ...

    def close(self) -> None: ...
