"""FINRA gateway: margin statistics (debit balances in margin accounts).

Live probe 2026-11-07 returned 403 (Cloudflare "Just a moment...") from this
egress IP; at runtime from a residential IP the page is server-rendered HTML
with a monthly table whose header includes "Debit Balances in Customers'
Securities Margin Accounts" (amounts in $ millions). The parser below is
written against that documented layout and fails safe with a short
CollectorError reason if the layout defeats regex.
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

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", flags=re.IGNORECASE | re.DOTALL)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", flags=re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
MONTH_LABEL_RE = re.compile(
    r"^(january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\s+(\d{4})$",
    flags=re.IGNORECASE,
)


def _cell_text(raw: str) -> str:
    text = TAG_RE.sub(" ", raw)
    text = text.replace("&#39;", "'").replace("’", "'").replace("&#x2019;", "'")
    return re.sub(r"\s+", " ", text).strip()


def parse_margin_months(page_text: str) -> list[tuple[date, float]]:
    """Return month-end (date, debit balances in $M) points, oldest first.

    Raises CollectorError when the documented table layout cannot be found.
    """
    normalized = page_text.replace("&#39;", "'").replace("’", "'")
    rows: list[list[str]] = []
    for row_match in ROW_RE.finditer(normalized):
        cells = [_cell_text(cell) for cell in CELL_RE.findall(row_match.group(1))]
        if cells:
            rows.append(cells)

    debit_column: int | None = None
    data_rows: list[list[str]] = []
    for cells in rows:
        joined = " ".join(cells).lower()
        if "debit balances" in joined and "margin accounts" in joined:
            for index, cell in enumerate(cells):
                if cell.lower().startswith("debit balances"):
                    debit_column = index
                    break
            debit_column = debit_column if debit_column is not None else 1
            continue
        if debit_column is not None:
            data_rows.append(cells)
    if debit_column is None:
        raise CollectorError("finra: не знайдено таблицю з 'Debit Balances ... Margin Accounts'.")

    points: dict[date, float] = {}
    for cells in data_rows:
        if not cells:
            continue
        month_match = MONTH_LABEL_RE.match(cells[0])
        if month_match is None:
            continue
        month = MONTH_NAMES.get(month_match.group(1).lower())
        year = int(month_match.group(2))
        if month is None:
            continue
        if debit_column >= len(cells):
            continue
        raw_value = cells[debit_column].replace(",", "").replace("$", "").replace("+", "").strip()
        if not raw_value or not re.match(r"^-?\d+(\.\d+)?$", raw_value):
            continue
        last_day = calendar.monthrange(year, month)[1]
        points[date(year, month, last_day)] = float(raw_value)

    if not points:
        raise CollectorError("finra: не вдалося розпарсити жодного місячного рядка маржі.")
    return sorted(points.items())


class FinraGateway(HttpGatewayBase):
    """SeriesFetcher for specs with source='finra' (monthly margin debt)."""

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
        if spec.source != "finra":
            raise CollectorError(f"Серія '{spec.key}': очікувався source='finra', отримано '{spec.source}'.")
        url = require_param(spec, "url")
        page_text = self._get_text(url, headers={"Accept-Language": "en-US,en;q=0.9"})
        points = parse_margin_months(page_text)
        observations = [
            SeriesObservation(
                series_id=spec.key,
                obs_date=point_date,
                value=point_value,
                source="finra",
                fetched_at=now_utc,
            )
            for point_date, point_value in points
        ]
        logger.debug("finra_fetch", extra={"series_key": spec.key, "rows": len(observations)})
        return observations
