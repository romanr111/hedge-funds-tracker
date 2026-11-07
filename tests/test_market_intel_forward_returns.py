from __future__ import annotations

from market_intel.domain.forward_returns import FORWARD_ROWS, evaluate_forward_rows
from market_intel.domain.models import SeriesSnapshot


def _snap(key, value) -> SeriesSnapshot:
    return SeriesSnapshot(
        key=key,
        title_uk=key,
        module_key=None,
        value=value,
        obs_date=None,
        percentile=None,
        band=None,
        is_stale=False,
        stale_reason_uk=None,
        meaning_uk="",
        source_uk="test",
        unit_uk="",
    )


def _active_keys(series) -> list[str]:
    return [r.rule_key for r in evaluate_forward_rows({}, series) if r.active_now]


def test_forward_rows_transcription_is_complete_and_verbatim() -> None:
    assert len(FORWARD_ROWS) == 19
    keys = [r.rule_key for r in FORWARD_ROWS]
    assert len(set(keys)) == len(keys)
    for row in FORWARD_ROWS:
        assert row.condition_uk and row.horizon_uk and row.citation_uk
        assert row.evidence_level in ("ESTABLISHED", "TENTATIVE", "NOT ESTABLISHED")
    assert keys[0] == "cape_gt_25"
    assert keys[-1] == "ndx_any"


def test_verbatim_cells_from_the_research_report() -> None:
    cape = FORWARD_ROWS[0]
    assert cape.result_uk == "~0,5%/р. середнє"
    assert cape.uncertainty_uk == "розкид -6,1..+8,3%/р."
    assert cape.citation_uk == "Asness-банди (через Swedroe); Vanguard R² 0,43"
    rsi_row = next(r for r in FORWARD_ROWS if r.rule_key == "weekly_rsi_below_30")
    assert rsi_row.result_uk == "мед. +8,6%, 79% винів"
    assert rsi_row.max_dd_uk == "12 міс. ~+24%, 90% позитивних"
    assert rsi_row.n_uk == "n=46 тижнів/50 р."
    margin = next(r for r in FORWARD_ROWS if r.rule_key == "margin_peak_after_yoy_30")
    assert margin.uncertainty_uk == "2000-02 -49%; 2007-09 -57%; 2021-22 -25%"
    claims = next(r for r in FORWARD_ROWS if r.rule_key == "claims_yoy_gt_20")
    assert claims.result_uk == "рецесія йде; домінує DD-ризик"

    ndx = FORWARD_ROWS[-1]
    assert ndx.evidence_level == "NOT ESTABLISHED"
    assert ndx.result_uk == "NOT ESTABLISHED"
    assert ndx.citation_uk == "перевірка дослідження 2026-11-06"
    assert ndx.evaluated_in_mvp is False


def test_non_mvp_rows_never_activate() -> None:
    non_mvp = {r.rule_key for r in FORWARD_ROWS if not r.evaluated_in_mvp}
    assert non_mvp == {
        "pct_below_50dma_lt_25", "spx_3sd_below_200dma", "zweig_thrust",
        "aaii_bulls_minus_2sd", "lowry_oversold_reversal", "ndx_any",
    }
    for row in evaluate_forward_rows({}, {}):
        if not row.evaluated_in_mvp:
            assert row.active_now is False
    # even a series full of plausible values cannot activate a non-MVP row
    rich = {
        "pct_below_50dma": _snap("pct_below_50dma", 10.0),
        "spx": _snap("spx", 1000.0),
        "adx": _snap("adx", 50.0),
        "aaii_bulls": _snap("aaii_bulls", 12.0),
        "lowry": _snap("lowry", 1.0),
        "ndx": _snap("ndx", 5000.0),
    }
    for row in evaluate_forward_rows({}, rich):
        if not row.evaluated_in_mvp:
            assert row.active_now is False


def test_missing_series_activate_nothing() -> None:
    rebuilt = evaluate_forward_rows({}, {})
    assert len(rebuilt) == len(FORWARD_ROWS)
    assert all(not row.active_now for row in rebuilt)
    assert rebuilt[0].condition_uk == "CAPE > 25"


def test_cape_predicates_are_mutually_exclusive() -> None:
    assert _active_keys({"cape": _snap("cape", 26.0)}) == ["cape_gt_25"]
    assert _active_keys({"cape": _snap("cape", 16.5)}) == ["cape_near_16"]
    assert _active_keys({"cape": _snap("cape", 25.0)}) == []  # 25 is not > 25
    assert _active_keys({"cape": _snap("cape", 19.0)}) == []


def test_curve_predicates_from_live_diagnostics() -> None:
    inverted = _active_keys({"curve_3m": _snap("curve_3m", -0.2),
                             "curve_slope_90d": _snap("curve_slope_90d", -0.1)})
    assert inverted == ["curve_inverted"]
    rising = _active_keys({"curve_3m": _snap("curve_3m", -0.2),
                           "curve_slope_90d": _snap("curve_slope_90d", 0.1)})
    assert rising == ["curve_inverted", "curve_inverted_rising"]
    normal = _active_keys({"curve_3m": _snap("curve_3m", 1.5),
                           "curve_slope_90d": _snap("curve_slope_90d", 0.1)})
    assert normal == ["curve_normal"]
    # after inverted past: curve > 0 now while 540d min was negative
    after = _active_keys({"curve_3m": _snap("curve_3m", 0.9),
                          "curve_min_540d": _snap("curve_min_540d", -0.3)})
    assert after == ["post_inversion_resteepening"]
    # flat curve without inversion history activates none of the curve rows
    neither = _active_keys({"curve_3m": _snap("curve_3m", 0.5),
                            "curve_min_540d": _snap("curve_min_540d", 0.4)})
    assert neither == []


def test_sentiment_predicates() -> None:
    assert _active_keys({"vix": _snap("vix", 40.5)}) == ["vix_close_above_40"]
    assert _active_keys({"vix": _snap("vix", 40.0)}) == []
    assert _active_keys({"weekly_rsi": _snap("weekly_rsi", 25.0)}) == ["weekly_rsi_below_30"]
    assert _active_keys({"fg": _snap("fg", 15.0)}) == ["fg_below_20"]
    assert _active_keys({"fg": _snap("fg", 20.0)}) == []


def test_margin_peak_requires_yoy_max_30_and_falling_mom() -> None:
    peak = _active_keys({"margin_yoy_max_6m": _snap("margin_yoy_max_6m", 32.0),
                         "margin_mom_change": _snap("margin_mom_change", -1.0)})
    assert peak == ["margin_peak_after_yoy_30"]
    # yoy max reached but momentum still rising -> no peak signal
    assert _active_keys({"margin_yoy_max_6m": _snap("margin_yoy_max_6m", 32.0),
                         "margin_mom_change": _snap("margin_mom_change", 2.0)}) == []
    assert _active_keys({"margin_yoy_max_6m": _snap("margin_yoy_max_6m", 29.9),
                         "margin_mom_change": _snap("margin_mom_change", -2.0)}) == []


def test_macro_and_credit_predicates() -> None:
    assert _active_keys({"claims_yoy": _snap("claims_yoy", 25.0)}) == ["claims_yoy_gt_20"]
    assert _active_keys({"claims_yoy": _snap("claims_yoy", 20.0)}) == []
    assert _active_keys({"nfci": _snap("nfci", 0.2)}) == ["nfci_above_0"]
    assert _active_keys({"nfci": _snap("nfci", 0.0)}) == []
    # HY compressed (<3.5) AND FCI easing (NFCI < 0) -> favorable-credit row
    assert _active_keys({"hy_oas": _snap("hy_oas", 3.2),
                         "nfci": _snap("nfci", -0.2)}) == ["hy_compressed_fci_easing"]
    # HY compressed but FCI tightening -> not the favorable row (nfci row fires)
    assert _active_keys({"hy_oas": _snap("hy_oas", 3.2),
                         "nfci": _snap("nfci", 0.3)}) == ["nfci_above_0"]
    # HY already wide -> row inactive
    assert _active_keys({"hy_oas": _snap("hy_oas", 3.8),
                         "nfci": _snap("nfci", -0.3)}) == []


def test_none_valued_snapshots_deactivate_predicates() -> None:
    none_snap = _snap("cape", 30.0)
    none_snap = SeriesSnapshot(**{**none_snap.__dict__, "value": None})
    assert _active_keys({"cape": none_snap}) == []
