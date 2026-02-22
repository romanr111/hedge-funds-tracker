from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from tracker.config import PipelineConfig
from tracker.domain.portfolio import PipelineKPI, RiskFilteredSignal, TargetPosition



def _max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for item in returns:
        equity *= 1.0 + item
        if equity > peak:
            peak = equity
        if peak > 0:
            drawdown = (peak - equity) / peak
            max_dd = max(max_dd, drawdown)
    return max_dd



def _write_csv(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(rows)



def _quality_status(
    *,
    overall_kpis: list[PipelineKPI],
    quarter_kpis: list[PipelineKPI],
    return_rows: list[dict[str, object]],
) -> tuple[str, list[str]]:
    overall = {item.metric: item.value for item in overall_kpis if item.scope == "overall"}
    observed_quarters = {item.scope_key for item in quarter_kpis if item.scope == "quarter" and item.scope_key}

    if len(observed_quarters) < 8:
        return ("INSUFFICIENT_SAMPLE", ["oos_quarters_below_8"])

    benchmark_returns = [float(item["benchmark_return"]) for item in return_rows]
    benchmark_mdd = _max_drawdown(benchmark_returns)
    strategy_mdd = float(overall.get("max_drawdown", 0.0))

    checks = {
        "excess_return_vs_spy": float(overall.get("excess_return_vs_spy", -1.0)) >= 0.0,
        "mean_ic": float(overall.get("mean_ic", -1.0)) > 0.0,
        "hit_rate_vs_spy": float(overall.get("hit_rate_vs_spy", 0.0)) >= 0.50,
        "max_drawdown": strategy_mdd <= (1.25 * benchmark_mdd if benchmark_mdd > 0 else strategy_mdd),
    }
    failed = [metric for metric, passed in checks.items() if not passed]
    if not failed:
        return ("PASS", [])
    if len(failed) <= 2:
        return ("WATCH", failed)
    return ("FAIL", failed)



def generate_kpi_report(
    *,
    report_root: Path,
    run_id: str,
    as_of_quarter: str,
    pipeline: PipelineConfig,
    return_rows: list[dict[str, object]],
    overall_kpis: list[PipelineKPI],
    quarter_kpis: list[PipelineKPI],
    risk_signals_by_quarter: dict[str, list[RiskFilteredSignal]],
    positions_by_quarter: dict[str, list[TargetPosition]],
) -> tuple[Path, str]:
    report_dir = report_root / run_id
    report_dir.mkdir(parents=True, exist_ok=True)

    overall_rows = [[item.metric, item.scope, item.scope_key or "", item.value] for item in overall_kpis]
    _write_csv(report_dir / "kpi_overall.csv", ["metric", "scope", "scope_key", "value"], overall_rows)

    quarter_rows = [[item.metric, item.scope, item.scope_key or "", item.value] for item in quarter_kpis]
    _write_csv(report_dir / "kpi_by_quarter.csv", ["metric", "scope", "scope_key", "value"], quarter_rows)

    return_series_rows = [
        [
            item["date"],
            item["strategy_gross_return"],
            item["strategy_net_return"],
            item["benchmark_return"],
            item["turnover"],
        ]
        for item in return_rows
    ]
    _write_csv(
        report_dir / "return_series.csv",
        ["date", "strategy_gross_return", "strategy_net_return", "benchmark_return", "turnover"],
        return_series_rows,
    )

    for quarter, positions in positions_by_quarter.items():
        rows = [[item.report_quarter, item.instrument_key, item.ticker, item.target_weight, item.weight_capped] for item in positions]
        _write_csv(
            report_dir / f"portfolio_weights_{quarter}.csv",
            ["report_quarter", "instrument_key", "ticker", "target_weight", "weight_capped"],
            rows,
        )

    filter_rows: list[list[object]] = []
    for quarter, signals in risk_signals_by_quarter.items():
        for signal in signals:
            filter_rows.append(
                [
                    quarter,
                    signal.instrument_key,
                    signal.ticker,
                    signal.passed_filters,
                    ";".join(signal.filter_reasons),
                    signal.score_raw,
                    signal.score_risk,
                    signal.target_weight,
                    signal.weight_capped,
                ]
            )
    _write_csv(
        report_dir / "filter_diagnostics.csv",
        [
            "report_quarter",
            "instrument_key",
            "ticker",
            "passed_filters",
            "filter_reasons",
            "score_raw",
            "score_risk",
            "target_weight",
            "weight_capped",
        ],
        filter_rows,
    )

    config_payload = {
        "as_of_quarter": as_of_quarter,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline": asdict(pipeline),
    }
    config_payload["pipeline"]["report_dir"] = str(pipeline.report_dir)
    config_payload["pipeline"]["symbol_metadata_file"] = str(pipeline.symbol_metadata_file)
    (report_dir / "config_snapshot.json").write_text(json.dumps(config_payload, indent=2, ensure_ascii=True))

    quality_status, failed_checks = _quality_status(
        overall_kpis=overall_kpis,
        quarter_kpis=quarter_kpis,
        return_rows=return_rows,
    )
    summary_lines = [
        f"# Quarterly pipeline summary ({run_id})",
        "",
        f"- As of quarter: `{as_of_quarter}`",
        f"- Quality status: `{quality_status}`",
        f"- Failed checks: `{', '.join(failed_checks) if failed_checks else 'none'}`",
        f"- Overall KPIs: `{len(overall_kpis)}`",
        f"- Quarter KPIs: `{len(quarter_kpis)}`",
        f"- Return rows: `{len(return_rows)}`",
    ]
    (report_dir / "summary.md").write_text("\n".join(summary_lines) + "\n")

    return (report_dir, quality_status)
