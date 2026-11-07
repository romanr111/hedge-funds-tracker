from __future__ import annotations

import pytest

from market_intel.domain.models import MODULE_KEYS
from market_intel.registries import SERIES_SPECS

KNOWN_SOURCES = {"fred", "yahoo", "multpl", "finra", "cnn_fg", "factset", "damodaran", "computed"}
KNOWN_FREQS = {"daily", "weekly", "monthly", "quarterly"}

# Literal band vocabulary agreed in the approved plan (superset of used bands).
KNOWN_BAND_REFS = {
    'cape', 'ecy', 'forward_pe', 'buffett_z1', 'erp', 'fed_funds', 'fg', 'vix',
    'vix_vix3m', 'margin_yoy', 'weekly_rsi', 'pct_200dma', 'drawdown_2y',
    'nfci', 'curve', 'claims_yoy', 'real_10y', 'netliq_yoy', 'hy_oas',
    'hy_change_6m', 'ig_oas', 'sofr_iorb',
}

# Weighted members per module (approved plan). Display-only and input entries
# carry weight=None. Note: approved weights sum to 1.0 only for `technique`;
# the composites normalize by these module totals (see orchestrator plan).
EXPECTED_WEIGHTS_BY_KEY = {
    "hy_oas": 0.35,
    "ig_oas": 0.15,
    "nfci": 0.25,
    "curve_3m": 0.20,
    "real_10y": 0.15,
    "vix": 0.20,
    "fg": 0.25,
    "cape": 1.0,
    "forward_pe": 1.0,
    "erp": 1.0,
    "vix_vix3m": 0.10,
    "claims_yoy": 0.20,
    "netliq_yoy": 0.10,
    "sofr_iorb": 0.15,
    "hy_change_6m": 0.20,
    "pct_200dma": 0.30,
    "drawdown_2y": 0.30,
    "weekly_rsi": 0.40,
    "buffett_z1": 1.0,
}

EXPECTED_MODULE_WEIGHT_TOTALS = {
    "credit": 0.85,
    "macro": 0.90,
    "sentiment": 0.55,
    "technique": 1.0,
    "valuation": 4.0,
}

DISPLAY_ONLY_KEYS = {"ecy", "fed_funds"}
INPUT_ONLY_KEYS = {
    "sofr", "iorb", "icsa", "walcl", "rrpontsyd", "wregen", "spx", "vix3m",
    "margin_debt",
}
COMPUTED_BANDED_KEYS = {
    "ecy", "vix_vix3m", "claims_yoy", "netliq_yoy", "sofr_iorb",
    "hy_change_6m", "pct_200dma", "drawdown_2y", "weekly_rsi", "buffett_z1",
}
INVERTED_KEYS = {"vix", "netliq_yoy"}

EXPECTED_MODULE_BY_KEY = {
    "hy_oas": "credit",
    "ig_oas": "credit",
    "sofr_iorb": "credit",
    "hy_change_6m": "credit",
    "nfci": "macro",
    "curve_3m": "macro",
    "real_10y": "macro",
    "claims_yoy": "macro",
    "netliq_yoy": "macro",
    "fed_funds": "macro",
    "vix": "sentiment",
    "fg": "sentiment",
    "vix_vix3m": "sentiment",
    "cape": "valuation",
    "forward_pe": "valuation",
    "erp": "valuation",
    "ecy": "valuation",
    "buffett_z1": "valuation",
    "pct_200dma": "technique",
    "drawdown_2y": "technique",
    "weekly_rsi": "technique",
}


def test_every_spec_has_valid_source_freq_staleness_module() -> None:
    assert len(SERIES_SPECS) > 20
    for key, spec in SERIES_SPECS.items():
        assert spec.source in KNOWN_SOURCES, key
        assert spec.freq in KNOWN_FREQS, key
        assert spec.staleness_days > 0, key
        assert spec.module_key in MODULE_KEYS or spec.module_key is None, key
        assert spec.title_uk.strip(), key


def test_band_refs_are_subset_of_known_vocabulary() -> None:
    for key, spec in SERIES_SPECS.items():
        if spec.band_ref is not None:
            assert spec.band_ref in KNOWN_BAND_REFS, key


def test_weighted_modules_match_approved_weights() -> None:
    for key, spec in SERIES_SPECS.items():
        expected = EXPECTED_WEIGHTS_BY_KEY.get(key)
        if spec.weight is None:
            assert expected is None, key
            continue
        assert expected is not None, f"unexpected weighted key: {key}"
        assert spec.weight == pytest.approx(expected, abs=1e-9), key
        assert spec.module_key == EXPECTED_MODULE_BY_KEY.get(key), key


def test_module_weight_totals_are_positive_and_documented() -> None:
    totals: dict[str, float] = {}
    for spec in SERIES_SPECS.values():
        if spec.weight is None:
            continue
        assert spec.module_key is not None
        totals[spec.module_key] = totals.get(spec.module_key, 0.0) + spec.weight
    assert set(totals) == set(EXPECTED_MODULE_WEIGHT_TOTALS)
    for module_key, expected in EXPECTED_MODULE_WEIGHT_TOTALS.items():
        assert totals[module_key] == pytest.approx(expected, abs=1e-9), module_key


def test_display_only_and_input_entries_have_no_weight_or_bands() -> None:
    for key in DISPLAY_ONLY_KEYS:
        spec = SERIES_SPECS[key]
        assert spec.weight is None, key
        assert spec.module_key is not None, key  # dashboard still shows them
    for key in INPUT_ONLY_KEYS:
        spec = SERIES_SPECS[key]
        assert spec.weight is None, key
        assert spec.module_key is None, key
        assert spec.band_ref is None, key
    # Display-only gauges still expose a band for the dashboard.
    assert SERIES_SPECS["ecy"].band_ref == "ecy"
    assert SERIES_SPECS["fed_funds"].band_ref == "fed_funds"


def test_computed_specs_reference_input_keys() -> None:
    for key in COMPUTED_BANDED_KEYS:
        spec = SERIES_SPECS[key]
        assert spec.source == "computed"
        assert spec.is_computed
        assert spec.params, f"computed spec {key} must reference its inputs"
    expected_params = {
        "ecy": {"cape": "cape", "real_10y": "real_10y"},
        "netliq_yoy": {"walcl": "walcl", "rrp": "rrpontsyd", "tga": "wregen"},
        "sofr_iorb": {"sofr": "sofr", "iorb": "iorb"},
        "hy_change_6m": {"hy": "hy_oas"},
        "claims_yoy": {"icsa": "icsa"},
        "vix_vix3m": {"vix": "vix", "vix3m": "vix3m"},
        "buffett_z1": {"equities": "ncbeilq027s", "gdp": "gdp"},
        "pct_200dma": {"market": "spx"},
        "drawdown_2y": {"market": "spx"},
        "weekly_rsi": {"market": "spx"},
    }
    for key, expected in expected_params.items():
        assert dict(SERIES_SPECS[key].params) == expected, key


def test_invert_flags_and_registry_keys_unique() -> None:
    keys = list(SERIES_SPECS.keys())
    assert len(keys) == len(set(keys))
    assert len(keys) == len({spec.key for spec in SERIES_SPECS.values()})
    for key, spec in SERIES_SPECS.items():
        assert spec.invert == (key in INVERTED_KEYS), key


def test_gateway_knobs_are_present_per_source() -> None:
    for key, spec in SERIES_SPECS.items():
        if spec.source == "fred":
            assert spec.params.get("series_id"), key
        elif spec.source == "yahoo":
            assert spec.params.get("symbol", "").startswith("^"), key
        elif spec.source in {"finra", "cnn_fg", "factset", "damodaran"}:
            assert spec.params.get("url", "").startswith("https://"), key
        elif spec.source == "multpl":
            assert spec.params.get("current_url", "").startswith("https://"), key
            assert spec.params.get("history_url", "").startswith("https://"), key


def test_mapping_is_immutable_mapping_proxy() -> None:
    with pytest.raises(TypeError):
        SERIES_SPECS["new_key"] = SERIES_SPECS["spx"]  # type: ignore[index]
