from __future__ import annotations

from pathlib import Path

import pytest

from signals.infrastructure.market.stooq_history_gateway import StooqHistoryGateway
from signals.infrastructure.market.stooq_price_gateway import StooqPriceGateway

_ALLOWED_REAL_NETWORK_TESTS = {"test_stooq_price_gateway.py", "test_stooq_history_gateway.py"}


@pytest.fixture(autouse=True)
def _offline_market_gateways(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Keep the suite hermetic: no real Stooq calls except in the gateway unit tests.

    Without this, CLI tests silently fetch live prices for hundreds of tickers and the
    whole run hangs for minutes whenever Stooq is slow or blocked.
    """
    test_file = Path(str(request.node.fspath)).name
    if test_file in _ALLOWED_REAL_NETWORK_TESTS:
        return

    def _no_latest_prices(self: StooqPriceGateway, tickers: list[str]) -> dict[str, float]:
        del self, tickers
        return {}

    def _no_eod_prices(
        self: StooqHistoryGateway,
        tickers: list[str],
        start_date: object,
        end_date: object,
    ) -> dict[str, dict[object, float]]:
        del self, tickers, start_date, end_date
        return {}

    monkeypatch.setattr(StooqPriceGateway, "get_latest_prices", _no_latest_prices, raising=False)
    monkeypatch.setattr(StooqHistoryGateway, "get_eod_prices", _no_eod_prices, raising=False)
