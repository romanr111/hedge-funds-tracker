"""Composite module gauges: weighted risk-percentile mean -> band (report sec. 6).

Score = weighted mean of member risk percentiles, where risk_pct =
percentile (or its mirror when spec.invert: risk = LOW raw value), computed
only over members that carry a percentile and a weight. Stale members are
excluded by compute_module_gauge; the surviving weights implicitly renormalize
through division by their sum (equivalent to renormalize_weights).
"""
from __future__ import annotations

from typing import Collection, Mapping, Sequence

from market_intel.domain.bands import evaluate_band
from market_intel.domain.exceptions import GaugeComputeError
from market_intel.domain.models import (
    COLOR_TOKEN_HEX,
    MODULE_TITLES_UK,
    ModuleGauge,
    SeriesSpec,
    SeriesSnapshot,
)
from market_intel.domain.verdicts import format_value_uk, module_forward_context_uk, module_verdict_uk

_RISK_CAP = 99.99
_UNAVAILABLE_COLOR = "#9E9E9E"
_UNAVAILABLE_LABEL_UK = "Недоступно"
_MAX_FACTS = 4
_INVERT_COMPLEMENT = 100.0

_STALE_STATE_KEY = "stale"
_THIN_WINDOW_NOTE_UK = "перцентиль недоступний: замале вікно історії"
_NO_WEIGHT_NOTE_UK = "виключений з композита: вага не задана"


def renormalize_weights(weights: Mapping[str, float], drop: Collection[str]) -> dict[str, float]:
    """Rescale the kept weights so they sum to 1.0.

    Keys in ``drop`` are removed first (unknown keys are ignored). Raise
    GaugeComputeError when nothing is left or the surviving sum is not
    positive.
    """
    drop_set = set(drop)
    kept = {key: float(weight) for key, weight in weights.items() if key not in drop_set}
    if not kept:
        raise GaugeComputeError("Cannot renormalize weights: no members left after drops")
    total = sum(kept.values())
    if not total > 0.0:
        raise GaugeComputeError("Cannot renormalize weights: total weight is not positive")
    return {key: weight / total for key, weight in kept.items()}


def module_score(members: Sequence[SeriesSnapshot], specs: Mapping[str, SeriesSpec]) -> float | None:
    """Weighted mean risk percentile over members with percentile and weight.

    Returns None when no member contributes. Members are expected pre-filtered
    for staleness by the caller (compute_module_gauge). risk_pct is capped at
    99.99 so a 0-percentile member cannot push the composite to exactly 100.
    """
    weighted_sum = 0.0
    weights_sum = 0.0
    for member in members:
        spec = specs.get(member.key)
        if spec is None or spec.weight is None or member.percentile is None:
            continue
        risk_pct = member.percentile if not spec.invert else _INVERT_COMPLEMENT - member.percentile
        weighted_sum += spec.weight * min(risk_pct, _RISK_CAP)
        weights_sum += spec.weight
    if weights_sum <= 0.0:
        return None
    return min(weighted_sum / weights_sum, _RISK_CAP)


def compute_module_gauge(
    module_key: str,
    members: Sequence[SeriesSnapshot],
    specs: Mapping[str, SeriesSpec],
) -> ModuleGauge:
    """Build one module gauge from its member snapshots.

    Stale members are dropped with their staleness reason, weight-less members
    with a note; the composite score maps to the composite_<module> band. When
    nothing contributes (all stale, thin percentile windows, no members) the
    gauge degrades to the 'Недоступно' chip without raising.
    """
    dropped_uk: list[str] = []
    live: list[SeriesSnapshot] = []
    for member in members:
        spec = specs.get(member.key)
        if member.is_stale:
            reason = member.stale_reason_uk or "застарілі дані"
            dropped_uk.append(f"{member.key}: {reason}")
            continue
        if spec is None:
            dropped_uk.append(f"{member.key}: немає специфікації серії")
            continue
        if spec.weight is None:
            dropped_uk.append(f"{member.key}: {_NO_WEIGHT_NOTE_UK}")
            continue
        if member.percentile is None:
            dropped_uk.append(f"{member.key}: {_THIN_WINDOW_NOTE_UK}")
            continue
        live.append(member)

    score = module_score(live, specs)
    members_used = len(live)
    members_total = len(members)

    if score is None or not live:
        state_key = _STALE_STATE_KEY
        chip_label = _UNAVAILABLE_LABEL_UK
        chip_color = _UNAVAILABLE_COLOR
        verdict_uk = module_verdict_uk(module_key, state_key, None, members_used)
    else:
        band = evaluate_band(f"composite_{module_key}", score)
        state_key = band.key
        chip_label = band.label_uk
        chip_color = COLOR_TOKEN_HEX[band.color_token]
        verdict_uk = module_verdict_uk(module_key, band.key, score, members_used)

    return ModuleGauge(
        module_key=module_key,
        title_uk=MODULE_TITLES_UK[module_key],
        score=score,
        state_key=state_key,
        chip_label_uk=chip_label,
        chip_color=chip_color,
        verdict_uk=verdict_uk,
        context_forward_uk=module_forward_context_uk(module_key),
        facts_uk=_member_facts_uk(live),
        members_used=members_used,
        members_total=members_total,
        dropped_uk=tuple(dropped_uk),
    )


def _member_facts_uk(live: Sequence[SeriesSnapshot]) -> tuple[str, ...]:
    """Generic dated facts from contributing members (curated sets override)."""
    facts: list[str] = []
    for member in live:
        if member.value is None or member.obs_date is None:
            continue
        facts.append(f"{member.title_uk}: {format_value_uk(member.value, member.unit_uk)} ({member.obs_date.isoformat()})")
        if len(facts) >= _MAX_FACTS:
            break
    return tuple(facts)
