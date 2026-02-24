from __future__ import annotations

import csv
import io
import time
from datetime import date

import requests


STOOQ_HISTORY_URL = "https://stooq.com/q/d/l/"
DEFAULT_TIMEOUT = (10, 30)
DAILY_LIMIT_MARKER = "Exceeded the daily hits limit"


class StooqHistoryGateway:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        min_interval_seconds: float = 0.2,
    ) -> None:
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": "HedgeFundsTracker/1.0 (market-data-history)"})
        self._min_interval = min_interval_seconds
        self._last_request_time = 0.0
        self._history_cache: dict[str, dict[date, tuple[float, float]]] = {}
        self._daily_limit_hit = False

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

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

    def _request_history(self, symbol: str) -> str:
        self._throttle()
        response = self._session.get(
            STOOQ_HISTORY_URL,
            params={"s": symbol, "i": "d"},
            timeout=DEFAULT_TIMEOUT,
        )
        self._last_request_time = time.time()
        response.raise_for_status()
        return response.text

    @staticmethod
    def _parse_csv_prices(payload: str) -> dict[date, tuple[float, float]]:
        # Value is tuple(close, volume)
        parsed: dict[date, tuple[float, float]] = {}
        reader = csv.DictReader(io.StringIO(payload))
        for row in reader:
            raw_date = (row.get("Date") or "").strip()
            raw_close = (row.get("Close") or "").strip()
            raw_volume = (row.get("Volume") or "").strip()
            if not raw_date or not raw_close:
                continue
            if raw_close == "N/D":
                continue
            try:
                day = date.fromisoformat(raw_date)
                close = float(raw_close)
            except ValueError:
                continue
            if close <= 0:
                continue
            volume = 0.0
            if raw_volume and raw_volume != "N/D":
                try:
                    volume = float(raw_volume)
                except ValueError:
                    volume = 0.0
            parsed[day] = (close, max(0.0, volume))
        return parsed

    def _fetch_ticker_history(self, ticker: str) -> dict[date, tuple[float, float]]:
        key = ticker.strip().upper()
        if not key:
            return {}
        if key in self._history_cache:
            return dict(self._history_cache[key])
        if self._daily_limit_hit:
            self._history_cache[key] = {}
            return {}

        for candidate in self._ticker_query_candidates(key):
            symbol = f"{candidate}.US"
            try:
                payload = self._request_history(symbol)
            except requests.RequestException:
                continue
            if DAILY_LIMIT_MARKER.lower() in payload.lower():
                self._daily_limit_hit = True
                self._history_cache[key] = {}
                return {}
            series = self._parse_csv_prices(payload)
            if series:
                self._history_cache[key] = series
                return dict(series)
        self._history_cache[key] = {}
        return {}

    def get_eod_prices(self, tickers: list[str], start_date: date, end_date: date) -> dict[str, dict[date, float]]:
        if end_date < start_date:
            return {}
        result: dict[str, dict[date, float]] = {}
        for ticker in sorted({item.strip().upper() for item in tickers if item and item.strip()}):
            series = self._fetch_ticker_history(ticker)
            if not series:
                continue
            filtered: dict[date, float] = {}
            for day, (close, _) in series.items():
                if start_date <= day <= end_date:
                    filtered[day] = close
            if filtered:
                result[ticker] = filtered
        return result

    def get_adv20_usd(self, ticker: str, as_of_date: date) -> float | None:
        series = self._fetch_ticker_history(ticker.strip().upper())
        if not series:
            return None
        rows = sorted((day, data) for day, data in series.items() if day <= as_of_date)
        if not rows:
            return None
        last_twenty = rows[-20:]
        if not last_twenty:
            return None
        values: list[float] = []
        for _, (close, volume) in last_twenty:
            if close <= 0 or volume <= 0:
                continue
            values.append(close * volume)
        if not values:
            return None
        return sum(values) / float(len(values))

    def get_benchmark_series(self, ticker: str, start_date: date, end_date: date) -> dict[date, float]:
        prices = self.get_eod_prices([ticker], start_date, end_date)
        return prices.get(ticker.strip().upper(), {})
