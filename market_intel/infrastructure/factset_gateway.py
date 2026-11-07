"""FactSet EarningsInsight gateway: forward 12-month P/E from the latest PDF.

Verified live 2026-11-07: https://www.factset.com/earningsinsight is served
after a 302 redirect as the raw PDF (advantage.factset.com/hubfs/...
EarningsInsight_110626A.pdf). pdftotext -layout text contains
"the forward 12-month P/E ratio for the S&P 500 is 17.7" near the top.
The gateway handles both the direct-PDF response and an HTML landing page
with .pdf links, always parses from a temp file that is deleted after use.

Observation date: the report's own publication date, resolved in priority
order (1) MMDDYY stamp in the resolved PDF filename (EarningsInsight_110626A.pdf
-> 2026-11-06), (2) a full 'Month D, YYYY' cover date anchored to the report
context (never the naive first date: weekly bodies cite historical references
like "since November 1, 2023"), (3) the injected run clock (now_utc in Kyiv
time) as the last resort so idempotent INSERT OR IGNORE dedupe still holds.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from urllib.parse import urljoin

import requests

from market_intel.domain.exceptions import CollectorError
from market_intel.domain.models import SeriesObservation, SeriesSpec
from market_intel.infrastructure.http_base import HttpGatewayBase, require_param
from signals.domain.timing import KYIV_TZ

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
PDF_LINK_RE = re.compile(r"href=[\"']([^\"']+?\.pdf(?:\?[^\"']*)?)[\"']", flags=re.IGNORECASE)
FORWARD_PE_LINE_RE = re.compile(r"forward[^\n]{0,120}?P/E ratio", flags=re.IGNORECASE)
FORWARD_PE_VALUE_RE = re.compile(r"(?:is|was|of)\s+(\d{1,3}(?:\.\d+)?)")
PDF_MAGIC = b"%PDF"
_FILENAME_DATE_RE = re.compile(r"(\d{6})[A-Za-z]?\.pdf", flags=re.IGNORECASE)
_FULL_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"([0-3]?\d),\s+(20\d{2})",
    flags=re.IGNORECASE,
)
_MONTH_BY_NAME = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_COVER_CONTEXT_LINES = 3  # lines around the forward-P/E header counted as report context
_COVER_HEAD_LINES = 10    # document head scanned when the P/E zone holds no date


def download_pdf_link(html: str, base_url: str) -> str | None:
    """First .pdf link on the landing page, resolved against base_url."""
    hrefs = PDF_LINK_RE.findall(html)
    if not hrefs:
        return None
    return urljoin(base_url, hrefs[0].strip())


def parse_forward_pe(pdf_text: str) -> float:
    for line in pdf_text.splitlines():
        line_match = FORWARD_PE_LINE_RE.search(line)
        if line_match is None:
            continue
        value_match = FORWARD_PE_VALUE_RE.search(line[line_match.end():])
        if value_match is None:
            continue
        value = float(value_match.group(1))
        if 5.0 <= value <= 100.0:
            return value
    raise CollectorError("factset: не знайдено числа після 'Forward P/E' у тексті PDF.")


def date_from_pdf_filename(url: str) -> date | None:
    """MMDDYY stamp from the resolved PDF filename (EarningsInsight_110626A.pdf).

    Returns None when the tail holds no 6-digit run or the digits are not a
    valid calendar date (protects against random numeric slugs).
    """
    match = _FILENAME_DATE_RE.search(url)
    if match is None:
        return None
    digits = match.group(1)
    try:
        return date(2000 + int(digits[4:]), int(digits[:2]), int(digits[2:4]))
    except ValueError:
        return None


def cover_date_from_pdf_text(pdf_text: str) -> date | None:
    """Publication date from the pdftotext text, anchored to report context.

    Only zones with cover semantics are searched: the lines immediately
    around the first forward-P/E sentence and, failing that, the document
    head. A bare scan of the whole text would pick up historical references
    ("since November 1, 2023") that are not publication dates.
    """
    lines = pdf_text.splitlines()
    pe_line_index = None
    for index, line in enumerate(lines):
        if FORWARD_PE_LINE_RE.search(line) is not None:
            pe_line_index = index
            break
    zones: list[list[str]] = []
    if pe_line_index is not None:
        low = max(0, pe_line_index - _COVER_CONTEXT_LINES)
        zones.append(lines[low:pe_line_index + _COVER_CONTEXT_LINES + 1])
    zones.append(lines[:_COVER_HEAD_LINES])
    for zone in zones:
        match = _FULL_DATE_RE.search("\n".join(zone))
        if match is None:
            continue
        try:
            return date(int(match.group(3)), _MONTH_BY_NAME[match.group(1).lower()], int(match.group(2)))
        except ValueError:
            continue
    return None


def run_pdftotext(pdf_path: str) -> str:
    executable = shutil.which("pdftotext")
    if executable is None:
        raise CollectorError("pdftotext недоступний.")
    completed = subprocess.run(
        [executable, "-layout", pdf_path, "-"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise CollectorError(f"pdftotext завершився з помилкою {completed.returncode}: {completed.stderr[:200]}")
    return completed.stdout


def _parse_pdf_bytes(pdf_bytes: bytes, *, spec_key: str) -> tuple[float, date | None]:
    pdf_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix=f"mi-{spec_key}-", suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            pdf_path = tmp.name
        pdf_text = run_pdftotext(pdf_path)
        return parse_forward_pe(pdf_text), cover_date_from_pdf_text(pdf_text)
    finally:
        if pdf_path:
            with contextlib.suppress(OSError):
                os.unlink(pdf_path)


class FactsetGateway(HttpGatewayBase):
    """SeriesFetcher for specs with source='factset' (weekly forward P/E)."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        min_interval_seconds: float = 1.0,
        timeout: tuple[float, float] = (10.0, 120.0),
    ) -> None:
        super().__init__(
            session=session,
            min_interval_seconds=min_interval_seconds,
            timeout=timeout,
            user_agent=BROWSER_UA,
        )

    def _resolve_pdf_bytes(self, url: str) -> tuple[bytes, str]:
        response = self._request("GET", url)
        content_type = str(response.headers.get("Content-Type", ""))
        if response.content.startswith(PDF_MAGIC) or "pdf" in content_type.lower():
            return response.content, str(response.url)
        landing_html = response.text
        pdf_url = download_pdf_link(landing_html, str(response.url))
        if pdf_url is None:
            raise CollectorError(f"factset: на сторінці {url} не знайдено посилання на PDF.")
        pdf_response = self._request("GET", pdf_url)
        if not pdf_response.content.startswith(PDF_MAGIC):
            raise CollectorError(f"factset: {pdf_url} не є PDF.")
        return pdf_response.content, pdf_url

    def fetch(self, spec: SeriesSpec, *, now_utc: datetime) -> list[SeriesObservation]:
        if spec.source != "factset":
            raise CollectorError(f"Серія '{spec.key}': очікувався source='factset', отримано '{spec.source}'.")
        url = require_param(spec, "url")
        pdf_bytes, resolved_url = self._resolve_pdf_bytes(url)
        forward_pe, text_date = _parse_pdf_bytes(pdf_bytes, spec_key=spec.key)
        obs_date = (
            date_from_pdf_filename(resolved_url)
            or text_date
            or now_utc.astimezone(KYIV_TZ).date()
        )
        observation = SeriesObservation(
            series_id=spec.key,
            obs_date=obs_date,
            value=forward_pe,
            source="factset",
            fetched_at=now_utc,
        )
        logger.debug(
            "factset_fetch",
            extra={
                "series_key": spec.key,
                "forward_pe": forward_pe,
                "obs_date": obs_date.isoformat(),
                "resolved_url": resolved_url,
            },
        )
        return [observation]
