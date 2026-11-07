"""Yahoo chart gateway: v8/finance/chart daily closes (undocumented endpoint).

Live probe 2026-11-07 returned 429 "Too Many Requests" from this egress IP -
expected for the undocumented endpoint; the gateway therefore keeps a
consecutive-429 circuit breaker (mirrors the stooq DAILY_LIMIT_MARKER
behavior) and is tested against fixture payloads recorded from the
documented v8 chart schema.
"""
from __future__ import annotations

import logging
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from market_intel.domain.exceptions import CollectorError
from market_intel.domain.models import SeriesObservation, SeriesSpec
from market_intel.infrastructure.http_base import HttpGatewayBase, RateLimitExceeded, require_param

logger = logging.getLogger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
NY_TZ = ZoneInfo("America/New_York")
MARKET_CLOSE_HOUR = 16
RATE_LIMIT_BREAKER_THRESHOLD = 3
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class YahooRateLimited(CollectorError):
    """Yahoo chart endpoint is rate limiting this run."""


def parse_chart_json(payload: object, *, now_utc: datetime) -> list[tuple[datetime, float]]:
    """Parse chart.result[0] timestamps + quote closes into (ny_datetime, close)."""
    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as exc:
        raise CollectorError(f"Yahoo chart: unexpected payload shape ({exc!r}).") from exc
    if not isinstance(timestamps, list) or not isinstance(closes, list):
        raise CollectorError("Yahoo chart: timestamp/close arrays are missing.")

    points: list[tuple[datetime, float]] = []
    for timestamp, close in zip(timestamps, closes):
        if close is None:
            continue  # Yahoo emits None closes for halted/incomplete sessions.
        try:
            bar_time = datetime.fromtimestamp(float(timestamp), tz=NY_TZ)
            points.append((bar_time, float(close)))
        except (TypeError, ValueError, OverflowError):
            continue
    return _drop_incomplete_last_session(points, now_utc=now_utc)


def _drop_incomplete_last_session(
    points: list[tuple[datetime, float]], *, now_utc: datetime
) -> list[tuple[datetime, float]]:
    """Drop the last bar when it is today's not-yet-closed NY session (NY < 16:00)."""
    if not points:
        return points
    now_ny = now_utc.astimezone(NY_TZ)
    last_time, _ = points[-1]
    if last_time.date() == now_ny.date() and now_ny.hour < MARKET_CLOSE_HOUR:
        return points[:-1]
    return points


class YahooChartGateway(HttpGatewayBase):
    """SeriesFetcher for specs with source='yahoo' (daily closes, 5y range)."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = 1.5,
        timeout: tuple[float, float] = (10.0, 60.0),
        session: requests.Session | None = None,
    ) -> None:
        super().__init__(
            session=session,
            min_interval_seconds=min_interval_seconds,
            timeout=timeout,
            user_agent=BROWSER_UA,
        )
        self._chart_cache: dict[str, list[tuple[datetime, float]]] = {}
        self._consecutive_rate_limited = 0
        self._breaker_open = False

    @property
    def breaker_open(self) -> bool:
        return self._breaker_open

    def _get_symbol_payload(self, symbol: str) -> object:
        url = YAHOO_CHART_URL.format(symbol=urllib.parse.quote(symbol, safe=""))
        try:
            # retries=1 so the breaker counts single consecutive 429 responses.
            return self._get_json(url, params={"range": "5y", "interval": "1d"}, retries=1)
        except RateLimitExceeded:
            self._consecutive_rate_limited += 1
            if self._consecutive_rate_limited >= RATE_LIMIT_BREAKER_THRESHOLD:
                self._breaker_open = True
                logger.warning("yahoo_breaker_open", extra={"consecutive": self._consecutive_rate_limited})
            raise
        except CollectorError:
            self._consecutive_rate_limited = 0
            raise

    def fetch(self, spec: SeriesSpec, *, now_utc: datetime) -> list[SeriesObservation]:
        if spec.source != "yahoo":
            raise CollectorError(f"Серія '{spec.key}': очікувався source='yahoo', отримано '{spec.source}'.")
        symbol = require_param(spec, "symbol")
        if self._breaker_open:
            # Serve cached (usually empty) data without any further network for the rest of the run.
            cached = self._chart_cache.get(symbol, [])
            logger.warning(
                "yahoo_breaker_serving_empty",
                extra={"symbol": symbol, "note": "Yahoo rate limit: breaker open; no further network this run"},
            )
            return self._observations(symbol, cached, now_utc=now_utc)

        payload = self._get_symbol_payload(symbol)
        points = parse_chart_json(payload, now_utc=now_utc)
        self._consecutive_rate_limited = 0
        self._chart_cache[symbol] = points
        return self._observations(symbol, points, now_utc=now_utc)

    def _observations(
        self, symbol: str, points: list[tuple[datetime, float]], *, now_utc: datetime
    ) -> list[SeriesObservation]:
        return [
            SeriesObservation(
                series_id=symbol,
                obs_date=bar_time.date(),
                value=close,
                source="yahoo",
                fetched_at=now_utc,
            )
            for bar_time, close in points
        ]
