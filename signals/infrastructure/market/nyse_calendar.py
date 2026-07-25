from __future__ import annotations

from datetime import date

import holidays


class NYSETradingCalendar:
    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in holidays.NYSE(years=[day.year])
