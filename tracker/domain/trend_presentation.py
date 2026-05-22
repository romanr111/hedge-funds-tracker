from __future__ import annotations


SELL_TABLE_MIN_CONF = 0.35


def target_confidence_for_regime(regime: str) -> float:
    normalized = (regime or "").strip().upper()
    if normalized.startswith("STRONG_"):
        return 0.65
    if normalized in {"REVERSAL_SELL", "EMERGING_SELL"}:
        return SELL_TABLE_MIN_CONF
    if normalized in {"REVERSAL_BUY", "EMERGING_BUY"}:
        return 0.45
    if normalized.startswith("WEAKENING_"):
        return 0.50
    return 0.50


def action_for_signal(regime: str, confidence: float) -> str:
    target = target_confidence_for_regime(regime)
    normalized = (regime or "").strip().upper()
    target_gap_pp = (target - float(confidence)) * 100.0

    if normalized.startswith("STRONG_") and float(confidence) >= target:
        if normalized.endswith("_BUY"):
            return "BUY"
        if normalized.endswith("_SELL"):
            return "SELL"
    has_direction = normalized.endswith("_BUY") or normalized.endswith("_SELL")
    if has_direction and target_gap_pp <= 5.0 + 1e-9:
        return "INTERESTING_IDEA"
    return "MONITOR"


def setup_for_regime(regime: str) -> str:
    normalized = (regime or "").strip().upper()
    if normalized.startswith("STRONG_"):
        return "Strong"
    if normalized.startswith("REVERSAL_"):
        return "Reversal"
    if normalized.startswith("EMERGING_"):
        return "Emerging"
    if normalized.startswith("WEAKENING_"):
        return "Weakening"
    return "Unknown"


def conviction_target(confidence: float, regime: str) -> str:
    confidence_pct = round(float(confidence) * 100)
    target_pct = round(target_confidence_for_regime(regime) * 100)
    return f"{confidence_pct}% (Target: {target_pct}%)"


def freshness_icon(freshness_ok: bool | None) -> str:
    if freshness_ok is None:
        return "?"
    return "+" if bool(freshness_ok) else "-"
