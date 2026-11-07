from __future__ import annotations

import math

import pytest

from market_intel.domain.bands import BANDS, evaluate_band
from market_intel.domain.exceptions import GaugeComputeError
from market_intel.domain.models import COLOR_TOKEN_HEX


def _bands_of(table_key: str):
    return BANDS[table_key].bands


def test_all_27_band_tables_registered() -> None:
    assert len(BANDS) == 27
    for key, table in BANDS.items():
        bands = table.bands
        assert sum(1 for b in bands if b.lower is None) == 1
        assert sum(1 for b in bands if b.upper is None) == 1
        # the intervals tile the real line: any finite boundary is a band edge,
        # and every midpoint between consecutive edges falls inside SOME band
        edges = sorted({x for b in bands for x in (b.lower, b.upper) if x is not None})
        assert edges
        probes = [-1e12, 1e12]
        probes += edges + [(a + b) / 2.0 for a, b in zip(edges, edges[1:])]
        for x in probes:
            evaluate_band(key, x)  # must not raise GaugeComputeError


def test_boundary_values_belong_to_higher_band_everywhere() -> None:
    eps = 1e-9
    for key, table in BANDS.items():
        for band in table.bands:
            if band.lower is None:
                continue
            # exactly on the boundary -> this band (it opens here)
            assert evaluate_band(key, band.lower) == band, (key, band.lower)
            # a hair below -> a different (previous) band
            below = evaluate_band(key, band.lower - eps)
            assert below != band, (key, band.lower)
        first_open = next(b for b in table.bands if b.lower is None)
        last_open = next(b for b in table.bands if b.upper is None)
        assert evaluate_band(key, -1e12) == first_open, key
        assert evaluate_band(key, 1e12) == last_open, key


def test_valuation_scale_examples() -> None:
    assert evaluate_band("cape", 15.0).label_uk == "Дешево"
    assert evaluate_band("cape", 20.0).label_uk == "Справедливо"  # boundary -> higher
    assert evaluate_band("cape", 26.0).label_uk == "Дорого"
    assert evaluate_band("cape", 38.79).label_uk == "Дуже дорого"
    assert evaluate_band("forward_pe", 18.7).label_uk == "Справедливо"
    assert evaluate_band("buffett_z1", 250.0).label_uk == "Дуже дорого"  # boundary
    assert evaluate_band("buffett_z1", 249.9).label_uk == "Дорого"


def test_descending_tables_rank_low_values_good() -> None:
    assert evaluate_band("ecy", 0.4).label_uk == "Дуже дорого"
    assert evaluate_band("ecy", 0.6).label_uk == "Дорого"
    assert evaluate_band("ecy", 1.5).label_uk == "Справедливо"  # boundary -> higher
    assert evaluate_band("ecy", 3.5).label_uk == "Дешево"
    assert evaluate_band("erp", 4.41).label_uk == "Справедливо"
    assert evaluate_band("vix", 11.9).label_uk == "Ейфорія"
    assert evaluate_band("vix", 12.0).label_uk == "Жадібність"  # boundary -> higher
    assert evaluate_band("vix", 16.61).label_uk == "Нейтрально"
    assert evaluate_band("vix", 45.0).label_uk == "Паніка"
    assert evaluate_band("netliq_yoy", 0.0).label_uk == "Розширення"  # boundary
    assert evaluate_band("netliq_yoy", -6.0).label_uk == "Сильний відтік"


def test_drawdown_scale_descending_intervals() -> None:
    assert evaluate_band("drawdown_2y", 0.0).label_uk == "Сьогодні пік"
    assert evaluate_band("drawdown_2y", -4.99).label_uk == "Дрібна"
    assert evaluate_band("drawdown_2y", -5.0).label_uk == "Дрібна"
    assert evaluate_band("drawdown_2y", -5.01).label_uk == "Корекція"
    assert evaluate_band("drawdown_2y", -10.0).label_uk == "Корекція"
    assert evaluate_band("drawdown_2y", -10.5).label_uk == "Глибока"
    assert evaluate_band("drawdown_2y", -20.0).label_uk == "Глибока"
    assert evaluate_band("drawdown_2y", -20.01).label_uk == "Важка"


def test_sentiment_technique_macro_examples() -> None:
    assert evaluate_band("fg", 19.85).label_uk == "Паніка"
    assert evaluate_band("fg", 76.0).label_uk == "Ейфорія"
    assert evaluate_band("vix_vix3m", 0.6644).label_uk == "Спокійно"
    assert evaluate_band("vix_vix3m", 1.0).label_uk == "Напруження"  # boundary
    assert evaluate_band("weekly_rsi", 50.0).label_uk == "Нейтрально"
    assert evaluate_band("weekly_rsi", 29.0).label_uk == "Перепродано"
    assert evaluate_band("pct_200dma", 0.0).label_uk == "Слабко"
    assert evaluate_band("nfci", -0.459).label_uk == "М'які умови"
    assert evaluate_band("curve", 0.67).label_uk == "Плоска"
    assert evaluate_band("claims_yoy", 0.0).label_uk == "Стабільно"
    assert evaluate_band("real_10y", 2.64).label_uk == "Рестриктивно"
    assert evaluate_band("margin_yoy", 26.82).label_uk == "Підвищено"
    assert evaluate_band("fed_funds", 3.875).label_uk == "Нейтрально"


def test_credit_scale_examples() -> None:
    assert evaluate_band("hy_oas", 3.32).label_uk == "Помірний"
    assert evaluate_band("hy_oas", 4.5).label_uk == "Підвищений"  # boundary
    assert evaluate_band("hy_change_6m", 24.999).label_uk == "Стабільно"
    assert evaluate_band("hy_change_6m", 25.0).label_uk == "Увага"
    assert evaluate_band("hy_change_6m", 200.0).label_uk == "Стрес"  # boundary -> higher
    assert evaluate_band("ig_oas", 0.91).label_uk == "Низький ризик"
    assert evaluate_band("sofr_iorb", 4.0).label_uk == "Спокійно"


def test_composite_scales_map_state_tokens_in_order() -> None:
    assert evaluate_band("composite_valuation", 5.0).color_token == "state1"
    assert evaluate_band("composite_valuation", 73.41).label_uk == "Дорого"
    assert evaluate_band("composite_valuation", 73.41).color_token == "state3"
    assert evaluate_band("composite_sentiment", 62.586).label_uk == "Обережно"
    assert evaluate_band("composite_technique", 50.0).label_uk == "Нейтрально"
    assert evaluate_band("composite_technique", 50.0).color_token == "state2"
    assert evaluate_band("composite_technique", 20.0).color_token == "state1"
    assert evaluate_band("composite_technique", 90.0).color_token == "state3"
    assert evaluate_band("composite_macro", 50.0).label_uk == "Нейтрально"
    assert evaluate_band("composite_credit", 50.02).label_uk == "Помірний"
    # chips resolve to concrete hex colors from the contract palette
    assert COLOR_TOKEN_HEX["state1"] == "#7CB342"
    assert COLOR_TOKEN_HEX["state5"] == "#E57373"
    band = evaluate_band("composite_valuation", 5.0)
    assert COLOR_TOKEN_HEX[band.color_token] == "#7CB342"


def test_evaluate_band_rejects_unknown_key_and_nonfinite_values() -> None:
    with pytest.raises(GaugeComputeError):
        evaluate_band("no_such_table", 1.0)
    with pytest.raises(GaugeComputeError):
        evaluate_band("cape", float("nan"))
    with pytest.raises(GaugeComputeError):
        evaluate_band("cape", float("inf"))
    with pytest.raises(GaugeComputeError):
        evaluate_band("cape", float("-inf"))


def test_direction_metadata_is_declared() -> None:
    ascending = {"cape", "forward_pe", "buffett_z1", "fed_funds", "fg", "vix_vix3m",
                 "margin_yoy", "weekly_rsi", "pct_200dma", "nfci", "curve",
                 "claims_yoy", "real_10y", "hy_oas", "hy_change_6m", "ig_oas",
                 "sofr_iorb"}
    descending = {"ecy", "erp", "vix", "drawdown_2y", "netliq_yoy"}
    for key in BANDS:
        if key in ascending or key.startswith("composite_"):
            assert BANDS[key].direction == "ascending", key
        elif key in descending:
            assert BANDS[key].direction == "descending", key
        else:
            raise AssertionError(f"table {key} not classified in the test")
    # sanity: no NaN snuck into boundaries
    for table in BANDS.values():
        for band in table.bands:
            for edge in (band.lower, band.upper):
                assert edge is None or math.isfinite(edge)
