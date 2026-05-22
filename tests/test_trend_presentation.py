from __future__ import annotations

import json

from tracker.domain.trend_presentation import (
    action_for_signal,
    conviction_target,
    directional_contributor_names,
    freshness_icon,
    setup_for_regime,
    target_confidence_for_regime,
)


def test_target_confidence_for_regime_matches_tracker_rules() -> None:
    assert target_confidence_for_regime("STRONG_BUY") == 0.65
    assert target_confidence_for_regime("REVERSAL_SELL") == 0.35
    assert target_confidence_for_regime("EMERGING_BUY") == 0.45
    assert target_confidence_for_regime("WEAKENING_BUY") == 0.50
    assert target_confidence_for_regime("NONE") == 0.50


def test_action_for_signal_matches_tracker_rules() -> None:
    assert action_for_signal("STRONG_BUY", 0.70) == "BUY"
    assert action_for_signal("STRONG_SELL", 0.70) == "SELL"
    assert action_for_signal("EMERGING_BUY", 0.43) == "INTERESTING_IDEA"
    assert action_for_signal("NONE", 0.90) == "MONITOR"


def test_setup_conviction_and_freshness_helpers() -> None:
    assert setup_for_regime("REVERSAL_BUY") == "Reversal"
    assert setup_for_regime("NONE") == "Unknown"
    assert conviction_target(0.533, "STRONG_BUY") == "53% (Target: 65%)"
    assert freshness_icon(True) == "+"
    assert freshness_icon(False) == "-"
    assert freshness_icon(None) == "?"


def test_directional_contributor_names_compact_known_funds_and_mark_high_signal() -> None:
    contributors_json = json.dumps(
        [
            {"manager_name": "TCI Fund Management Ltd", "signal_value": 0.40, "manager_weight_configured": 1.0},
            {"manager_name": "Pershing Square Capital Management, L.P.", "signal_value": -0.35, "manager_weight_configured": 1.0},
            {"manager_name": "Coatue Management LLC", "signal_value": 0.30, "manager_weight_configured": 1.0},
            {"manager_name": "Appaloosa Management LP", "signal_value": 0.20, "manager_weight_configured": 1.5},
            {"manager_name": "Viking Global Investors LP", "signal_value": 0.10, "manager_weight_configured": 1.0},
        ],
        separators=(",", ":"),
    )

    assert directional_contributor_names(contributors_json, "BUY") == "[TCI, Coatue, ✅ Appaloosa]"


def test_directional_contributor_names_compact_existing_payload_without_high_signal_weight() -> None:
    contributors_json = json.dumps(
        [
            {"manager_name": "Appaloosa Management LP", "signal_value": -0.40},
            {"manager_name": "Some Capital Management LLC", "signal_value": -0.30},
        ],
        separators=(",", ":"),
    )

    assert directional_contributor_names(contributors_json, "REDUCTION") == "[Appaloosa, Some Capital Management]"
