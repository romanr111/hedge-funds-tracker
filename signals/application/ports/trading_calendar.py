from __future__ import annotations

from datetime import date
from typing import Protocol


class TradingCalendarPort(Protocol):
    def is_trading_day(self, day: date) -> bool:
        ...
