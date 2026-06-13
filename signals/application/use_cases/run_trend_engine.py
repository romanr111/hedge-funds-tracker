from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from signals.domain.models import ManagerQuarterSnapshot, TrendStockSignal
from signals.domain.quarters import quarter_sort_key
from signals.domain.trends import TrendSignalRow, compute_trend_signals
from signals.infrastructure.storage.sqlite_state_repository import StateStore


WINDOW_QUARTERS = 4
TREND_ENGINE_VERSION = "v1.5"
COMPUTE_MODE_LATEST = "latest"
COMPUTE_MODE_BACKFILL = "backfill"
OPTION_SIGNAL_LIMIT_PER_TYPE = 5
OPTION_TYPES = {"CALL", "PUT"}


class _WeightedManagerLike(Protocol):
    cik: str
    weight: float


@dataclass(frozen=True)
class TrendEngineResult:
    status: str
    report_quarter: str | None
    signals_count: int



def _fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_put_call(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized in OPTION_TYPES else None


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _option_rank(position: dict[str, object]) -> tuple[int, int, str]:
    volume = _positive_int(position.get("shares"))
    value = _positive_int(position.get("value")) or 0
    rank_value = volume if volume is not None else value
    return (rank_value, value, str(position.get("cusip") or ""))


def _select_top_option_positions(
    positions: list[dict[str, object]],
    *,
    per_type_limit: int = OPTION_SIGNAL_LIMIT_PER_TYPE,
) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = {"CALL": [], "PUT": []}
    for position in positions:
        put_call = _normalize_put_call(position.get("put_call"))
        if put_call is None:
            continue
        normalized = dict(position)
        normalized["put_call"] = put_call
        grouped[put_call].append(normalized)

    selected: list[dict[str, object]] = []
    for put_call in ("CALL", "PUT"):
        ranked = sorted(grouped[put_call], key=_option_rank, reverse=True)
        selected.extend(ranked[:per_type_limit])
    return selected


def _option_snapshots(snapshots: list[ManagerQuarterSnapshot]) -> list[ManagerQuarterSnapshot]:
    option_rows: list[ManagerQuarterSnapshot] = []
    for snapshot in snapshots:
        positions = _select_top_option_positions(snapshot.positions)
        aum_value_k = sum(
            value
            for position in positions
            for value in [position.get("value")]
            if isinstance(value, int) and value > 0
        )
        option_rows.append(
            ManagerQuarterSnapshot(
                cik=snapshot.cik,
                manager_name=snapshot.manager_name,
                report_quarter=snapshot.report_quarter,
                report_date=snapshot.report_date,
                filing_date=snapshot.filing_date,
                acceptance_datetime=snapshot.acceptance_datetime,
                accession=snapshot.accession,
                source_form=snapshot.source_form,
                positions=positions,
                aum_value_k=aum_value_k,
                positions_count=len(positions),
                updated_at=snapshot.updated_at,
            )
        )
    return option_rows


def _has_option_positions(snapshots: list[ManagerQuarterSnapshot]) -> bool:
    return any(_select_top_option_positions(snapshot.positions) for snapshot in snapshots)


def _row_to_signal(
    row: TrendSignalRow,
    *,
    report_quarter: str,
    computed_at: str,
    is_backfill: bool,
    backfill_batch_id: str | None,
) -> TrendStockSignal:
    return TrendStockSignal(
        report_quarter=report_quarter,
        instrument_key=row.instrument_key,
        cusip=row.cusip,
        put_call=row.put_call,
        issuer_name=row.issuer_name,
        title=row.title,
        np_raw=row.np_raw,
        np_adj=row.np_adj,
        impulse_score=row.impulse_score,
        accumulation_score=row.accumulation_score,
        confidence=row.confidence,
        trend_ewma=row.trend_ewma,
        trend_delta=row.trend_delta,
        breadth_buy_weight=row.breadth_buy_weight,
        breadth_sell_weight=row.breadth_sell_weight,
        buy_managers=row.buy_managers,
        sell_managers=row.sell_managers,
        crowding_hhi=row.crowding_hhi,
        persistence_buy=row.persistence_buy,
        persistence_sell=row.persistence_sell,
        regime=row.regime,
        contributors_json=json.dumps(row.contributors, separators=(",", ":"), ensure_ascii=True),
        computed_at=computed_at,
        freshness_multiplier=row.freshness_multiplier,
        freshness_ok=row.freshness_ok,
        is_backfill=is_backfill,
        backfill_batch_id=backfill_batch_id,
    )



def detect_latest_completed_report_quarter(
    managers: list[_WeightedManagerLike],
    store: StateStore,
) -> str | None:
    ciks = [manager.cik for manager in managers]
    common_quarters = store.list_common_report_quarters(ciks)
    if not common_quarters:
        return None
    return common_quarters[-1]



def _build_snapshot_index(snapshots: list[ManagerQuarterSnapshot]) -> dict[str, dict[str, ManagerQuarterSnapshot]]:
    by_quarter: dict[str, dict[str, ManagerQuarterSnapshot]] = {}
    for snapshot in snapshots:
        by_quarter.setdefault(snapshot.report_quarter, {})[snapshot.cik] = snapshot
    return by_quarter



def _build_input_fingerprint_payload(
    snapshots: list[ManagerQuarterSnapshot],
    managers: list[_WeightedManagerLike],
    *,
    blend_mode: str,
    latest_prices: dict[str, float] | None,
) -> dict[str, object]:
    latest_prices_payload = {
        key.strip().upper(): round(float(value), 8)
        for key, value in (latest_prices or {}).items()
        if isinstance(key, str) and isinstance(value, (int, float)) and float(value) > 0
    }
    payload: list[dict[str, str | None]] = []
    for snapshot in sorted(snapshots, key=lambda item: (item.report_quarter, item.cik)):
        payload.append(
            {
                "quarter": snapshot.report_quarter,
                "cik": snapshot.cik,
                "accession": snapshot.accession,
                "report_date": snapshot.report_date,
                "filing_date": snapshot.filing_date,
                "acceptance_datetime": snapshot.acceptance_datetime,
            }
        )
    manager_weights_payload = {
        manager.cik: round(float(manager.weight), 8)
        for manager in sorted(managers, key=lambda m: m.cik)
    }
    return {
        "engine_version": TREND_ENGINE_VERSION,
        "blend_mode": blend_mode,
        "latest_prices": latest_prices_payload,
        "manager_weights": manager_weights_payload,
        "snapshots": payload,
    }



def _sanitize_latest_prices(latest_prices: dict[str, float] | None) -> dict[str, float] | None:
    if not latest_prices:
        return None
    normalized: dict[str, float] = {}
    for raw_key, raw_price in latest_prices.items():
        if not isinstance(raw_key, str):
            continue
        if not isinstance(raw_price, (int, float)):
            continue
        value = float(raw_price)
        if value <= 0:
            continue
        key = raw_key.strip().upper()
        if key:
            normalized[key] = value
    return normalized or None



def _build_top_fingerprint_payload(signals: list[TrendSignalRow], *, limit: int = 20) -> dict[str, list[str]]:
    top_buy = [
        row.instrument_key
        for row in sorted(
            (item for item in signals if "BUY" in item.regime and item.regime != "REVERSAL_SELL"),
            key=lambda item: (-item.trend_ewma, item.instrument_key),
        )[:limit]
    ]
    top_sell = [
        row.instrument_key
        for row in sorted(
            (item for item in signals if "SELL" in item.regime and item.regime != "REVERSAL_BUY"),
            key=lambda item: (item.trend_ewma, item.instrument_key),
        )[:limit]
    ]
    reversals = [
        row.instrument_key
        for row in sorted(
            (item for item in signals if item.regime in {"REVERSAL_BUY", "REVERSAL_SELL"}),
            key=lambda item: (-abs(item.trend_delta), item.instrument_key),
        )
    ][:limit]
    return {"top_buy": top_buy, "top_sell": top_sell, "reversals": reversals}



def run_trend_engine_for_target_quarter(
    managers: list[_WeightedManagerLike],
    store: StateStore,
    *,
    target_quarter: str,
    dry_run: bool,
    blend_mode: str = "tactical",
    latest_prices: dict[str, float] | None = None,
    force_recompute: bool = False,
    compute_mode: str = COMPUTE_MODE_LATEST,
    backfill_batch_id: str | None = None,
    skip_if_exists: bool = False,
    logger: logging.Logger | None = None,
) -> TrendEngineResult:
    app_logger = logger or logging.getLogger(__name__)
    resolved_latest_prices = _sanitize_latest_prices(latest_prices)
    if dry_run:
        return TrendEngineResult(status="dry_run", report_quarter=target_quarter, signals_count=0)
    if not managers:
        return TrendEngineResult(status="no_managers", report_quarter=target_quarter, signals_count=0)
    if compute_mode not in {COMPUTE_MODE_LATEST, COMPUTE_MODE_BACKFILL}:
        raise ValueError("compute_mode must be 'latest' or 'backfill'.")

    ciks = [manager.cik for manager in managers]
    common_quarters = store.list_common_report_quarters(ciks)
    common_quarters = sorted(common_quarters, key=quarter_sort_key)
    if target_quarter not in common_quarters:
        return TrendEngineResult(status="pending_no_target_quarter", report_quarter=target_quarter, signals_count=0)

    target_idx = common_quarters.index(target_quarter)
    quarter_window = common_quarters[max(0, target_idx - (WINDOW_QUARTERS - 1)) : target_idx + 1]
    if len(quarter_window) < 2:
        app_logger.info(
            "Trend engine pending: insufficient quarter history",
            extra={"target_quarter": target_quarter, "quarters_count": len(quarter_window), "compute_mode": compute_mode},
        )
        return TrendEngineResult(status="pending_insufficient_history", report_quarter=target_quarter, signals_count=0)

    snapshots = store.list_snapshots_for_quarters(quarter_window, ciks)
    snapshots_by_quarter = _build_snapshot_index(snapshots)
    option_rows_expected = _has_option_positions(snapshots)
    if (
        skip_if_exists
        and store.has_trend_signals_for_quarter(target_quarter)
        and (not option_rows_expected or store.has_trend_option_signals_for_quarter(target_quarter))
    ):
        return TrendEngineResult(status="skipped_existing_quarter", report_quarter=target_quarter, signals_count=0)

    for quarter in quarter_window:
        for manager in managers:
            if manager.cik not in snapshots_by_quarter.get(quarter, {}):
                app_logger.info(
                    "Trend engine pending: incomplete snapshot matrix",
                    extra={
                        "target_quarter": target_quarter,
                        "missing_quarter": quarter,
                        "missing_cik": manager.cik,
                        "compute_mode": compute_mode,
                    },
                )
                return TrendEngineResult(
                    status="pending_incomplete_snapshot_matrix",
                    report_quarter=target_quarter,
                    signals_count=0,
                )

    manager_weights = {manager.cik: float(manager.weight) for manager in managers}
    input_fingerprint = _fingerprint(
        _build_input_fingerprint_payload(snapshots, list(managers), blend_mode=blend_mode, latest_prices=resolved_latest_prices)
    )
    previous_run = store.get_trend_run(target_quarter)
    now_iso = datetime.now(timezone.utc).isoformat()
    is_backfill = compute_mode == COMPUTE_MODE_BACKFILL
    option_rows_missing = option_rows_expected and not store.has_trend_option_signals_for_quarter(target_quarter)

    if not force_recompute and not option_rows_missing and previous_run and previous_run.input_fingerprint == input_fingerprint:
        latest_trend_run_quarter = store.get_latest_trend_run_quarter()
        status = (
            "skipped_no_new_completed_quarter"
            if compute_mode == COMPUTE_MODE_LATEST and latest_trend_run_quarter == target_quarter
            else "skipped_unchanged_input"
        )
        store.upsert_trend_run(
            report_quarter=target_quarter,
            input_fingerprint=input_fingerprint,
            top_fingerprint=previous_run.top_fingerprint,
            status=status,
            computed_at=now_iso,
            notes_json=previous_run.notes_json,
            is_backfill=is_backfill,
            backfill_batch_id=backfill_batch_id,
        )
        return TrendEngineResult(status=status, report_quarter=target_quarter, signals_count=0)
    computed = compute_trend_signals(
        quarters=quarter_window,
        snapshots_by_quarter=snapshots_by_quarter,
        manager_weights=manager_weights,
        blend_mode=blend_mode,
        latest_prices=resolved_latest_prices,
    )
    stock_rows = [row for row in computed.signals if row.put_call is None]
    option_computed = compute_trend_signals(
        quarters=quarter_window,
        snapshots_by_quarter=_build_snapshot_index(_option_snapshots(snapshots)),
        manager_weights=manager_weights,
        blend_mode=blend_mode,
        latest_prices=None,
    )

    top_payload = {
        "stock": _build_top_fingerprint_payload(stock_rows),
        "options": _build_top_fingerprint_payload(option_computed.signals),
    }
    top_fingerprint = _fingerprint(top_payload)
    if (
        not force_recompute
        and not option_rows_missing
        and resolved_latest_prices is None
        and previous_run
        and previous_run.top_fingerprint == top_fingerprint
    ):
        store.upsert_trend_run(
            report_quarter=target_quarter,
            input_fingerprint=input_fingerprint,
            top_fingerprint=top_fingerprint,
            status="skipped_no_top_change",
            computed_at=now_iso,
            notes_json=json.dumps(
                {"top": top_payload, "blend_mode": blend_mode, "compute_mode": compute_mode},
                separators=(",", ":"),
                ensure_ascii=True,
            ),
            is_backfill=is_backfill,
            backfill_batch_id=backfill_batch_id,
        )
        return TrendEngineResult(status="skipped_no_top_change", report_quarter=target_quarter, signals_count=0)

    rows_to_store = [
        _row_to_signal(
            row,
            report_quarter=target_quarter,
            computed_at=now_iso,
            is_backfill=is_backfill,
            backfill_batch_id=backfill_batch_id,
        )
        for row in stock_rows
    ]
    option_rows_to_store = [
        _row_to_signal(
            row,
            report_quarter=target_quarter,
            computed_at=now_iso,
            is_backfill=is_backfill,
            backfill_batch_id=backfill_batch_id,
        )
        for row in option_computed.signals
    ]
    store.replace_trend_stock_signals(target_quarter, rows_to_store)
    store.replace_trend_option_signals(target_quarter, option_rows_to_store)
    store.upsert_trend_run(
        report_quarter=target_quarter,
        input_fingerprint=input_fingerprint,
        top_fingerprint=top_fingerprint,
        status="computed",
        computed_at=now_iso,
        notes_json=json.dumps(
            {"top": top_payload, "blend_mode": blend_mode, "compute_mode": compute_mode},
            separators=(",", ":"),
            ensure_ascii=True,
        ),
        is_backfill=is_backfill,
        backfill_batch_id=backfill_batch_id,
    )

    app_logger.info(
        "Trend engine completed",
        extra={
            "report_quarter": target_quarter,
            "signals_count": len(rows_to_store),
            "option_signals_count": len(option_rows_to_store),
            "compute_mode": compute_mode,
            "backfill_batch_id": backfill_batch_id,
        },
    )
    return TrendEngineResult(
        status="computed",
        report_quarter=target_quarter,
        signals_count=len(rows_to_store) + len(option_rows_to_store),
    )



def run_trend_engine_for_latest_completed_quarter(
    managers: list[_WeightedManagerLike],
    store: StateStore,
    *,
    dry_run: bool,
    blend_mode: str = "tactical",
    latest_prices: dict[str, float] | None = None,
    force_recompute: bool = False,
    logger: logging.Logger | None = None,
) -> TrendEngineResult:
    app_logger = logger or logging.getLogger(__name__)
    if dry_run:
        return TrendEngineResult(status="dry_run", report_quarter=None, signals_count=0)
    if not managers:
        return TrendEngineResult(status="no_managers", report_quarter=None, signals_count=0)

    target_quarter = detect_latest_completed_report_quarter(managers, store)
    if target_quarter is None:
        app_logger.info("Trend engine pending: no completed report quarter for all managers")
        return TrendEngineResult(status="pending_no_completed_quarter", report_quarter=None, signals_count=0)

    return run_trend_engine_for_target_quarter(
        managers,
        store,
        target_quarter=target_quarter,
        dry_run=dry_run,
        blend_mode=blend_mode,
        latest_prices=latest_prices,
        force_recompute=force_recompute,
        compute_mode=COMPUTE_MODE_LATEST,
        backfill_batch_id=None,
        skip_if_exists=False,
        logger=app_logger,
    )
