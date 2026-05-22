from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from tracker.domain.models import TrendStockSignal


SELL_TABLE_MIN_CONF = 0.35
BUY_TABLE_MIN_TREND = 0.001


class TrendIdeaState(str, Enum):
    PROMOTED = "Promoted"
    MONITOR = "Monitor"
    REJECTED = "Rejected"


class TrendIdeaDirection(str, Enum):
    BUY = "BUY"
    REDUCTION = "REDUCTION"


@dataclass(frozen=True)
class TrendIdeaDecision:
    signal: TrendStockSignal
    state: TrendIdeaState
    direction: TrendIdeaDirection | None
    idea_score: float
    reason: str

    @property
    def directional_managers(self) -> int:
        if self.direction == TrendIdeaDirection.BUY:
            return int(self.signal.buy_managers)
        if self.direction == TrendIdeaDirection.REDUCTION:
            return int(self.signal.sell_managers)
        return 0

    @property
    def opposite_managers(self) -> int:
        if self.direction == TrendIdeaDirection.BUY:
            return int(self.signal.sell_managers)
        if self.direction == TrendIdeaDirection.REDUCTION:
            return int(self.signal.buy_managers)
        return 0

    @property
    def directional_persistence(self) -> int:
        if self.direction == TrendIdeaDirection.BUY:
            return int(self.signal.persistence_buy)
        if self.direction == TrendIdeaDirection.REDUCTION:
            return int(self.signal.persistence_sell)
        return 0


@dataclass(frozen=True)
class TrendIdeaSelection:
    promoted_buy: list[TrendIdeaDecision]
    promoted_reduction: list[TrendIdeaDecision]
    monitored: list[TrendIdeaDecision]
    rejected: list[TrendIdeaDecision]
    by_instrument: Mapping[str, TrendIdeaDecision]
    buy_candidates_count: int
    reduction_candidates_count: int


def _direction_for_signal(signal: TrendStockSignal, *, min_conf: float) -> TrendIdeaDirection | None:
    regime = str(signal.regime or "").upper()
    if (
        "BUY" in regime
        and regime != "REVERSAL_SELL"
        and float(signal.confidence) >= min_conf
        and float(signal.trend_ewma) >= BUY_TABLE_MIN_TREND
    ):
        return TrendIdeaDirection.BUY
    sell_min_conf = min(min_conf, SELL_TABLE_MIN_CONF)
    if "SELL" in regime and regime != "REVERSAL_BUY" and float(signal.confidence) >= sell_min_conf:
        return TrendIdeaDirection.REDUCTION
    return None


def _idea_score(signal: TrendStockSignal) -> float:
    return abs(float(signal.accumulation_score)) * float(signal.confidence)


def _sort_key(decision: TrendIdeaDecision) -> tuple[bool, float, int, int, float, str]:
    signal = decision.signal
    return (
        signal.freshness_ok is False,
        -decision.idea_score,
        -decision.directional_managers,
        decision.opposite_managers,
        -abs(float(signal.trend_ewma)),
        str(signal.instrument_key),
    )


def _decision_for_signal(signal: TrendStockSignal, *, min_conf: float) -> TrendIdeaDecision:
    direction = _direction_for_signal(signal, min_conf=min_conf)
    if direction is None:
        return TrendIdeaDecision(
            signal=signal,
            state=TrendIdeaState.REJECTED,
            direction=None,
            idea_score=_idea_score(signal),
            reason="Not a directional candidate for this view.",
        )

    decision = TrendIdeaDecision(
        signal=signal,
        state=TrendIdeaState.MONITOR,
        direction=direction,
        idea_score=_idea_score(signal),
        reason="Directional move lacks multi-manager support or persistence.",
    )
    if decision.directional_managers >= 2:
        return TrendIdeaDecision(
            signal=signal,
            state=TrendIdeaState.PROMOTED,
            direction=direction,
            idea_score=decision.idea_score,
            reason="Promoted by multi-manager directional support.",
        )
    if decision.directional_persistence >= 2:
        return TrendIdeaDecision(
            signal=signal,
            state=TrendIdeaState.PROMOTED,
            direction=direction,
            idea_score=decision.idea_score,
            reason="Promoted by directional persistence.",
        )
    return decision


def select_trend_ideas(
    signals: Sequence[TrendStockSignal],
    *,
    min_conf: float = 0.45,
    limit: int | None = None,
) -> TrendIdeaSelection:
    decisions = [_decision_for_signal(signal, min_conf=min_conf) for signal in signals]
    promoted_buy_all = sorted(
        [
            row
            for row in decisions
            if row.state == TrendIdeaState.PROMOTED and row.direction == TrendIdeaDirection.BUY
        ],
        key=_sort_key,
    )
    promoted_reduction_all = sorted(
        [
            row
            for row in decisions
            if row.state == TrendIdeaState.PROMOTED and row.direction == TrendIdeaDirection.REDUCTION
        ],
        key=_sort_key,
    )
    monitored = sorted([row for row in decisions if row.state == TrendIdeaState.MONITOR], key=_sort_key)
    buy_candidates_count = len(promoted_buy_all) + sum(
        1 for row in monitored if row.direction == TrendIdeaDirection.BUY
    )
    reduction_candidates_count = len(promoted_reduction_all) + sum(
        1 for row in monitored if row.direction == TrendIdeaDirection.REDUCTION
    )
    promoted_buy = promoted_buy_all
    promoted_reduction = promoted_reduction_all
    if limit is not None:
        effective_limit = max(1, limit)
        promoted_buy = promoted_buy[:effective_limit]
        promoted_reduction = promoted_reduction[:effective_limit]
    rejected = sorted(
        [row for row in decisions if row.state == TrendIdeaState.REJECTED],
        key=lambda row: str(row.signal.instrument_key),
    )
    return TrendIdeaSelection(
        promoted_buy=promoted_buy,
        promoted_reduction=promoted_reduction,
        monitored=monitored,
        rejected=rejected,
        by_instrument={row.signal.instrument_key: row for row in decisions},
        buy_candidates_count=buy_candidates_count,
        reduction_candidates_count=reduction_candidates_count,
    )
