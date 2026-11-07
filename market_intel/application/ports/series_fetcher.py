"""Port for one source's series fetcher (implemented by HTTP/parse gateways)."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from market_intel.domain.models import SeriesObservation, SeriesSpec


class SeriesFetcher(Protocol):
    def fetch(self, spec: SeriesSpec, *, now_utc: datetime) -> list[SeriesObservation]:
        """Fetch fresh observations for the given spec (may return any history extent)."""
        ...
