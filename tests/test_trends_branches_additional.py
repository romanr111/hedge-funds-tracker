from __future__ import annotations

from datetime import datetime, timezone

import pytest

import signals.domain.trends as trends_module
from signals.domain.models import ManagerQuarterSnapshot


def _snapshot(cik: str, quarter: str, positions: list[dict[str, object]]) -> ManagerQuarterSnapshot:
    return ManagerQuarterSnapshot(
        cik=cik,
        manager_name=f"Fund {cik}",
        report_quarter=quarter,
        report_date="2025-03-31",
        filing_date="2025-05-15",
        acceptance_datetime="20250515120000",
        accession=f"{cik}-{quarter}",
        source_form="13F-HR",
        positions=positions,
        aum_value_k=100,
        positions_count=len(positions),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def test_trends_low_level_helpers_cover_unvisited_branches() -> None:
    assert trends_module.instrument_key("111", "CALL") == "111|CALL"

    aggregated = trends_module.aggregate_positions_by_instrument(
        [
            {"cusip": None, "value": 10},
            {"cusip": "111", "put_call": None, "name": None, "title": None, "value": 100, "shares": "n/a"},
            {"cusip": "111", "put_call": None, "name": "Alpha", "title": "COM", "value": 50, "shares": 10},
        ]
    )
    assert len(aggregated) == 1
    assert aggregated[0]["name"] == "Alpha"
    assert aggregated[0]["title"] == "COM"
    assert aggregated[0]["shares"] == 10

    weighted = trends_module._weights_by_instrument(
        _snapshot(
            "1",
            "2025Q1",
            [
                {"cusip": None, "value": 100},
                {"cusip": "111", "value": -1},
                {"cusip": "111", "value": 100, "shares": 10, "name": "A", "title": "COM"},
                {"cusip": "111", "value": 50, "shares": 5, "name": "A2", "title": "COM"},
            ],
        )
    )
    assert "111" in weighted
    assert weighted["111"]["shares"] == 15
    assert trends_module._weights_by_instrument(_snapshot("1", "2025Q1", [{"cusip": "111", "value": 0}])) == {}

    assert trends_module._trade_flow_delta(prev_weight=0.0, curr_weight=0.0, prev_shares=0, curr_shares=0) == 0.0
    assert trends_module._trade_flow_delta(prev_weight=0.05, curr_weight=0.06, prev_shares=10, curr_shares=12) > 0
    assert trends_module._trade_flow_delta(prev_weight=0.0, curr_weight=0.04, prev_shares=0, curr_shares=10) > 0
    assert trends_module._trade_flow_delta(prev_weight=0.05, curr_weight=0.0, prev_shares=0, curr_shares=0) < 0
    assert trends_module._trade_participation(trade_dw=0.1, max_weight=0.0) == 0.0
    assert trends_module._robust_sigma([]) == trends_module.MAD_EPS
    assert trends_module._percentile([1.0, 2.0], 0.0) == 1.0
    assert trends_module._percentile([1.0, 2.0], 1.0) == 2.0
    assert trends_module._position_signal_weight(0.0) == 0.0
    assert trends_module._adaptive_breadth_thresholds(0) == (
        trends_module.BREADTH_MIN_MANAGERS_BASE,
        trends_module.BREADTH_WEIGHT_BASE,
    )
    assert trends_module._turnover_between_snapshots(_snapshot("1", "2025Q1", []), _snapshot("1", "2025Q2", [])) == 0.0
    assert trends_module._manager_quality_multipliers(quarters=["2025Q1"], snapshots_by_quarter={}, manager_weights={"1": 0.0}) == {}

    multipliers = trends_module._manager_quality_multipliers(
        quarters=["2025Q1", "2025Q2"],
        snapshots_by_quarter={
            "2025Q1": {"1": _snapshot("1", "2025Q1", [])},
            "2025Q2": {},
        },
        manager_weights={"1": 1.0},
    )
    assert "1" in multipliers

    normalized_prices = trends_module._normalize_latest_prices(
        {1: 1.0, "A": "x", "B": float("nan"), " ": 10.0, "C": -1.0, "D": 10.0}
    )
    assert normalized_prices == {"D": 10.0}

    assert trends_module._resolve_latest_price(
        instrument_key="AAA",
        metadata={"cusip": "AAA", "put_call": None},
        latest_prices={"AAA": 10.0},
    ) == 10.0
    assert trends_module._resolve_latest_price(
        instrument_key="",
        metadata={"cusip": "AAA", "put_call": "PUT"},
        latest_prices={"AAA|PUT": 20.0},
    ) == 20.0
    assert trends_module._resolve_latest_price(
        instrument_key="",
        metadata={"cusip": "AAA", "put_call": None},
        latest_prices={"AAA": 30.0},
    ) == 30.0
    assert trends_module._resolve_latest_price(
        instrument_key="",
        metadata={"cusip": None, "put_call": None},
        latest_prices={"AAA": 30.0},
    ) is None

    assert trends_module._price_freshness_multiplier(-1.0, 100.0) == 1.0
    assert trends_module._is_strong_price_drift(-1.0, 100.0) is None

    assert trends_module._classify_regime(
        0.5,
        0.1,
        0.1,
        buy_gate=True,
        sell_gate=False,
        persistence_buy=2,
        persistence_sell=0,
    ) == "STRONG_BUY"
    assert trends_module._classify_regime(
        0.5,
        0.1,
        -0.1,
        buy_gate=True,
        sell_gate=False,
        persistence_buy=1,
        persistence_sell=0,
    ) == "REVERSAL_BUY"
    assert trends_module._classify_regime(
        0.5,
        -0.1,
        0.2,
        buy_gate=True,
        sell_gate=False,
        persistence_buy=1,
        persistence_sell=0,
    ) == "WEAKENING_BUY"
    assert trends_module._classify_regime(
        -0.5,
        -0.1,
        0.1,
        buy_gate=False,
        sell_gate=True,
        persistence_buy=0,
        persistence_sell=2,
    ) == "REVERSAL_SELL"
    assert trends_module._classify_regime(
        -0.5,
        -0.1,
        -0.1,
        buy_gate=False,
        sell_gate=True,
        persistence_buy=0,
        persistence_sell=2,
    ) == "STRONG_SELL"
    assert trends_module._classify_regime(
        -0.5,
        -0.1,
        -0.1,
        buy_gate=False,
        sell_gate=True,
        persistence_buy=0,
        persistence_sell=1,
    ) == "EMERGING_SELL"
    assert trends_module._classify_regime(
        -0.5,
        0.1,
        -0.1,
        buy_gate=False,
        sell_gate=True,
        persistence_buy=0,
        persistence_sell=1,
    ) == "WEAKENING_SELL"


def test_compute_quarter_metrics_and_compute_trend_signals_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    prev = {
        "1": _snapshot(
            "1",
            "2025Q1",
            [
                {"cusip": "AAA", "value": 1, "shares": 0, "name": "A", "title": "COM"},
                {"cusip": "BBB", "value": 10000, "shares": 0, "name": "B", "title": "COM"},
            ],
        )
    }
    curr = {
        "1": _snapshot(
            "1",
            "2025Q2",
            [
                {"cusip": "AAA", "value": 1, "shares": 0, "name": "A", "title": "COM"},
                {"cusip": "BBB", "value": 12000, "shares": 0, "name": "B", "title": "COM"},
            ],
        )
    }

    metrics = trends_module._compute_quarter_metrics(
        prev,
        curr,
        manager_base_weights={"1": 1.0, "2": 0.0},
        manager_effective_weights={"1": 1.0, "2": 0.0},
        manager_quality={"1": 1.0, "2": 1.0},
        contributor_limit=5,
    )
    assert "BBB" in metrics

    with pytest.raises(ValueError, match="At least 2 quarters are required"):
        trends_module.compute_trend_signals(quarters=["2025Q1"], snapshots_by_quarter={}, manager_weights={"1": 1.0})

    with pytest.raises(ValueError, match="at least one positive value"):
        trends_module.compute_trend_signals(
            quarters=["2025Q1", "2025Q2"],
            snapshots_by_quarter={"2025Q1": prev, "2025Q2": curr},
            manager_weights={"1": 0.0},
        )

    monkeypatch.setattr(trends_module, "_manager_quality_multipliers", lambda **_kwargs: {"1": 0.0})
    result = trends_module.compute_trend_signals(
        quarters=["2025Q1", "2025Q2"],
        snapshots_by_quarter={"2025Q1": prev, "2025Q2": curr},
        manager_weights={"1": 1.0},
    )
    assert result.target_quarter == "2025Q2"
