"""Use case: collect vintages, compute the dashboard, persist and publish it.

Signature contract (per the orchestrator's approved plan):

    run_market_intel(config, runtime, *, dry_run, no_publish, reset_sheet,
                     skip_sources: Sequence[str] = ()) -> RunResult

The sibling use cases `collect_vintages` and `compute_dashboard` are owned by
the parallel data/compute agents and are imported from the `use_cases`
package. While their modules are not yet present in a branch state, module
attributes stay `None` and tests monkeypatch them; integration wires the real
imports without touching this file.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from signals.domain.timing import now_kyiv

from market_intel.domain.exceptions import MarketIntelError
from market_intel.domain.models import (
    GaugeHistoryPoint,
    RunResult,
)

try:
    # Agent A (data layer): market_intel/application/use_cases/collect_vintages.py
    # -> collect_vintages(store, fetchers, series_specs, *, skip_sources, now_utc)
    from market_intel.application.use_cases.collect_vintages import collect_vintages
except ModuleNotFoundError:  # pragma: no cover - until integration lands
    try:
        from market_intel.application.use_cases import collect_vintages
    except ImportError:
        collect_vintages = None

try:
    # Agent B (compute layer): market_intel/application/use_cases/compute_dashboard.py
    # -> compute_dashboard(store, series_specs, *, as_of, now_kyiv_datetime)
    from market_intel.application.use_cases.compute_dashboard import compute_dashboard
except ModuleNotFoundError:  # pragma: no cover - until integration lands
    try:
        from market_intel.application.use_cases import compute_dashboard
    except ImportError:
        compute_dashboard = None

from market_intel.registries import SERIES_SPECS

if TYPE_CHECKING:
    from market_intel.composition import MarketIntelRuntime
    from market_intel.config import MarketIntelConfig


def run_market_intel(
    config: MarketIntelConfig,
    runtime: MarketIntelRuntime,
    *,
    dry_run: bool,
    no_publish: bool,
    reset_sheet: bool,
    skip_sources: Sequence[str] = (),
) -> RunResult:
    """Run one full pipeline pass: collect -> compute -> persist -> publish."""
    del config  # composition already applied config; CLI wiring keeps parity.

    if collect_vintages is None or compute_dashboard is None:
        raise MarketIntelError(
            "collect_vintages/compute_dashboard не підключені: очікується "
            "інтеграція паралельних агентських гілок (data/compute)."
        )

    now_utc = datetime.now(timezone.utc)
    kyiv_now = now_kyiv()
    as_of = kyiv_now.date()

    collected = collect_vintages(
        runtime.store,
        runtime.fetchers,
        SERIES_SPECS,
        skip_sources=skip_sources,
        now_utc=now_utc,
    )
    dashboard = compute_dashboard(
        runtime.store,
        SERIES_SPECS,
        as_of=as_of,
        now_kyiv_datetime=kyiv_now,
    )

    for module in dashboard.modules:
        runtime.store.upsert_gauge_history(
            GaugeHistoryPoint(
                run_date=as_of,
                module_key=module.module_key,
                score=module.score,
                state_key=module.state_key,
                members_used=module.members_used,
                members_total=module.members_total,
                computed_at=kyiv_now,
            )
        )

    publish = None
    if not dry_run and not no_publish and runtime.publisher is not None:
        publish = runtime.publisher.publish(
            dashboard,
            runtime.store.read_gauge_history(),
            reset_sheet=reset_sheet,
        )

    return RunResult(collected=collected, dashboard=dashboard, publish=publish)
