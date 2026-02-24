from __future__ import annotations

from datetime import date

from tracker.infrastructure.market.stooq_history_gateway import StooqHistoryGateway


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.headers: dict[str, str] = {}

    def get(self, url: str, params: dict[str, str], timeout: tuple[int, int]) -> _FakeResponse:
        del url, params, timeout
        self.calls += 1
        if not self._responses:
            raise RuntimeError("No fake responses configured")
        return self._responses.pop(0)


def test_history_gateway_caches_ticker_history_between_calls() -> None:
    payload = "\n".join(
        [
            "Date,Open,High,Low,Close,Volume",
            "2025-01-02,100,101,99,100,1000",
            "2025-01-03,101,102,100,101,1200",
            "2025-01-06,102,103,101,102,1100",
        ]
    )
    session = _FakeSession([_FakeResponse(payload)])
    gateway = StooqHistoryGateway(session=session, min_interval_seconds=0.0)

    prices = gateway.get_eod_prices(["AAA"], date(2025, 1, 1), date(2025, 1, 10))
    assert "AAA" in prices
    adv20 = gateway.get_adv20_usd("AAA", date(2025, 1, 10))
    assert adv20 is not None and adv20 > 0
    prices_again = gateway.get_eod_prices(["AAA"], date(2025, 1, 1), date(2025, 1, 10))
    assert prices_again.get("AAA")
    assert session.calls == 1


def test_history_gateway_stops_network_calls_after_daily_limit_message() -> None:
    session = _FakeSession([_FakeResponse("Exceeded the daily hits limit")])
    gateway = StooqHistoryGateway(session=session, min_interval_seconds=0.0)

    benchmark = gateway.get_benchmark_series("SPY", date(2025, 1, 1), date(2025, 1, 10))
    assert benchmark == {}
    prices = gateway.get_eod_prices(["AAA"], date(2025, 1, 1), date(2025, 1, 10))
    assert prices == {}
    assert session.calls == 1

