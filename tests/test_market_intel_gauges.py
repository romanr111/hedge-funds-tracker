from __future__ import annotations

from datetime import date

import pytest

from market_intel.domain.exceptions import GaugeComputeError
from market_intel.domain.gauges import (
    compute_module_gauge,
    module_score,
    renormalize_weights,
)
from market_intel.domain.models import ModuleGauge, SeriesSnapshot, SeriesSpec, MODULE_TITLES_UK
from market_intel.domain.verdicts import module_forward_context_uk

_OBS = date(2026, 11, 5)


def _snap(key, value, *, percentile=None, is_stale=False, stale_reason=None,
          unit="", obs_date=_OBS, title=None) -> SeriesSnapshot:
    return SeriesSnapshot(
        key=key,
        title_uk=title or key,
        module_key=None,
        value=value,
        obs_date=obs_date,
        percentile=percentile,
        band=None,
        is_stale=is_stale,
        stale_reason_uk=stale_reason,
        meaning_uk="",
        source_uk="test",
        unit_uk=unit,
    )


def _spec(key, module, weight, *, invert=False) -> SeriesSpec:
    return SeriesSpec(
        key=key,
        title_uk=key,
        source="test",
        freq="daily",
        staleness_days=3,
        band_ref=None,
        weight=weight,
        module_key=module,
        invert=invert,
        percentile_window_days=400,
        params={},
    )


def test_renormalize_weights_sums_to_one_and_drops() -> None:
    weights = {"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0}
    assert renormalize_weights(weights, ["b"]) == {"a": pytest.approx(1 / 3),
                                                   "c": pytest.approx(1 / 3),
                                                   "d": pytest.approx(1 / 3)}
    # uneven weights renormalize proportionally
    assert renormalize_weights({"a": 0.25, "b": 0.25}, []) == {"a": 0.5, "b": 0.5}
    # unknown drop keys are ignored
    kept = renormalize_weights({"a": 2.0, "b": 2.0}, ["nope"])
    assert kept == {"a": 0.5, "b": 0.5}
    with pytest.raises(GaugeComputeError):
        renormalize_weights({"a": 1.0}, ["a"])
    with pytest.raises(GaugeComputeError):
        renormalize_weights({"a": 0.0}, [])


def test_module_score_is_weighted_hand_computed() -> None:
    # sentiment weights .25/.20/.10/.25 with percentiles 75.125/62.625/62.625/50
    specs = {k: _spec(k, "sentiment", w) for k, w in (("fg", 0.25), ("vix", 0.20), ("vix_vix3m", 0.10), ("margin_yoy", 0.25))}
    members = [
        _snap("fg", 19.85, percentile=75.125),
        _snap("vix", 16.61, percentile=62.625),
        _snap("vix_vix3m", 0.6644, percentile=62.625),
        _snap("margin_yoy", 26.82, percentile=50.0),
    ]
    assert module_score(members, specs) == pytest.approx(62.5859375)


def test_module_score_inverts_low_risky_series_once() -> None:
    # ERP: risky when LOW, pct 50.125 -> risk 49.875; single member -> mean 49.875
    specs = {"erp": _spec("erp", "valuation", 1.0, invert=True)}
    members = [_snap("erp", 4.41, percentile=50.125)]
    assert module_score(members, specs) == pytest.approx(49.875)
    # full inversion of a 0 percentile caps at 99.99
    members = [_snap("erp", 9.9, percentile=0.0)]
    assert module_score(members, specs) == pytest.approx(99.99)


def test_module_score_skips_weightless_or_thin_members() -> None:
    specs = {"a": _spec("a", "valuation", 1.0), "b": _spec("b", "valuation", None), "c": _spec("c", "valuation", 1.0)}
    members = [
        _snap("a", 1.0, percentile=80.0),
        _snap("b", 2.0, percentile=10.0),  # no weight
        _snap("c", 3.0, percentile=None),  # thin window
    ]
    assert module_score(members, specs) == pytest.approx(80.0)
    # nothing contributes -> None
    assert module_score([_snap("b", 2.0)], specs) is None
    assert module_score([], specs) is None
    assert module_score([_snap("a", 1.0, percentile=50.0)], {"d": _spec("d", "valuation", 1.0)}) is None


def test_compute_module_gauge_valuation_hand_computed_renormalization() -> None:
    specs = {
        "cape": _spec("cape", "valuation", 1.0),
        "forward_pe": _spec("forward_pe", "valuation", 1.0),
        "buffett_z1": _spec("buffett_z1", "valuation", 1.0),
        "erp": _spec("erp", "valuation", 1.0, invert=True),
    }
    live = [
        _snap("cape", 38.79, percentile=99.875),
        _snap("forward_pe", 18.7, percentile=45.125),
        _snap("buffett_z1", 250.0, percentile=98.78048780487805),
        _snap("erp", 4.41, percentile=50.125),  # invert -> risk 49.875
    ]
    gauge = compute_module_gauge("valuation", live, specs)
    assert isinstance(gauge, ModuleGauge)
    assert gauge.score == pytest.approx(73.41387195121953)
    assert gauge.chip_label_uk == "Дорого"  # composite band 60..85
    assert gauge.state_key == "composite_valuation3"
    assert gauge.members_used == 4 and gauge.members_total == 4
    assert gauge.dropped_uk == ()

    stale = [live[0], live[2], live[3]]
    gauge2 = compute_module_gauge(
        "valuation",
        stale + [_snap("forward_pe", 18.7, percentile=45.125,
                       is_stale=True, stale_reason="дані від 2026-10-01, застарілі понад 3 дні")],
        specs,
    )
    # the stale member's weight (1.0) renormalizes over the survivors
    assert gauge2.score == pytest.approx((99.875 + 98.78048780487805 + 49.875) / 3)
    assert gauge2.members_used == 3 and gauge2.members_total == 4
    assert gauge2.dropped_uk == ("forward_pe: дані від 2026-10-01, застарілі понад 3 дні",)
    assert gauge2.verdict_uk.endswith("Система не прогнозує напрям: це історичні асоціації, а не таймінг.")


def test_compute_module_gauge_drops_with_reason_notes() -> None:
    specs = {
        "a": _spec("a", "credit", 1.0),
        "b": _spec("b", "credit", None),
        "c": _spec("c", "credit", 1.0),
    }
    members = [
        _snap("a", 1.0, percentile=40.0),
        _snap("b", 2.0),  # weight None
        _snap("c", 3.0),  # thin percentile window
        _snap("ghost", 9.0),  # spec missing entirely
    ]
    gauge = compute_module_gauge("credit", members, specs)
    assert gauge.members_total == 4
    assert gauge.members_used == 1
    assert gauge.dropped_uk == (
        "b: виключений з композита: вага не задана",
        "c: перцентиль недоступний: замале вікно історії",
        "ghost: немає специфікації серії",
    )
    assert gauge.score == pytest.approx(40.0)


def test_compute_module_gauge_all_stale_degrades_to_unavailable() -> None:
    specs = {"a": _spec("a", "macro", 1.0), "b": _spec("b", "macro", 1.0)}
    members = [
        _snap("a", 1.0, is_stale=True, stale_reason="дані від 2026-10-01, застарілі понад 62 дні"),
        _snap("b", 2.0, is_stale=True, stale_reason="дані від 2026-10-01, застарілі понад 62 дні"),
    ]
    gauge = compute_module_gauge("macro", members, specs)
    assert gauge.state_key == "stale"
    assert gauge.chip_label_uk == "Недоступно"
    assert gauge.chip_color == "#9E9E9E"
    assert gauge.score is None
    assert gauge.members_used == 0
    assert "недоступний" in gauge.verdict_uk
    assert MODULE_TITLES_UK["macro"] in gauge.title_uk


def test_compute_module_gauge_facts_are_dated_and_capped() -> None:
    specs = {k: _spec(k, "valuation", 1.0) for k in ("a", "b", "c", "d", "e")}
    members = [
        _snap(k, 10.0 + i, percentile=30.0 + i, unit="%", title=f"Серія {k}")
        for i, k in enumerate(("a", "b", "c", "d", "e"))
    ]
    gauge = compute_module_gauge("valuation", members, specs)
    assert len(gauge.facts_uk) == 4  # generic facts cap at 4 per module
    assert gauge.facts_uk[0] == "Серія a: 10,00% (2026-11-05)"
    assert gauge.context_forward_uk == module_forward_context_uk("valuation")
    assert gauge.context_forward_uk.endswith(".")
