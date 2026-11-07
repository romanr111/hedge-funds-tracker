"""FactSet EarningsInsight gateway: forward 12-month P/E from the latest PDF.

Verified live 2026-11-07: https://www.factset.com/earningsinsight is served
after a 302 redirect as the raw PDF (advantage.factset.com/hubfs/...
EarningsInsight_110626A.pdf). pdftotext -layout text contains
"the forward 12-month P/E ratio for the S&P 500 is 17.7" near the top.
The gateway handles both the direct-PDF response and an HTML landing page
with .pdf links, always parses from a temp file that is deleted after use.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from urllib.parse import urljoin

import requests

from market_intel.domain.exceptions import CollectorError
from market_intel.domain.models import SeriesObservation, SeriesSpec
from market_intel.infrastructure.http_base import HttpGatewayBase, require_param
from signals.domain.timing import now_kyiv

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
PDF_LINK_RE = re.compile(r"href=[\"']([^\"']+?\.pdf(?:\?[^\"']*)?)[\"']", flags=re.IGNORECASE)
FORWARD_PE_LINE_RE = re.compile(r"forward[^\n]{0,120}?P/E ratio", flags=re.IGNORECASE)
FORWARD_PE_VALUE_RE = re.compile(r"(?:is|was|of)\s+(\d{1,3}(?:\.\d+)?)")
PDF_MAGIC = b"%PDF"


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


def _parse_pdf_bytes(pdf_bytes: bytes, *, spec_key: str) -> float:
    pdf_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix=f"mi-{spec_key}-", suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            pdf_path = tmp.name
        pdf_text = run_pdftotext(pdf_path)
        return parse_forward_pe(pdf_text)
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
        forward_pe = _parse_pdf_bytes(pdf_bytes, spec_key=spec.key)
        observation = SeriesObservation(
            series_id=spec.key,
            obs_date=now_kyiv().date(),
            value=forward_pe,
            source="factset",
            fetched_at=now_utc,
        )
        logger.debug(
            "factset_fetch",
            extra={"series_key": spec.key, "forward_pe": forward_pe, "resolved_url": resolved_url},
        )
        return [observation]
