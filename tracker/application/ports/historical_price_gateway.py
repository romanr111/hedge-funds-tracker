from __future__ import annotations

from datetime import date
from typing import Protocol


class HistoricalPriceGateway(Protocol):
    def get_eod_prices(self, tickers: list[str], start_date: date, end_date: date) -> dict[str, dict[date, float]]:
        ...

    def get_adv20_usd(self, ticker: str, as_of_date: date) -> float | None:
        ...

    def get_benchmark_series(self, ticker: str, start_date: date, end_date: date) -> dict[date, float]:
        ...
