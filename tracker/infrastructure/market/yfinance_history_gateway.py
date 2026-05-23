from __future__ import annotations

from datetime import date, timedelta
from typing import Mapping

import yfinance


class YFinanceHistoryGateway:
    """Drop-in replacement for StooqHistoryGateway using Yahoo Finance via yfinance."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[date, float]] = {}

    def _fetch(self, ticker: str) -> dict[date, float]:
        key = ticker.strip().upper()
        if not key:
            return {}
        if key in self._cache:
            return dict(self._cache[key])

        try:
            ticker_obj = yfinance.Ticker(key)
            # Request a generous window; yfinance may return more
            hist = ticker_obj.history(period="max", interval="1d")
        except Exception:
            self._cache[key] = {}
            return {}

        if hist is None or hist.empty:
            self._cache[key] = {}
            return {}

        series: dict[date, float] = {}
        for idx, row in hist.iterrows():
            try:
                day = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                close = float(row["Close"])
                if close > 0:
                    series[day] = close
            except (ValueError, TypeError, KeyError):
                continue

        self._cache[key] = series
        return dict(series)

    def get_eod_prices(
        self, tickers: list[str], start_date: date, end_date: date
    ) -> dict[str, dict[date, float]]:
        if end_date < start_date:
            return {}
        result: dict[str, dict[date, float]] = {}
        for ticker in sorted({item.strip().upper() for item in tickers if item and item.strip()}):
            series = self._fetch(ticker)
            if not series:
                continue
            filtered = {day: price for day, price in series.items() if start_date <= day <= end_date}
            if filtered:
                result[ticker] = filtered
        return result

    def get_benchmark_series(self, ticker: str, start_date: date, end_date: date) -> dict[date, float]:
        prices = self.get_eod_prices([ticker], start_date, end_date)
        return prices.get(ticker.strip().upper(), {})
