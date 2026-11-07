"""Forward-return mapping rows (report sec. 5.1) and live activation.

Cells are transcribed verbatim from the research report (sec. 5.1; the
orchestrator's per-row cell lists are the binding compression). Missing
report cells carry '-', cells the report marks TBC carry 'TBC'; no numbers
are invented. evaluate_forward_rows only flips active_now per live
predicates from the series snapshots.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from market_intel.domain.models import ForwardReturnRow, ModuleGauge, SeriesSnapshot
from market_intel.domain.thresholds import (
    CLAIMS_YOY_ALARM,
    MARGIN_YOY_EUPHORIA,
    RSI_OVERSOLD,
    VIX_PANIC,
)

_HYPHEN = "-"
_CAPE_NEAR_16_LOW = 15.0
_CAPE_NEAR_16_HIGH = 18.0
_CAPE_GT = 25.0
_FG_EXTREME_FEAR = 20.0
_CURVE_INVERTED = 0.0
_CURVE_NORMAL_LOW = 1.0
_CURVE_NORMAL_HIGH = 2.0
_HY_COMPRESSED = 3.5

FORWARD_ROWS: tuple[ForwardReturnRow, ...] = (
    ForwardReturnRow(
        rule_key="cape_gt_25",
        condition_uk="CAPE > 25",
        horizon_uk="10 років реальних",
        result_uk="~0,5%/р. середнє",
        uncertainty_uk="розкид -6,1..+8,3%/р.",
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="Asness-банди (через Swedroe); Vanguard R² 0,43",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="cape_near_16",
        condition_uk="CAPE ~16",
        horizon_uk="10 років реальних",
        result_uk="~10%/р.",
        uncertainty_uk="TBC",
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="Research Affiliates 2018",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="curve_inverted",
        condition_uk="Крива 3M10Y < 0",
        horizon_uk="12 міс.",
        result_uk="~+2,5% ср.",
        uncertainty_uk="висока варіативність",
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="Kucer et al. 2024 (1988-2023)",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="curve_inverted_rising",
        condition_uk="Крива <0 і росте",
        horizon_uk="12 міс.",
        result_uk="~-12,9% ср. (гірший стан)",
        uncertainty_uk=_HYPHEN,
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="Kucer et al. 2024",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="post_inversion_resteepening",
        condition_uk="Після інверсії знову крутість",
        horizon_uk="18 міс.",
        result_uk="~-7% ср., 3/10 позитивних",
        uncertainty_uk="+38% (1998), +22% (2006) фальстарти",
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="Saxo 2019",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="curve_normal",
        condition_uk="Крива нормальна (1-2%)",
        horizon_uk="12 міс.",
        result_uk="~+10-12% ср.",
        uncertainty_uk=_HYPHEN,
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="Kucer et al. 2024",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="vix_close_above_40",
        condition_uk="VIX закриття >40",
        horizon_uk="1 міс. 2008: далі -25..-30%; 12 міс.",
        result_uk="~+30-33% ср., >90% винів",
        uncertainty_uk="~10% 12-міс. негативних; 2008 контр-приклад",
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="Wells Fargo/CNBC 2025; Cboe; Hartford",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="weekly_rsi_below_30",
        condition_uk="Тижневий RSI(14) <30",
        horizon_uk="1 міс.",
        result_uk="мед. +8,6%, 79% винів",
        uncertainty_uk="лип.2020 -20,6%",
        max_dd_uk="12 міс. ~+24%, 90% позитивних",
        n_uk="n=46 тижнів/50 р.",
        evidence_level="ESTABLISHED",
        citation_uk="InvestorPlace 2022; SteadyTape",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="pct_below_50dma_lt_25",
        condition_uk="%>50DMA <25",
        horizon_uk="1 міс., 6 міс., 12 міс.",
        result_uk="+1,8% ср. (1 міс.); +10,6% ср., 10/10 (6 міс.); +15,8% ср., >=80% винів (12 міс.)",
        uncertainty_uk="TBC (3 міс.); not established (36-120 міс.)",
        max_dd_uk="сер. max DD у році ~8%",
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="POEMS",
        active_now=False,
        evaluated_in_mvp=False,
    ),
    ForwardReturnRow(
        rule_key="spx_3sd_below_200dma",
        condition_uk="SPX >=3σ нижче 200DMA",
        horizon_uk="12 міс.",
        result_uk="мед. +10%, 80% винів",
        uncertainty_uk="92% ex-2008",
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="Knowledge Leaders 2019",
        active_now=False,
        evaluated_in_mvp=False,
    ),
    ForwardReturnRow(
        rule_key="zweig_thrust",
        condition_uk="Zweig breadth thrust",
        horizon_uk="12 міс.",
        result_uk="мед. +22,9%, 18/18 позитивних",
        uncertainty_uk=_HYPHEN,
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="SentimenTrader; Detrick",
        active_now=False,
        evaluated_in_mvp=False,
    ),
    ForwardReturnRow(
        rule_key="fg_below_20",
        condition_uk="F&G <20",
        horizon_uk="12 міс.",
        result_uk="майже завжди позитивно",
        uncertainty_uk="~10-15% негативних",
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="TENTATIVE",
        citation_uk="Code Meets Capital 2011-2025",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="margin_peak_after_yoy_30",
        condition_uk="Пік маржі після YoY>+30%",
        horizon_uk="12 міс.",
        result_uk="багатократний bear follows",
        uncertainty_uk="2000-02 -49%; 2007-09 -57%; 2021-22 -25%",
        max_dd_uk=_HYPHEN,
        n_uk="n=3-4",
        evidence_level="ESTABLISHED",
        citation_uk="dshort/Advisor Perspectives",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="claims_yoy_gt_20",
        condition_uk="Claims YoY >+20%",
        horizon_uk=_HYPHEN,
        result_uk="рецесія йде; домінує DD-ризик",
        uncertainty_uk="LPL 2015-16 хибний",
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="Potomac 2026; RSM 2023",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="nfci_above_0",
        condition_uk="NFCI > 0",
        horizon_uk="хвіст",
        result_uk="лівий хвіст розширений",
        uncertainty_uk="середнє ненадійне",
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="Adrian et al. 2019; CXO 2020",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="hy_compressed_fci_easing",
        condition_uk="HY стиснутий + FCI easing",
        horizon_uk="6-12 міс.",
        result_uk="сприятливо",
        uncertainty_uk="1998/2007 епізоди платять 6-12 міс., потім вибухають",
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="TENTATIVE",
        citation_uk="BBVA 2025; Fama-French 1989",
        active_now=False,
    ),
    ForwardReturnRow(
        rule_key="aaii_bulls_minus_2sd",
        condition_uk="AAII bulls -2SD",
        horizon_uk="6 міс.",
        result_uk="~+14% ср., 100%",
        uncertainty_uk="змішано поза екстремумами (2003-04, 2007-09)",
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="AAII Journal 2013",
        active_now=False,
        evaluated_in_mvp=False,
    ),
    ForwardReturnRow(
        rule_key="lowry_oversold_reversal",
        condition_uk="Реверсія з oversold-ширини",
        horizon_uk="12 міс. і 24 міс.",
        result_uk="+22,6% ср., без негативів з 1989 (12 міс.); +38,1% ср. (24 міс.)",
        uncertainty_uk=_HYPHEN,
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="ESTABLISHED",
        citation_uk="Lowry (через ProActive)",
        active_now=False,
        evaluated_in_mvp=False,
    ),
    ForwardReturnRow(
        rule_key="ndx_any",
        condition_uk="Nasdaq-100: будь-які умови",
        horizon_uk="все",
        result_uk="NOT ESTABLISHED",
        uncertainty_uk="верифікованих мапінгів немає",
        max_dd_uk=_HYPHEN,
        n_uk=_HYPHEN,
        evidence_level="NOT ESTABLISHED",
        citation_uk="перевірка дослідження 2026-11-06",
        active_now=False,
        evaluated_in_mvp=False,
    ),
)

# Diagnostic snapshot keys consumed by predicates (built by compute_dashboard).
_CURVE_SLOPE_90D_KEY = "curve_slope_90d"
_CURVE_MIN_540D_KEY = "curve_min_540d"
_MARGIN_YOY_MAX_6M_KEY = "margin_yoy_max_6m"
_MARGIN_MOM_CHANGE_KEY = "margin_mom_change"


def evaluate_forward_rows(
    gauges: Mapping[str, ModuleGauge],
    series: Mapping[str, SeriesSnapshot],
) -> tuple[ForwardReturnRow, ...]:
    """Rebuild FORWARD_ROWS with active_now set from the live series values.

    A missing or None-valued series deactivates its predicate (honest blank,
    not a guess). Rows outside MVP evaluation never activate. ``gauges`` is
    accepted for signature stability; current predicates use series values
    only.
    """
    rows: list[ForwardReturnRow] = []
    for row in FORWARD_ROWS:
        active = row.evaluated_in_mvp and _PREDICATES[row.rule_key](series)
        rows.append(replace(row, active_now=active))
    return tuple(rows)


def _value(series: Mapping[str, SeriesSnapshot], key: str) -> float | None:
    snapshot = series.get(key)
    if snapshot is None:
        return None
    return snapshot.value


def _cape(series: Mapping[str, SeriesSnapshot]) -> bool:
    cape = _value(series, "cape")
    return cape is not None and cape > _CAPE_GT


def _cape_near_16(series: Mapping[str, SeriesSnapshot]) -> bool:
    cape = _value(series, "cape")
    return cape is not None and _CAPE_NEAR_16_LOW <= cape <= _CAPE_NEAR_16_HIGH


def _curve(series: Mapping[str, SeriesSnapshot]) -> bool:
    curve = _value(series, "curve_3m")
    return curve is not None and curve < _CURVE_INVERTED


def _curve_rising(series: Mapping[str, SeriesSnapshot]) -> bool:
    curve = _value(series, "curve_3m")
    slope = _value(series, _CURVE_SLOPE_90D_KEY)
    return curve is not None and curve < _CURVE_INVERTED and slope is not None and slope > 0.0


def _post_resteepening(series: Mapping[str, SeriesSnapshot]) -> bool:
    curve = _value(series, "curve_3m")
    minimum = _value(series, _CURVE_MIN_540D_KEY)
    return curve is not None and curve > _CURVE_INVERTED and minimum is not None and minimum < 0.0


def _curve_normal(series: Mapping[str, SeriesSnapshot]) -> bool:
    curve = _value(series, "curve_3m")
    return curve is not None and _CURVE_NORMAL_LOW <= curve <= _CURVE_NORMAL_HIGH


def _vix(series: Mapping[str, SeriesSnapshot]) -> bool:
    vix = _value(series, "vix")
    return vix is not None and vix > VIX_PANIC


def _weekly_rsi(series: Mapping[str, SeriesSnapshot]) -> bool:
    rsi_value = _value(series, "weekly_rsi")
    return rsi_value is not None and rsi_value < RSI_OVERSOLD


def _fg_below(series: Mapping[str, SeriesSnapshot]) -> bool:
    fg = _value(series, "fg")
    return fg is not None and fg < _FG_EXTREME_FEAR


def _margin_peak(series: Mapping[str, SeriesSnapshot]) -> bool:
    yoy_max = _value(series, _MARGIN_YOY_MAX_6M_KEY)
    mom = _value(series, _MARGIN_MOM_CHANGE_KEY)
    return (
        yoy_max is not None
        and yoy_max >= MARGIN_YOY_EUPHORIA
        and mom is not None
        and mom < 0.0
    )


def _claims_yoy(series: Mapping[str, SeriesSnapshot]) -> bool:
    claims = _value(series, "claims_yoy")
    return claims is not None and claims > CLAIMS_YOY_ALARM


def _nfci_above(series: Mapping[str, SeriesSnapshot]) -> bool:
    nfci = _value(series, "nfci")
    return nfci is not None and nfci > 0.0


def _hy_compressed_fci_easing(series: Mapping[str, SeriesSnapshot]) -> bool:
    hy = _value(series, "hy_oas")
    nfci = _value(series, "nfci")
    return hy is not None and hy < _HY_COMPRESSED and nfci is not None and nfci < 0.0


_PREDICATES = {
    "cape_gt_25": _cape,
    "cape_near_16": _cape_near_16,
    "curve_inverted": _curve,
    "curve_inverted_rising": _curve_rising,
    "post_inversion_resteepening": _post_resteepening,
    "curve_normal": _curve_normal,
    "vix_close_above_40": _vix,
    "weekly_rsi_below_30": _weekly_rsi,
    "fg_below_20": _fg_below,
    "margin_peak_after_yoy_30": _margin_peak,
    "claims_yoy_gt_20": _claims_yoy,
    "nfci_above_0": _nfci_above,
    "hy_compressed_fci_easing": _hy_compressed_fci_easing,
}
