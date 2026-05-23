from __future__ import annotations

from datetime import datetime, timezone

from tracker.domain.models import TrendStockSignal
from tracker.domain.trend_ideas import TrendIdeaState, select_trend_ideas


def _signal(
    instrument_key: str,
    *,
    regime: str = "REVERSAL_BUY",
    confidence: float = 0.70,
    trend_ewma: float = 0.05,
    accumulation_score: float = 0.04,
    buy_managers: int = 2,
    sell_managers: int = 0,
    persistence_buy: int = 1,
    persistence_sell: int = 0,
    freshness_ok: bool | None = True,
) -> TrendStockSignal:
    return TrendStockSignal(
        report_quarter="2026Q1",
        instrument_key=instrument_key,
        cusip=instrument_key,
        put_call=None,
        issuer_name=f"Issuer {instrument_key}",
        title="COM",
        np_raw=accumulation_score,
        np_adj=accumulation_score,
        impulse_score=trend_ewma,
        accumulation_score=accumulation_score,
        confidence=confidence,
        trend_ewma=trend_ewma,
        trend_delta=trend_ewma,
        breadth_buy_weight=0.12 if buy_managers else 0.0,
        breadth_sell_weight=0.12 if sell_managers else 0.0,
        buy_managers=buy_managers,
        sell_managers=sell_managers,
        crowding_hhi=0.20,
        persistence_buy=persistence_buy,
        persistence_sell=persistence_sell,
        regime=regime,
        contributors_json="[]",
        computed_at=datetime.now(timezone.utc).isoformat(),
        freshness_multiplier=1.0,
        freshness_ok=freshness_ok,
    )


def test_select_trend_ideas_promotes_supported_buy_and_monitors_single_manager_reversal() -> None:
    supported = _signal("supported", buy_managers=2)
    single_manager = _signal("single", buy_managers=1)

    selection = select_trend_ideas([single_manager, supported])

    assert [row.signal.instrument_key for row in selection.promoted_buy] == ["supported"]
    assert selection.by_instrument["supported"].state == TrendIdeaState.PROMOTED
    assert selection.by_instrument["single"].state == TrendIdeaState.MONITOR


def test_select_trend_ideas_promotes_persistent_single_manager_direction() -> None:
    persistent = _signal("persistent", buy_managers=1, persistence_buy=2)

    selection = select_trend_ideas([persistent])

    assert [row.signal.instrument_key for row in selection.promoted_buy] == ["persistent"]
    assert selection.by_instrument["persistent"].reason == "Promoted by directional persistence."


def test_select_trend_ideas_ranks_stale_rows_after_fresh_or_unknown_rows() -> None:
    high_score_stale = _signal("stale", accumulation_score=0.20, freshness_ok=False)
    unknown = _signal("unknown", accumulation_score=0.06, freshness_ok=None)
    fresh = _signal("fresh", accumulation_score=0.05, freshness_ok=True)

    selection = select_trend_ideas([high_score_stale, fresh, unknown])

    assert [row.signal.instrument_key for row in selection.promoted_buy] == ["fresh", "unknown", "stale"]


def test_select_trend_ideas_uses_same_support_gate_for_reduction_candidates() -> None:
    supported = _signal(
        "reduce-supported",
        regime="REVERSAL_SELL",
        trend_ewma=-0.04,
        accumulation_score=-0.03,
        buy_managers=0,
        sell_managers=2,
    )
    one_manager = _signal(
        "reduce-monitor",
        regime="REVERSAL_SELL",
        trend_ewma=-0.06,
        accumulation_score=-0.05,
        buy_managers=0,
        sell_managers=1,
    )

    selection = select_trend_ideas([one_manager, supported])

    assert [row.signal.instrument_key for row in selection.promoted_reduction] == ["reduce-supported"]
    assert selection.by_instrument["reduce-monitor"].state == TrendIdeaState.MONITOR
