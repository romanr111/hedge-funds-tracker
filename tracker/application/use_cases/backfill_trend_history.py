from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from tracker.application.use_cases.run_trend_engine import (
    COMPUTE_MODE_BACKFILL,
    TrendEngineResult,
    run_trend_engine_for_target_quarter,
)
from tracker.infrastructure.storage.sqlite_state_repository import StateStore
from tracker.domain.quarters import parse_report_quarter, quarter_sort_key


DEFAULT_BACKFILL_QUARTERS = 9


class _WeightedManagerLike(Protocol):
    cik: str
    weight: float


@dataclass(frozen=True)
class BackfillQuarterResult:
    report_quarter: str
    status: str
    signals_count: int


@dataclass(frozen=True)
class BackfillTrendHistoryResult:
    status: str
    batch_id: str
    quarters_requested: int
    computed: int
    skipped_existing: int
    failed: int
    details: list[BackfillQuarterResult]



def _is_expected_non_error_status(status: str) -> bool:
    return status.startswith("pending_") or status.startswith("skipped_")


def _validate_optional_quarter(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if parse_report_quarter(raw) is None:
        raise ValueError(f"{field_name} must be in format YYYYQn")
    return raw



def _select_backfill_quarters(
    *,
    common_quarters: list[str],
    latest_quarter: str,
    from_quarter: str | None,
    to_quarter: str | None,
    include_latest: bool,
) -> list[str]:
    selected = list(common_quarters)
    if not include_latest:
        selected = [quarter for quarter in selected if quarter != latest_quarter]

    if from_quarter is not None:
        selected = [quarter for quarter in selected if quarter_sort_key(quarter) >= quarter_sort_key(from_quarter)]
    if to_quarter is not None:
        selected = [quarter for quarter in selected if quarter_sort_key(quarter) <= quarter_sort_key(to_quarter)]

    if from_quarter is None and to_quarter is None and len(selected) > DEFAULT_BACKFILL_QUARTERS:
        selected = selected[-DEFAULT_BACKFILL_QUARTERS:]
    return selected



def run_backfill_trend_history(
    managers: list[_WeightedManagerLike],
    store: StateStore,
    *,
    dry_run: bool,
    blend_mode: str = "tactical",
    latest_prices: dict[str, float] | None = None,
    from_quarter: str | None = None,
    to_quarter: str | None = None,
    include_latest: bool = False,
    force_recompute: bool = False,
    logger: logging.Logger | None = None,
) -> BackfillTrendHistoryResult:
    app_logger = logger or logging.getLogger(__name__)
    batch_id = datetime.now(timezone.utc).strftime("backfill-%Y%m%dT%H%M%SZ")

    if dry_run:
        return BackfillTrendHistoryResult(
            status="dry_run",
            batch_id=batch_id,
            quarters_requested=0,
            computed=0,
            skipped_existing=0,
            failed=0,
            details=[],
        )
    if not managers:
        return BackfillTrendHistoryResult(
            status="no_managers",
            batch_id=batch_id,
            quarters_requested=0,
            computed=0,
            skipped_existing=0,
            failed=0,
            details=[],
        )

    normalized_from = _validate_optional_quarter(from_quarter, field_name="--backfill-from-quarter")
    normalized_to = _validate_optional_quarter(to_quarter, field_name="--backfill-to-quarter")
    if normalized_from and normalized_to and quarter_sort_key(normalized_from) > quarter_sort_key(normalized_to):
        raise ValueError("--backfill-from-quarter must be <= --backfill-to-quarter")

    ciks = [manager.cik for manager in managers]
    common_quarters = sorted(store.list_common_report_quarters(ciks), key=quarter_sort_key)
    if not common_quarters:
        return BackfillTrendHistoryResult(
            status="pending_no_completed_quarter",
            batch_id=batch_id,
            quarters_requested=0,
            computed=0,
            skipped_existing=0,
            failed=0,
            details=[],
        )

    latest_quarter = common_quarters[-1]
    quarters = _select_backfill_quarters(
        common_quarters=common_quarters,
        latest_quarter=latest_quarter,
        from_quarter=normalized_from,
        to_quarter=normalized_to,
        include_latest=include_latest,
    )
    if not quarters:
        return BackfillTrendHistoryResult(
            status="no_target_quarters",
            batch_id=batch_id,
            quarters_requested=0,
            computed=0,
            skipped_existing=0,
            failed=0,
            details=[],
        )

    details: list[BackfillQuarterResult] = []
    computed = 0
    skipped_existing = 0
    failed = 0

    for quarter in quarters:
        if not force_recompute and store.has_trend_signals_for_quarter(quarter):
            details.append(BackfillQuarterResult(report_quarter=quarter, status="skipped_existing_quarter", signals_count=0))
            skipped_existing += 1
            continue

        result: TrendEngineResult = run_trend_engine_for_target_quarter(
            managers,
            store,
            target_quarter=quarter,
            dry_run=dry_run,
            blend_mode=blend_mode,
            latest_prices=latest_prices,
            force_recompute=force_recompute,
            compute_mode=COMPUTE_MODE_BACKFILL,
            backfill_batch_id=batch_id,
            skip_if_exists=not force_recompute,
            logger=app_logger,
        )
        details.append(
            BackfillQuarterResult(
                report_quarter=quarter,
                status=result.status,
                signals_count=result.signals_count,
            )
        )
        if result.status == "computed":
            computed += 1
        elif result.status == "skipped_existing_quarter":
            skipped_existing += 1
        elif _is_expected_non_error_status(result.status):
            continue
        else:
            failed += 1

    if failed > 0 and computed == 0:
        status = "failed"
    elif failed > 0:
        status = "completed_with_errors"
    else:
        status = "completed"

    return BackfillTrendHistoryResult(
        status=status,
        batch_id=batch_id,
        quarters_requested=len(quarters),
        computed=computed,
        skipped_existing=skipped_existing,
        failed=failed,
        details=details,
    )
