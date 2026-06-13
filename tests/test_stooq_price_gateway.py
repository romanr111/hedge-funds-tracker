from __future__ import annotations

import requests

from signals.infrastructure.market import StooqPriceGateway


class _FakeResponse:
    def __init__(self, text: str, *, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")


class _FakeSession:
    def __init__(self, payload: str) -> None:
        self._payload = payload
        self.headers: dict[str, str] = {}
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, *, params: dict[str, str], timeout: tuple[int, int]) -> _FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return _FakeResponse(self._payload)


def test_stooq_price_gateway_parses_prices_and_ignores_nd() -> None:
    payload = "\n".join(
        [
            "AAPL.US,20260220,220019,258.97,264.75,258.16,264.58,42070499,",
            "MSFT.US,20260220,220019,396.11,400.1159,395.16,397.23,34015249,",
            "BRK-B.US,N/D,N/D,N/D,N/D,N/D,N/D,N/D,N/D",
        ]
    )
    session = _FakeSession(payload)
    gateway = StooqPriceGateway(session=session, min_interval_seconds=0.0, batch_size=100)

    prices = gateway.get_latest_prices(["AAPL", "MSFT", "BRK/B"])

    assert prices["AAPL"] == 264.58
    assert prices["MSFT"] == 397.23
    assert "BRK/B" not in prices
    assert len(session.calls) == 1


def test_stooq_price_gateway_returns_empty_for_empty_input() -> None:
    session = _FakeSession("")
    gateway = StooqPriceGateway(session=session, min_interval_seconds=0.0)

    prices = gateway.get_latest_prices([])

    assert prices == {}
    assert session.calls == []
