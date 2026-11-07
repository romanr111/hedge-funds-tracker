from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from market_intel.application.use_cases.compute_dashboard import compute_dashboard
from market_intel.domain.models import (
    MODULE_KEYS,
    COLOR_TOKEN_HEX,
    DashboardSnapshot,
    ModuleGauge,
    SeriesObservation,
    SeriesSnapshot,
    SeriesSpec,
)

_FETCHED = datetime(2026, 11, 7, 6, 0)
AS_OF = date(2026, 11, 6)
NOW = datetime(2026, 11, 7, 7, 0)

# Deterministic seed set: daily grid 2025-01-01..2026-11-06 (675 points); the
# trailing 400-value window used by the test specs is indices 275..674. All
# expected numbers below are hand-computed from the documented windows.

N_DAILY = 675
W_START = 275
DAILY_DATES = [date(2025, 1, 1) + timedelta(days=i) for i in range(N_DAILY)]
_WEEKLY_96 = [date(2025, 1, 8) + timedelta(days=7 * i) for i in range(96)]
_WEEKLY_96_THU = [date(2025, 1, 9) + timedelta(days=7 * i) for i in range(96)]
_WEEKLY_52_THU = [date(2025, 11, 6) + timedelta(days=7 * i) for i in range(52)]
_MARGIN_42 = [date(2026, 10, 16) - timedelta(days=30 * k) for k in range(41, -1, -1)]
_QUARTER_41 = [date(2016, 9, 30) + timedelta(days=91 * i) for i in range(41)]


def _daily(key: str, values: list[float], dates: list[date] | None = None) -> list[SeriesObservation]:
    return [
        SeriesObservation(series_id=key, obs_date=d, value=v, source="test", fetched_at=_FETCHED)
        for d, v in zip(dates or DAILY_DATES, values)
    ]


def _ramp(final: float, start: float = 20.0) -> list[float]:
    return [start + i * (final - start) / (N_DAILY - 1) for i in range(N_DAILY)]


def _layered(filler: float, chunks: list[tuple[int, float]]) -> list[float]:
    """Filler up to the window start, then fixed-value chunks, final value last."""
    values = [filler] * W_START
    for count, value in chunks[:-1]:
        values.extend([value] * count)
    values.append(chunks[-1][1])
    assert len(values) == N_DAILY
    return values


def _margin_growth(growth: float) -> list[float]:
    return [600000.0 * growth ** i for i in range(42)]


def _seed_data(*, margin: list[float] | None = None, extra_ignores_vix3m: bool = False,
               short_fg: bool = False, insane_ncbei: bool = False,
               stale_ig: bool = False, future_peek: bool = False) -> dict[str, list[SeriesObservation]]:
    fg_vals = _layered(20.0, [(300, 10.0), (99, 50.0), (1, 19.85)])
    if short_fg:
        fg_vals = [50.0] * 20
    ncbei = [(150.0 + (250.0 - 150.0) * i / 40) * 10.0 * (20000.0 + 200.0 * i) for i in range(41)]
    if insane_ncbei:
        ncbei[-1] *= 20.0
    ig_vals = [0.91] * (N_DAILY - 36) if stale_ig else [0.91] * N_DAILY
    data = {
        "cape": _daily("cape", _ramp(38.79)),
        "forward_pe": _daily("forward_pe", _layered(20.0, [(180, 15.0), (219, 22.0), (1, 18.7)])),
        "erp": _daily("erp", _layered(4.0, [(200, 2.0), (199, 6.0), (1, 4.41)])),
        "real_10y": _daily("real_10y", [2.64] * N_DAILY),
        "vix": _daily("vix", _layered(10.0, [(250, 12.0), (149, 20.0), (1, 16.61)])),
        "fg": _daily("fg", fg_vals),
        "spx": _daily("spx", [1000.0] * N_DAILY),
        "hy_oas": _daily("hy_oas", [3.32] * N_DAILY),
        "ig_oas": _daily("ig_oas", ig_vals),
        "sofr": _daily("sofr", _layered(4.28, [(200, 4.28), (199, 4.45), (1, 4.29)])),
        "iorb": _daily("iorb", [4.25] * N_DAILY),
        "curve_3m": _daily("curve_3m", [0.67] * N_DAILY),
        "fed_funds": _daily("fed_funds", [3.875] * N_DAILY),
        "rrpontsyd": _daily("rrpontsyd", [0.1] * N_DAILY),
        "wregen": _daily("wregen", [957.0] * N_DAILY),
        "icsa": _daily("icsa", [200000.0] * 96, _WEEKLY_96_THU),
        "walcl": _daily("walcl", [6793000.0] * 96, _WEEKLY_96),
        "nfci": _daily("nfci", [-0.459] * 52, _WEEKLY_52_THU),
        "margin_debt": _daily("margin_debt", margin or [600000.0] * 42, _MARGIN_42),
        "gdp": _daily("gdp", [20000.0 + 200.0 * i for i in range(41)], _QUARTER_41),
        "ncbeilq027s": _daily("ncbeilq027s", ncbei, _QUARTER_41),
    }
    if not extra_ignores_vix3m:
        data["vix3m"] = _daily("vix3m", [25.0] * N_DAILY)
    if future_peek:
        # a print dated AFTER as_of must never influence the compute
        data["cape"] = data["cape"] + [
            SeriesObservation(series_id="cape", obs_date=date(2027, 1, 1), value=99999.0,
                              source="future", fetched_at=_FETCHED)
        ]
    # an unspecced series in the store must be ignored silently
    data["mystery"] = _daily("mystery", [1.0] * N_DAILY)
    return data


def _spec(key: str, module: str | None, weight: float | None, band: str | None = None, *,
          freq: str = "daily", staleness: int = 3, window: int = 400, invert: bool = False,
          unit: str = "", computed: bool = False) -> SeriesSpec:
    return SeriesSpec(
        key=key,
        title_uk=key,
        source="computed" if computed else "test source",
        freq=freq,
        staleness_days=staleness,
        band_ref=band,
        weight=weight,
        module_key=module,
        invert=invert,
        percentile_window_days=window,
        params={"unit": unit},
    )


def _specs() -> dict[str, SeriesSpec]:
    return {
        "cape": _spec("cape", "valuation", 1.0, "cape"),
        "forward_pe": _spec("forward_pe", "valuation", 1.0, "forward_pe"),
        "erp": _spec("erp", "valuation", 1.0, "erp", invert=True),
        "ecy": _spec("ecy", "valuation", None, "ecy", computed=True),
        "ncbeilq027s": _spec("ncbeilq027s", "valuation", None, None, freq="quarterly", staleness=62, window=4000),
        "gdp": _spec("gdp", "valuation", None, None, freq="quarterly", staleness=62, window=4000),
        "buffett_z1": _spec("buffett_z1", "valuation", 1.0, "buffett_z1", freq="quarterly", staleness=62, window=4000, computed=True),
        "fg": _spec("fg", "sentiment", 0.25, "fg"),
        "vix": _spec("vix", "sentiment", 0.20, "vix"),
        "vix3m": _spec("vix3m", "sentiment", None, None),
        "vix_vix3m": _spec("vix_vix3m", "sentiment", 0.10, "vix_vix3m", computed=True),
        "margin_debt": _spec("margin_debt", "sentiment", None, None, freq="monthly", staleness=35, window=4000),
        "margin_yoy": _spec("margin_yoy", "sentiment", 0.25, "margin_yoy", freq="monthly", staleness=35, window=4000, computed=True, unit="%"),
        "spx": _spec("spx", "technique", None, None),
        "weekly_rsi": _spec("weekly_rsi", "technique", 0.40, "weekly_rsi", freq="weekly", staleness=8, computed=True),
        "pct_200dma": _spec("pct_200dma", "technique", 0.30, "pct_200dma", computed=True),
        "drawdown_2y": _spec("drawdown_2y", "technique", 0.30, "drawdown_2y", computed=True),
        "nfci": _spec("nfci", "macro", 0.25, "nfci", freq="weekly", staleness=8),
        "curve_3m": _spec("curve_3m", "macro", 0.20, "curve"),
        "icsa": _spec("icsa", "macro", None, None, freq="weekly", staleness=8, window=2000),
        "claims_yoy": _spec("claims_yoy", "macro", 0.20, "claims_yoy", freq="weekly", staleness=8, window=800, computed=True, unit="%"),
        "real_10y": _spec("real_10y", "macro", 0.15, "real_10y", unit="%"),
        "fed_funds": _spec("fed_funds", "macro", None, "fed_funds", unit="%"),
        "walcl": _spec("walcl", "macro", None, None, freq="weekly", staleness=8, window=4000),
        "rrpontsyd": _spec("rrpontsyd", "macro", None, None, window=4000),
        "wregen": _spec("wregen", "macro", None, None, freq="weekly", staleness=8, window=4000),
        "netliq_tbn": _spec("netliq_tbn", "macro", None, None, freq="weekly", staleness=8, window=4000, computed=True),
        "netliq_yoy": _spec("netliq_yoy", "macro", 0.10, "netliq_yoy", freq="weekly", staleness=8, window=4000, computed=True, unit="%"),
        "hy_oas": _spec("hy_oas", "credit", 0.35, "hy_oas", unit="%"),
        "hy_change_6m": _spec("hy_change_6m", "credit", 0.20, "hy_change_6m", computed=True, unit="bp"),
        "ig_oas": _spec("ig_oas", "credit", 0.15, "ig_oas", unit="%"),
        "sofr": _spec("sofr", "credit", None, None),
        "iorb": _spec("iorb", "credit", None, None),
        "sofr_iorb": _spec("sofr_iorb", "credit", 0.15, "sofr_iorb", computed=True, unit="bp"),
    }


class FakeVintageRepository:
    """In-memory vintage store implementing the compute side of the port."""

    def __init__(self, data: dict[str, list[SeriesObservation]]) -> None:
        self.data = data

    def read_point_in_time(self, series_id: str, as_of: date) -> SeriesObservation | None:
        candidates = [o for o in self.data.get(series_id, []) if o.obs_date <= as_of]
        if not candidates:
            return None
        return max(candidates, key=lambda o: o.obs_date)

    def read_history(self, series_id: str, *, start=None, end=None) -> list[SeriesObservation]:
        return [
            o for o in self.data.get(series_id, [])
            if (start is None or o.obs_date >= start) and (end is None or o.obs_date <= end)
        ]

    def append_observations(self, items) -> None:
        raise AssertionError("compute must never write to the vintage store")

    def close(self) -> None:
        return None


def _compute(data, specs=None) -> DashboardSnapshot:
    return compute_dashboard(FakeVintageRepository(data), specs or _specs(), as_of=AS_OF, now_kyiv_datetime=NOW)


def _by_key(snapshot: DashboardSnapshot, module: str) -> dict[str, SeriesSnapshot]:
    return {s.key: s for s in snapshot.indicators if s.module_key == module}


def _module(snapshot: DashboardSnapshot, module: str) -> ModuleGauge:
    return {m.module_key: m for m in snapshot.modules}[module]


def test_module_scores_and_chips_hand_computed() -> None:
    snapshot = _compute(_seed_data())
    scores = {m.module_key: m for m in snapshot.modules}
    # valuation: mean of risk percentiles 99.875, 45.125, 98.78049, 49.875
    assert scores["valuation"].score == pytest.approx(73.41387195121953)
    assert scores["valuation"].chip_label_uk == "Дорого"
    assert scores["valuation"].chip_color == COLOR_TOKEN_HEX["state3"]
    # sentiment: (.25*75.125 + .20*62.625 + .10*62.625 + .25*50) / .80
    assert scores["sentiment"].score == pytest.approx(62.5859375)
    assert scores["sentiment"].chip_label_uk == "Обережно"
    # technique/macro: constant seeds -> every percentile 50 -> composite 50
    assert scores["technique"].score == pytest.approx(50.0)
    assert scores["technique"].chip_label_uk == "Нейтрально"
    assert scores["macro"].score == pytest.approx(50.0)
    # credit: (.35*50 + .20*50 + .15*50 + .15*50.125) / .85 (sofr craft)
    assert scores["credit"].score == pytest.approx(50.022058823529406)
    assert scores["credit"].chip_label_uk == "Помірний"
    assert [m.module_key for m in snapshot.modules] == list(MODULE_KEYS)


def test_indicator_key_percentiles_match_seeds() -> None:
    snapshot = _compute(_seed_data())
    valuation = _by_key(snapshot, "valuation")
    assert valuation["cape"].value == pytest.approx(38.79)
    assert valuation["cape"].percentile == pytest.approx(99.875)
    assert valuation["cape"].band.label_uk == "Дуже дорого"
    assert valuation["forward_pe"].percentile == pytest.approx(45.125)
    assert valuation["erp"].percentile == pytest.approx(50.125)  # RAW percentile stored
    assert valuation["buffett_z1"].value == pytest.approx(250.0)
    assert valuation["buffett_z1"].percentile == pytest.approx(98.78048780487805)
    assert valuation["ecy"].band.label_uk == "Дуже дорого"  # ecy = 100/38.79 - 2.64 < 0
    sentiment = _by_key(snapshot, "sentiment")
    assert sentiment["fg"].percentile == pytest.approx(75.125)
    assert sentiment["vix"].percentile == pytest.approx(62.625)
    assert sentiment["vix_vix3m"].value == pytest.approx(0.6644)
    assert sentiment["margin_yoy"].value == pytest.approx(0.0)
    assert sentiment["margin_yoy"].percentile == pytest.approx(50.0)
    technique = _by_key(snapshot, "technique")
    assert technique["weekly_rsi"].value == pytest.approx(50.0)
    assert technique["pct_200dma"].value == pytest.approx(0.0)
    assert technique["drawdown_2y"].value == pytest.approx(0.0)
    macro = _by_key(snapshot, "macro")
    assert macro["claims_yoy"].value == pytest.approx(0.0)
    assert macro["netliq_tbn"].value == pytest.approx(5835.9)
    assert macro["netliq_yoy"].band.label_uk == "Розширення"  # boundary 0 -> higher band
    credit = _by_key(snapshot, "credit")
    assert credit["hy_change_6m"].value == pytest.approx(0.0)
    assert credit["ig_oas"].band.label_uk == "Низький ризик"
    assert credit["sofr_iorb"].value == pytest.approx(4.0, rel=1e-9)


def test_indicators_ordered_by_module_then_key_diagnostics_excluded() -> None:
    snapshot = _compute(_seed_data())
    assert len(snapshot.indicators) == 34
    order = [MODULE_KEYS.index(s.module_key) for s in snapshot.indicators]
    assert order == sorted(order)
    for module in MODULE_KEYS:
        keys = [s.key for s in snapshot.indicators if s.module_key == module]
        assert keys == sorted(keys)
    diagnostic_keys = {"curve_slope_90d", "curve_min_540d", "margin_yoy_max_6m",
                       "margin_mom_change", "fg_min_28d", "mystery"}
    rendered_keys = {s.key for s in snapshot.indicators}
    assert not rendered_keys & diagnostic_keys


def test_staleness_rules_and_exact_reason_strings() -> None:
    snapshot = _compute(_seed_data(stale_ig=True))
    ig = _by_key(snapshot, "credit")["ig_oas"]
    assert ig.is_stale
    assert ig.stale_reason_uk == "дані від 2026-10-01, застарілі понад 3 дні"
    credit = {m.module_key: m for m in snapshot.modules}["credit"]
    # ig drops; surviving weights renormalize over hy .35, hy_change .20, sofr .15
    # (.35*50 + .20*50 + .15*50.125) / .70 (sofr risk pct is 50.125)
    assert credit.score == pytest.approx(50.02678571428571)
    assert credit.dropped_uk == ("ig_oas: дані від 2026-10-01, застарілі понад 3 дні",)
    assert credit.members_used == 3 and credit.members_total == 4
    assert credit.chip_label_uk == "Помірний"
    # fresh run keeps every credit member
    fresh = _compute(_seed_data())
    fresh_credit = {m.module_key: m for m in fresh.modules}["credit"]
    assert fresh_credit.members_used == 4
    assert fresh_credit.score == pytest.approx(50.022058823529406)
    # quarterly Z.1 (37 days) sits inside its 62-day budget: not stale
    assert not _by_key(fresh, "valuation")["buffett_z1"].is_stale


def test_missing_member_spec_input_is_skipped_without_crash() -> None:
    snapshot = _compute(_seed_data())
    assert all(s.key != "mystery" for s in snapshot.indicators)


def test_missing_vix3m_series_warns_and_renormalizes() -> None:
    snapshot = _compute(_seed_data(extra_ignores_vix3m=True))
    assert any("vix_vix3m" in w and "не обчислено" in w for w in snapshot.warnings_uk)
    sentiment = {m.module_key: m for m in snapshot.modules}["sentiment"]
    # (.25*75.125 + .20*62.625 + .25*50) / .70
    assert sentiment.score == pytest.approx(62.580357142857146)
    assert sentiment.members_used == 3


def test_thin_percentile_window_excludes_member_with_warning() -> None:
    snapshot = _compute(_seed_data(short_fg=True))
    assert any("fg" in w and "перцентильн" in w.lower() for w in snapshot.warnings_uk)
    sentiment = {m.module_key: m for m in snapshot.modules}["sentiment"]
    assert "fg" in sentiment.dropped_uk[0].split(":")[0] or "fg" in sentiment.dropped_uk[0]
    # (.20*62.625 + .10*62.625 + .25*50) / .55
    assert sentiment.score == pytest.approx(56.88636363636364)
    assert sentiment.members_used == 3


def test_unit_sanity_failure_drops_buffett_from_composite() -> None:
    snapshot = _compute(_seed_data(insane_ncbei=True))
    assert any("buffett_z1" in w and "саніті" in w for w in snapshot.warnings_uk)
    valuation = {m.module_key: m for m in snapshot.modules}["valuation"]
    assert valuation.members_used == 3
    # (99.875 + 45.125 + 49.875) / 3
    assert valuation.score == pytest.approx(64.95833333333333)


def test_no_peeking_beyond_as_of() -> None:
    snapshot = _compute(_seed_data(future_peek=True))
    cape = _by_key(snapshot, "valuation")["cape"]
    assert cape.value == pytest.approx(38.79)
    assert cape.percentile == pytest.approx(99.875)  # 2027 print excluded from the window


def test_generated_at_and_as_of_are_explicit() -> None:
    snapshot = _compute(_seed_data())
    assert snapshot.as_of == AS_OF
    assert snapshot.generated_at == NOW


def test_forward_rows_active_set_for_base_seed() -> None:
    snapshot = _compute(_seed_data())
    assert len(snapshot.forward_rows) == 19
    active = {r.rule_key for r in snapshot.forward_rows if r.active_now}
    assert active == {"cape_gt_25", "fg_below_20", "hy_compressed_fci_easing"}
    ndx = next(r for r in snapshot.forward_rows if r.rule_key == "ndx_any")
    assert ndx.active_now is False and ndx.evaluated_in_mvp is False


def test_system_signal_contains_states_and_valuation_divergence() -> None:
    snapshot = _compute(_seed_data())
    text = snapshot.system_signal_uk
    assert text.startswith("Стан модулів:")
    assert "Дорого" in text and "Обережно" in text and "Помірний" in text
    # CAPE pct 99.875 vs forward P/E pct 45.125 -> divergence sentence
    assert "Ціна очікуваного зростання" in text
    assert "перц. пунктів" in text
    assert "Капітуляційний кластер" not in text
    assert "Ейфорія-позиціонування" not in text
    assert "Кредитний гейт" not in text


def test_euphoria_sentence_when_margin_yoy_exceeds_30() -> None:
    snapshot = _compute(_seed_data(margin=_margin_growth(1.026)))
    text = snapshot.system_signal_uk
    assert "Ейфорія-позиціонування: 1 з 2 умов." in text
    # the F&G 4-week condition IS evaluable here (min 19.85, not >80): no note
    assert "не перевірена" not in text
    # margin peak row still requires FALLING momentum: inactive at rising margins
    margin_row = next(r for r in snapshot.forward_rows if r.rule_key == "margin_peak_after_yoy_30")
    assert margin_row.active_now is False


def test_credit_gate_sentence_on_wide_hy() -> None:
    data = _seed_data()
    data["hy_oas"] = _daily("hy_oas", [4.61] * N_DAILY)
    snapshot = _compute(data)
    assert "Кредитний гейт: зупинити ризик-нарощування" in snapshot.system_signal_uk
    assert "HY OAS 4,61% > 4,5%" in snapshot.system_signal_uk


def test_capitulation_cluster_sentence_on_vix_spike() -> None:
    data = _seed_data()
    data["vix"] = _daily("vix", _layered(10.0, [(250, 12.0), (149, 20.0), (1, 45.0)]))
    data["vix3m"] = _daily("vix3m", [25.0] * N_DAILY)
    snapshot = _compute(data)
    assert "Капітуляційний кластер: 1 з 3" in snapshot.system_signal_uk


def test_breadth_deferral_is_always_warned() -> None:
    snapshot = _compute(_seed_data())
    assert "Ширина ринку (% акцій над 200DMA) відкладена - у MVP відсутня" in snapshot.warnings_uk


def test_unknown_band_ref_warns_but_keeps_snapshot() -> None:
    specs = _specs()
    specs["nfci"] = _spec("nfci", "macro", 0.25, "nfci_unknown_table", freq="weekly", staleness=8)
    snapshot = _compute(_seed_data(), specs)
    assert any("невідома band_ref" in w and "nfci" in w for w in snapshot.warnings_uk)
    nfci = _by_key(snapshot, "macro")["nfci"]
    assert nfci.band is None
    # the member still contributes its percentile to the composite
    assert nfci.percentile == pytest.approx(50.0)


def test_meanings_and_sources_populated_from_defaults() -> None:
    snapshot = _compute(_seed_data())
    cape = _by_key(snapshot, "valuation")["cape"]
    assert cape.meaning_uk and cape.meaning_uk.endswith(".")
    assert cape.source_uk == "test source"
    ecy = _by_key(snapshot, "valuation")["ecy"]
    assert ecy.source_uk == "розрахунок системи"
    assert cape.unit_uk == "%"  # fallback unit map supplies plain units
    hy = _by_key(snapshot, "credit")["hy_oas"]
    assert hy.unit_uk == "%"
