from __future__ import annotations

import json
import re


SELL_TABLE_MIN_CONF = 0.35
HIGH_SIGNAL_WEIGHT_THRESHOLD = 1.0
MANAGER_SHORT_NAME_OVERRIDES = {
    "TCI Fund Management Ltd": "TCI",
    "Coatue Management LLC": "Coatue",
    "Appaloosa Management LP": "Appaloosa",
    "Pershing Square Capital Management, L.P.": "Pershing Square",
    "Viking Global Investors LP": "Viking Global",
    "Li Lu - Himalaya Capital Management": "Himalaya",
    "Bill Nygren - Oakmark Select Fund": "Oakmark",
    "Richard Pzena - Hancock Classic Value": "Pzena",
    "Francois Rochon - Giverny Capital": "Giverny",
    "Christopher Davis - Davis Advisors": "Davis",
    "Thomas Gayner - Markel Group": "Markel",
}
LEGAL_SUFFIX_PATTERN = re.compile(r"(?:,?\s+(?:L\.?P\.?|L\.?L\.?C\.?|LTD\.?|INC\.?))$", re.IGNORECASE)


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


def _compact_manager_name(name: str) -> str:
    known_name = MANAGER_SHORT_NAME_OVERRIDES.get(name)
    if known_name is not None:
        return known_name
    return LEGAL_SUFFIX_PATTERN.sub("", name).strip()


def _configured_weight(contributor: dict[str, object]) -> float | None:
    raw_weight = contributor.get("manager_weight_configured")
    if isinstance(raw_weight, (int, float)):
        return float(raw_weight)
    return None


def directional_contributor_names(contributors_json: str, direction: str, *, limit: int = 3) -> str:
    try:
        contributors = json.loads(contributors_json)
    except (TypeError, json.JSONDecodeError):
        return "-"
    if not isinstance(contributors, list):
        return "-"

    buy_direction = direction == "BUY"
    names: list[str] = []
    for contributor in contributors:
        if not isinstance(contributor, dict):
            continue
        signal_value = contributor.get("signal_value")
        if not isinstance(signal_value, (int, float)):
            continue
        if (buy_direction and signal_value <= 0) or (not buy_direction and signal_value >= 0):
            continue
        name = str(contributor.get("manager_name") or contributor.get("manager_cik") or "").strip()
        if name:
            name = _compact_manager_name(name)
            weight = _configured_weight(contributor)
            if weight is not None and weight > HIGH_SIGNAL_WEIGHT_THRESHOLD:
                name = f"✅ {name}"
        if name and name not in names:
            names.append(name)
        if len(names) >= max(1, limit):
            break
    return f"[{', '.join(names)}]" if names else "-"
