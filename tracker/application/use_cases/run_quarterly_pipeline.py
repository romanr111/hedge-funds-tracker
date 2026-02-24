from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tracker.application.ports.historical_price_gateway import HistoricalPriceGateway
from tracker.application.use_cases.build_risk_filtered_portfolio import build_risk_filtered_portfolio
from tracker.application.use_cases.generate_kpi_report import generate_kpi_report
from tracker.application.use_cases.run_walk_forward_backtest import run_walk_forward_backtest
from tracker.config import PipelineConfig
from tracker.domain.filings import parse_iso_date
from tracker.domain.models import TrendStockSignal
from tracker.domain.portfolio import RiskFilteredSignal, TargetPosition
from tracker.domain.quarters import parse_report_quarter, quarter_sort_key
from tracker.infrastructure.storage.sqlite_state_repository import StateStore


@dataclass(frozen=True)
class QuarterlyPipelineResult:
    run_id: str
    status: str
    as_of_quarter: str | None
    report_dir: Path | None
    quality_status: str | None



def _pipeline_run_id(as_of_quarter: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{as_of_quarter}-{ts}"



def _serialize_pipeline_config(pipeline: PipelineConfig) -> str:
    payload = asdict(pipeline)
    payload["report_dir"] = str(pipeline.report_dir)
    payload["symbol_metadata_file"] = str(pipeline.symbol_metadata_file)
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)



def _default_signal_available_day(quarter: str) -> date:
    parsed = parse_report_quarter(quarter)
    if parsed is None:
        raise ValueError(f"Invalid report quarter: {quarter}")
    year, quarter_idx = parsed
    if quarter_idx == 1:
        quarter_end = date(year, 3, 31)
    elif quarter_idx == 2:
        quarter_end = date(year, 6, 30)
    elif quarter_idx == 3:
        quarter_end = date(year, 9, 30)
    else:
        quarter_end = date(year, 12, 31)
    return quarter_end + timedelta(days=45)


def _signal_available_day(*, store: StateStore, quarter: str) -> date:
    snapshots = store.list_snapshots_for_quarter(quarter)
    filing_days = [
        filed_day
        for snapshot in snapshots
        for filed_day in [parse_iso_date(snapshot.filing_date)]
        if filed_day is not None
    ]
    if not filing_days:
        return _default_signal_available_day(quarter)
    return max(filing_days) + timedelta(days=1)



def run_quarterly_pipeline(
    *,
    store: StateStore,
    history_gateway: HistoricalPriceGateway,
    symbol_map_file: Path,
    pipeline: PipelineConfig,
    as_of_quarter: str | None,
    dry_run_report: bool,
) -> QuarterlyPipelineResult:
    available_quarters = sorted(store.list_trend_quarters(), key=quarter_sort_key)
    if not available_quarters:
        return QuarterlyPipelineResult(
            run_id="",
            status="no_trend_data",
            as_of_quarter=None,
            report_dir=None,
            quality_status=None,
        )

    resolved_as_of = as_of_quarter or available_quarters[-1]
    if resolved_as_of not in available_quarters:
        return QuarterlyPipelineResult(
            run_id="",
            status="invalid_as_of_quarter",
            as_of_quarter=resolved_as_of,
            report_dir=None,
            quality_status=None,
        )

    quarters = [
        quarter
        for quarter in available_quarters
        if quarter_sort_key(quarter) <= quarter_sort_key(resolved_as_of)
    ]
    if not quarters:
        return QuarterlyPipelineResult(
            run_id="",
            status="no_quarters_before_as_of",
            as_of_quarter=resolved_as_of,
            report_dir=None,
            quality_status=None,
        )
    if len(quarters) < pipeline.min_oos_quarters:
        return QuarterlyPipelineResult(
            run_id="",
            status="insufficient_input_quarters",
            as_of_quarter=resolved_as_of,
            report_dir=None,
            quality_status=None,
        )

    run_id = _pipeline_run_id(resolved_as_of)
    created_at = datetime.now(timezone.utc).isoformat()
    config_json = _serialize_pipeline_config(pipeline)

    if not dry_run_report:
        store.upsert_quarterly_pipeline_run(
            run_id=run_id,
            as_of_quarter=resolved_as_of,
            status="running",
            config_json=config_json,
            created_at=created_at,
            finished_at=None,
            notes_json=None,
        )

    risk_signals_by_quarter: dict[str, list[RiskFilteredSignal]] = {}
    positions_by_quarter: dict[str, list[TargetPosition]] = {}
    signal_available_by_quarter: dict[str, date] = {}

    for quarter in quarters:
        signal_available_day = _signal_available_day(store=store, quarter=quarter)
        signal_available_by_quarter[quarter] = signal_available_day
        raw_signals: list[TrendStockSignal] = store.list_trend_stock_signals(quarter)
        if not raw_signals:
            risk_signals_by_quarter[quarter] = []
            positions_by_quarter[quarter] = []
            continue

        built = build_risk_filtered_portfolio(
            report_quarter=quarter,
            signals=raw_signals,
            as_of_trade_date=signal_available_day,
            price_gateway=history_gateway,
            pipeline=pipeline,
            symbol_map_file=symbol_map_file,
        )
        risk_signals_by_quarter[quarter] = built.risk_signals
        positions_by_quarter[quarter] = built.selected_positions

        if not dry_run_report:
            store.replace_quarterly_portfolio_positions(
                run_id,
                quarter,
                [
                    {
                        "instrument_key": signal.instrument_key,
                        "ticker": signal.ticker,
                        "sector": signal.sector,
                        "country": signal.country,
                        "score_raw": signal.score_raw,
                        "score_risk": signal.score_risk,
                        "target_weight": signal.target_weight,
                        "weight_capped": signal.weight_capped,
                        "passed_filters": signal.passed_filters,
                        "filter_reasons": signal.filter_reasons,
                    }
                    for signal in built.risk_signals
                ],
            )

    backtest = run_walk_forward_backtest(
        quarters=quarters,
        signal_available_by_quarter=signal_available_by_quarter,
        positions_by_quarter=positions_by_quarter,
        risk_signals_by_quarter=risk_signals_by_quarter,
        price_gateway=history_gateway,
        pipeline=pipeline,
        benchmark_ticker="SPY",
    )

    all_kpis = list(backtest.overall_kpis) + list(backtest.quarter_kpis)
    if backtest.status != "ok":
        notes_json = json.dumps({"backtest_status": backtest.status}, separators=(",", ":"), ensure_ascii=True)
        if not dry_run_report:
            store.update_quarterly_pipeline_run_status(
                run_id=run_id,
                status=backtest.status,
                finished_at=datetime.now(timezone.utc).isoformat(),
                notes_json=notes_json,
            )
        return QuarterlyPipelineResult(
            run_id=run_id,
            status=backtest.status,
            as_of_quarter=resolved_as_of,
            report_dir=None,
            quality_status=None,
        )

    report_dir, quality_status = generate_kpi_report(
        report_root=pipeline.report_dir,
        run_id=run_id,
        as_of_quarter=resolved_as_of,
        pipeline=pipeline,
        return_rows=backtest.return_rows,
        overall_kpis=backtest.overall_kpis,
        quarter_kpis=backtest.quarter_kpis,
        risk_signals_by_quarter=risk_signals_by_quarter,
        positions_by_quarter=positions_by_quarter,
    )

    if not dry_run_report:
        store.replace_quarterly_return_series(run_id, backtest.return_rows)
        store.replace_quarterly_kpis(
            run_id,
            [
                {
                    "metric": item.metric,
                    "scope": item.scope,
                    "scope_key": item.scope_key,
                    "value": item.value,
                }
                for item in all_kpis
            ],
        )
        store.update_quarterly_pipeline_run_status(
            run_id=run_id,
            status=quality_status.lower(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            notes_json=json.dumps({"backtest_status": backtest.status, "quality_status": quality_status}),
        )

    return QuarterlyPipelineResult(
        run_id=run_id,
        status=backtest.status,
        as_of_quarter=resolved_as_of,
        report_dir=report_dir,
        quality_status=quality_status,
    )
