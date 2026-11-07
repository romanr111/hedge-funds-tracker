"""Series registry (Agent A owns the entries).

Fill SERIES_SPECS with one SeriesSpec per series/computed value per the
approved plan (5 modules + display-only entries). Gateway-specific knobs
(fred series id, yahoo symbol, source URLs, parse hints) go into params.
Weights: None = display-only (excluded from composites: ecy, fed_funds; the
CAPE family is represented by `cape` alone - ecy is same-family anti-dup).
"""
from __future__ import annotations

from typing import Final, Mapping

from market_intel.domain.models import SeriesSpec

SERIES_SPECS: Final[Mapping[str, SeriesSpec]] = {}
