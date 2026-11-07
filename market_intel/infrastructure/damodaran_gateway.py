"""Damodaran gateway: implied US equity risk premium (annual table).

Live probe 2026-11-07: the old datafile/implprem.html URL is 404; the site's
"Current Data" page now points to
https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histimpl.html -
an annual table (Year ... "Implied ERP (FCFE)") with the most recent year in
the last data row (2025 -> 4.23%). The latest parseable row is the current
implied ERP; monthly .xlsx files (pc/implprem/ERP*.xlsx) exist but need a
spreadsheet parser, so the HTML annual table is the robust MVP source.
"""
from __future__ import annotations

import html as html_module
import logging
import re
from datetime import datetime

import requests

from market_intel.domain.exceptions import CollectorError
from market_intel.domain.models import SeriesObservation, SeriesSpec
from market_intel.infrastructure.http_base import HttpGatewayBase, require_param
from signals.domain.timing import now_kyiv

logger = logging.getLogger(__name__)

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", flags=re.IGNORECASE | re.DOTALL)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", flags=re.IGNORECASE | re.DOTALL)
YEAR_RE = re.compile(r"^\d{4}$")
PERCENT_RE = re.compile(r"^(\d+(?:\.\d+)?)%$")


def _cell_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_implied_erp(page_text: str) -> float | None:
    """Return the implied ERP percent from the last parseable data row."""
    erp_column: int | None = None
    latest_value: float | None = None
    for row_match in ROW_RE.finditer(page_text):
        cells = [_cell_text(cell) for cell in CELL_RE.findall(row_match.group(1))]
        joined = " ".join(cells).lower()
        if "year" in joined and "implied" in joined and "erp" in joined:
            for index, cell in enumerate(cells):
                if "implied" in cell.lower() and "erp" in cell.lower():
                    erp_column = index
                    break
            erp_column = erp_column if erp_column is not None else len(cells) - 1
            continue
        if erp_column is None:
            continue
        if not cells or YEAR_RE.match(cells[0]) is None:
            continue
        if erp_column >= len(cells):
            continue
        percent_match = PERCENT_RE.match(cells[erp_column].replace("\xa0", "").strip())
        if percent_match is None:
            continue
        latest_value = float(percent_match.group(1))
    return latest_value


class DamodaranGateway(HttpGatewayBase):
    """SeriesFetcher for specs with source='damodaran' (implied ERP)."""

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
            user_agent="market-intel/0.1 (local CLI)",
        )

    def fetch(self, spec: SeriesSpec, *, now_utc: datetime) -> list[SeriesObservation]:
        if spec.source != "damodaran":
            raise CollectorError(
                f"Серія '{spec.key}': очікувався source='damodaran', отримано '{spec.source}'."
            )
        url = require_param(spec, "url")
        page_text = self._get_text(url)
        implied_erp = parse_implied_erp(page_text)
        if implied_erp is None:
            raise CollectorError("damodaran: не вдалося знайти implied ERP у таблиці.")
        observation = SeriesObservation(
            series_id=spec.key,
            obs_date=now_kyiv().date(),
            value=implied_erp,
            source="damodaran",
            fetched_at=now_utc,
        )
        logger.debug("damodaran_fetch", extra={"series_key": spec.key, "implied_erp": implied_erp})
        return [observation]
