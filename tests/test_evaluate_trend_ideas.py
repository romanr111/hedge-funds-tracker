from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tracker.application.use_cases.evaluate_trend_ideas import evaluate_trend_ideas
from tracker.domain.models import TrendStockSignal
from tracker.infrastructure.storage.sqlite_state_repository import StateStore


class _FakeHistoryGateway:
    def __init__(
        self,
        *,
        ticker_prices: dict[str, dict[date, float]] | None = None,
        benchmark_prices: dict[date, float] | None = None,
    ) -> None:
        self.ticker_prices = ticker_prices or {}
        self.benchmark_prices = benchmark_prices or {}
        self.price_requests: list[tuple[list[str], date, date]] = []

    def get_eod_prices(self, tickers: list[str], start_date: date, end_date: date) -> dict[str, dict[date, float]]:
        self.price_requests.append((list(tickers), start_date, end_date))
        return {
            ticker: {
                day: price
                for day, price in self.ticker_prices.get(ticker, {}).items()
                if start_date <= day <= end_date
            }
            for ticker in tickers
            if ticker in self.ticker_prices
        }

    def get_benchmark_series(self, ticker: str, start_date: date, end_date: date) -> dict[date, float]:
        del ticker
        return {day: price for day, price in self.benchmark_prices.items() if start_date <= day <= end_date}


def _signal(instrument_key: str, *, regime: str = "REVERSAL_BUY") -> TrendStockSignal:
    reduction = regime.endswith("_SELL")
    return TrendStockSignal(
        report_quarter="2025Q4",
        instrument_key=instrument_key,
        cusip=instrument_key,
        put_call=None,
        issuer_name=f"Issuer {instrument_key}",
        title="COM",
        np_raw=-0.08 if reduction else 0.08,
        np_adj=-0.08 if reduction else 0.08,
        impulse_score=-0.07 if reduction else 0.07,
        accumulation_score=-0.06 if reduction else 0.06,
        confidence=0.80,
        trend_ewma=-0.05 if reduction else 0.05,
        trend_delta=-0.05 if reduction else 0.05,
        breadth_buy_weight=0.12 if not reduction else 0.0,
        breadth_sell_weight=0.12 if reduction else 0.0,
        buy_managers=0 if reduction else 2,
        sell_managers=2 if reduction else 0,
        crowding_hhi=0.20,
        persistence_buy=1 if not reduction else 0,
        persistence_sell=1 if reduction else 0,
        regime=regime,
        contributors_json="[]",
        computed_at=datetime.now(timezone.utc).isoformat(),
        freshness_multiplier=1.0,
        freshness_ok=True,
    )


def _seed_snapshot(store: StateStore, *, cik: str, acceptance_datetime: str) -> None:
    store.upsert_manager_quarter_snapshot(
        cik=cik,
        manager_name=f"Fund {cik[-1]}",
        report_quarter="2025Q4",
        report_date="2025-12-31",
        filing_date="2026-02-14",
        acceptance_datetime=acceptance_datetime,
        accession=f"{cik}-2025Q4",
        source_form="13F-HR",
        positions=[{"name": "Issuer", "title": "COM", "cusip": "111111111", "value": 10_000, "shares": 100}],
        aum_value_k=10_000,
    )


def test_evaluate_trend_ideas_uses_latest_snapshot_acceptance_as_availability(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        store.replace_trend_stock_signals("2025Q4", [_signal("111111111")])
        _seed_snapshot(store, cik="0000000001", acceptance_datetime="2026-02-14T10:00:00Z")
        _seed_snapshot(store, cik="0000000002", acceptance_datetime="2026-02-16T12:00:00Z")
        gateway = _FakeHistoryGateway()

        result = evaluate_trend_ideas(
            store,
            gateway,
            report_quarters=["2025Q4"],
            symbol_map={"111111111": "AAA"},
        )
    finally:
        store.close()

    row = result.quarters[0]
    assert row.availability_date == date(2026, 2, 16)
    assert gateway.price_requests[0][1] == date(2026, 2, 16)


def test_evaluate_trend_ideas_reports_symbol_and_price_coverage_gaps(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        store.replace_trend_stock_signals("2025Q4", [_signal("111111111"), _signal("222222222")])
        _seed_snapshot(store, cik="0000000001", acceptance_datetime="2026-02-14T10:00:00Z")

        result = evaluate_trend_ideas(
            store,
            _FakeHistoryGateway(),
            report_quarters=["2025Q4"],
            symbol_map={"111111111": "AAA"},
        )
    finally:
        store.close()

    promoted = result.quarters[0].promoted
    assert promoted.candidates == 2
    assert promoted.mapped_symbols == 1
    assert promoted.priced_candidates == 0
    assert promoted.forward_return_coverage == {30: 0, 90: 0, 180: 0}


def test_evaluate_trend_ideas_counts_only_available_forward_windows(tmp_path: Path) -> None:
    availability = date(2026, 2, 14)
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        store.replace_trend_stock_signals("2025Q4", [_signal("111111111")])
        _seed_snapshot(store, cik="0000000001", acceptance_datetime="2026-02-14T10:00:00Z")
        gateway = _FakeHistoryGateway(
            ticker_prices={
                "AAA": {
                    availability: 100.0,
                    date(2026, 3, 16): 110.0,
                }
            },
            benchmark_prices={
                availability: 200.0,
                date(2026, 3, 16): 210.0,
            },
        )

        result = evaluate_trend_ideas(
            store,
            gateway,
            report_quarters=["2025Q4"],
            symbol_map={"111111111": "AAA"},
        )
    finally:
        store.close()

    promoted = result.quarters[0].promoted
    assert promoted.priced_candidates == 1
    assert promoted.forward_return_coverage == {30: 1, 90: 0, 180: 0}
    assert promoted.benchmark_relative_coverage == {30: 1, 90: 0, 180: 0}


def test_evaluate_trend_ideas_uses_next_trading_day_after_forward_window(tmp_path: Path) -> None:
    availability = date(2026, 2, 14)
    next_trading_day_after_horizon = availability + timedelta(days=181)
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        store.replace_trend_stock_signals("2025Q4", [_signal("111111111")])
        _seed_snapshot(store, cik="0000000001", acceptance_datetime="2026-02-14T10:00:00Z")
        gateway = _FakeHistoryGateway(
            ticker_prices={
                "AAA": {
                    availability: 100.0,
                    availability + timedelta(days=30): 105.0,
                    availability + timedelta(days=90): 110.0,
                    next_trading_day_after_horizon: 120.0,
                }
            },
            benchmark_prices={
                availability: 200.0,
                availability + timedelta(days=30): 205.0,
                availability + timedelta(days=90): 210.0,
                next_trading_day_after_horizon: 220.0,
            },
        )

        result = evaluate_trend_ideas(
            store,
            gateway,
            report_quarters=["2025Q4"],
            symbol_map={"111111111": "AAA"},
        )
    finally:
        store.close()

    promoted = result.quarters[0].promoted
    assert promoted.forward_return_coverage == {30: 1, 90: 1, 180: 1}
    assert promoted.benchmark_relative_coverage == {30: 1, 90: 1, 180: 1}


def test_evaluate_trend_ideas_does_not_fill_early_windows_from_later_prices(tmp_path: Path) -> None:
    availability = date(2026, 2, 14)
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        store.replace_trend_stock_signals("2025Q4", [_signal("111111111")])
        _seed_snapshot(store, cik="0000000001", acceptance_datetime="2026-02-14T10:00:00Z")
        gateway = _FakeHistoryGateway(
            ticker_prices={
                "AAA": {
                    availability: 100.0,
                    availability + timedelta(days=180): 120.0,
                }
            },
            benchmark_prices={
                availability: 200.0,
                availability + timedelta(days=180): 220.0,
            },
        )

        result = evaluate_trend_ideas(
            store,
            gateway,
            report_quarters=["2025Q4"],
            symbol_map={"111111111": "AAA"},
        )
    finally:
        store.close()

    promoted = result.quarters[0].promoted
    assert promoted.forward_return_coverage == {30: 0, 90: 0, 180: 1}
    assert promoted.benchmark_relative_coverage == {30: 0, 90: 0, 180: 1}


def test_evaluate_trend_ideas_requires_price_near_availability(tmp_path: Path) -> None:
    availability = date(2026, 2, 14)
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        store.replace_trend_stock_signals("2025Q4", [_signal("111111111")])
        _seed_snapshot(store, cik="0000000001", acceptance_datetime="2026-02-14T10:00:00Z")
        gateway = _FakeHistoryGateway(
            ticker_prices={
                "AAA": {
                    availability + timedelta(days=30): 100.0,
                    availability + timedelta(days=60): 110.0,
                }
            },
            benchmark_prices={
                availability + timedelta(days=30): 200.0,
                availability + timedelta(days=60): 210.0,
            },
        )

        result = evaluate_trend_ideas(
            store,
            gateway,
            report_quarters=["2025Q4"],
            symbol_map={"111111111": "AAA"},
            windows=(30,),
        )
    finally:
        store.close()

    promoted = result.quarters[0].promoted
    assert promoted.priced_candidates == 0
    assert promoted.forward_return_coverage == {30: 0}
    assert promoted.benchmark_relative_coverage == {30: 0}
