"""FRED CSV gateway: fredgraph.csv per-series fetcher (1 request per second).

Verified live 2026-11-07: GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2
returns header row `observation_date,<SERIES_ID>` followed by `YYYY-MM-DD,<value>`
rows; missing observations arrive as an empty value or a literal '.'.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime

import requests

from market_intel.domain.exceptions import CollectorError
from market_intel.domain.models import SeriesObservation, SeriesSpec
from market_intel.infrastructure.http_base import HttpGatewayBase, require_param

logger = logging.getLogger(__name__)

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
USER_AGENT = "market-intel/0.1 (local CLI)"
MISSING_VALUE_TOKENS: frozenset[str] = frozenset({"", "."})


def parse_fred_csv(payload: str, *, series_id: str) -> list[tuple[date, float]]:
    """Return (obs_date, value) tuples parsed from a fredgraph.csv payload."""
    rows: list[tuple[date, float]] = []
    reader = csv.reader(io.StringIO(payload))
    for row in reader:
        if len(row) < 2:
            continue
        if row[0].strip() == "observation_date":
            # Header row is authoritative; the value column must match the series id.
            if series_id not in (cell.strip() for cell in row[1:]):
                raise CollectorError(f"FRED CSV: unexpected header for series {series_id}.")
            continue
        raw_date = row[0].strip()
        raw_value = row[1].strip()
        if not raw_date or raw_value in MISSING_VALUE_TOKENS:
            continue
        try:
            obs_date = date.fromisoformat(raw_date.split(" ")[0])
            value = float(raw_value)
        except ValueError:
            continue
        rows.append((obs_date, value))
    return rows


class FredCsvGateway(HttpGatewayBase):
    """SeriesFetcher for specs with source='fred' (one HTTP call per series)."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        min_interval_seconds: float = 1.0,
        timeout: tuple[float, float] = (10.0, 60.0),
    ) -> None:
        super().__init__(
            session=session,
            min_interval_seconds=min_interval_seconds,
            timeout=timeout,
            user_agent=USER_AGENT,
        )

    def _fetch_csv(self, series_id: str, cosd: str | None) -> str:
        url = FRED_CSV_URL
        if cosd:
            # Range-limited request; on non-200 fall back to the plain full series.
            try:
                return self._get_text(url, params={"id": series_id, "cosd": cosd})
            except CollectorError:
                logger.warning("fred_cosd_retry_plain", extra={"series_id": series_id})
                return self._get_text(url, params={"id": series_id})
        return self._get_text(url, params={"id": series_id})

    def fetch(self, spec: SeriesSpec, *, now_utc: datetime) -> list[SeriesObservation]:
        if spec.source != "fred":
            raise CollectorError(f"Серія '{spec.key}': очікувався source='fred', отримано '{spec.source}'.")
        series_id = require_param(spec, "series_id")
        payload = self._fetch_csv(series_id, spec.params.get("cosd"))
        parsed = parse_fred_csv(payload, series_id=series_id)
        observations = [
            SeriesObservation(
                series_id=spec.key,
                obs_date=obs_date,
                value=value,
                source="fred",
                fetched_at=now_utc,
            )
            for obs_date, value in parsed
        ]
        logger.debug("fred_fetch", extra={"series_key": spec.key, "rows": len(observations)})
        return observations
