from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from tracker.domain.models import ManagerQuarterSnapshot, TrendStockSignal
from tracker.domain.quarters import quarter_sort_key
from tracker.domain.trends import TrendSignalRow, compute_trend_signals
from tracker.infrastructure.storage.sqlite_state_repository import StateStore


WINDOW_QUARTERS = 4
TREND_ENGINE_VERSION = "v1.4"


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
    return {
        "engine_version": TREND_ENGINE_VERSION,
        "blend_mode": blend_mode,
        "latest_prices": latest_prices_payload,
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
    resolved_latest_prices = _sanitize_latest_prices(latest_prices)
    if dry_run:
        return TrendEngineResult(status="dry_run", report_quarter=None, signals_count=0)
    if not managers:
        return TrendEngineResult(status="no_managers", report_quarter=None, signals_count=0)

    target_quarter = detect_latest_completed_report_quarter(managers, store)
    if target_quarter is None:
        app_logger.info("Trend engine pending: no completed report quarter for all managers")
        return TrendEngineResult(status="pending_no_completed_quarter", report_quarter=None, signals_count=0)

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
            extra={"target_quarter": target_quarter, "quarters_count": len(quarter_window)},
        )
        return TrendEngineResult(status="pending_insufficient_history", report_quarter=target_quarter, signals_count=0)

    snapshots = store.list_snapshots_for_quarters(quarter_window, ciks)
    snapshots_by_quarter = _build_snapshot_index(snapshots)

    for quarter in quarter_window:
        for manager in managers:
            if manager.cik not in snapshots_by_quarter.get(quarter, {}):
                app_logger.info(
                    "Trend engine pending: incomplete snapshot matrix",
                    extra={"target_quarter": target_quarter, "missing_quarter": quarter, "missing_cik": manager.cik},
                )
                return TrendEngineResult(
                    status="pending_incomplete_snapshot_matrix",
                    report_quarter=target_quarter,
                    signals_count=0,
                )

    input_fingerprint = _fingerprint(
        _build_input_fingerprint_payload(snapshots, blend_mode=blend_mode, latest_prices=resolved_latest_prices)
    )
    previous_run = store.get_trend_run(target_quarter)
    now_iso = datetime.now(timezone.utc).isoformat()

    if not force_recompute and previous_run and previous_run.input_fingerprint == input_fingerprint:
        latest_trend_run_quarter = store.get_latest_trend_run_quarter()
        status = (
            "skipped_no_new_completed_quarter"
            if latest_trend_run_quarter == target_quarter
            else "skipped_unchanged_input"
        )
        store.upsert_trend_run(
            report_quarter=target_quarter,
            input_fingerprint=input_fingerprint,
            top_fingerprint=previous_run.top_fingerprint,
            status=status,
            computed_at=now_iso,
            notes_json=previous_run.notes_json,
        )
        return TrendEngineResult(status=status, report_quarter=target_quarter, signals_count=0)

    manager_weights = {manager.cik: float(manager.weight) for manager in managers}
    computed = compute_trend_signals(
        quarters=quarter_window,
        snapshots_by_quarter=snapshots_by_quarter,
        manager_weights=manager_weights,
        blend_mode=blend_mode,
        latest_prices=resolved_latest_prices,
    )

    top_payload = _build_top_fingerprint_payload(computed.signals)
    top_fingerprint = _fingerprint(top_payload)
    if (
        not force_recompute
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
            notes_json=json.dumps({"top": top_payload, "blend_mode": blend_mode}, separators=(",", ":"), ensure_ascii=True),
        )
        return TrendEngineResult(status="skipped_no_top_change", report_quarter=target_quarter, signals_count=0)

    rows_to_store = [
        TrendStockSignal(
            report_quarter=target_quarter,
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
            computed_at=now_iso,
            freshness_multiplier=row.freshness_multiplier,
            freshness_ok=row.freshness_ok,
        )
        for row in computed.signals
    ]
    store.replace_trend_stock_signals(target_quarter, rows_to_store)
    store.upsert_trend_run(
        report_quarter=target_quarter,
        input_fingerprint=input_fingerprint,
        top_fingerprint=top_fingerprint,
        status="computed",
        computed_at=now_iso,
        notes_json=json.dumps({"top": top_payload, "blend_mode": blend_mode}, separators=(",", ":"), ensure_ascii=True),
    )

    app_logger.info(
        "Trend engine completed",
        extra={"report_quarter": target_quarter, "signals_count": len(rows_to_store)},
    )
    return TrendEngineResult(status="computed", report_quarter=target_quarter, signals_count=len(rows_to_store))
