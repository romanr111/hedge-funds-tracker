"""Use case: collect vintages, compute the dashboard, persist and publish it.

Contract:

    run_market_intel(config, runtime, *, dry_run, no_publish, reset_sheet,
                     skip_sources: Sequence[str] = ()) -> RunResult

Collection and compute run unconditionally; gauge persistence and publishing
are skipped when no external source succeeded, so a fully degraded run never
records stale numbers as today's result.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from signals.domain.timing import now_kyiv

from market_intel.domain.exceptions import MarketIntelError
from market_intel.domain.models import (
    SKIP_COMPUTED,
    SKIP_MANUAL,
    SKIP_NO_ADAPTER,
    CollectionResult,
    GaugeHistoryPoint,
    RunResult,
)

try:
    # Preferred module path; the fallback keeps partial checkouts importable.
    from market_intel.application.use_cases.collect_vintages import collect_vintages
except ModuleNotFoundError:  # pragma: no cover - partial checkout only
    try:
        from market_intel.application.use_cases import collect_vintages
    except ImportError:
        collect_vintages = None

try:
    # Preferred module path; the fallback keeps partial checkouts importable.
    from market_intel.application.use_cases.compute_dashboard import compute_dashboard
except ModuleNotFoundError:  # pragma: no cover - partial checkout only
    try:
        from market_intel.application.use_cases import compute_dashboard
    except ImportError:
        compute_dashboard = None

from market_intel.registries import SERIES_SPECS

if TYPE_CHECKING:
    from market_intel.composition import MarketIntelRuntime
    from market_intel.config import MarketIntelConfig

# Skip codes that are benign: a manual skip, a computed series deferred to the
# compute stage, or a source with no adapter - none is an upstream fetch
# failure, so none means "the external world is down".
_HANDLED_SKIP_CODES: frozenset[str] = frozenset(
    {SKIP_MANUAL, SKIP_COMPUTED, SKIP_NO_ADAPTER}
)


def _all_external_sources_failed(collected: CollectionResult) -> bool:
    """True when nothing was fetched and skips were not all benign.

    A run in this state has no fresh data: publishing it would overwrite the
    sheet with numbers computed from what the store happened to hold, so the
    use case stops before any persistence or publish side effect.
    """
    if collected.fetched:
        return False
    unhandled = [
        skip for skip in collected.skipped if skip.code not in _HANDLED_SKIP_CODES
    ]
    return bool(unhandled) or bool(collected.errors)


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

    if _all_external_sources_failed(collected):
        # No fresh input reached the store. Compute still ran (the CLI prints
        # the dashboard), but persisting a gauge point or publishing now would
        # record stale or empty data as today's result.
        return RunResult(
            collected=collected,
            dashboard=dashboard,
            publish=None,
            all_external_sources_failed=True,
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
