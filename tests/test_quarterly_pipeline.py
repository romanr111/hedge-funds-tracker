from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tracker.application.use_cases.build_risk_filtered_portfolio import build_risk_filtered_portfolio
from tracker.application.use_cases.run_quarterly_pipeline import run_quarterly_pipeline
from tracker.config import PipelineConfig
from tracker.domain.models import TrendStockSignal
from tracker.infrastructure.storage.sqlite_state_repository import StateStore


class FakeHistoryGateway:
    def __init__(self, prices: dict[str, dict[date, float]], adv20: dict[str, float]) -> None:
        self._prices = prices
        self._adv20 = adv20

    def get_eod_prices(self, tickers: list[str], start_date: date, end_date: date) -> dict[str, dict[date, float]]:
        resolved: dict[str, dict[date, float]] = {}
        for ticker in tickers:
            series = self._prices.get(ticker, {})
            resolved[ticker] = {
                day: value
                for day, value in series.items()
                if start_date <= day <= end_date
            }
        return resolved

    def get_adv20_usd(self, ticker: str, as_of_date: date) -> float | None:
        _ = as_of_date
        return self._adv20.get(ticker)

    def get_benchmark_series(self, ticker: str, start_date: date, end_date: date) -> dict[date, float]:
        return self.get_eod_prices([ticker], start_date, end_date).get(ticker, {})



def _signal(*, quarter: str, key: str, conf: float, trend: float, hhi: float, regime: str = "STRONG_BUY") -> TrendStockSignal:
    return TrendStockSignal(
        report_quarter=quarter,
        instrument_key=key,
        cusip=key,
        put_call=None,
        issuer_name=key,
        title="COM",
        np_raw=1.0,
        np_adj=1.0,
        impulse_score=1.0,
        accumulation_score=1.0,
        confidence=conf,
        trend_ewma=trend,
        trend_delta=0.1,
        breadth_buy_weight=0.2,
        breadth_sell_weight=0.1,
        buy_managers=3,
        sell_managers=1,
        crowding_hhi=hhi,
        persistence_buy=2,
        persistence_sell=0,
        regime=regime,
        contributors_json="[]",
        computed_at=datetime.now(timezone.utc).isoformat(),
        freshness_multiplier=1.0,
        freshness_ok=True,
    )



def test_build_risk_filtered_portfolio_applies_caps_and_filters(tmp_path: Path) -> None:
    symbol_map_path = tmp_path / "symbols.json"
    symbol_map_path.write_text(json.dumps({"AAA": "AAA", "BBB": "BBB", "CCC": "CCC"}))

    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(
        json.dumps(
            {
                "AAA": {"sector": "TECH", "country": "US"},
                "BBB": {"sector": "TECH", "country": "US"},
                "CCC": {"sector": "ENERGY", "country": "US"},
            }
        )
    )

    gateway = FakeHistoryGateway(
        prices={
            "AAA": {date(2025, 2, 15): 100.0},
            "BBB": {date(2025, 2, 15): 40.0},
            "CCC": {date(2025, 2, 15): 30.0},
        },
        adv20={"AAA": 9_000_000.0, "BBB": 7_000_000.0, "CCC": 8_000_000.0},
    )
    pipeline = PipelineConfig(
        top_k=3,
        min_conf=0.40,
        hold_quarters=2,
        position_cap=0.60,
        sector_cap=0.65,
        adv20_usd_min=3_000_000,
        price_min=5,
        cost_bps_per_side=10,
        report_dir=tmp_path / "reports",
        symbol_metadata_file=metadata_path,
    )

    result = build_risk_filtered_portfolio(
        report_quarter="2024Q4",
        signals=[
            _signal(quarter="2024Q4", key="AAA", conf=0.8, trend=1.2, hhi=0.2),
            _signal(quarter="2024Q4", key="BBB", conf=0.7, trend=1.0, hhi=0.3),
            _signal(quarter="2024Q4", key="CCC", conf=0.7, trend=0.8, hhi=0.2),
            _signal(quarter="2024Q4", key="CCC", conf=0.9, trend=-0.5, hhi=0.2, regime="STRONG_SELL"),
        ],
        as_of_trade_date=date(2025, 2, 15),
        price_gateway=gateway,
        pipeline=pipeline,
        symbol_map_file=symbol_map_path,
    )

    assert result.selected_positions
    for position in result.selected_positions:
        assert position.weight_capped <= pipeline.position_cap + 1e-8

    tech_weight = sum(item.weight_capped for item in result.selected_positions if item.ticker in {"AAA", "BBB"})
    assert tech_weight <= pipeline.sector_cap + 1e-8



def test_run_quarterly_pipeline_generates_report_and_db_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    store = StateStore(db_path)
    try:
        quarters = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
        for quarter in quarters:
            store.replace_trend_stock_signals(
                quarter,
                [
                    _signal(quarter=quarter, key="AAA", conf=0.80, trend=1.2, hhi=0.2),
                    _signal(quarter=quarter, key="BBB", conf=0.70, trend=0.8, hhi=0.3),
                ],
            )

        start = date(2023, 1, 1)
        end = date(2026, 1, 1)
        prices_aaa: dict[date, float] = {}
        prices_bbb: dict[date, float] = {}
        prices_spy: dict[date, float] = {}
        day = start
        idx = 0
        while day <= end:
            prices_aaa[day] = 100.0 + (idx * 0.08)
            prices_bbb[day] = 80.0 + (idx * 0.03)
            prices_spy[day] = 300.0 + (idx * 0.04)
            day += timedelta(days=1)
            idx += 1

        gateway = FakeHistoryGateway(
            prices={"AAA": prices_aaa, "BBB": prices_bbb, "SPY": prices_spy},
            adv20={"AAA": 10_000_000.0, "BBB": 9_000_000.0, "SPY": 1_000_000_000.0},
        )

        symbol_map_file = tmp_path / "symbols.json"
        symbol_map_file.write_text(json.dumps({"AAA": "AAA", "BBB": "BBB", "SPY": "SPY"}))

        metadata_file = tmp_path / "metadata.json"
        metadata_file.write_text(
            json.dumps(
                {
                    "AAA": {"sector": "TECH", "country": "US"},
                    "BBB": {"sector": "HEALTH", "country": "US"},
                    "SPY": {"sector": "ETF", "country": "US"},
                }
            )
        )

        pipeline = PipelineConfig(
            top_k=2,
            min_conf=0.4,
            hold_quarters=2,
            position_cap=0.7,
            sector_cap=0.8,
            adv20_usd_min=3_000_000,
            price_min=5,
            cost_bps_per_side=10,
            report_dir=tmp_path / "reports",
            symbol_metadata_file=metadata_file,
        )

        result = run_quarterly_pipeline(
            store=store,
            history_gateway=gateway,
            symbol_map_file=symbol_map_file,
            pipeline=pipeline,
            as_of_quarter="2023Q4",
            dry_run_report=False,
        )

        assert result.run_id
        assert result.status == "ok"
        assert result.quality_status == "INSUFFICIENT_SAMPLE"
        assert result.report_dir is not None
        assert (result.report_dir / "summary.md").exists()
        assert (result.report_dir / "kpi_overall.csv").exists()
        assert store.list_quarterly_kpi_rows(result.run_id)
    finally:
        store.close()
