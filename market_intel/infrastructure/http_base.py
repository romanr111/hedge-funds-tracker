"""Shared HTTP plumbing for market_intel gateways.

Mirrors the proven signals patterns (SecClient throttle/retry, Stooq daily
limit breaker) but stays exception-isolated: failures raise CollectorError
from market_intel.domain.exceptions.
"""
from __future__ import annotations

import logging
import time

import requests

from market_intel.domain.exceptions import CollectorError
from market_intel.domain.models import SeriesSpec

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 503})


class RateLimitExceeded(CollectorError):
    """Final non-2xx after exhausting retries on 429/503 responses."""

    def __init__(self, url: str, status_code: int, attempts: int) -> None:
        super().__init__(f"HTTP {status_code} for {url} after {attempts} attempts")
        self.url = url
        self.status_code = status_code
        self.attempts = attempts


class HttpGatewayBase:
    """Throttled requests.Session wrapper with polite retries."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        min_interval_seconds: float = 1.0,
        timeout: tuple[float, float] = (10.0, 60.0),
        user_agent: str,
    ) -> None:
        self._session = session if session is not None else requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._timeout = timeout
        self._last_request_time = 0.0

    def _throttle(self) -> None:
        # Sleep-before-request keeps the politeness interval between calls.
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _sleep_backoff(self, attempt: int) -> None:
        time.sleep(attempt)

    def _request(self, method: str, url: str, *, retries: int = 3, **kwargs: object) -> requests.Response:
        last_response: requests.Response | None = None
        last_error: requests.RequestException | None = None
        for attempt in range(1, retries + 1):
            self._throttle()
            try:
                response = self._session.request(method, url, timeout=self._timeout, **kwargs)
            except requests.RequestException as exc:
                last_error = exc
                if attempt < retries:
                    self._sleep_backoff(attempt)
                    continue
                break
            self._last_request_time = time.monotonic()
            last_response = response

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt < retries:
                    self._sleep_retry_after(response, attempt)
                    continue
                break

            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise CollectorError(f"HTTP {response.status_code} for {url}") from exc
            return response

        if last_response is not None:
            status_code = last_response.status_code
            if status_code in RETRYABLE_STATUS_CODES:
                raise RateLimitExceeded(url, status_code, retries)
            raise CollectorError(f"HTTP {status_code} for {url}")
        raise CollectorError(f"Failed to fetch {url}: {last_error!r}")

    def _sleep_retry_after(self, response: requests.Response, attempt: int) -> None:
        retry_after = response.headers.get("Retry-After", "")
        delay = int(retry_after) if retry_after.isdigit() else attempt
        time.sleep(max(0, delay))

    def _get_json(self, url: str, **kwargs: object) -> object:
        response = self._request("GET", url, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise CollectorError(f"Failed to decode JSON from {url}: {exc}") from exc

    def _get_text(self, url: str, **kwargs: object) -> str:
        try:
            response = self._request("GET", url, **kwargs)
        except requests.RequestException as exc:
            raise CollectorError(f"Failed to fetch text from {url}: {exc}") from exc
        return response.text


def require_param(spec: SeriesSpec, name: str) -> str:
    """Read a required gateway knob from spec.params without leaking KeyError."""
    raw = spec.params.get(name)
    if raw is None or not str(raw).strip():
        raise CollectorError(f"Серія '{spec.key}': не заданий параметр '{name}' ({spec.source}).")
    return str(raw).strip()
