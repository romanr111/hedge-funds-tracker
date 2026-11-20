"""multpl.com gateway: Shiller CAPE current + monthly history scraper.

Verified live 2026-11-07:
- current page text contains "Current Shiller PE Ratio is 38.91, a change of
  +0.12 from previous market close.";
- the by-month table (`<table id="datatable">`) holds newest-first rows like
  `<td>Nov 6, 2026</td><td>&#x2002; 38.91 </td>` where the leading row is the
  current daily value and the rest are month-start prints.
Monthly rows are stamped at month-end; month-ends in the future are skipped
(the current daily observation covers the live month). Vintage rows carry
series_id == spec.key.
"""
from __future__ import annotations

import calendar
import logging
import re
from datetime import date, datetime

import requests

from market_intel.domain.exceptions import CollectorError
from market_intel.domain.models import SeriesObservation, SeriesSpec
from market_intel.infrastructure.http_base import HttpGatewayBase, require_param
from signals.domain.timing import KYIV_TZ

logger = logging.getLogger(__name__)

CURRENT_VALUE_RE = re.compile(
    r"Current Shiller PE Ratio\s*(?:is)?\s*:?\s*(\d+(?:\.\d+)?)", flags=re.IGNORECASE
)
ROW_RE = re.compile(
    r"<td>\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})\s*</td>\s*<td[^>]*>\s*(?:&#x2002;|\s)*(-?\d+(?:\.\d+)?)\s*(%?)\s*</td>",
    flags=re.IGNORECASE,
)
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def parse_current_value(page_text: str) -> float | None:
    match = CURRENT_VALUE_RE.search(page_text)
    if match is None:
        return None
    return float(match.group(1))


def _parse_english_date(raw: str) -> date | None:
    raw = re.sub(r"\s+", " ", raw.strip())
    for pattern in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    return None


def parse_monthly_history(page_text: str, *, fetched_on: date) -> list[tuple[date, float]]:
    """Parse the by-month table into month-end (date, value) points.

    Newest-first rows for the same month collapse to the last parsed value
    (the freshest daily print wins). Percent-delta artifacts and future
    month-ends are skipped.
    """
    latest_by_month_end: dict[date, float] = {}
    for raw_date, raw_value, is_percent in ROW_RE.findall(page_text):
        if is_percent:
            continue  # delta artifact, not a level
        row_date = _parse_english_date(raw_date)
        if row_date is None:
            continue
        last_day = calendar.monthrange(row_date.year, row_date.month)[1]
        month_end = row_date.replace(day=last_day)
        if month_end > fetched_on:
            continue  # the live month is covered by the current-page observation
        if month_end not in latest_by_month_end:
            # Table is newest-first: the freshest print for a month wins.
            latest_by_month_end[month_end] = float(raw_value)
    return sorted(latest_by_month_end.items())


class MultplGateway(HttpGatewayBase):
    """SeriesFetcher for specs with source='multpl' (CAPE current + history)."""

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
            user_agent=BROWSER_UA,
        )

    def fetch(self, spec: SeriesSpec, *, now_utc: datetime) -> list[SeriesObservation]:
        if spec.source != "multpl":
            raise CollectorError(f"Серія '{spec.key}': очікувався source='multpl', отримано '{spec.source}'.")
        current_url = require_param(spec, "current_url")
        history_url = require_param(spec, "history_url")
        fetched_on = now_utc.astimezone(KYIV_TZ).date()

        current_text = self._get_text(current_url)
        current_value = parse_current_value(current_text)
        if current_value is None:
            raise CollectorError(
                f"multpl: не знайдено 'Current Shiller PE Ratio' ({current_url})."
            )
        history_text = self._get_text(history_url)
        history_points = parse_monthly_history(history_text, fetched_on=fetched_on)

        observations = [
            SeriesObservation(
                series_id=spec.key,
                obs_date=point_date,
                value=point_value,
                source="multpl",
                fetched_at=now_utc,
            )
            for point_date, point_value in history_points
            if point_date != fetched_on  # avoid colliding with the current print
        ]
        observations.append(
            SeriesObservation(
                series_id=spec.key,
                obs_date=fetched_on,
                value=current_value,
                source="multpl",
                fetched_at=now_utc,
            )
        )
        logger.debug(
            "multpl_fetch",
            extra={"series_key": spec.key, "rows": len(observations)},
        )
        return observations
