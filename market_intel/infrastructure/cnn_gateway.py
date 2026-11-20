"""CNN Fear & Greed gateway (unofficial production.dataviz.cnn.io endpoint).

Verified live 2026-11-07: with browser headers the endpoint returns JSON
with fear_and_greed.score (float, 22.08), rating ("extreme fear"),
previous_close and an iso timestamp (2026-11-06T23:59:54+00:00).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from market_intel.domain.exceptions import CollectorError
from market_intel.domain.models import SeriesObservation, SeriesSpec
from market_intel.infrastructure.http_base import HttpGatewayBase, require_param

logger = logging.getLogger(__name__)

US_TZ = ZoneInfo("America/New_York")
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def extract_fear_and_greed(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CollectorError("cnn_fg: відповідь не є JSON-об'єктом.")
    fg = payload.get("fear_and_greed")
    if not isinstance(fg, dict) or "score" not in fg:
        raise CollectorError("cnn_fg: не знайдено fear_and_greed.score у відповіді.")
    return fg


def parse_obs_date(fg: dict[str, Any], *, now_utc: datetime) -> date:
    """fetched_on if present, else the timestamp's US date, else today (US)."""
    raw = fg.get("fetched_on")
    if raw:
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", str(raw).strip())
        if match is not None:
            return date.fromisoformat(match.group(1))
    raw = fg.get("timestamp")
    if raw:
        try:
            stamp = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=US_TZ)
            return stamp.astimezone(US_TZ).date()
        except ValueError:
            pass
    return now_utc.astimezone(US_TZ).date()


class CnnFgGateway(HttpGatewayBase):
    """SeriesFetcher for specs with source='cnn_fg' (latest score)."""

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
        if spec.source != "cnn_fg":
            raise CollectorError(f"Серія '{spec.key}': очікувався source='cnn_fg', отримано '{spec.source}'.")
        url = require_param(spec, "url")
        payload = self._get_json(
            url,
            headers={
                "Referer": "https://edition.cnn.com/",
                "Accept": "application/json",
            },
        )
        fg = extract_fear_and_greed(payload)
        try:
            score = float(fg["score"])
        except (TypeError, ValueError) as exc:
            raise CollectorError(f"cnn_fg: score не є числом ({fg['score']!r}).") from exc
        obs_date = parse_obs_date(fg, now_utc=now_utc)
        observation = SeriesObservation(
            series_id=spec.key,
            obs_date=obs_date,
            value=score,
            source="cnn_fg",
            fetched_at=now_utc,
        )
        logger.debug("cnn_fg_fetch", extra={"series_key": spec.key, "score": score})
        return [observation]
