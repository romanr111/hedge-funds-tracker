from __future__ import annotations

from datetime import date

from signals.infrastructure.market.nyse_calendar import NYSETradingCalendar


def test_nyse_calendar_treats_good_friday_as_a_market_holiday() -> None:
    calendar = NYSETradingCalendar()

    assert calendar.is_trading_day(date(2024, 3, 28))
    assert not calendar.is_trading_day(date(2024, 3, 29))
