"""Band scales: exact thresholds and Ukrainian labels (report sec. 4).

Every table splits the real line into adjacent half-open intervals; a value
exactly on a boundary belongs to the HIGHER band of the pair. Ascending tables
read low -> good-to-bad in table order; descending tables reverse (risk = LOW
value there), so their bands are stored in the same "state1..stateN" order as
their good -> bad mapping.

Colors: state1 (green, good for the user) -> state5 (red, bad); for composite
tables the token is assigned per band order so green always maps to the band
that is good for the user, per the contract color note.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Final, Mapping

from market_intel.domain.exceptions import GaugeComputeError
from market_intel.domain.models import BandPoint

_T1, _T2, _T3, _T4, _T5 = "state1", "state2", "state3", "state4", "state5"


@dataclass(frozen=True)
class BandTable:
    """One value -> band lookup table.

    direction: "ascending" (risk grows with value) or "descending" (risk grows
    as value falls). Boundaries ascend in table order for both directions; a
    descending table simply maps its FIRST band (lowest values) to the best
    state.
    """

    key: str
    direction: str  # "ascending" | "descending"
    bands: tuple[BandPoint, ...]


def _pt(key: str, label_uk: str, token: str, lower: float | None, upper: float | None) -> BandPoint:
    return BandPoint(key=key, label_uk=label_uk, color_token=token, lower=lower, upper=upper)


# --- valuation / macro-liquid input scales --------------------------------
CAPE_BANDS: Final[BandTable] = BandTable(
    key="cape",
    direction="ascending",
    bands=(
        _pt("cape1", "Дешево", _T1, None, 20.0),
        _pt("cape2", "Справедливо", _T2, 20.0, 26.0),
        _pt("cape3", "Дорого", _T3, 26.0, 32.0),
        _pt("cape4", "Дуже дорого", _T4, 32.0, None),
    ),
)

# descending: low ECY = expensive (risk = LOW raw value)
ECY_BANDS: Final[BandTable] = BandTable(
    key="ecy",
    direction="descending",
    bands=(
        _pt("ecy1", "Дуже дорого", _T4, None, 0.5),
        _pt("ecy2", "Дорого", _T3, 0.5, 1.5),
        _pt("ecy3", "Справедливо", _T2, 1.5, 3.0),
        _pt("ecy4", "Дешево", _T1, 3.0, None),
    ),
)

FORWARD_PE_BANDS: Final[BandTable] = BandTable(
    key="forward_pe",
    direction="ascending",
    bands=(
        _pt("forward_pe1", "Дешево", _T1, None, 16.0),
        _pt("forward_pe2", "Справедливо", _T2, 16.0, 19.5),
        _pt("forward_pe3", "Дорого", _T3, 19.5, 23.0),
        _pt("forward_pe4", "Дуже дорого", _T4, 23.0, None),
    ),
)

BUFFETT_Z1_BANDS: Final[BandTable] = BandTable(
    key="buffett_z1",
    direction="ascending",
    bands=(
        _pt("buffett_z11", "Дешево", _T1, None, 140.0),
        _pt("buffett_z12", "Справедливо", _T2, 140.0, 180.0),
        _pt("buffett_z13", "Дорого", _T3, 180.0, 250.0),
        _pt("buffett_z14", "Дуже дорого", _T4, 250.0, None),
    ),
)

# descending: low implied ERP = expensive equity risk pricing
ERP_BANDS: Final[BandTable] = BandTable(
    key="erp",
    direction="descending",
    bands=(
        _pt("erp1", "Дуже дорого", _T4, None, 2.5),
        _pt("erp2", "Дорого", _T3, 2.5, 3.5),
        _pt("erp3", "Справедливо", _T2, 3.5, 5.5),
        _pt("erp4", "Дешево", _T1, 5.5, None),
    ),
)

FED_FUNDS_BANDS: Final[BandTable] = BandTable(
    key="fed_funds",
    direction="ascending",
    bands=(
        _pt("fed_funds1", "М'які гроші", _T1, None, 2.5),
        _pt("fed_funds2", "Нейтрально", _T3, 2.5, 4.0),
        _pt("fed_funds3", "Рестриктивно", _T4, 4.0, None),
    ),
)

# --- sentiment -------------------------------------------------------------
FG_BANDS: Final[BandTable] = BandTable(
    key="fg",
    direction="ascending",
    bands=(
        _pt("fg1", "Паніка", _T5, None, 25.0),
        _pt("fg2", "Страх", _T4, 25.0, 45.0),
        _pt("fg3", "Нейтрально", _T3, 45.0, 55.0),
        _pt("fg4", "Жадібність", _T2, 55.0, 75.0),
        _pt("fg5", "Ейфорія", _T1, 75.0, None),
    ),
)

# descending: low VIX = complacency
VIX_BANDS: Final[BandTable] = BandTable(
    key="vix",
    direction="descending",
    bands=(
        _pt("vix1", "Ейфорія", _T1, None, 12.0),
        _pt("vix2", "Жадібність", _T2, 12.0, 15.0),
        _pt("vix3", "Нейтрально", _T3, 15.0, 25.0),
        _pt("vix4", "Страх", _T4, 25.0, 40.0),
        _pt("vix5", "Паніка", _T5, 40.0, None),
    ),
)

VIX_VIX3M_BANDS: Final[BandTable] = BandTable(
    key="vix_vix3m",
    direction="ascending",
    bands=(
        _pt("vix_vix3m1", "Спокійно", _T1, None, 0.85),
        _pt("vix_vix3m2", "Увага", _T3, 0.85, 1.0),
        _pt("vix_vix3m3", "Напруження", _T5, 1.0, None),
    ),
)

MARGIN_YOY_BANDS: Final[BandTable] = BandTable(
    key="margin_yoy",
    direction="ascending",
    bands=(
        _pt("margin_yoy1", "Нейтрально", _T3, None, 0.0),
        _pt("margin_yoy2", "Помірно", _T2, 0.0, 15.0),
        _pt("margin_yoy3", "Підвищено", _T4, 15.0, 30.0),
        _pt("margin_yoy4", "Ейфорія-позиціонування", _T5, 30.0, None),
    ),
)

# --- technique -------------------------------------------------------------
WEEKLY_RSI_BANDS: Final[BandTable] = BandTable(
    key="weekly_rsi",
    direction="ascending",
    bands=(
        _pt("weekly_rsi1", "Перепродано", _T5, None, 30.0),
        _pt("weekly_rsi2", "Нейтрально", _T3, 30.0, 70.0),
        _pt("weekly_rsi3", "Перекуплено", _T1, 70.0, None),
    ),
)

PCT_200DMA_BANDS: Final[BandTable] = BandTable(
    key="pct_200dma",
    direction="ascending",
    bands=(
        _pt("pct_200dma1", "Нижче середньої", _T5, None, 0.0),
        _pt("pct_200dma2", "Слабко", _T4, 0.0, 5.0),
        _pt("pct_200dma3", "Позитивно", _T3, 5.0, 15.0),
        _pt("pct_200dma4", "Розтягнуто вгору", _T1, 15.0, None),
    ),
)

# drawdown is <=0: 0 on the peak, more negative = deeper; [lower, upper) edges
DRAWDOWN_2Y_BANDS: Final[BandTable] = BandTable(
    key="drawdown_2y",
    direction="descending",
    bands=(
        _pt("drawdown_2y1", "Сьогодні пік", _T1, 0.0, None),
        _pt("drawdown_2y2", "Дрібна", _T2, -5.0, 0.0),
        _pt("drawdown_2y3", "Корекція", _T3, -10.0, -5.0),
        _pt("drawdown_2y4", "Глибока", _T4, -20.0, -10.0),
        _pt("drawdown_2y5", "Важка", _T5, None, -20.0),
    ),
)

# --- macro -----------------------------------------------------------------
NFCI_BANDS: Final[BandTable] = BandTable(
    key="nfci",
    direction="ascending",
    bands=(
        _pt("nfci1", "М'які умови", _T1, None, 0.0),
        _pt("nfci2", "Підтягування", _T3, 0.0, 0.5),
        _pt("nfci3", "Тісні умови", _T5, 0.5, None),
    ),
)

CURVE_BANDS: Final[BandTable] = BandTable(
    key="curve",
    direction="ascending",
    bands=(
        _pt("curve1", "Інверсія", _T5, None, 0.0),
        _pt("curve2", "Плоска", _T4, 0.0, 1.0),
        _pt("curve3", "Нормальна", _T3, 1.0, 3.0),
        _pt("curve4", "Крута", _T2, 3.0, None),
    ),
)

CLAIMS_YOY_BANDS: Final[BandTable] = BandTable(
    key="claims_yoy",
    direction="ascending",
    bands=(
        _pt("claims_yoy1", "Покращення", _T1, None, 0.0),
        _pt("claims_yoy2", "Стабільно", _T2, 0.0, 10.0),
        _pt("claims_yoy3", "Увага", _T4, 10.0, 20.0),
        _pt("claims_yoy4", "Тривога", _T5, 20.0, None),
    ),
)

REAL_10Y_BANDS: Final[BandTable] = BandTable(
    key="real_10y",
    direction="ascending",
    bands=(
        _pt("real_10y1", "М'яко", _T1, None, 1.0),
        _pt("real_10y2", "Нейтрально", _T3, 1.0, 2.0),
        _pt("real_10y3", "Рестриктивно", _T4, 2.0, 3.0),
        _pt("real_10y4", "Дуже рестриктивно", _T5, 3.0, None),
    ),
)

# descending: net liquidity expansion (>=0) is supportive
NETLIQ_YOY_BANDS: Final[BandTable] = BandTable(
    key="netliq_yoy",
    direction="descending",
    bands=(
        _pt("netliq_yoy1", "Розширення", _T1, 0.0, None),
        _pt("netliq_yoy2", "Нейтрально", _T3, -2.0, 0.0),
        _pt("netliq_yoy3", "Відтік", _T4, -5.0, -2.0),
        _pt("netliq_yoy4", "Сильний відтік", _T5, None, -5.0),
    ),
)

# --- credit ----------------------------------------------------------------
HY_OAS_BANDS: Final[BandTable] = BandTable(
    key="hy_oas",
    direction="ascending",
    bands=(
        _pt("hy_oas1", "Низький ризик", _T1, None, 3.0),
        _pt("hy_oas2", "Помірний", _T2, 3.0, 4.5),
        _pt("hy_oas3", "Підвищений", _T4, 4.5, 7.0),
        _pt("hy_oas4", "Високий", _T5, 7.0, None),
    ),
)

# basis points; <=25 stable (boundary at 25 belongs to the lower band per contract)
HY_CHANGE_6M_BANDS: Final[BandTable] = BandTable(
    key="hy_change_6m",
    direction="ascending",
    bands=(
        _pt("hy_change_6m1", "Стабільно", _T1, None, 25.0),
        _pt("hy_change_6m2", "Увага", _T3, 25.0, 100.0),
        _pt("hy_change_6m3", "Розширення", _T4, 100.0, 200.0),
        _pt("hy_change_6m4", "Стрес", _T5, 200.0, None),
    ),
)

IG_OAS_BANDS: Final[BandTable] = BandTable(
    key="ig_oas",
    direction="ascending",
    bands=(
        _pt("ig_oas1", "Низький ризик", _T1, None, 1.2),
        _pt("ig_oas2", "Помірний", _T2, 1.2, 2.0),
        _pt("ig_oas3", "Підвищений", _T4, 2.0, 3.0),
        _pt("ig_oas4", "Високий", _T5, 3.0, None),
    ),
)

# basis points
SOFR_IORB_BANDS: Final[BandTable] = BandTable(
    key="sofr_iorb",
    direction="ascending",
    bands=(
        _pt("sofr_iorb1", "Спокійно", _T1, None, 5.0),
        _pt("sofr_iorb2", "Увага", _T3, 5.0, 15.0),
        _pt("sofr_iorb3", "Напруження", _T4, 15.0, 30.0),
        _pt("sofr_iorb4", "Серйозне напруження", _T5, 30.0, None),
    ),
)

# --- composite scales (risk percentile 0..100) -----------------------------
# state tokens assigned per band order: green = good for the user.
COMPOSITE_VALUATION_BANDS: Final[BandTable] = BandTable(
    key="composite_valuation",
    direction="ascending",
    bands=(
        _pt("composite_valuation1", "Дешево", _T1, None, 25.0),
        _pt("composite_valuation2", "Справедливо", _T2, 25.0, 60.0),
        _pt("composite_valuation3", "Дорого", _T3, 60.0, 85.0),
        _pt("composite_valuation4", "Дуже дорого", _T4, 85.0, None),
    ),
)

COMPOSITE_SENTIMENT_BANDS: Final[BandTable] = BandTable(
    key="composite_sentiment",
    direction="ascending",
    bands=(
        _pt("composite_sentiment1", "Спокійно", _T1, None, 25.0),
        _pt("composite_sentiment2", "Нейтрально", _T2, 25.0, 50.0),
        _pt("composite_sentiment3", "Обережно", _T3, 50.0, 75.0),
        _pt("composite_sentiment4", "Ейфорія", _T4, 75.0, None),
    ),
)

COMPOSITE_TECHNIQUE_BANDS: Final[BandTable] = BandTable(
    key="composite_technique",
    direction="ascending",
    bands=(
        _pt("composite_technique1", "Перепродано", _T1, None, 33.0),
        _pt("composite_technique2", "Нейтрально", _T2, 33.0, 67.0),
        _pt("composite_technique3", "Перекуплено", _T3, 67.0, None),
    ),
)

COMPOSITE_MACRO_BANDS: Final[BandTable] = BandTable(
    key="composite_macro",
    direction="ascending",
    bands=(
        _pt("composite_macro1", "Сприятливо", _T1, None, 40.0),
        _pt("composite_macro2", "Нейтрально", _T2, 40.0, 60.0),
        _pt("composite_macro3", "Рестриктивно", _T3, 60.0, 80.0),
        _pt("composite_macro4", "Дуже рестриктивно", _T4, 80.0, None),
    ),
)

COMPOSITE_CREDIT_BANDS: Final[BandTable] = BandTable(
    key="composite_credit",
    direction="ascending",
    bands=(
        _pt("composite_credit1", "Низький ризик", _T1, None, 40.0),
        _pt("composite_credit2", "Помірний", _T2, 40.0, 70.0),
        _pt("composite_credit3", "Підвищений", _T3, 70.0, 90.0),
        _pt("composite_credit4", "Високий", _T4, 90.0, None),
    ),
)

BANDS: Final[Mapping[str, BandTable]] = {
    table.key: table
    for table in (
        CAPE_BANDS,
        ECY_BANDS,
        FORWARD_PE_BANDS,
        BUFFETT_Z1_BANDS,
        ERP_BANDS,
        FED_FUNDS_BANDS,
        FG_BANDS,
        VIX_BANDS,
        VIX_VIX3M_BANDS,
        MARGIN_YOY_BANDS,
        WEEKLY_RSI_BANDS,
        PCT_200DMA_BANDS,
        DRAWDOWN_2Y_BANDS,
        NFCI_BANDS,
        CURVE_BANDS,
        CLAIMS_YOY_BANDS,
        REAL_10Y_BANDS,
        NETLIQ_YOY_BANDS,
        HY_OAS_BANDS,
        HY_CHANGE_6M_BANDS,
        IG_OAS_BANDS,
        SOFR_IORB_BANDS,
        COMPOSITE_VALUATION_BANDS,
        COMPOSITE_SENTIMENT_BANDS,
        COMPOSITE_TECHNIQUE_BANDS,
        COMPOSITE_MACRO_BANDS,
        COMPOSITE_CREDIT_BANDS,
    )
}

def evaluate_band(table_key: str, value: float) -> BandPoint:
    """Look up the band for ``value`` in table ``table_key``.

    Raise GaugeComputeError on an unknown key, on NaN/inf values, or when no
    band covers the value (impossible for well-formed tables). Every interval
    is half-open [lower, upper): a value exactly on a boundary belongs to the
    HIGHER band (higher-valued side for descending tables too; the written
    "<=25"/">200" edge notes in hy_change_6m are treated as informal shorthand).
    """
    table = BANDS.get(table_key)
    if table is None:
        raise GaugeComputeError(f"Unknown band table: {table_key!r}")
    if value != value or value == float("inf") or value == float("-inf"):
        raise GaugeComputeError(f"Non-finite value {value!r} cannot be banded in {table_key!r}")
    for band in table.bands:
        below_lower = band.lower is not None and value < band.lower
        above_upper = band.upper is not None and value >= band.upper
        if not below_lower and not above_upper:
            return band
    raise GaugeComputeError(f"Value {value!r} not covered by band table {table_key!r}")
