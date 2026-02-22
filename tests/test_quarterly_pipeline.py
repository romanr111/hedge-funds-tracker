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


class RecordingHistoryGateway(FakeHistoryGateway):
    def __init__(self, prices: dict[str, dict[date, float]], adv20: dict[str, float]) -> None:
        super().__init__(prices=prices, adv20=adv20)
        self.adv20_as_of_days: list[date] = []

    def get_adv20_usd(self, ticker: str, as_of_date: date) -> float | None:
        self.adv20_as_of_days.append(as_of_date)
        return super().get_adv20_usd(ticker, as_of_date)



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


def _seed_snapshot(
    store: StateStore,
    *,
    cik: str,
    manager_name: str,
    quarter: str,
    filing_date: str,
) -> None:
    store.upsert_manager_quarter_snapshot(
        cik=cik,
        manager_name=manager_name,
        report_quarter=quarter,
        report_date=None,
        filing_date=filing_date,
        acceptance_datetime=filing_date.replace("-", "") + "120000",
        accession=f"{cik}-{quarter}",
        source_form="13F-HR",
        positions=[
            {
                "name": "Synthetic",
                "title": "COM",
                "cusip": "111111111",
                "put_call": None,
                "value": 100,
                "shares": 100,
            }
        ],
        aum_value_k=100,
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


def test_build_risk_filtered_portfolio_unknown_sector_is_not_single_bucket(tmp_path: Path) -> None:
    symbol_map_path = tmp_path / "symbols.json"
    symbol_map_path.write_text(json.dumps({"AAA": "AAA", "BBB": "BBB", "CCC": "CCC"}))
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text("{}")

    gateway = FakeHistoryGateway(
        prices={
            "AAA": {date(2025, 2, 15): 100.0},
            "BBB": {date(2025, 2, 15): 50.0},
            "CCC": {date(2025, 2, 15): 30.0},
        },
        adv20={"AAA": 10_000_000.0, "BBB": 10_000_000.0, "CCC": 10_000_000.0},
    )
    pipeline = PipelineConfig(
        top_k=3,
        min_conf=0.40,
        hold_quarters=2,
        position_cap=0.60,
        sector_cap=0.30,
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
        ],
        as_of_trade_date=date(2025, 2, 15),
        price_gateway=gateway,
        pipeline=pipeline,
        symbol_map_file=symbol_map_path,
    )

    invested = sum(item.weight_capped for item in result.selected_positions)
    assert invested > 0.85



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
            _seed_snapshot(
                store,
                cik="0000000001",
                manager_name="Fund A",
                quarter=quarter,
                filing_date="2023-11-14" if quarter != "2023Q4" else "2024-02-14",
            )
            _seed_snapshot(
                store,
                cik="0000000002",
                manager_name="Fund B",
                quarter=quarter,
                filing_date="2023-11-13" if quarter != "2023Q4" else "2024-02-13",
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
        kpi_rows = store.list_quarterly_kpi_rows(result.run_id)
        assert kpi_rows
        metrics = {str(item["metric"]) for item in kpi_rows}
        assert "beta_vs_spy" in metrics
        assert "volatility" in metrics
        assert "calmar" in metrics
        assert "rolling_4q_hit_rate" in metrics
        assert "average_invested_weight" in metrics
        assert "universe_coverage_ratio" in metrics
    finally:
        store.close()


def test_run_quarterly_pipeline_uses_max_filing_date_plus_one_day(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    store = StateStore(db_path)
    try:
        store.replace_trend_stock_signals(
            "2024Q4",
            [_signal(quarter="2024Q4", key="AAA", conf=0.80, trend=1.2, hhi=0.2)],
        )
        _seed_snapshot(
            store,
            cik="0000000001",
            manager_name="Fund A",
            quarter="2024Q4",
            filing_date="2025-02-12",
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            manager_name="Fund B",
            quarter="2024Q4",
            filing_date="2025-02-14",
        )

        gateway = RecordingHistoryGateway(
            prices={
                "AAA": {date(2025, 2, 15): 100.0, date(2025, 2, 18): 101.0},
                "SPY": {date(2025, 2, 15): 300.0, date(2025, 2, 18): 301.0},
            },
            adv20={"AAA": 10_000_000.0},
        )
        symbol_map_file = tmp_path / "symbols.json"
        symbol_map_file.write_text(json.dumps({"AAA": "AAA"}))
        metadata_file = tmp_path / "metadata.json"
        metadata_file.write_text(json.dumps({"AAA": {"sector": "TECH", "country": "US"}}))
        pipeline = PipelineConfig(
            top_k=1,
            min_conf=0.4,
            hold_quarters=1,
            position_cap=1.0,
            sector_cap=1.0,
            adv20_usd_min=3_000_000,
            price_min=5,
            cost_bps_per_side=10,
            report_dir=tmp_path / "reports",
            symbol_metadata_file=metadata_file,
        )

        run_quarterly_pipeline(
            store=store,
            history_gateway=gateway,
            symbol_map_file=symbol_map_file,
            pipeline=pipeline,
            as_of_quarter="2024Q4",
            dry_run_report=True,
        )

        assert gateway.adv20_as_of_days
        assert set(gateway.adv20_as_of_days) == {date(2025, 2, 15)}
    finally:
        store.close()


def test_run_quarterly_pipeline_returns_partial_data_when_benchmark_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    store = StateStore(db_path)
    try:
        store.replace_trend_stock_signals(
            "2024Q4",
            [_signal(quarter="2024Q4", key="AAA", conf=0.80, trend=1.2, hhi=0.2)],
        )
        _seed_snapshot(
            store,
            cik="0000000001",
            manager_name="Fund A",
            quarter="2024Q4",
            filing_date="2025-02-14",
        )

        gateway = FakeHistoryGateway(
            prices={"AAA": {date(2025, 2, 15): 100.0, date(2025, 2, 18): 101.0}},
            adv20={"AAA": 10_000_000.0},
        )
        symbol_map_file = tmp_path / "symbols.json"
        symbol_map_file.write_text(json.dumps({"AAA": "AAA"}))
        metadata_file = tmp_path / "metadata.json"
        metadata_file.write_text(json.dumps({"AAA": {"sector": "TECH", "country": "US"}}))
        pipeline = PipelineConfig(
            top_k=1,
            min_conf=0.4,
            hold_quarters=1,
            position_cap=1.0,
            sector_cap=1.0,
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
            as_of_quarter="2024Q4",
            dry_run_report=True,
        )
        assert result.status == "partial_data"
        assert result.report_dir is None
    finally:
        store.close()
