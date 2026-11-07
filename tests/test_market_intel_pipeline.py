"""True-pipeline integration test: real registry -> real store -> real compute.

The offline e2e test monkeypatches the core use cases; this file is the
regression gate the 2026-11-07 review asked for: it runs the real SERIES_SPECS
through a real VintageStore and compute_dashboard end to end and pins the
seams the review found broken (consume-key parity, ERP risk direction, sheet
tab contract).

Seed sizing (all deterministic, ascending grids ending at as_of):
- 1300 daily points: every monthly cape anchor finds a daily mate (the ecy
  pairing span is 1260 days) and the 200DMA + 730d drawdown windows fit.
- 120 weekly points: covers the 365d YoY lookback + 35d tolerance chains
  (claims_yoy, netliq_yoy) comfortably above the 30-value percentile floor.
- 42-60 monthly / 41 quarterly points: above the 30-value percentile floor
  and the bufferett unit-sanity window ratio stays within (50, 400).
- ERP has two variants: "high" (strictly increasing, current = all-time
  high -> raw percentile ~99) and "mid" (3-cycle at the exact midpoint ->
  raw percentile exactly 50). The valuation composite must DROP in the
  high variant because the invert lives at the gauges layer.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from signals.domain.timing import KYIV_TZ

from market_intel.application.use_cases.compute_dashboard import compute_dashboard
from market_intel.domain.models import DashboardSnapshot, SeriesObservation
from market_intel.infrastructure.export.gog_sheets_publisher import (
    GogSheetsPublisher,
    STATUS_CREATED,
)
from market_intel.infrastructure.export.sheet_layout import (
    TAB_NAMES_UK,
    build_sheet_document,
)
from market_intel.infrastructure.storage.vintage_store import VintageStore
from market_intel.registries import SERIES_SPECS

from test_market_intel_gog_publisher import FakeGogClient

AS_OF = date(2026, 11, 6)  # Friday
FETCHED_AT = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
NOW_KYIV = datetime(2026, 11, 6, 20, 0, tzinfo=KYIV_TZ)

# The one warning compute_dashboard always emits (breadth gauge deferred).
_BREADTH_DEFERRED_UK = "Ширина ринку (% акцій над 200DMA) відкладена - у MVP відсутня"

_MODULE_MEMBER_COUNTS = {
    "credit": (4, 4),
    "macro": (5, 5),
    "sentiment": (4, 4),
    "technique": (3, 3),
    "valuation": (4, 4),
}


def _grid(last: date, step_days: int, count: int) -> list[date]:
    """Ascending date grid ending at ``last`` with ``step_days`` spacing."""
    return [last - timedelta(days=step_days * i) for i in range(count)][::-1]


def _oscillate(
    base: float, amplitude: float, count: int, rng: random.Random, period: float
) -> list[float]:
    """Deterministic bounded pseudo-noise walk around ``base``."""
    values: list[float] = []
    for i in range(count):
        phase = 2.0 * math.pi * i / period
        drift = 0.7 * math.sin(phase) + rng.uniform(-0.35, 0.35)
        values.append(base * (1.0 + amplitude * drift))
    return values


def _append(store: VintageStore, key: str, dates: list[date], values: list[float]) -> None:
    store.append_observations(
        [
            SeriesObservation(
                series_id=key, obs_date=d, value=v, source="test", fetched_at=FETCHED_AT
            )
            for d, v in zip(dates, values)
        ]
    )


def _seed_store(db_path: Path, *, erp_mode: str) -> VintageStore:
    store = VintageStore(db_path)
    rng = random.Random(20261106)

    def osc(
        key: str, base: float, amplitude: float, count: int, *, step_days: int, period: float
    ) -> None:
        _append(store, key, _grid(AS_OF, step_days, count), _oscillate(base, amplitude, count, rng, period))

    # Daily inputs (1300 points: the CAPE pairing span is 1260 days, so
    # every monthly cape anchor needs a same-window daily mate; also covers
    # the 200-point 200DMA window, the 730d drawdown and percentile floors).
    osc("vix", 18.0, 0.25, 1300, step_days=1, period=91)
    osc("vix3m", 20.0, 0.20, 1300, step_days=1, period=131)
    osc("fg", 45.0, 0.28, 1300, step_days=1, period=149)
    osc("real_10y", 2.0, 0.20, 1300, step_days=1, period=97)
    osc("curve_3m", 0.30, 0.80, 1300, step_days=1, period=89)
    osc("sofr", 4.30, 0.006, 1300, step_days=1, period=401)
    osc("iorb", 4.33, 0.006, 1300, step_days=1, period=383)
    osc("hy_oas", 3.60, 0.12, 1300, step_days=1, period=211)
    osc("ig_oas", 1.15, 0.12, 1300, step_days=1, period=197)
    osc("fed_funds", 4.25, 0.002, 1300, step_days=1, period=727)
    osc("rrpontsyd", 450.0, 0.30, 1300, step_days=1, period=127)
    osc("wregen", 640.0, 0.15, 1300, step_days=1, period=157)
    osc("spx", 5500.0, 0.08, 1300, step_days=1, period=909)

    # Weekly inputs (120 points > 365d + 35d YoY tolerance chains).
    osc("nfci", 0.40, 0.60, 120, step_days=7, period=53)
    osc("icsa", 250.0, 0.30, 120, step_days=7, period=67)
    osc("walcl", 7_000_000.0, 0.02, 120, step_days=7, period=101)

    # Monthly / quarterly inputs (all above the 30-value percentile floor).
    osc("cape", 30.0, 0.02, 42, step_days=30, period=29)
    osc("margin_debt", 900_000.0, 0.01, 60, step_days=30, period=71)
    osc("forward_pe", 20.0, 0.02, 60, step_days=7, period=61)
    osc("ncbeilq027s", 30_000_000.0, 0.01, 41, step_days=91, period=43)
    osc("gdp", 30_000.0, 0.01, 41, step_days=91, period=47)

    assert erp_mode in {"high", "mid"}
    erp_grid = _grid(AS_OF, 30, 60)
    if erp_mode == "high":
        erp_values = [3.0 + 0.01 * i for i in range(60)]  # strictly increasing
    else:
        erp_values = [3.0, 5.0, 4.0] * 20  # current 4.0 = exact midpoint
    _append(store, "erp", erp_grid, erp_values)
    return store


def _compute(store: VintageStore) -> DashboardSnapshot:
    return compute_dashboard(store, SERIES_SPECS, as_of=AS_OF, now_kyiv_datetime=NOW_KYIV)


def test_real_pipeline_computes_full_dashboard(tmp_path: Path) -> None:
    snapshot = _compute(_seed_store(tmp_path / "pipeline.sqlite3", erp_mode="mid"))

    # The fully-seeded pipeline must produce exactly the known benign warning.
    assert snapshot.warnings_uk == (_BREADTH_DEFERRED_UK,)

    counts = {m.module_key: (m.members_used, m.members_total) for m in snapshot.modules}
    assert counts == _MODULE_MEMBER_COUNTS

    by_key = {s.key: s for s in snapshot.indicators}
    assert {"margin_yoy", "netliq_yoy", "vix_vix3m", "curve_3m"} <= set(by_key)

    # ERP stores the RAW percentile (the risk invert lives at the gauges layer).
    assert by_key["erp"].percentile == pytest.approx(50.0)

    # Fixed sheet-tab contract from the real snapshot (unconditional 8 tabs).
    assert [tab.name for tab in build_sheet_document(snapshot, ()).tabs] == list(TAB_NAMES_UK)


def test_erp_all_time_high_stores_raw_pct_and_lowers_valuation(tmp_path: Path) -> None:
    high = _compute(_seed_store(tmp_path / "high.sqlite3", erp_mode="high"))
    mid = _compute(_seed_store(tmp_path / "mid.sqlite3", erp_mode="mid"))

    high_erp = next(s for s in high.indicators if s.key == "erp").percentile
    assert high_erp is not None and high_erp > 50.0

    valuation_high = next(m for m in high.modules if m.module_key == "valuation")
    valuation_mid = next(m for m in mid.modules if m.module_key == "valuation")
    assert valuation_high.score is not None and valuation_mid.score is not None
    assert valuation_high.score < valuation_mid.score


def test_pipeline_publisher_creates_sheet_with_module_titles(tmp_path: Path) -> None:
    store = _seed_store(tmp_path / "publish.sqlite3", erp_mode="mid")
    snapshot = _compute(store)

    client = FakeGogClient()
    publisher = GogSheetsPublisher(store, client=client)
    result = publisher.publish(snapshot, [], reset_sheet=False)

    assert result.status == STATUS_CREATED
    blob = repr(client.batches)
    for title in ("Оцінка вартості", "Настрої", "Техніка", "Макро і ліквідність", "Кредит"):
        assert title in blob
