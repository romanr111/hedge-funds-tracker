from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Any, Literal

from tracker.domain.models import ManagerQuarterSnapshot, Position


MIN_POSITION_WEIGHT = 0.015
MAX_POSITION_WEIGHT_FULL_SIGNAL = 0.05
Z_CLIP = 5.0
Z_SCALE = 2.0
MAD_EPS = 1e-6
ANTI_CROWD_H0 = 0.35

IMPULSE_HALFLIFE_QUARTERS = 1.0
ACCUMULATION_HALFLIFE_QUARTERS = 3.0
BLEND_TACTICAL = "tactical"
BLEND_PORTFOLIO = "portfolio"
BLEND_WEIGHTS: dict[str, tuple[float, float]] = {
    BLEND_TACTICAL: (0.60, 0.40),
    BLEND_PORTFOLIO: (0.35, 0.65),
}

BREADTH_MIN_MANAGERS_BASE = 3
BREADTH_MANAGERS_RATIO = 0.08
BREADTH_WEIGHT_BASE = 0.10
BREADTH_WEIGHT_MAX = 0.15
BREADTH_WEIGHT_STEP_START = 20
BREADTH_WEIGHT_STEP = 0.001

MANAGER_QUALITY_MIN = 0.75
MANAGER_QUALITY_MAX = 1.25
MANAGER_QUALITY_TURNOVER_MIN = 0.80
MANAGER_QUALITY_TURNOVER_MAX = 1.20
MANAGER_QUALITY_ACTIVITY_MIN = 0.85
MANAGER_QUALITY_ACTIVITY_MAX = 1.15

NEW_ENTRY_IMPULSE_WEIGHT_MIN = 0.03
NEW_ENTRY_IMPULSE_WEIGHT_MAX = 0.05
NEW_ENTRY_IMPULSE_MULT_MIN = 2.0
NEW_ENTRY_IMPULSE_MULT_MAX = 2.5

MIN_HHI_PARTICIPANTS_FOR_PENALTY = 3
DISAGREEMENT_GAMMA = 0.70
MAGNITUDE_FALLBACK_SCALE = 0.08
MAGNITUDE_QUARTER_PERCENTILE = 0.90


@dataclass(frozen=True)
class TrendSignalRow:
    instrument_key: str
    cusip: str | None
    put_call: str | None
    issuer_name: str | None
    title: str | None
    np_raw: float
    np_adj: float
    impulse_score: float
    accumulation_score: float
    confidence: float
    trend_ewma: float
    trend_delta: float
    breadth_buy_weight: float
    breadth_sell_weight: float
    buy_managers: int
    sell_managers: int
    crowding_hhi: float
    persistence_buy: int
    persistence_sell: int
    regime: str
    contributors: list[dict[str, Any]]


@dataclass(frozen=True)
class TrendComputationResult:
    target_quarter: str
    signals: list[TrendSignalRow]
    top_buy: list[str]
    top_sell: list[str]
    reversals: list[str]


@dataclass(frozen=True)
class _TradeRecord:
    instrument_key: str
    trade_dw: float
    prev_weight: float
    curr_weight: float
    max_weight: float
    metadata: dict[str, str | None]


@dataclass(frozen=True)
class _ManagerContribution:
    manager_cik: str
    manager_name: str
    manager_weight_base: float
    manager_quality_multiplier: float
    manager_weight_effective: float
    signal_value: float
    trade_dw: float
    prev_weight: float
    curr_weight: float


@dataclass(frozen=True)
class _QuarterInstrumentMetric:
    metadata: dict[str, str | None]
    np_raw: float
    np_adj: float
    np_impulse_adj: float
    breadth_buy_weight: float
    breadth_sell_weight: float
    buy_managers: int
    sell_managers: int
    crowding_hhi: float
    contributors: list[dict[str, Any]]


@dataclass(frozen=True)
class _InstrumentState:
    metadata: dict[str, str | None]
    impulse_ewma: float
    accumulation_ewma: float
    trend_ewma: float
    persistence_buy: int
    persistence_sell: int


def _clean_optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def instrument_key(cusip: str | None, put_call: str | None) -> str:
    cusip_value = (cusip or "").strip()
    put_call_value = (put_call or "").strip()
    if put_call_value:
        return f"{cusip_value}|{put_call_value}"
    return cusip_value


def aggregate_positions_by_instrument(positions: list[Position]) -> list[Position]:
    grouped: dict[str, dict[str, Any]] = {}
    for position in positions:
        cusip = _clean_optional_str(position.get("cusip"))
        if cusip is None:
            continue
        put_call = _clean_optional_str(position.get("put_call"))
        key = instrument_key(cusip, put_call)
        value = position.get("value")
        shares = position.get("shares")
        value_int = value if isinstance(value, int) and value > 0 else 0
        shares_int = shares if isinstance(shares, int) and shares > 0 else 0

        current = grouped.get(key)
        if current is None:
            grouped[key] = {
                "name": _clean_optional_str(position.get("name")),
                "title": _clean_optional_str(position.get("title")),
                "cusip": cusip,
                "put_call": put_call,
                "value": value_int,
                "shares": shares_int if shares_int > 0 else None,
            }
            continue

        current["value"] = int(current["value"]) + value_int
        if shares_int > 0:
            current_shares = current.get("shares")
            if isinstance(current_shares, int):
                current["shares"] = current_shares + shares_int
            else:
                current["shares"] = shares_int
        if current.get("name") is None:
            current["name"] = _clean_optional_str(position.get("name"))
        if current.get("title") is None:
            current["title"] = _clean_optional_str(position.get("title"))

    return list(grouped.values())


def _weights_by_instrument(snapshot: ManagerQuarterSnapshot) -> dict[str, dict[str, Any]]:
    total_value = 0
    values: dict[str, int] = {}
    metadata: dict[str, dict[str, str | None]] = {}

    for position in snapshot.positions:
        cusip = _clean_optional_str(position.get("cusip"))
        if cusip is None:
            continue
        put_call = _clean_optional_str(position.get("put_call"))
        key = instrument_key(cusip, put_call)
        value = position.get("value")
        if not isinstance(value, int) or value <= 0:
            continue
        values[key] = values.get(key, 0) + value
        total_value += value
        if key not in metadata:
            metadata[key] = {
                "cusip": cusip,
                "put_call": put_call,
                "issuer_name": _clean_optional_str(position.get("name")),
                "title": _clean_optional_str(position.get("title")),
            }

    if total_value <= 0:
        return {}

    weighted: dict[str, dict[str, Any]] = {}
    for key, value in values.items():
        weighted[key] = {
            "value": value,
            "weight": float(value) / float(total_value),
            "metadata": metadata[key],
        }
    return weighted


def _robust_sigma(values: list[float]) -> float:
    if not values:
        return MAD_EPS
    center = median(values)
    deviations = [abs(value - center) for value in values]
    mad = median(deviations)
    sigma = 1.4826 * mad
    if sigma < MAD_EPS:
        return MAD_EPS
    return sigma


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if percentile <= 0:
        return min(values)
    if percentile >= 1:
        return max(values)

    ordered = sorted(values)
    rank = percentile * (len(ordered) - 1)
    lower_idx = int(math.floor(rank))
    upper_idx = int(math.ceil(rank))
    if lower_idx == upper_idx:
        return ordered[lower_idx]
    lower_value = ordered[lower_idx]
    upper_value = ordered[upper_idx]
    weight = rank - lower_idx
    return lower_value + ((upper_value - lower_value) * weight)


def _clip(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _position_signal_weight(max_weight: float) -> float:
    if max_weight <= 0:
        return 0.0
    ratio = max_weight / MAX_POSITION_WEIGHT_FULL_SIGNAL
    return min(1.0, math.sqrt(ratio))


def _resolve_blend_weights(blend_mode: str) -> tuple[float, float]:
    resolved = blend_mode.strip().lower()
    if resolved not in BLEND_WEIGHTS:
        allowed = ", ".join(sorted(BLEND_WEIGHTS))
        raise ValueError(f"Unsupported blend mode '{blend_mode}'. Allowed: {allowed}.")
    return BLEND_WEIGHTS[resolved]


def _entry_impulse_multiplier(prev_weight: float, curr_weight: float) -> float:
    prev = max(0.0, prev_weight)
    curr = max(0.0, curr_weight)

    is_new_entry = prev < MIN_POSITION_WEIGHT and curr >= NEW_ENTRY_IMPULSE_WEIGHT_MIN
    is_near_full_exit = curr < MIN_POSITION_WEIGHT and prev >= NEW_ENTRY_IMPULSE_WEIGHT_MIN
    if not is_new_entry and not is_near_full_exit:
        return 1.0

    event_weight = curr if is_new_entry else prev
    clipped = _clip(event_weight, NEW_ENTRY_IMPULSE_WEIGHT_MIN, NEW_ENTRY_IMPULSE_WEIGHT_MAX)
    scale = (clipped - NEW_ENTRY_IMPULSE_WEIGHT_MIN) / max(
        MAD_EPS, NEW_ENTRY_IMPULSE_WEIGHT_MAX - NEW_ENTRY_IMPULSE_WEIGHT_MIN
    )
    return NEW_ENTRY_IMPULSE_MULT_MIN + (NEW_ENTRY_IMPULSE_MULT_MAX - NEW_ENTRY_IMPULSE_MULT_MIN) * scale


def _adaptive_breadth_thresholds(manager_count: int) -> tuple[int, float]:
    if manager_count <= 0:
        return (BREADTH_MIN_MANAGERS_BASE, BREADTH_WEIGHT_BASE)
    min_managers = max(BREADTH_MIN_MANAGERS_BASE, math.ceil(BREADTH_MANAGERS_RATIO * manager_count))
    extra = max(0, manager_count - BREADTH_WEIGHT_STEP_START)
    min_weight = min(BREADTH_WEIGHT_MAX, BREADTH_WEIGHT_BASE + (extra * BREADTH_WEIGHT_STEP))
    return (min_managers, min_weight)


def _turnover_between_snapshots(prev_snapshot: ManagerQuarterSnapshot, curr_snapshot: ManagerQuarterSnapshot) -> float:
    prev_weights = _weights_by_instrument(prev_snapshot)
    curr_weights = _weights_by_instrument(curr_snapshot)
    keys = prev_weights.keys() | curr_weights.keys()
    if not keys:
        return 0.0
    turnover = 0.5 * sum(
        abs(float(curr_weights.get(key, {}).get("weight", 0.0)) - float(prev_weights.get(key, {}).get("weight", 0.0)))
        for key in keys
    )
    return turnover


def _manager_quality_multipliers(
    *,
    quarters: list[str],
    snapshots_by_quarter: dict[str, dict[str, ManagerQuarterSnapshot]],
    manager_weights: dict[str, float],
) -> dict[str, float]:
    active_ciks = [cik for cik, weight in manager_weights.items() if weight > 0]
    if not active_ciks:
        return {}

    turnovers_by_manager: dict[str, list[float]] = {cik: [] for cik in active_ciks}
    for idx in range(1, len(quarters)):
        prev_q = quarters[idx - 1]
        curr_q = quarters[idx]
        prev_snapshots = snapshots_by_quarter.get(prev_q, {})
        curr_snapshots = snapshots_by_quarter.get(curr_q, {})
        for cik in active_ciks:
            prev_snapshot = prev_snapshots.get(cik)
            curr_snapshot = curr_snapshots.get(cik)
            if prev_snapshot is None or curr_snapshot is None:
                continue
            turnovers_by_manager[cik].append(_turnover_between_snapshots(prev_snapshot, curr_snapshot))

    manager_medians = {
        cik: median(values) if values else 0.0
        for cik, values in turnovers_by_manager.items()
    }
    positive_medians = [value for value in manager_medians.values() if value > 0]
    global_median = median(positive_medians) if positive_medians else 0.0

    multipliers: dict[str, float] = {}
    for cik, values in turnovers_by_manager.items():
        manager_median = manager_medians[cik]
        if global_median > 0 and manager_median > 0:
            turnover_component = _clip(
                math.sqrt(global_median / manager_median),
                MANAGER_QUALITY_TURNOVER_MIN,
                MANAGER_QUALITY_TURNOVER_MAX,
            )
        else:
            turnover_component = 1.0

        active_periods = sum(1 for value in values if value > 0)
        total_periods = max(1, len(values))
        activity_ratio = active_periods / total_periods
        activity_component = MANAGER_QUALITY_ACTIVITY_MIN + (
            (MANAGER_QUALITY_ACTIVITY_MAX - MANAGER_QUALITY_ACTIVITY_MIN) * activity_ratio
        )

        multiplier = _clip(
            turnover_component * activity_component,
            MANAGER_QUALITY_MIN,
            MANAGER_QUALITY_MAX,
        )
        multipliers[cik] = multiplier
    return multipliers


def _confidence_score(
    *,
    direction: Literal["BUY", "SELL"],
    directional_weight: float,
    opposite_weight: float,
    directional_managers: int,
    opposite_managers: int,
    crowding_hhi: float,
    directional_persistence: int,
    min_managers: int,
    min_weight: float,
    magnitude_value: float,
    magnitude_scale: float,
) -> float:
    del direction  # explicit for callsite readability
    breadth_count_score = min(1.0, directional_managers / max(1, min_managers))
    breadth_weight_score = min(1.0, directional_weight / max(MAD_EPS, min_weight))
    breadth_score = 0.5 * (breadth_count_score + breadth_weight_score)

    persistence_score = min(1.0, directional_persistence / 3.0)
    total_participants = directional_managers + opposite_managers
    if total_participants >= MIN_HHI_PARTICIPANTS_FOR_PENALTY:
        crowding_score = 1.0 - max(0.0, (crowding_hhi - ANTI_CROWD_H0) / max(MAD_EPS, 1.0 - ANTI_CROWD_H0))
    else:
        crowding_score = 1.0
    magnitude_score = min(1.0, abs(magnitude_value) / max(MAD_EPS, magnitude_scale))

    base = (0.40 * breadth_score) + (0.25 * persistence_score) + (0.20 * crowding_score) + (0.15 * magnitude_score)
    base_confidence = _clip(base, 0.0, 1.0)
    disagreement = min(1.0, opposite_weight / max(MAD_EPS, directional_weight))
    return _clip(base_confidence * (1.0 - (DISAGREEMENT_GAMMA * disagreement)), 0.0, 1.0)


def _compute_quarter_metrics(
    prev_snapshots: dict[str, ManagerQuarterSnapshot],
    curr_snapshots: dict[str, ManagerQuarterSnapshot],
    manager_base_weights: dict[str, float],
    manager_effective_weights: dict[str, float],
    manager_quality: dict[str, float],
    *,
    contributor_limit: int,
) -> dict[str, _QuarterInstrumentMetric]:
    by_instrument: dict[str, list[_ManagerContribution]] = {}
    metadata_by_instrument: dict[str, dict[str, str | None]] = {}

    for manager_cik, curr_snapshot in curr_snapshots.items():
        prev_snapshot = prev_snapshots.get(manager_cik)
        if prev_snapshot is None:
            continue
        manager_weight_effective = manager_effective_weights.get(manager_cik, 0.0)
        manager_weight_base = manager_base_weights.get(manager_cik, 0.0)
        manager_quality_multiplier = manager_quality.get(manager_cik, 1.0)
        if manager_weight_effective <= 0 or manager_weight_base <= 0:
            continue

        prev_weights = _weights_by_instrument(prev_snapshot)
        curr_weights = _weights_by_instrument(curr_snapshot)
        keys = prev_weights.keys() | curr_weights.keys()

        trade_records: list[_TradeRecord] = []
        for key in keys:
            prev_weight = float(prev_weights.get(key, {}).get("weight", 0.0))
            curr_weight = float(curr_weights.get(key, {}).get("weight", 0.0))
            max_weight = max(prev_weight, curr_weight)
            if max_weight < MIN_POSITION_WEIGHT:
                continue
            metadata = dict(curr_weights.get(key, {}).get("metadata", prev_weights.get(key, {}).get("metadata", {})))
            trade_records.append(
                _TradeRecord(
                    instrument_key=key,
                    trade_dw=curr_weight - prev_weight,
                    prev_weight=prev_weight,
                    curr_weight=curr_weight,
                    max_weight=max_weight,
                    metadata=metadata,
                )
            )

        sigma = _robust_sigma([record.trade_dw for record in trade_records])
        for record in trade_records:
            z_score = _clip(record.trade_dw / sigma, -Z_CLIP, Z_CLIP)
            position_weight = _position_signal_weight(record.max_weight)
            signal_value = manager_weight_effective * position_weight * math.tanh(z_score / Z_SCALE)
            contribution = _ManagerContribution(
                manager_cik=manager_cik,
                manager_name=curr_snapshot.manager_name,
                manager_weight_base=manager_weight_base,
                manager_quality_multiplier=manager_quality_multiplier,
                manager_weight_effective=manager_weight_effective,
                signal_value=signal_value,
                trade_dw=record.trade_dw,
                prev_weight=record.prev_weight,
                curr_weight=record.curr_weight,
            )
            by_instrument.setdefault(record.instrument_key, []).append(contribution)
            if record.instrument_key not in metadata_by_instrument:
                metadata_by_instrument[record.instrument_key] = record.metadata

    metrics: dict[str, _QuarterInstrumentMetric] = {}
    for key, contributions in by_instrument.items():
        np_raw = sum(item.signal_value for item in contributions)
        np_impulse_raw = 0.0
        for item in contributions:
            impulse_mult = _entry_impulse_multiplier(item.prev_weight, item.curr_weight)
            np_impulse_raw += item.signal_value * impulse_mult
        breadth_buy_weight = sum(item.manager_weight_effective for item in contributions if item.signal_value > 0)
        breadth_sell_weight = sum(item.manager_weight_effective for item in contributions if item.signal_value < 0)
        buy_managers = sum(1 for item in contributions if item.signal_value > 0)
        sell_managers = sum(1 for item in contributions if item.signal_value < 0)
        abs_total = sum(abs(item.signal_value) for item in contributions)
        if abs_total > 0:
            crowding_hhi = sum((abs(item.signal_value) / abs_total) ** 2 for item in contributions)
        else:
            crowding_hhi = 0.0
        # Keep NP unpenalized and apply crowding only in confidence.
        np_adj = np_raw
        np_impulse_adj = np_impulse_raw

        top_contributors = sorted(contributions, key=lambda item: abs(item.signal_value), reverse=True)[:contributor_limit]
        contributor_payload = [
            {
                "manager_cik": item.manager_cik,
                "manager_name": item.manager_name,
                "manager_weight_base": round(item.manager_weight_base, 8),
                "manager_quality_multiplier": round(item.manager_quality_multiplier, 8),
                "manager_weight_effective": round(item.manager_weight_effective, 8),
                "signal_value": round(item.signal_value, 8),
                "trade_dw": round(item.trade_dw, 8),
                "prev_weight": round(item.prev_weight, 8),
                "curr_weight": round(item.curr_weight, 8),
                "impulse_multiplier": round(_entry_impulse_multiplier(item.prev_weight, item.curr_weight), 8),
            }
            for item in top_contributors
        ]

        metrics[key] = _QuarterInstrumentMetric(
            metadata=metadata_by_instrument.get(key, {}),
            np_raw=np_raw,
            np_adj=np_adj,
            np_impulse_adj=np_impulse_adj,
            breadth_buy_weight=breadth_buy_weight,
            breadth_sell_weight=breadth_sell_weight,
            buy_managers=buy_managers,
            sell_managers=sell_managers,
            crowding_hhi=crowding_hhi,
            contributors=contributor_payload,
        )

    return metrics


def _classify_regime(
    trend_ewma: float,
    trend_delta: float,
    prev_trend_ewma: float,
    *,
    buy_gate: bool,
    sell_gate: bool,
    persistence_buy: int,
    persistence_sell: int,
) -> str:
    if trend_ewma > 0:
        if prev_trend_ewma <= 0 and buy_gate:
            return "REVERSAL_BUY"
        if persistence_buy >= 2 and buy_gate:
            return "STRONG_BUY"
        if trend_delta > 0 and buy_gate:
            return "EMERGING_BUY"
        if trend_delta < 0 and buy_gate:
            return "WEAKENING_BUY"
        return "NONE"

    if trend_ewma < 0:
        if prev_trend_ewma >= 0 and sell_gate:
            return "REVERSAL_SELL"
        if persistence_sell >= 2 and sell_gate:
            return "STRONG_SELL"
        if trend_delta < 0 and sell_gate:
            return "EMERGING_SELL"
        if trend_delta > 0 and sell_gate:
            return "WEAKENING_SELL"
        return "NONE"

    return "NONE"


def compute_trend_signals(
    *,
    quarters: list[str],
    snapshots_by_quarter: dict[str, dict[str, ManagerQuarterSnapshot]],
    manager_weights: dict[str, float],
    blend_mode: Literal["tactical", "portfolio"] = BLEND_TACTICAL,
    contributor_limit: int = 5,
) -> TrendComputationResult:
    if len(quarters) < 2:
        raise ValueError("At least 2 quarters are required to compute trend signals.")

    total_weight = sum(weight for weight in manager_weights.values() if weight > 0)
    if total_weight <= 0:
        raise ValueError("Manager weights must contain at least one positive value.")
    normalized_base_weights = {
        cik: weight / total_weight
        for cik, weight in manager_weights.items()
        if weight > 0
    }

    manager_quality = _manager_quality_multipliers(
        quarters=quarters,
        snapshots_by_quarter=snapshots_by_quarter,
        manager_weights=normalized_base_weights,
    )
    quality_weighted = {
        cik: weight * manager_quality.get(cik, 1.0)
        for cik, weight in normalized_base_weights.items()
    }
    quality_weight_total = sum(quality_weighted.values())
    if quality_weight_total <= 0:
        normalized_effective_weights = normalized_base_weights
    else:
        normalized_effective_weights = {
            cik: weight / quality_weight_total
            for cik, weight in quality_weighted.items()
        }

    manager_count = len(normalized_effective_weights)
    breadth_min_managers, breadth_min_weight = _adaptive_breadth_thresholds(manager_count)
    impulse_blend_weight, accumulation_blend_weight = _resolve_blend_weights(blend_mode)

    impulse_decay = math.exp(math.log(0.5) / IMPULSE_HALFLIFE_QUARTERS)
    accumulation_decay = math.exp(math.log(0.5) / ACCUMULATION_HALFLIFE_QUARTERS)
    state_by_instrument: dict[str, _InstrumentState] = {}
    final_rows: dict[str, TrendSignalRow] = {}

    for idx in range(1, len(quarters)):
        prev_quarter = quarters[idx - 1]
        curr_quarter = quarters[idx]
        quarter_metrics = _compute_quarter_metrics(
            snapshots_by_quarter.get(prev_quarter, {}),
            snapshots_by_quarter.get(curr_quarter, {}),
            normalized_base_weights,
            normalized_effective_weights,
            manager_quality,
            contributor_limit=contributor_limit,
        )
        quarter_abs_np_adj = [abs(metric.np_adj) for metric in quarter_metrics.values() if abs(metric.np_adj) > 0]
        quarter_magnitude_scale = _percentile(quarter_abs_np_adj, MAGNITUDE_QUARTER_PERCENTILE)
        if quarter_magnitude_scale <= 0:
            quarter_magnitude_scale = MAGNITUDE_FALLBACK_SCALE
        keys = state_by_instrument.keys() | quarter_metrics.keys()

        next_state: dict[str, _InstrumentState] = {}
        for key in keys:
            metric = quarter_metrics.get(key)
            previous_state = state_by_instrument.get(
                key,
                _InstrumentState(
                    metadata={},
                    impulse_ewma=0.0,
                    accumulation_ewma=0.0,
                    trend_ewma=0.0,
                    persistence_buy=0,
                    persistence_sell=0,
                ),
            )

            np_raw = metric.np_raw if metric else 0.0
            np_adj = metric.np_adj if metric else 0.0
            np_impulse_adj = metric.np_impulse_adj if metric else 0.0
            breadth_buy_weight = metric.breadth_buy_weight if metric else 0.0
            breadth_sell_weight = metric.breadth_sell_weight if metric else 0.0
            buy_managers = metric.buy_managers if metric else 0
            sell_managers = metric.sell_managers if metric else 0
            crowding_hhi = metric.crowding_hhi if metric else 0.0
            contributors = metric.contributors if metric else []

            buy_gate = buy_managers >= breadth_min_managers or breadth_buy_weight >= breadth_min_weight
            sell_gate = sell_managers >= breadth_min_managers or breadth_sell_weight >= breadth_min_weight

            persistence_buy = previous_state.persistence_buy + 1 if np_adj > 0 and buy_gate else 0
            persistence_sell = previous_state.persistence_sell + 1 if np_adj < 0 and sell_gate else 0

            prev_trend = previous_state.trend_ewma
            impulse_score = (impulse_decay * previous_state.impulse_ewma) + ((1.0 - impulse_decay) * np_impulse_adj)
            accumulation_score = (accumulation_decay * previous_state.accumulation_ewma) + (
                (1.0 - accumulation_decay) * np_adj
            )
            blended_score = (impulse_blend_weight * impulse_score) + (
                accumulation_blend_weight * accumulation_score
            )
            direction: Literal["BUY", "SELL"] = "BUY" if blended_score >= 0 else "SELL"
            if direction == "BUY":
                directional_weight = breadth_buy_weight
                opposite_weight = breadth_sell_weight
                directional_managers = buy_managers
                opposite_managers = sell_managers
                directional_persistence = persistence_buy
            else:
                directional_weight = breadth_sell_weight
                opposite_weight = breadth_buy_weight
                directional_managers = sell_managers
                opposite_managers = buy_managers
                directional_persistence = persistence_sell
            confidence = _confidence_score(
                direction=direction,
                directional_weight=directional_weight,
                opposite_weight=opposite_weight,
                directional_managers=directional_managers,
                opposite_managers=opposite_managers,
                crowding_hhi=crowding_hhi,
                directional_persistence=directional_persistence,
                min_managers=breadth_min_managers,
                min_weight=breadth_min_weight,
                magnitude_value=np_adj,
                magnitude_scale=quarter_magnitude_scale,
            )
            trend_ewma = blended_score * confidence
            trend_delta = trend_ewma - prev_trend

            regime = _classify_regime(
                trend_ewma,
                trend_delta,
                prev_trend,
                buy_gate=buy_gate,
                sell_gate=sell_gate,
                persistence_buy=persistence_buy,
                persistence_sell=persistence_sell,
            )

            metadata = metric.metadata if metric else previous_state.metadata
            next_state[key] = _InstrumentState(
                metadata=metadata,
                impulse_ewma=impulse_score,
                accumulation_ewma=accumulation_score,
                trend_ewma=trend_ewma,
                persistence_buy=persistence_buy,
                persistence_sell=persistence_sell,
            )

            if idx != len(quarters) - 1:
                continue

            final_rows[key] = TrendSignalRow(
                instrument_key=key,
                cusip=metadata.get("cusip"),
                put_call=metadata.get("put_call"),
                issuer_name=metadata.get("issuer_name"),
                title=metadata.get("title"),
                np_raw=np_raw,
                np_adj=np_adj,
                impulse_score=impulse_score,
                accumulation_score=accumulation_score,
                confidence=confidence,
                trend_ewma=trend_ewma,
                trend_delta=trend_delta,
                breadth_buy_weight=breadth_buy_weight,
                breadth_sell_weight=breadth_sell_weight,
                buy_managers=buy_managers,
                sell_managers=sell_managers,
                crowding_hhi=crowding_hhi,
                persistence_buy=persistence_buy,
                persistence_sell=persistence_sell,
                regime=regime,
                contributors=contributors,
            )

        state_by_instrument = next_state

    target_quarter = quarters[-1]
    signals = sorted(final_rows.values(), key=lambda item: item.instrument_key)
    top_buy = [
        row.instrument_key
        for row in sorted(
            (item for item in signals if "BUY" in item.regime and item.regime != "REVERSAL_SELL"),
            key=lambda item: (-item.trend_ewma, item.instrument_key),
        )
    ]
    top_sell = [
        row.instrument_key
        for row in sorted(
            (item for item in signals if "SELL" in item.regime and item.regime != "REVERSAL_BUY"),
            key=lambda item: (item.trend_ewma, item.instrument_key),
        )
    ]
    reversals = [
        row.instrument_key
        for row in sorted(
            (item for item in signals if item.regime in {"REVERSAL_BUY", "REVERSAL_SELL"}),
            key=lambda item: (-abs(item.trend_delta), item.instrument_key),
        )
    ]
    return TrendComputationResult(
        target_quarter=target_quarter,
        signals=signals,
        top_buy=top_buy,
        top_sell=top_sell,
        reversals=reversals,
    )
