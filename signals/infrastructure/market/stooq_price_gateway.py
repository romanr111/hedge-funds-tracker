from __future__ import annotations

import time
from collections.abc import Iterable

import requests


STOOQ_QUOTES_URL = "https://stooq.com/q/l/"
DEFAULT_TIMEOUT = (10, 30)
DEFAULT_BATCH_SIZE = 50


class StooqPriceGateway:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        min_interval_seconds: float = 0.2,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": "Signals/1.0 (market-data)"})
        self._min_interval = min_interval_seconds
        self._batch_size = max(1, batch_size)
        self._last_request_time = 0.0

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _request_quotes(self, query_symbols: list[str]) -> str:
        self._throttle()
        response = self._session.get(
            STOOQ_QUOTES_URL,
            # Stooq expects symbols separated by spaces (encoded by requests as '+').
            params={"s": " ".join(query_symbols), "i": "d"},
            timeout=DEFAULT_TIMEOUT,
        )
        self._last_request_time = time.time()
        response.raise_for_status()
        return response.text

    @staticmethod
    def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
        for idx in range(0, len(values), size):
            yield values[idx : idx + size]

    @staticmethod
    def _ticker_query_candidates(ticker: str) -> list[str]:
        value = ticker.strip().upper()
        if not value:
            return []
        normalized = value.replace("/", "-").replace(" ", "")
        alternatives = [normalized]
        if "." in normalized:
            alternatives.append(normalized.replace(".", "-"))
        if "-" in normalized:
            alternatives.append(normalized.replace("-", "."))
        unique: list[str] = []
        for item in alternatives:
            if item and item not in unique:
                unique.append(item)
        return unique

    def get_latest_prices(self, tickers: list[str]) -> dict[str, float]:
        unique_tickers = sorted({ticker.strip().upper() for ticker in tickers if ticker and ticker.strip()})
        if not unique_tickers:
            return {}

        symbol_to_tickers: dict[str, set[str]] = {}
        for ticker in unique_tickers:
            candidates = self._ticker_query_candidates(ticker)
            for candidate in candidates:
                symbol = f"{candidate}.US"
                symbol_to_tickers.setdefault(symbol, set()).add(ticker)

        resolved: dict[str, float] = {}
        symbols = sorted(symbol_to_tickers.keys())
        for batch in self._chunks(symbols, self._batch_size):
            try:
                payload = self._request_quotes(batch)
            except requests.RequestException:
                continue
            for raw_line in payload.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                parts = [item.strip() for item in line.split(",")]
                if len(parts) < 7:
                    continue
                symbol = parts[0].upper()
                close_raw = parts[6]
                if not close_raw or close_raw == "N/D":
                    continue
                try:
                    close_price = float(close_raw)
                except ValueError:
                    continue
                if close_price <= 0:
                    continue
                for ticker in symbol_to_tickers.get(symbol, set()):
                    # Do not override earlier successful match for the same ticker.
                    resolved.setdefault(ticker, close_price)
        return resolved
