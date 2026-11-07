"""Compute the full dashboard snapshot from the vintage store.

Orchestrates pure domain pieces: point-in-time reads (no peeking - history is
capped at ``as_of``), percentiles, derived series, module gauges, forward-row
activation and the system signal narrative. No I/O here beyond the injected
``VintageRepository`` port.

Semantic decisions implemented here (contract interpretation):
- Snapshots store RAW percentiles (no invert flip); the invert flip for risk
  direction is applied once, inside module_score (gauges.py).
- Derived series are keyed: ecy, vix_vix3m, claims_yoy, sofr_iorb,
  hy_change_6m, netliq_tbn, netliq_yoy, buffett_z1, weekly_rsi, pct_200dma,
  drawdown_2y, margin_yoy, fed_funds (input series, display-only).
- Diagnostic snapshots (curve_slope_90d, curve_min_540d, margin_yoy_max_6m,
  margin_mom_change, fg_min_28d) feed forward-row predicates only; they are
  not rendered among dashboard indicators.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Mapping, Sequence

from market_intel.application.ports.vintage_repository import VintageRepository
from market_intel.domain import calculations as calc
from market_intel.domain.bands import evaluate_band
from market_intel.domain.exceptions import GaugeComputeError
from market_intel.domain.forward_returns import evaluate_forward_rows
from market_intel.domain.gauges import compute_module_gauge
from market_intel.domain.models import (
    MODULE_KEYS,
    DashboardSnapshot,
    ModuleGauge,
    SeriesSpec,
    SeriesSnapshot,
)
from market_intel.domain.percentiles import percentile_from_history
from market_intel.domain.verdicts import format_value_uk, uk_days_word

_DAY = timedelta(days=1)
_PAIR_TOL_DAYS = 4  # calendar proxy for "2 trading days" (weekend gap tolerance)
_GA_PAIR_TOL_DAYS = 10  # quarter pair tolerance (Buffett)
_NETLIQ_PAIR_TOL_DAYS = 5
_HY_6M_DAYS = 182
_HY_6M_TOL_DAYS = 30
_CURVE_SLOPE_DAYS = 90
_CURVE_SLOPE_TOL_DAYS = 30
_CURVE_MIN_DAYS = 540
_MARGIN_MOM_TOL_DAYS = 45
_MARGIN_MAX_WINDOW_DAYS = 183
_FG_MIN_WINDOW_DAYS = 28
_FG_MIN_OBS_COUNT = 20
_BREADTH_DEFERRED_UK = "Ширина ринку (% акцій над 200DMA) відкладена - у MVP відсутня"
_THIN_WINDOW_WARNING = "перцентильне вікно замале (<30 значень у вікні)"
_UNIT_SANITY_RANGE = (50.0, 400.0)
_UNIT_SANITY_WARNING = "саніті юнітів невдалось"
_COMPUTED_FALLBACK_SOURCE = "розрахунок системи"
_FG_EUPHORIA_THRESHOLD = 80.0
_FG_CAPITULATION_THRESHOLD = 10.0
_VIX_CAPITULATION_THRESHOLD = 30.0
_VIX_VIX3M_CAPITULATION_THRESHOLD = 1.0
_RSI_OVERSOLD = 30.0
_MARGIN_YOY_EUPHORIA = 30.0
_CREDIT_GATE_HY = 4.5
_CREDIT_GATE_CLAIMS_YOY = 20.0
_CLAIMS_YOY_ALARM = _CREDIT_GATE_CLAIMS_YOY

_DEFAULT_MEANINGS_UK: Mapping[str, str] = {
    "cape": "Акції США коштують у десятки разів більше 10-річних середніх прибутків: за довгими мірилками це дорого.",
    "real_10y": "Доходність 10-річних інфляційно-захищених казначейок: висока реальна ставка тисне на дорогі ризик-активи.",
    "forward_pe": "Скільки років прибутків платять за акції за консенсус-прогнозом: 16-20 вважається чесною зоною.",
    "ncbeilq027s": "Ринкова вартість американських акцій у звіті ФРС Z.1 - частина індикатора Баффета.",
    "gdp": "Номінальний ВВП США - знаменник індикатора Баффета.",
    "erp": "Премія за ризик, яку ринок закладає в акції: мала премія означає платіж за очікуване зростання, не за ризик.",
    "fg": "Індекс страху і жадібності CNN: нижче 25 - паніка, вище 75 - ейфорія.",
    "vix": "Очікувана волатильність S&P 500 на місяць уперед: вище 30 - тривога, вище 40 - паніка.",
    "vix3m": "Волатильність на 3 місяці уперед - база порівняння для VIX/VIX3M.",
    "margin_debt": "Заборгованість клієнтів брокерів під заставу: швидке зростання означає ринок, розгінаний на плечі.",
    "spx": "Індекс S&P 500 - база для тижневого RSI, позиції проти 200DMA і просадок.",
    "icsa": "Первинні заявки на допомогу по безробіттю: стійке зростання попереджає про ринок праці.",
    "walcl": "Баланс ФРС - головний резерв долара на ринках.",
    "rrpontsyd": "Нічні зворотні репо ФРС: дренаж ліквідності з ринку.",
    "wregen": "Сальдо казначейського рахунку (TGA): дренаж ліквідності з ринку.",
    "hy_oas": "Наскільки дорожче високоризиковані облігації проти надійних: ширше - стрес.",
    "ig_oas": "Спред надійних корпоративних облігацій: зростає раніше HY - ранній дренаж довіри.",
    "nfci": "Індекс фінансових умов ФРС Чикаго: вище нуля - умови тісні, типово пізній цикл.",
    "curve_3m": "Різниця дохідностей 10-річних і 3-місячних казначейок: нижче нуля - інверсія, попереджала про рецесії.",
    "fed_funds": "Цільова ставка ФРС: вище 4% - рестриктивна зона для ризик-активів.",
    "sofr": "Нічна ставка грошового ринку SOFR: сплески означають фандинг-стрес.",
    "iorb": "Ставка ФРС за резервними балансами - база для SOFR-IORB.",
    # derived
    "ecy": "Дохідність CAPE мінус реальна 10-річна ставка: нижче нуля - акції дають менше за безризикову облігацію.",
    "vix_vix3m": "VIX проти 3-місячної волатильності: вище 1 - короткострокова паніка швидша за довгу, напруження.",
    "claims_yoy": "Первинні заявки рік-до-року: вище +20% історично супроводжував рецесії.",
    "sofr_iorb": "SOFR мінус ставка по резервам: вище 15 bp - напруження в грошовому ринку.",
    "hy_change_6m": "Зміна HY OAS за 6 місяців: вище +25 bp - кредит починає ширше.",
    "netliq_tbn": "Нетто-ліквідність (баланс ФРС мінус RRP і TGA): буфер грошей для ризик-активів.",
    "netliq_yoy": "Нетто-ліквідність рік-до-року: відтік висушує купівельну спроможність ринку.",
    "buffett_z1": "Вартість акцій проти ВВП (індикатор Баффета): вище 250% - екстремальна зона за мірилками Z.1.",
    "weekly_rsi": "Тижневий RSI(14) індексу S&P 500: нижче 30 - перепродано (історично хороші 12-місячні вікна).",
    "pct_200dma": "S&P 500 проти 200-денної середньої: нижче - слабкий тренд, сильно вище - розтягнутість угору.",
    "drawdown_2y": "Просадка від 2-річного максимуму: глибина поточного падіння.",
    "margin_yoy": "Маржинальна заборгованість рік-до-року: вище +30% - ейфорія-позиціонування.",
}

# Human units per rendered key (registry params have no 'unit' keys; keep the
# map here so the module tab shows plain units for a non-professional reader).
_UNITS_BY_KEY: Mapping[str, str] = {
    "cape": "%",
    "ecy": "pp",
    "forward_pe": "×",
    "buffett_z1": "% ВВП",
    "erp": "%",
    "fed_funds": "%",
    "vix": "",
    "vix_vix3m": "коеф.",
    "fg": "0-100",
    "margin_yoy": "%",
    "weekly_rsi": "",
    "pct_200dma": "%",
    "drawdown_2y": "%",
    "nfci": "",
    "curve": "%",
    "claims_yoy": "%",
    "real_10y": "%",
    "netliq_yoy": "%",
    "hy_oas": "pp",
    "hy_change_6m": "bp",
    "ig_oas": "pp",
    "sofr_iorb": "bp",
}

# Diagnostic keys -> (title_uk, meaning_uk, unit_uk). module_key=None, render skipped.
_DIAGNOSTIC_META: Mapping[str, tuple[str, str, str]] = {
    "curve_slope_90d": (
        "Крива 3M10Y: зміна за 90 днів",
        "Зростання інвертованої кривої - історично найгірший стан для 12-місячної доходності.",
        "pp",
    ),
    "curve_min_540d": (
        "Крива 3M10Y: мінімум за 540 днів",
        "Чи була інверсія за останні ~18 місяців - умова post-inversion re-steepening.",
        "pp",
    ),
    "margin_yoy_max_6m": (
        "Маржа р/р: максимум за 6 місяців",
        "Чи досягала маржинальна заборгованість +30% р/р за останні 6 місяців.",
        "%",
    ),
    "margin_mom_change": (
        "Маржа: зміна місяць-до-місяця",
        "Перший спад після ейфорійного зростання - класичний маркер піка.",
        "%",
    ),
    "fg_min_28d": (
        "F&G: мінімум за 28 днів",
        "Якщо всі дні останніх 4 тижнів вище 80 - умова стійкої ейфорії виконана.",
        "",
    ),
}

_MODULE_FACTS: Mapping[str, tuple[str, ...]] = {
    "valuation": ("cape", "buffett_z1", "forward_pe", "ecy"),
    "sentiment": ("fg", "margin_yoy", "vix", "vix_vix3m"),
    "technique": ("weekly_rsi", "pct_200dma", "drawdown_2y"),
    "macro": ("nfci", "curve_3m", "claims_yoy", "real_10y"),
    "credit": ("hy_oas", "hy_change_6m", "sofr_iorb", "ig_oas"),
}

_FACT_LABELS_UK: Mapping[str, str] = {
    "cape": "CAPE",
    "buffett_z1": "Баффет",
    "forward_pe": "Форвардний P/E",
    "ecy": "ECY",
    "fg": "F&G",
    "margin_yoy": "Маржа р/р",
    "vix": "VIX",
    "vix_vix3m": "VIX/VIX3M",
    "weekly_rsi": "Тижневий RSI",
    "pct_200dma": "% над 200DMA",
    "drawdown_2y": "Просадка від 2р-піку",
    "nfci": "NFCI",
    "curve_3m": "Крива 3M10Y",
    "claims_yoy": "Claims р/р",
    "real_10y": "Реальна 10р",
    "hy_oas": "HY OAS",
    "hy_change_6m": "HY 6м",
    "sofr_iorb": "SOFR-IORB",
    "ig_oas": "IG OAS",
}


# ---------------------------------------------------------------------------
# point helpers
# ---------------------------------------------------------------------------

def _dedup_first(observations: Sequence) -> list[tuple[date, float]]:
    """(obs_date, value) per date keeping the FIRST print, ascending by date.

    Point-in-time discipline: a revised print is a new row; series logic uses
    first prints, so duplicates collapse to the earliest stored row per date.
    """
    by_date: dict[date, float] = {}
    for obs in observations:
        if obs.obs_date not in by_date:
            by_date[obs.obs_date] = float(obs.value)
    return sorted(by_date.items())


def _nearest_point(
    points: list[tuple[date, float]],
    target: date,
    tol_days: int,
) -> tuple[date, float] | None:
    """Obs closest to ``target`` within ``tol_days``; earlier date wins ties."""
    if not points:
        return None
    dates = [d for d, _ in points]
    idx = bisect_left(dates, target)
    candidates: list[tuple[int, date, float]] = []
    for i in (idx - 1, idx):
        if 0 <= i < len(points):
            diff = abs((points[i][0] - target).days)
            if diff <= tol_days:
                candidates.append((diff, points[i][0], points[i][1]))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, d, v = candidates[0]
    return (d, v)


def _mean(points: Sequence[tuple[date, float]]) -> float | None:
    if not points:
        return None
    return sum(v for _, v in points) / len(points)


class _SortedSeries:
    """Pre-sorted dated series supporting fast windowed lookups via bisect."""

    def __init__(self, points: list[tuple[date, float]]) -> None:
        self.points = points
        self.dates = [d for d, _ in points]

    def _slice(self, start: date, end: date) -> list[tuple[date, float]]:
        lo = bisect_left(self.dates, start)
        hi = bisect_right(self.dates, end)
        return self.points[lo:hi]

    def rolling_mean(self, window_days: int, as_of: date) -> float | None:
        return _mean(self._slice(as_of - timedelta(days=window_days), as_of))

    def yoy(self, as_of: date) -> float | None:
        """% change vs latest point <= as_of against obs near as_of-365d.

        Mirrors domain year_over_year semantics on pre-sorted points.
        """
        hi = bisect_right(self.dates, as_of)
        if hi < 2:
            return None
        window = self.points[:hi]
        now_d, now_v = window[-1]
        target = now_d - timedelta(days=365)
        year_ago = _nearest_point(window, target, 35)
        if year_ago is None or abs((year_ago[0] - target).days) > 35:
            return None
        if year_ago[1] == 0.0:
            return None
        return (now_v / year_ago[1] - 1.0) * 100.0


def _pair_points(
    a: list[tuple[date, float]],
    b: list[tuple[date, float]],
    tol_days: int,
) -> list[tuple[date, float, float]]:
    """Anchor on each ``a`` date; pair with nearest ``b`` obs within tol."""
    b_series = _SortedSeries(b)
    out: list[tuple[date, float, float]] = []
    for d_a, v_a in a:
        pair_b = _nearest_point(b, d_a, tol_days)
        if pair_b is None:
            continue
        out.append((max(d_a, pair_b[0]), v_a, pair_b[1]))
    return out


def _weekly_closes(points: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Last observed close per ISO week, anchored at that close's date."""
    by_week: dict[tuple[int, int], tuple[date, float]] = {}
    for d, v in points:
        iso = d.isocalendar()
        by_week[(iso[0], iso[1])] = (d, v)
    return sorted(by_week.values())


def _weekly_rsi_series(closes: list[tuple[date, float]], period: int = 14) -> list[tuple[date, float]]:
    """Wilder RSI evaluated at every weekly close once the warmup completes."""
    if len(closes) < period + 1:
        return []
    changes = [closes[i][1] - closes[i - 1][1] for i in range(1, len(closes))]
    seed = changes[:period]
    avg_gain = sum(c for c in seed if c > 0) / period
    avg_loss = sum(-c for c in seed if c < 0) / period
    out: list[tuple[date, float]] = [_rsi_point(closes[period][0], avg_gain, avg_loss)]
    for offset, change in enumerate(changes[period:], start=period):
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out.append(_rsi_point(closes[offset + 1][0], avg_gain, avg_loss))
    return out


def _rsi_point(d: date, avg_gain: float, avg_loss: float) -> tuple[date, float]:
    if avg_loss == 0.0:
        value = 100.0 if avg_gain > 0.0 else 50.0
    else:
        rs = avg_gain / avg_loss
        value = 100.0 - 100.0 / (1.0 + rs)
    return (d, value)


def _pct_ma_series(points: list[tuple[date, float]], window: int) -> list[tuple[date, float]]:
    """(last/window-SMA - 1)*100 per day once the SMA window fills."""
    out: list[tuple[date, float]] = []
    running = deque[float]([])
    total = 0.0
    for d, v in points:
        running.append(v)
        total += v
        if len(running) > window:
            total -= running.popleft()
        if len(running) == window and total != 0.0:
            sma = total / window
            out.append((d, (v / sma - 1.0) * 100.0))
    return out


def _drawdown_series(points: list[tuple[date, float]], lookback_days: int) -> list[tuple[date, float]]:
    """(last/max-in-lookback - 1)*100 per day; sliding-window max via deque."""
    out: list[tuple[date, float]] = []
    window: deque[tuple[date, float]] = deque()
    maxima: deque[tuple[date, float]] = deque()
    for d, v in points:
        window.append((d, v))
        while maxima and maxima[-1][1] <= v:
            maxima.pop()
        maxima.append((d, v))
        cutoff = d - timedelta(days=lookback_days - 1)
        while window and window[0][0] < cutoff:
            drop_d, drop_v = window.popleft()
            if maxima and maxima[0] == (drop_d, drop_v):
                maxima.popleft()
        if window and maxima and maxima[0][1] != 0.0:
            out.append((d, (window[-1][1] / maxima[0][1] - 1.0) * 100.0))
    return out


# ---------------------------------------------------------------------------
# derived series builders (all pure: points in, points out)
# ---------------------------------------------------------------------------

def _latest_at_or_before(points: list[tuple[date, float]], as_of: date) -> tuple[date, float] | None:
    latest: tuple[date, float] | None = None
    for d, v in points:
        if d > as_of:
            break
        latest = (d, v)
    return latest


def _derive_ratio_like(
    a: list[tuple[date, float]] | None,
    b: list[tuple[date, float]] | None,
    *,
    combine,
) -> tuple[list[tuple[date, float]], tuple[date, float]] | None:
    """Pair two series by anchor dates (tol _PAIR_TOL_DAYS) and fold values."""
    if not a or not b:
        return None
    pairs = _pair_points(a, b, _PAIR_TOL_DAYS)
    points: list[tuple[date, float]] = []
    for d, v_a, v_b in pairs:
        combined = combine(v_a, v_b)
        if combined is not None:
            points.append((d, combined))
    if not points:
        return None
    return points, points[-1]


def _derive_claims_yoy(icsa: list[tuple[date, float]] | None, as_of: date) -> tuple[list[tuple[date, float]], tuple[date, float]] | None:
    """YoY of the 28-day trailing mean of initial claims."""
    if not icsa:
        return None
    series = _SortedSeries(icsa)

    def _yoy_at(t: date) -> tuple[date, float] | None:
        m_now = series.rolling_mean(28, t)
        m_year = series.rolling_mean(28, t - timedelta(days=365))
        if m_now is None or m_year is None or m_year == 0.0:
            return None
        return (t, (m_now / m_year - 1.0) * 100.0)

    hist_points = [p for anchor in icsa if (p := _yoy_at(anchor[0])) is not None]
    current = _yoy_at(as_of)
    if current is None or not hist_points:
        return None
    return hist_points, current


def _derive_hy_change_6m(hy: list[tuple[date, float]] | None, as_of: date) -> tuple[list[tuple[date, float]], tuple[date, float]] | None:
    """HY OAS change vs the value nearest 6 months ago (bp)."""
    if not hy:
        return None
    points: list[tuple[date, float]] = []
    for d, v in hy:
        base = _nearest_point(hy, d - timedelta(days=_HY_6M_DAYS), _HY_6M_TOL_DAYS)
        if base is None or base[0] >= d:
            continue
        points.append((d, (v - base[1]) * 100.0))
    if not points:
        return None
    now = _latest_at_or_before(hy, as_of)
    if now is None:
        return None
    base_now = _nearest_point(hy, as_of - timedelta(days=_HY_6M_DAYS), _HY_6M_TOL_DAYS)
    if base_now is None or base_now[0] >= now[0]:
        return points, (now[0], points[-1][1])
    return points, (now[0], (now[1] - base_now[1]) * 100.0)


def _derive_netliq(
    walcl: list[tuple[date, float]] | None,
    rrp: list[tuple[date, float]] | None,
    tga: list[tuple[date, float]] | None,
    as_of: date,
) -> tuple[list[tuple[date, float]], tuple[date, float]] | None:
    """netliq($B) = walcl($M)/1000 - rrp($B) - tga($B) at WALCL dates."""
    if not walcl or not rrp or not tga:
        return None
    points: list[tuple[date, float]] = []
    for d, w in walcl:
        r = _nearest_point(rrp, d, _NETLIQ_PAIR_TOL_DAYS)
        t = _nearest_point(tga, d, _NETLIQ_PAIR_TOL_DAYS)
        if r is None or t is None:
            continue
        points.append((d, w / 1000.0 - r[1] - t[1]))
    current = _latest_at_or_before(points, as_of)
    if not points or current is None:
        return None
    return points, current


def _derive_buffett(
    equities: list[tuple[date, float]] | None,
    gdp: list[tuple[date, float]] | None,
) -> tuple[list[tuple[date, float]], tuple[date, float]] | None:
    """Buffett % = (NCBEILQ027S $M/1000) / GDP $B * 100; sanity 50..400.

    The current value is None when the unit sanity check fails (warning at the
    use site); the percentile history keeps sane points only.
    """
    if not equities or not gdp:
        return None
    pairs = _pair_points(equities, gdp, _GA_PAIR_TOL_DAYS)
    if not pairs:
        return None
    points: list[tuple[date, float]] = []
    for d, v_e, v_g in pairs:
        if v_g == 0.0:
            continue
        value = (v_e / 1000.0) / v_g * 100.0
        if _UNIT_SANITY_RANGE[0] <= value <= _UNIT_SANITY_RANGE[1]:
            points.append((d, value))
    last_d, last_e, last_g = pairs[-1]
    last_value = None
    if last_g != 0.0:
        candidate = (last_e / 1000.0) / last_g * 100.0
        if _UNIT_SANITY_RANGE[0] <= candidate <= _UNIT_SANITY_RANGE[1]:
            last_value = candidate
    return points, (last_d, last_value)


def _derive_margin_family(
    margin: list[tuple[date, float]] | None,
    as_of: date,
) -> tuple[tuple[list[tuple[date, float]], tuple[date, float]], tuple[date, float] | None, float | None] | None:
    """(yoy series/current, mom change, yoy max over last 6 months) for margin debt."""
    if not margin:
        return None
    series = _SortedSeries(margin)
    yoy_points = [(d, y) for d, _ in margin if (y := series.yoy(d)) is not None]
    if not yoy_points:
        return None
    now = _latest_at_or_before(yoy_points, as_of)
    if now is None:
        return None

    mom: tuple[date, float] | None = None
    hi = bisect_right(series.dates, as_of)
    if hi >= 2:
        prev_d, prev_v = series.points[hi - 2]
        last_d, last_v = series.points[hi - 1]
        if (last_d - prev_d).days <= _MARGIN_MOM_TOL_DAYS and prev_v != 0.0:
            mom = (last_d, (last_v / prev_v - 1.0) * 100.0)

    cutoff = as_of - timedelta(days=_MARGIN_MAX_WINDOW_DAYS)
    recent_max = max((y for d, y in yoy_points if d >= cutoff), default=None)

    return (yoy_points, now), mom, recent_max


def _derive_curve_diagnostics(
    curve: list[tuple[date, float]] | None,
    as_of: date,
) -> tuple[float | None, float | None]:
    """(90d slope pp, 540d min pp) of the 3M10Y curve."""
    if not curve:
        return None, None
    latest = _latest_at_or_before(curve, as_of)
    if latest is None:
        return None, None
    slope: float | None = None
    base = _nearest_point(curve, latest[0] - timedelta(days=_CURVE_SLOPE_DAYS), _CURVE_SLOPE_TOL_DAYS)
    if base is not None:
        slope = latest[1] - base[1]
    window = [v for d, v in curve if as_of - timedelta(days=_CURVE_MIN_DAYS) <= d <= as_of]
    minimum: float | None = min(window) if window else None
    return slope, minimum


# ---------------------------------------------------------------------------
# snapshot assembly
# ---------------------------------------------------------------------------

def _make_snapshot(
    key: str,
    spec: SeriesSpec,
    *,
    value: float | None,
    obs_date: date | None,
    percentile: float | None,
    as_of: date,
    warnings: list[str],
) -> SeriesSnapshot:
    days_late = (as_of - obs_date).days if obs_date is not None else None
    is_stale = days_late is not None and days_late > spec.staleness_days
    stale_reason = None
    if is_stale and obs_date is not None:
        stale_reason = (
            f"дані від {obs_date.isoformat()}, "
            f"застарілі понад {spec.staleness_days} {uk_days_word(spec.staleness_days)}"
        )
    band = None
    if spec.band_ref and value is not None:
        try:
            band = evaluate_band(spec.band_ref, value)
        except GaugeComputeError:
            band = None
            warnings.append(f"{key}: невідома band_ref {spec.band_ref!r} - банду пропущено")
    return SeriesSnapshot(
        key=key,
        title_uk=spec.title_uk,
        module_key=spec.module_key,
        value=value,
        obs_date=obs_date,
        percentile=percentile,
        band=band,
        is_stale=is_stale,
        stale_reason_uk=stale_reason,
        meaning_uk=spec.params.get("meaning_uk", _DEFAULT_MEANINGS_UK.get(key, "")),
        source_uk=spec.source if spec.source and spec.source != "computed" else _COMPUTED_FALLBACK_SOURCE,
        unit_uk=spec.params.get("unit", "") or _UNITS_BY_KEY.get(key, ""),
    )


def _make_diagnostic(key: str, value: float | None, obs_date: date | None) -> SeriesSnapshot:
    title_uk, meaning_uk, unit_uk = _DIAGNOSTIC_META[key]
    return SeriesSnapshot(
        key=key,
        title_uk=title_uk,
        module_key=None,
        value=value,
        obs_date=obs_date,
        percentile=None,
        band=None,
        is_stale=False,
        stale_reason_uk=None,
        meaning_uk=meaning_uk,
        source_uk=_COMPUTED_FALLBACK_SOURCE,
        unit_uk=unit_uk,
    )


_MODULE_INDEX_FALLBACK = len(MODULE_KEYS)


def _module_index(module_key: str | None) -> int:
    if module_key in MODULE_KEYS:
        return MODULE_KEYS.index(module_key)
    return _MODULE_INDEX_FALLBACK


# facts ---------------------------------------------------------------------

def _curated_facts(module_key: str, snapshots: Mapping[str, SeriesSnapshot]) -> tuple[str, ...]:
    facts: list[str] = []
    for key in _MODULE_FACTS.get(module_key, ()):
        snap = snapshots.get(key)
        if snap is None or snap.value is None or snap.obs_date is None:
            continue
        label = _FACT_LABELS_UK.get(key, snap.title_uk)
        facts.append(f"{label} {format_value_uk(snap.value, '')} ({snap.obs_date.isoformat()})")
        if len(facts) >= _MAX_CURATED_FACTS:
            break
    return tuple(facts)


_MAX_CURATED_FACTS = 4
_VALUATION_DIVERGENCE_PCT = 30.0


# system signal --------------------------------------------------------------

def _system_signal_uk(
    modules: Sequence[ModuleGauge],
    snapshots: Mapping[str, SeriesSnapshot],
) -> str:
    by_module = {m.module_key: m for m in modules}
    v_chip = _chip_or_unavailable(by_module, "valuation")
    s_chip = _chip_or_unavailable(by_module, "sentiment")
    t_chip = _chip_or_unavailable(by_module, "technique")
    m_chip = _chip_or_unavailable(by_module, "macro")
    c_chip = _chip_or_unavailable(by_module, "credit")
    sentences = [
        f"Стан модулів: валютація - {v_chip}, настрої - {s_chip}, техніка - {t_chip}, макро - {m_chip}, кредит - {c_chip}."
    ]

    vix = _sval(snapshots, "vix")
    vix_vix3m = _sval(snapshots, "vix_vix3m")
    weekly_rsi = _sval(snapshots, "weekly_rsi")
    fg = _sval(snapshots, "fg")
    capitulation_count = sum(
        (
            (vix is not None and vix > _VIX_CAPITULATION_THRESHOLD) or (vix_vix3m is not None and vix_vix3m > _VIX_VIX3M_CAPITULATION_THRESHOLD),
            weekly_rsi is not None and weekly_rsi < _RSI_OVERSOLD,
            fg is not None and fg < _FG_CAPITULATION_THRESHOLD,
        )
    )
    if capitulation_count >= 1:
        sentences.append(f"Капітуляційний кластер: {capitulation_count} з 3 обчислюваних умов виконано.")

    margin_yoy = _sval(snapshots, "margin_yoy")
    fg_min = _sval(snapshots, "fg_min_28d")
    euphoria_count = 0
    euphoria_notes: list[str] = []
    if margin_yoy is not None and margin_yoy >= _MARGIN_YOY_EUPHORIA:
        euphoria_count += 1
    if fg_min is None:
        # history too short to confirm/refute the 4-week greed condition
        euphoria_notes.append("умова F&G (>80 чотири тижні) не перевірена - коротка історія")
    elif fg_min > _FG_EUPHORIA_THRESHOLD:
        euphoria_count += 1
    if euphoria_count >= 1:
        note = " " + "; ".join(euphoria_notes) + "." if euphoria_notes else ""
        sentences.append(f"Ейфорія-позиціонування: {euphoria_count} з 2 умов.{note}")

    hy = _sval(snapshots, "hy_oas")
    claims = _sval(snapshots, "claims_yoy")
    gate_triggers: list[str] = []
    if hy is not None and hy > _CREDIT_GATE_HY:
        gate_triggers.append(f"HY OAS {format_value_uk(hy, '')}% > 4,5%")
    if claims is not None and claims > _CLAIMS_YOY_ALARM:
        gate_triggers.append(f"claims р/р {format_value_uk(claims, '')}% > +20%")
    if gate_triggers:
        sentences.append(f"Кредитний гейт: зупинити ризик-нарощування ({'; '.join(gate_triggers)}).")

    cape_pct = _spct(snapshots, "cape")
    fpe_pct = _spct(snapshots, "forward_pe")
    if cape_pct is not None and fpe_pct is not None and abs(cape_pct - fpe_pct) > _VALUATION_DIVERGENCE_PCT:
        gap = abs(cape_pct - fpe_pct)
        sentences.append(
            f"Ціна очікуваного зростання: розрив {gap:.0f} перц. пунктів між CAPE і форвардним P/E - "
            "повільні якорі дорогі, швидкий якір чесний; ринок платить за консенсус-зростання, а не за премію за ризик."
        )

    if len(sentences) == 1:
        sentences.append("Діючі історичні асоціації для поточних умов - у таблиці «умови -> форвардна дохідність».")
    return " ".join(sentences)


def _chip_or_unavailable(by_module: Mapping[str, ModuleGauge], module_key: str) -> str:
    gauge = by_module.get(module_key)
    return gauge.chip_label_uk if gauge is not None else "недоступно"


def _sval(snapshots: Mapping[str, SeriesSnapshot], key: str) -> float | None:
    snap = snapshots.get(key)
    return snap.value if snap is not None else None


def _spct(snapshots: Mapping[str, SeriesSnapshot], key: str) -> float | None:
    snap = snapshots.get(key)
    return snap.percentile if snap is not None else None


# ---------------------------------------------------------------------------
# main use case
# ---------------------------------------------------------------------------

def compute_dashboard(
    reader: VintageRepository,
    specs: Mapping[str, SeriesSpec],
    *,
    as_of: date,
    now_kyiv_datetime: datetime,
) -> DashboardSnapshot:
    """Build the full dashboard snapshot (inputs -> derived -> gauges -> rows).

    Missing specs/data degrade to warnings, never exceptions; every series is
    evaluated strictly point-in-time (history capped at ``as_of``).
    ``now_kyiv_datetime`` is used verbatim as generated_at.
    """
    warnings: list[str] = []
    histories: dict[str, list[tuple[date, float]]] = {}
    snapshots: dict[str, SeriesSnapshot] = {}

    input_specs = {key: spec for key, spec in specs.items() if not spec.is_computed}
    derived_specs = {key: spec for key, spec in specs.items() if spec.is_computed}

    for key in sorted(input_specs):
        spec = input_specs[key]
        pit = reader.read_point_in_time(key, as_of)
        hist_obs = reader.read_history(key, end=as_of)
        points = _dedup_first(hist_obs if hist_obs else [])
        if pit is None or not points:
            warnings.append(f"{key}: немає даних у сховищі станом на {as_of.isoformat()}")
            continue
        value = float(pit.value)
        percentile = percentile_from_history(points, value, window_days=spec.percentile_window_days)
        if percentile is None:
            warnings.append(f"{key}: {_THIN_WINDOW_WARNING}")
        snapshots[key] = _make_snapshot(
            key, spec, value=value, obs_date=pit.obs_date, percentile=percentile,
            as_of=as_of, warnings=warnings,
        )
        histories[key] = points

    def _points(key: str) -> list[tuple[date, float]] | None:
        return histories.get(key) or None

    # --- derived: paired daily series ----------------------------------------
    for key, builder, inputs in (
        ("ecy", _derive_ratio_like, ("cape", "real_10y")),
        ("vix_vix3m", _derive_ratio_like, ("vix", "vix3m")),
        ("sofr_iorb", _derive_ratio_like, ("sofr", "iorb")),
    ):
        _consume_derived(
            snapshots, histories, derived_specs, warnings, key,
            builder(_points(inputs[0]), _points(inputs[1]), combine=_PAIR_COMBINES[key]),
            as_of=as_of,
        )
    _consume_derived(
        snapshots, histories, derived_specs, warnings, "buffett_z1",
        _derive_buffett(_points("ncbeilq027s"), _points("gdp")),
        as_of=as_of,
    )
    _consume_derived(
        snapshots, histories, derived_specs, warnings, "hy_change_6m",
        _derive_hy_change_6m(_points("hy_oas"), as_of),
        as_of=as_of,
    )
    _consume_derived(
        snapshots, histories, derived_specs, warnings, "claims_yoy",
        _derive_claims_yoy(_points("icsa"), as_of),
        as_of=as_of,
    )
    _consume_derived(
        snapshots, histories, derived_specs, warnings, "netliq_tbn",
        _derive_netliq(_points("walcl"), _points("rrpontsyd"), _points("wregen"), as_of),
        as_of=as_of,
    )

    # netliq_yoy: YoY of the merged netliq series
    netliq_points = histories.get("netliq_tbn")
    if netliq_points:
        netliq_series = _SortedSeries(netliq_points)
        yoy_points = [(d, y) for d, _ in netliq_points if (y := netliq_series.yoy(d)) is not None]
        current = netliq_series.yoy(as_of)
        if yoy_points and current is not None:
            obs_date = _latest_at_or_before(netliq_points, as_of)
            _consume_derived(
                snapshots, histories, derived_specs, warnings, "netliq_yoy",
                (yoy_points, (obs_date[0] if obs_date else as_of, current)),
                as_of=as_of,
            )
        else:
            warnings.append("netliq_yoy: недостатньо історії нетто-ліквідності для річного порівняння")
    else:
        warnings.append("netliq_yoy: нетто-ліквідність не обчислена (немає вхідних серій)")

    # --- SPX technique family -------------------------------------------------
    spx = _points("spx")
    if spx:
        weekly = _weekly_closes(spx)
        _consume_derived(
            snapshots, histories, derived_specs, warnings, "weekly_rsi",
            _wrap_series(_weekly_rsi_series(weekly)),
            as_of=as_of,
        )
        _consume_derived(
            snapshots, histories, derived_specs, warnings, "pct_200dma",
            _wrap_series(_pct_ma_series(spx, _PCT_200DMA_WINDOW)),
            as_of=as_of,
        )
        _consume_derived(
            snapshots, histories, derived_specs, warnings, "drawdown_2y",
            _wrap_series(_drawdown_series(spx, _DRAWDOWN_LOOKBACK_DAYS)),
            as_of=as_of,
        )
    else:
        for key in ("weekly_rsi", "pct_200dma", "drawdown_2y"):
            warnings.append(f"{key}: немає історії SPX для розрахунку")

    # --- margin family ----------------------------------------------------------
    margin_family = _derive_margin_family(_points("margin_debt"), as_of)
    if margin_family is not None:
        (yoy_points, current), mom, yoy_max = margin_family
        _consume_derived(
            snapshots, histories, derived_specs, warnings, "margin_yoy",
            (yoy_points, current),
            as_of=as_of,
        )
        if mom is not None:
            snapshots.setdefault("margin_mom_change", _make_diagnostic("margin_mom_change", mom[1], mom[0]))
        if yoy_max is not None:
            snapshots.setdefault("margin_yoy_max_6m", _make_diagnostic("margin_yoy_max_6m", yoy_max, current[0]))
    else:
        warnings.append("margin_yoy: немає історії маржинальної заборгованості")

    # --- curve + fg diagnostics --------------------------------------------------
    curve_slope, curve_min = _derive_curve_diagnostics(_points("curve_3m"), as_of)
    if curve_slope is not None:
        snapshots.setdefault("curve_slope_90d", _make_diagnostic("curve_slope_90d", curve_slope, _latest_date(_points("curve_3m"), as_of)))
    if curve_min is not None:
        snapshots.setdefault("curve_min_540d", _make_diagnostic("curve_min_540d", curve_min, _latest_date(_points("curve_3m"), as_of)))
    fg_min, fg_min_obs = _derive_fg_min_28d_with_obs(_points("fg"), as_of)
    if fg_min is None and _points("fg"):
        warnings.append("fg_min_28d: коротка історія F&G - 4-тижневу умову ейфорії не перевірено")
    if fg_min is not None:
        snapshots.setdefault("fg_min_28d", _make_diagnostic("fg_min_28d", fg_min, fg_min_obs))

    # --- module gauges -----------------------------------------------------------
    modules: list[ModuleGauge] = []
    for module_key in MODULE_KEYS:
        member_keys = sorted(
            key
            for key, snap in snapshots.items()
            if (spec := specs.get(key)) is not None
            and spec.module_key == module_key
            and spec.weight is not None
            and snap.module_key == module_key
        )
        members = [snapshots[k] for k in member_keys]
        gauge = compute_module_gauge(module_key, members, specs)
        curated = _curated_facts(module_key, snapshots)
        if curated:
            gauge = replace(gauge, facts_uk=curated)
        modules.append(gauge)

    indicators = tuple(
        snapshots[key]
        for key in sorted(snapshots, key=lambda k: (_module_index(snapshots[k].module_key), k))
        if snapshots[key].module_key is not None
    )

    system_signal_uk = _system_signal_uk(modules, snapshots)
    forward_rows = evaluate_forward_rows({m.module_key: m for m in modules}, snapshots)
    if _BREADTH_DEFERRED_UK not in warnings:
        warnings.append(_BREADTH_DEFERRED_UK)

    return DashboardSnapshot(
        as_of=as_of,
        generated_at=now_kyiv_datetime,
        modules=tuple(modules),
        indicators=indicators,
        forward_rows=forward_rows,
        system_signal_uk=system_signal_uk,
        warnings_uk=tuple(warnings),
    )


_PCT_200DMA_WINDOW = 200
_DRAWDOWN_LOOKBACK_DAYS = 730

_PAIR_COMBINES = {
    "ecy": lambda cape, real: (1.0 / cape) * 100.0 - real if cape != 0.0 else None,
    "vix_vix3m": lambda vix, vix3m: (vix / vix3m if vix3m != 0.0 else None),
    "sofr_iorb": lambda sofr, iorb: (sofr - iorb) * 100.0,
}


def _wrap_series(points: list[tuple[date, float]]) -> tuple[list[tuple[date, float]], tuple[date, float]] | None:
    if not points:
        return None
    return points, points[-1]


def _latest_date(points: list[tuple[date, float]] | None, as_of: date) -> date | None:
    latest = _latest_at_or_before(points, as_of) if points else None
    return latest[0] if latest else None


def _derive_fg_min_28d_with_obs(
    fg: list[tuple[date, float]] | None,
    as_of: date,
) -> tuple[float | None, date | None]:
    """(min F&G over 28d, latest fg obs date); None value when history short."""
    if not fg:
        return None, None
    latest = _latest_at_or_before(fg, as_of)
    if latest is None:
        return None, None
    window = [v for d, v in fg if as_of - timedelta(days=_FG_MIN_WINDOW_DAYS - 1) <= d <= as_of]
    if len(window) < _FG_MIN_OBS_COUNT:
        return None, latest[0]
    return min(window), latest[0]


def _consume_derived(
    snapshots: dict[str, SeriesSnapshot],
    histories: dict[str, list[tuple[date, float]]],
    derived_specs: Mapping[str, SeriesSpec],
    warnings: list[str],
    key: str,
    derived: tuple[list[tuple[date, float]], tuple[date, float]] | None,
    *,
    as_of: date,
) -> None:
    """Register one derived snapshot (value/percentile/staleness/band)."""
    if derived is None:
        warnings.append(f"{key}: не обчислено - відсутні або неспарені вхідні серії")
        return
    series_points, (current_date, current_value) = derived
    spec = derived_specs.get(key)
    if spec is None:
        warnings.append(f"{key}: немає специфікації для обчислюваної серії - пропущено")
        return
    if current_value is None:
        warnings.append(f"{key}: {_UNIT_SANITY_WARNING}")
        return
    percentile = percentile_from_history(series_points, current_value, window_days=spec.percentile_window_days)
    if percentile is None:
        warnings.append(f"{key}: {_THIN_WINDOW_WARNING}")
    snapshots[key] = _make_snapshot(
        key, spec, value=current_value, obs_date=current_date, percentile=percentile,
        as_of=as_of, warnings=warnings,
    )
    histories[key] = series_points
