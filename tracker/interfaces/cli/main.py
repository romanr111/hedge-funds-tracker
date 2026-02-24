from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tracker.application.ports.notifier import NotifierPort
from tracker.application.use_cases.notify_quarterly_reports_completion import (
    notify_if_all_reports_published_for_current_quarter,
)
from tracker.application.use_cases.backfill_trend_history import run_backfill_trend_history
from tracker.application.use_cases.run_quarterly_pipeline import run_quarterly_pipeline
from tracker.application.use_cases.run_trend_engine import run_trend_engine_for_latest_completed_quarter
from tracker.application.use_cases.sync_quarter_snapshots import sync_quarter_snapshots
from tracker.application.use_cases.track_manager import process_manager
from tracker.composition import build_notifier_list, build_runtime
from tracker.config import load_config
from tracker.domain.exceptions import StateStoreError
from tracker.domain.models import Manager
from tracker.domain.quarters import parse_report_quarter, quarter_sort_key
from tracker.domain.timing import format_local_datetime, now_kyiv
from tracker.infrastructure.logging import configure_logging, log_context, new_trace_id
from tracker.infrastructure.market import StooqPriceGateway

SELL_TABLE_MIN_CONF = 0.35
BUY_TABLE_MIN_TREND = 0.001
IDEAS_OUTPUT_MAX_ROWS = 8
PORTFOLIO_VALUE_HOLD_BAND = 0.075
PORTFOLIO_SHARES_HOLD_BAND = 0.075
PIPELINE_AUTO_BACKFILL_MIN_QUARTERS = 8
PIPELINE_HARD_FAIL_STATUSES = {
    "invalid_as_of_quarter",
    "no_trend_data",
    "no_quarters",
    "no_quarters_before_as_of",
    "insufficient_input_quarters",
    "partial_data",
}


@dataclass(frozen=True)
class _PortfolioValueTrendSummary:
    report_quarter: str
    previous_quarter: str
    selected_managers: int
    analyzed_managers: int
    missing_current: int
    missing_previous: int
    growing_managers: int
    holding_managers: int
    reducing_managers: int
    previous_total_value_k: int
    current_total_value_k: int
    shares_analyzed_managers: int
    shares_growing_managers: int
    shares_holding_managers: int
    shares_reducing_managers: int
    previous_total_shares: int
    current_total_shares: int


def _send_notifications(notifiers: Sequence[NotifierPort], subject: str, body: str) -> None:
    for notifier in notifiers:
        notifier.send(subject, body)


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def render(parts: list[str]) -> str:
        return " | ".join(part.ljust(widths[idx]) for idx, part in enumerate(parts))

    separator = "-+-".join("-" * width for width in widths)
    output = [render(headers), separator]
    output.extend(render(row) for row in rows)
    return "\n".join(output)


def _load_symbol_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    mapping: dict[str, str] = {}
    for raw_key, raw_value in payload.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            continue
        key = raw_key.strip().upper()
        value = raw_value.strip().upper()
        if key and value:
            mapping[key] = value
    return mapping


def _ticker_for_signal(signal: Any, symbol_map: dict[str, str]) -> str:
    keys = [
        (signal.instrument_key or "").strip().upper(),
        (signal.cusip or "").strip().upper(),
    ]
    for key in keys:
        if key and key in symbol_map:
            return symbol_map[key]
    for key in keys:
        if key:
            return key
    return "UNKNOWN"


def _action_for_signal(signal: Any) -> str:
    target = _target_confidence_for_signal(signal)
    regime = (signal.regime or "").upper()
    confidence = float(signal.confidence)
    target_gap_pp = (target - confidence) * 100.0

    if regime.startswith("STRONG_") and confidence >= target:
        if regime.endswith("_BUY"):
            return "BUY"
        if regime.endswith("_SELL"):
            return "SELL"
    has_direction = regime.endswith("_BUY") or regime.endswith("_SELL")
    if has_direction and target_gap_pp <= 5.0 + 1e-9:
        return "INTERESTING_IDEA"
    return "MONITOR"


def _setup_for_signal(signal: Any) -> str:
    regime = (signal.regime or "").upper()
    if regime.startswith("STRONG_"):
        return "Strong"
    if regime.startswith("REVERSAL_"):
        return "Reversal"
    if regime.startswith("EMERGING_"):
        return "Emerging"
    if regime.startswith("WEAKENING_"):
        return "Weakening"
    return "Unknown"


def _target_confidence_for_signal(signal: Any) -> float:
    regime = (signal.regime or "").upper()
    if regime.startswith("STRONG_"):
        return 0.65
    if regime in {"REVERSAL_SELL", "EMERGING_SELL"}:
        return SELL_TABLE_MIN_CONF
    if regime in {"REVERSAL_BUY", "EMERGING_BUY"}:
        return 0.45
    if regime.startswith("WEAKENING_"):
        return 0.50
    return 0.50


def _conviction_target_for_signal(signal: Any) -> str:
    confidence_pct = round(float(signal.confidence) * 100)
    target_pct = round(_target_confidence_for_signal(signal) * 100)
    return f"{confidence_pct}% (Target: {target_pct}%)"


def _freshness_icon(signal: Any) -> str:
    freshness_ok = getattr(signal, "freshness_ok", None)
    if freshness_ok is None:
        return "❌"
    return "✅" if bool(freshness_ok) else "❌"


def _print_section(title: str, headers: list[str], rows: list[list[str]]) -> None:
    print()
    print(title)
    if not rows:
        print("(empty)")
        return
    print(_format_table(headers, rows))


def _previous_report_quarter(report_quarter: str) -> str | None:
    parsed = parse_report_quarter(report_quarter)
    if parsed is None:
        return None
    year, quarter = parsed
    if quarter == 1:
        return f"{year - 1}Q4"
    return f"{year}Q{quarter - 1}"


def _portfolio_value_change_ratio(previous_value_k: int, current_value_k: int) -> float:
    if previous_value_k <= 0:
        return 0.0 if current_value_k <= 0 else 1.0
    return (current_value_k - previous_value_k) / previous_value_k


def _portfolio_value_direction(change_ratio: float) -> str:
    if change_ratio > PORTFOLIO_VALUE_HOLD_BAND:
        return "Growing"
    if change_ratio < -PORTFOLIO_VALUE_HOLD_BAND:
        return "Reducing"
    return "Holding"


def _portfolio_shares_direction(change_ratio: float) -> str:
    if change_ratio > PORTFOLIO_SHARES_HOLD_BAND:
        return "Growing"
    if change_ratio < -PORTFOLIO_SHARES_HOLD_BAND:
        return "Reducing"
    return "Holding"


def _format_value_k(value_k: int) -> str:
    value_billions = round(value_k / 1_000_000_000)
    return f"${value_billions:,}B"


def _format_int(value: int) -> str:
    return f"{value:,}"


def _format_signed_ratio(value: float) -> str:
    return f"{value * 100:+.1f}%"


def _format_ratio(value: float) -> str:
    return f"{value * 100:.1f}%"


def _compute_portfolio_value_trend_summary(
    store: Any,
    report_quarter: str,
    manager_ciks: list[str],
) -> _PortfolioValueTrendSummary | None:
    selected_ciks = [cik.strip() for cik in manager_ciks if cik and cik.strip()]
    if not selected_ciks:
        return None

    previous_quarter = _previous_report_quarter(report_quarter)
    if previous_quarter is None:
        return None

    snapshots = store.list_snapshots_for_quarters([previous_quarter, report_quarter], selected_ciks)
    snapshot_by_key = {(snapshot.cik, snapshot.report_quarter): snapshot for snapshot in snapshots}

    analyzed_managers = 0
    missing_current = 0
    missing_previous = 0
    growing_managers = 0
    holding_managers = 0
    reducing_managers = 0
    previous_total_value_k = 0
    current_total_value_k = 0
    shares_analyzed_managers = 0
    shares_growing_managers = 0
    shares_holding_managers = 0
    shares_reducing_managers = 0
    previous_total_shares = 0
    current_total_shares = 0

    def _total_snapshot_shares(snapshot: Any) -> int | None:
        total_shares = 0
        has_shares = False
        for position in snapshot.positions:
            shares = position.get("shares")
            if isinstance(shares, int) and shares > 0:
                total_shares += shares
                has_shares = True
        return total_shares if has_shares else None

    for cik in selected_ciks:
        current_snapshot = snapshot_by_key.get((cik, report_quarter))
        if current_snapshot is None:
            missing_current += 1
            continue
        previous_snapshot = snapshot_by_key.get((cik, previous_quarter))
        if previous_snapshot is None:
            missing_previous += 1
            continue

        analyzed_managers += 1
        previous_total_value_k += previous_snapshot.aum_value_k
        current_total_value_k += current_snapshot.aum_value_k

        change_ratio = _portfolio_value_change_ratio(previous_snapshot.aum_value_k, current_snapshot.aum_value_k)
        direction = _portfolio_value_direction(change_ratio)
        if direction == "Growing":
            growing_managers += 1
        elif direction == "Reducing":
            reducing_managers += 1
        else:
            holding_managers += 1

        previous_shares = _total_snapshot_shares(previous_snapshot)
        current_shares = _total_snapshot_shares(current_snapshot)
        if previous_shares is not None and current_shares is not None:
            shares_analyzed_managers += 1
            previous_total_shares += previous_shares
            current_total_shares += current_shares
            shares_change_ratio = _portfolio_value_change_ratio(previous_shares, current_shares)
            shares_direction = _portfolio_shares_direction(shares_change_ratio)
            if shares_direction == "Growing":
                shares_growing_managers += 1
            elif shares_direction == "Reducing":
                shares_reducing_managers += 1
            else:
                shares_holding_managers += 1

    return _PortfolioValueTrendSummary(
        report_quarter=report_quarter,
        previous_quarter=previous_quarter,
        selected_managers=len(selected_ciks),
        analyzed_managers=analyzed_managers,
        missing_current=missing_current,
        missing_previous=missing_previous,
        growing_managers=growing_managers,
        holding_managers=holding_managers,
        reducing_managers=reducing_managers,
        previous_total_value_k=previous_total_value_k,
        current_total_value_k=current_total_value_k,
        shares_analyzed_managers=shares_analyzed_managers,
        shares_growing_managers=shares_growing_managers,
        shares_holding_managers=shares_holding_managers,
        shares_reducing_managers=shares_reducing_managers,
        previous_total_shares=previous_total_shares,
        current_total_shares=current_total_shares,
    )


def _print_portfolio_value_trend_summary(
    store: Any,
    report_quarter: str,
    *,
    manager_ciks: list[str] | None,
) -> None:
    if not manager_ciks:
        return
    summary = _compute_portfolio_value_trend_summary(store, report_quarter, manager_ciks)
    if summary is None:
        return

    print()
    print("Hedge Funds Portfolio Value Trend (QoQ)")
    print(f"Compared quarters: {summary.previous_quarter} -> {summary.report_quarter}")
    print(f"Managers analyzed: {summary.analyzed_managers}/{summary.selected_managers}")

    if summary.analyzed_managers == 0:
        print("Not enough comparable snapshots to determine portfolio value direction.")
        return

    aggregate_change_ratio = _portfolio_value_change_ratio(
        summary.previous_total_value_k,
        summary.current_total_value_k,
    )
    aggregate_direction = _portfolio_value_direction(aggregate_change_ratio)
    print(
        "Aggregate portfolio value: "
        f"{_format_value_k(summary.previous_total_value_k)} -> {_format_value_k(summary.current_total_value_k)} "
        f"({_format_signed_ratio(aggregate_change_ratio)} {aggregate_direction})"
    )
    if summary.shares_analyzed_managers > 0:
        print(f"Managers analyzed (Shares): {summary.shares_analyzed_managers}/{summary.analyzed_managers}")
        aggregate_shares_change_ratio = _portfolio_value_change_ratio(
            summary.previous_total_shares,
            summary.current_total_shares,
        )
        aggregate_shares_direction = _portfolio_shares_direction(aggregate_shares_change_ratio)
        print(
            "Aggregate portfolio shares: "
            f"{_format_int(summary.previous_total_shares)} -> {_format_int(summary.current_total_shares)} "
            f"({_format_signed_ratio(aggregate_shares_change_ratio)} {aggregate_shares_direction})"
        )

    headers = ["Direction", "Value Managers", "Value Share", "Shares Managers", "Shares Share"]

    def _format_direction_cells(*, count: int, total: int) -> tuple[str, str]:
        if total <= 0:
            return ("n/a", "n/a")
        return (str(count), _format_ratio(count / total))

    growing_shares_count, growing_shares_share = _format_direction_cells(
        count=summary.shares_growing_managers,
        total=summary.shares_analyzed_managers,
    )
    holding_shares_count, holding_shares_share = _format_direction_cells(
        count=summary.shares_holding_managers,
        total=summary.shares_analyzed_managers,
    )
    reducing_shares_count, reducing_shares_share = _format_direction_cells(
        count=summary.shares_reducing_managers,
        total=summary.shares_analyzed_managers,
    )
    rows = [
        [
            "Growing",
            str(summary.growing_managers),
            _format_ratio(summary.growing_managers / summary.analyzed_managers),
            growing_shares_count,
            growing_shares_share,
        ],
        [
            "Holding",
            str(summary.holding_managers),
            _format_ratio(summary.holding_managers / summary.analyzed_managers),
            holding_shares_count,
            holding_shares_share,
        ],
        [
            "Reducing",
            str(summary.reducing_managers),
            _format_ratio(summary.reducing_managers / summary.analyzed_managers),
            reducing_shares_count,
            reducing_shares_share,
        ],
    ]
    print(_format_table(headers, rows))


def _print_detailed_trend_table(
    store: Any,
    report_quarter: str,
    *,
    min_conf: float = 0.45,
    limit: int = IDEAS_OUTPUT_MAX_ROWS,
    show_reversals: bool = False,
    symbols_file: str = "config/cusip_tickers.json",
    manager_ciks: list[str] | None = None,
) -> None:
    symbol_map = _load_symbol_map(Path(symbols_file))
    signals = store.list_trend_stock_signals(report_quarter)
    if not signals:
        print(f"No trend signals found for {report_quarter}.")
        return

    effective_limit = max(1, min(limit, IDEAS_OUTPUT_MAX_ROWS))
    sell_min_conf = min(min_conf, SELL_TABLE_MIN_CONF)
    buy = sorted(
        [
            item
            for item in signals
            if (
                "BUY" in item.regime
                and item.regime != "REVERSAL_SELL"
                and item.confidence >= min_conf
                and item.trend_ewma >= BUY_TABLE_MIN_TREND
            )
        ],
        key=lambda item: item.trend_ewma,
        reverse=True,
    )[:effective_limit]
    sell = sorted(
        [
            item
            for item in signals
            if "SELL" in item.regime and item.regime != "REVERSAL_BUY" and item.confidence >= sell_min_conf
        ],
        key=lambda item: item.trend_ewma,
    )[:effective_limit]
    reversals = sorted(
        [
            item
            for item in signals
            if item.regime in {"REVERSAL_BUY", "REVERSAL_SELL"} and item.confidence >= min_conf
        ],
        key=lambda item: abs(item.trend_delta),
        reverse=True,
    )[:effective_limit]

    print()
    print(f"Report quarter: {report_quarter}")
    print(f"Signals total: {len(signals)}")

    headers = [
        "Ticker",
        "Action",
        "Setup (Regime)",
        "Conviction / Target",
        "Trend",
        "Consensus (+/-)",
        "Data Fresh",
    ]

    def _row(item: Any) -> list[str]:
        row = [
            _ticker_for_signal(item, symbol_map),
            _action_for_signal(item),
            _setup_for_signal(item),
            _conviction_target_for_signal(item),
            f"{item.trend_ewma:.4f}",
            f"{item.buy_managers}/{item.sell_managers}",
            _freshness_icon(item),
        ]
        return row

    _print_section("Top Buy Trends", headers, [_row(item) for item in buy])
    _print_section("Top Sell Trends", headers, [_row(item) for item in sell])
    _print_portfolio_value_trend_summary(
        store,
        report_quarter,
        manager_ciks=manager_ciks,
    )
    if show_reversals:
        _print_section("Reversals", headers, [_row(item) for item in reversals])


def _should_print_trend_table(*, dry_run: bool, status: str, report_quarter: str | None) -> bool:
    if dry_run or report_quarter is None:
        return False
    if status.startswith("pending_"):
        return False
    return sys.stdout.isatty()


def _load_live_latest_prices(
    *,
    symbols_file: str,
    logger: logging.Logger,
) -> dict[str, float] | None:
    key_to_ticker = _load_symbol_map(Path(symbols_file))
    if not key_to_ticker:
        logger.warning(
            "Live prices symbols file is empty or invalid; live freshness input skipped",
            extra={"symbols_file": symbols_file},
        )
        return None

    gateway = StooqPriceGateway()
    ticker_prices = gateway.get_latest_prices(sorted(set(key_to_ticker.values())))
    if not ticker_prices:
        logger.warning(
            "Live prices source returned no quotes; live freshness input skipped",
            extra={"source": "stooq"},
        )
        return None

    latest_prices: dict[str, float] = {}
    for key, ticker in key_to_ticker.items():
        price = ticker_prices.get(ticker)
        if price is not None:
            latest_prices[key] = price
    if not latest_prices:
        logger.warning(
            "Live prices fetched but no symbol keys matched; live freshness input skipped",
            extra={"source": "stooq", "symbols_file": symbols_file},
        )
        return None
    logger.info(
        "Live prices loaded for freshness decay",
        extra={
            "source": "stooq",
            "symbols_file": symbols_file,
            "mapped_keys": len(latest_prices),
            "available_tickers": len(ticker_prices),
            "configured_keys": len(key_to_ticker),
        },
    )
    return latest_prices


def _count_pipeline_trend_quarters(*, store: Any, as_of_quarter: str | None) -> int:
    quarters = store.list_trend_quarters()
    if as_of_quarter is None:
        return len(quarters)
    target_key = quarter_sort_key(as_of_quarter)
    return sum(1 for quarter in quarters if quarter_sort_key(quarter) <= target_key)


def _snapshot_sync_max_quarters(min_oos_quarters: int) -> int:
    # +2 buffer: one quarter is consumed by trend-history warmup and one
    # additional quarter protects against occasional empty selection windows.
    return max(9, min_oos_quarters + 2)


def _auto_backfill_from_quarter(
    *,
    store: Any,
    manager_ciks: list[str],
    as_of_quarter: str | None,
    required_trend_quarters: int,
) -> str | None:
    common_quarters = sorted(store.list_common_report_quarters(manager_ciks), key=quarter_sort_key)
    if as_of_quarter is not None:
        target_key = quarter_sort_key(as_of_quarter)
        common_quarters = [quarter for quarter in common_quarters if quarter_sort_key(quarter) <= target_key]
    if not common_quarters:
        return None
    # One extra quarter is usually needed so earliest target can be computed (history dependency).
    required_snapshot_quarters = required_trend_quarters + 1
    if len(common_quarters) <= required_snapshot_quarters:
        return common_quarters[0]
    return common_quarters[-required_snapshot_quarters]


def _pipeline_quality_fail_set() -> set[str]:
    raw = os.environ.get("PIPELINE_FAIL_ON_QUALITY", "")
    values = {item.strip().upper() for item in raw.split(",") if item.strip()}
    return {item for item in values if item in {"WATCH", "FAIL"}}


def _pipeline_exit_code(*, status: str | None, quality_status: str | None, fail_on_quality: set[str]) -> int:
    normalized_status = (status or "").strip().lower()
    if normalized_status in PIPELINE_HARD_FAIL_STATUSES:
        return 1
    normalized_quality = (quality_status or "").strip().upper()
    if normalized_quality and normalized_quality in fail_on_quality:
        return 1
    return 0


def main() -> int:
    configure_logging()
    logger = logging.getLogger(__name__)
    trace_id = new_trace_id()

    with log_context(trace_id=trace_id):
        return _main(logger)


def _main(logger: logging.Logger) -> int:
    parser = argparse.ArgumentParser(description="Track 13F filings and send notifications.")
    parser.add_argument("--notify_on_first_start", action="store_true", help="Notify on initial baseline set")
    parser.add_argument(
        "clean_state",
        nargs="?",
        choices=["clean_state"],
        help="Clear persisted manager state before running checks.",
    )
    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="Send a test notification and exit (without SEC checks).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not send notifications or write state")
    parser.add_argument(
        "--force-trend-recompute",
        action="store_true",
        help="Force trend engine recomputation even when input and top fingerprints are unchanged.",
    )
    parser.add_argument(
        "--show-trends-detailed",
        action="store_true",
        help="Always print detailed trends table after tracker run (also in non-interactive output).",
    )
    parser.add_argument(
        "--show-trends-only",
        action="store_true",
        help="Print detailed trends table from existing DB signals and exit (no sync/trend recompute).",
    )
    parser.add_argument(
        "--trends-quarter",
        help="Quarter for --show-trends-detailed/--show-trends-only in format YYYYQn. Default: computed/latest quarter.",
    )
    parser.add_argument(
        "--trends-min-conf",
        type=float,
        default=0.45,
        help="Buy/reversal confidence for --show-trends-detailed/--show-trends-only (default: 0.45; sells use min(threshold, 0.35)).",
    )
    parser.add_argument(
        "--trends-limit",
        type=int,
        default=IDEAS_OUTPUT_MAX_ROWS,
        help="Rows per section for --show-trends-detailed/--show-trends-only (default: 8, max: 8).",
    )
    parser.add_argument(
        "--trends-show-reversals",
        action="store_true",
        help="Include reversals section in --show-trends-detailed/--show-trends-only output.",
    )
    parser.add_argument(
        "--trends-symbols-file",
        default="config/cusip_tickers.json",
        help="Symbol map JSON for --show-trends-detailed/--show-trends-only.",
    )
    parser.add_argument(
        "--trend-live-prices-symbols-file",
        default=os.environ.get("TREND_LIVE_PRICES_SYMBOLS_FILE", "config/cusip_tickers.json"),
        help="Symbol map JSON for live prices from stooq (key: CUSIP/instrument_key, value: ticker).",
    )
    parser.add_argument(
        "--run-quarterly-pipeline",
        action="store_true",
        help="Run quarterly research pipeline (risk-filter, portfolio, walk-forward KPI report).",
    )
    parser.add_argument(
        "--pipeline-quarter",
        help="Quarter for quarterly pipeline in format YYYYQn. Default: latest trend quarter.",
    )
    parser.add_argument(
        "--pipeline-dry-run-report",
        action="store_true",
        help="Generate quarterly pipeline report without writing quarterly pipeline tables.",
    )
    parser.add_argument(
        "--backfill-trend-history",
        action="store_true",
        help="Run backfill trend computation for historical quarters as a separate mode.",
    )
    parser.add_argument(
        "--backfill-from-quarter",
        help="Optional start quarter for backfill mode in format YYYYQn.",
    )
    parser.add_argument(
        "--backfill-to-quarter",
        help="Optional end quarter for backfill mode in format YYYYQn.",
    )
    parser.add_argument(
        "--backfill-force",
        action="store_true",
        help="Recompute backfill quarters even if trend signals already exist.",
    )
    parser.add_argument(
        "--backfill-include-latest",
        action="store_true",
        help="Include latest completed quarter into backfill run.",
    )
    args = parser.parse_args()
    show_trends_only_flag = bool(getattr(args, "show_trends_only", False))
    run_quarterly_pipeline_flag = bool(getattr(args, "run_quarterly_pipeline", False))
    pipeline_quarter = getattr(args, "pipeline_quarter", None)
    pipeline_dry_run_report = bool(getattr(args, "pipeline_dry_run_report", False))
    backfill_trend_history_flag = bool(getattr(args, "backfill_trend_history", False))
    backfill_from_quarter = getattr(args, "backfill_from_quarter", None)
    backfill_to_quarter = getattr(args, "backfill_to_quarter", None)
    backfill_force = bool(getattr(args, "backfill_force", False))
    backfill_include_latest = bool(getattr(args, "backfill_include_latest", False))

    if args.test_notification and args.dry_run:
        logger.error("Cannot combine --test-notification with --dry-run")
        return 2
    if args.test_notification and args.clean_state == "clean_state":
        logger.error("Cannot combine --test-notification with clean_state")
        return 2
    if args.dry_run and args.clean_state == "clean_state":
        logger.error("Cannot combine --dry-run with clean_state")
        return 2
    if show_trends_only_flag and args.test_notification:
        logger.error("Cannot combine --show-trends-only with --test-notification")
        return 2
    if show_trends_only_flag and args.clean_state == "clean_state":
        logger.error("Cannot combine --show-trends-only with clean_state")
        return 2
    if show_trends_only_flag and args.force_trend_recompute:
        logger.error("Cannot combine --show-trends-only with --force-trend-recompute")
        return 2
    if args.trends_min_conf < 0 or args.trends_min_conf > 1:
        logger.error("--trends-min-conf must be between 0 and 1")
        return 2
    if args.trends_limit <= 0:
        logger.error("--trends-limit must be > 0")
        return 2
    if pipeline_quarter and parse_report_quarter(pipeline_quarter) is None:
        logger.error("--pipeline-quarter must use YYYYQn format")
        return 2
    if backfill_from_quarter and parse_report_quarter(backfill_from_quarter) is None:
        logger.error("--backfill-from-quarter must use YYYYQn format")
        return 2
    if backfill_to_quarter and parse_report_quarter(backfill_to_quarter) is None:
        logger.error("--backfill-to-quarter must use YYYYQn format")
        return 2
    if backfill_from_quarter and backfill_to_quarter:
        if parse_report_quarter(backfill_from_quarter) > parse_report_quarter(backfill_to_quarter):
            logger.error("--backfill-from-quarter must be <= --backfill-to-quarter")
            return 2
    if backfill_trend_history_flag and run_quarterly_pipeline_flag:
        logger.error("Cannot combine --backfill-trend-history with --run-quarterly-pipeline")
        return 2
    if show_trends_only_flag and run_quarterly_pipeline_flag:
        logger.error("Cannot combine --show-trends-only with --run-quarterly-pipeline")
        return 2
    if show_trends_only_flag and backfill_trend_history_flag:
        logger.error("Cannot combine --show-trends-only with --backfill-trend-history")
        return 2
    if not backfill_trend_history_flag and (
        backfill_from_quarter or backfill_to_quarter or backfill_force or backfill_include_latest
    ):
        logger.error("Backfill options require --backfill-trend-history")
        return 2

    try:
        config = load_config(notify_initial=args.notify_on_first_start)
    except (ValueError, FileNotFoundError) as exc:
        logger.error("Configuration validation failed", extra={"error": str(exc)})
        return 1

    if not config.notifiers:
        logger.warning("No notifiers configured, running without notifications")
    logger.info(
        "Tracker run started",
        extra={
            "managers_count": len(config.managers),
            "dry_run": args.dry_run,
            "test_notification": args.test_notification,
            "clean_state": args.clean_state == "clean_state",
        },
    )
    manager_ciks = [manager.cik for manager in config.managers]

    if args.test_notification:
        try:
            notifiers = build_notifier_list(config, dry_run=args.dry_run, test_notification=True)
        except ValueError as exc:
            logger.error("Notifier initialization failed", extra={"error": str(exc)})
            return 1
        if not notifiers:
            logger.error("No notifiers configured for test notification")
            return 1
        subject = "13F Tracker test notification"
        body = f"Test notification sent at {format_local_datetime(now_kyiv())}."
        _send_notifications(notifiers, subject, body)
        logger.info("Test notification sent")
        return 0

    try:
        runtime = build_runtime(config, dry_run=args.dry_run, test_notification=args.test_notification)
    except (ValueError, StateStoreError) as exc:
        logger.error("Runtime initialization failed", extra={"error": str(exc)})
        return 1

    if show_trends_only_flag:
        quarter_for_table = args.trends_quarter or runtime.store.get_latest_trend_quarter()
        if quarter_for_table is None:
            print("No trend signals found.")
        else:
            _print_detailed_trend_table(
                runtime.store,
                quarter_for_table,
                min_conf=args.trends_min_conf,
                limit=args.trends_limit,
                show_reversals=args.trends_show_reversals,
                symbols_file=args.trends_symbols_file,
                manager_ciks=manager_ciks,
            )
        runtime.store.close()
        logger.info(
            "Tracker run finished",
            extra={
                "finished_at_local": format_local_datetime(now_kyiv()),
                "managers_count": len(config.managers),
                "dry_run": args.dry_run,
                "mode": "show_trends_only",
                "report_quarter": quarter_for_table,
            },
        )
        return 0

    if backfill_trend_history_flag:
        if args.clean_state == "clean_state":
            runtime.store.close()
            logger.error("Cannot combine clean_state with --backfill-trend-history")
            return 2
        trend_blend_mode = os.environ.get("TREND_BLEND_MODE", "tactical").strip().lower()
        trend_latest_prices = _load_live_latest_prices(
            symbols_file=args.trend_live_prices_symbols_file,
            logger=logger,
        )
        try:
            backfill_result = run_backfill_trend_history(
                list(config.managers),
                runtime.store,
                dry_run=args.dry_run,
                blend_mode=trend_blend_mode,
                latest_prices=trend_latest_prices,
                from_quarter=backfill_from_quarter,
                to_quarter=backfill_to_quarter,
                include_latest=backfill_include_latest,
                force_recompute=backfill_force,
                logger=logger,
            )
        except ValueError as exc:
            runtime.store.close()
            logger.error("Backfill trend history configuration failed", extra={"error": str(exc)})
            return 1

        logger.info(
            "Backfill trend history status",
            extra={
                "status": backfill_result.status,
                "batch_id": backfill_result.batch_id,
                "quarters_requested": backfill_result.quarters_requested,
                "computed": backfill_result.computed,
                "skipped_existing": backfill_result.skipped_existing,
                "failed": backfill_result.failed,
                "force_recompute": backfill_force,
                "include_latest": backfill_include_latest,
                "from_quarter": backfill_from_quarter,
                "to_quarter": backfill_to_quarter,
            },
        )
        print()
        print(
            "Backfill summary: "
            f"requested={backfill_result.quarters_requested}, "
            f"computed={backfill_result.computed}, "
            f"skipped_existing={backfill_result.skipped_existing}, "
            f"failed={backfill_result.failed}, "
            f"status={backfill_result.status}, "
            f"batch_id={backfill_result.batch_id}"
        )

        runtime.store.close()
        logger.info(
            "Tracker run finished",
            extra={
                "finished_at_local": format_local_datetime(now_kyiv()),
                "managers_count": len(config.managers),
                "dry_run": args.dry_run,
                "mode": "backfill",
            },
        )
        return 1 if backfill_result.status == "failed" else 0

    if args.clean_state == "clean_state":
        cleared_rows = runtime.store.clear_state()
        logger.info("State store cleared before run", extra={"rows_deleted": cleared_rows})

    managers = [Manager(name=manager_config.name, cik=manager_config.cik) for manager_config in config.managers]
    for manager in managers:
        process_manager(
            manager,
            runtime.store,
            runtime.client,
            runtime.notifiers,
            notify_initial=config.notify_initial,
            dry_run=args.dry_run,
            max_filing_age_days=config.max_filing_age_days,
            logger=logger,
        )

    sync_quarter_snapshots(
        managers,
        runtime.store,
        runtime.client,
        max_quarters=_snapshot_sync_max_quarters(config.pipeline.min_oos_quarters),
        max_filing_age_days=config.max_filing_age_days,
        dry_run=args.dry_run,
        logger=logger,
    )
    trend_blend_mode = os.environ.get("TREND_BLEND_MODE", "tactical").strip().lower()
    trend_latest_prices = _load_live_latest_prices(
        symbols_file=args.trend_live_prices_symbols_file,
        logger=logger,
    )
    try:
        trend_result = run_trend_engine_for_latest_completed_quarter(
            list(config.managers),
            runtime.store,
            dry_run=args.dry_run,
            blend_mode=trend_blend_mode,
            latest_prices=trend_latest_prices,
            force_recompute=args.force_trend_recompute,
            logger=logger,
        )
    except ValueError as exc:
        runtime.store.close()
        logger.error("Trend engine configuration failed", extra={"error": str(exc), "blend_mode": trend_blend_mode})
        return 1
    logger.info(
        "Trend engine status",
        extra={
            "status": trend_result.status,
            "report_quarter": trend_result.report_quarter,
            "signals_count": trend_result.signals_count,
            "blend_mode": trend_blend_mode,
            "force_trend_recompute": args.force_trend_recompute,
        },
    )
    if args.show_trends_detailed:
        quarter_for_table = args.trends_quarter or trend_result.report_quarter or runtime.store.get_latest_trend_quarter()
        if quarter_for_table is None:
            print("No trend signals found.")
        else:
            _print_detailed_trend_table(
                runtime.store,
                quarter_for_table,
                min_conf=args.trends_min_conf,
                limit=args.trends_limit,
                show_reversals=args.trends_show_reversals,
                symbols_file=args.trends_symbols_file,
                manager_ciks=manager_ciks,
            )
    elif _should_print_trend_table(
        dry_run=args.dry_run,
        status=trend_result.status,
        report_quarter=trend_result.report_quarter,
    ):
        _print_detailed_trend_table(
            runtime.store,
            trend_result.report_quarter,
            min_conf=args.trends_min_conf,
            limit=args.trends_limit,
            show_reversals=args.trends_show_reversals,
            symbols_file=args.trends_symbols_file,
            manager_ciks=manager_ciks,
        )

    notify_if_all_reports_published_for_current_quarter(
        managers,
        runtime.store,
        runtime.notifiers,
        dry_run=args.dry_run,
        logger=logger,
    )

    pipeline_exit_code = 0
    if run_quarterly_pipeline_flag:
        fail_on_quality = _pipeline_quality_fail_set()
        required_pipeline_quarters = max(
            PIPELINE_AUTO_BACKFILL_MIN_QUARTERS,
            config.pipeline.min_oos_quarters + 1,
        )
        available_pipeline_quarters = _count_pipeline_trend_quarters(store=runtime.store, as_of_quarter=pipeline_quarter)
        if not args.dry_run and available_pipeline_quarters < required_pipeline_quarters:
            auto_backfill_from_quarter = _auto_backfill_from_quarter(
                store=runtime.store,
                manager_ciks=manager_ciks,
                as_of_quarter=pipeline_quarter,
                required_trend_quarters=required_pipeline_quarters,
            )
            logger.info(
                "Quarterly pipeline auto-backfill started",
                extra={
                    "available_quarters": available_pipeline_quarters,
                    "required_quarters": required_pipeline_quarters,
                    "pipeline_quarter": pipeline_quarter,
                    "from_quarter": auto_backfill_from_quarter,
                },
            )
            try:
                auto_backfill_result = run_backfill_trend_history(
                    list(config.managers),
                    runtime.store,
                    dry_run=args.dry_run,
                    blend_mode=trend_blend_mode,
                    latest_prices=trend_latest_prices,
                    from_quarter=auto_backfill_from_quarter,
                    to_quarter=pipeline_quarter,
                    include_latest=False,
                    force_recompute=False,
                    logger=logger,
                )
            except ValueError as exc:
                runtime.store.close()
                logger.error("Quarterly pipeline auto-backfill configuration failed", extra={"error": str(exc)})
                return 1

            logger.info(
                "Quarterly pipeline auto-backfill status",
                extra={
                    "status": auto_backfill_result.status,
                    "batch_id": auto_backfill_result.batch_id,
                    "quarters_requested": auto_backfill_result.quarters_requested,
                    "computed": auto_backfill_result.computed,
                    "skipped_existing": auto_backfill_result.skipped_existing,
                    "failed": auto_backfill_result.failed,
                    "to_quarter": pipeline_quarter,
                },
            )
            print()
            print(
                "Auto-backfill summary: "
                f"requested={auto_backfill_result.quarters_requested}, "
                f"computed={auto_backfill_result.computed}, "
                f"skipped_existing={auto_backfill_result.skipped_existing}, "
                f"failed={auto_backfill_result.failed}, "
                f"status={auto_backfill_result.status}, "
                f"batch_id={auto_backfill_result.batch_id}"
            )
            if auto_backfill_result.status == "failed":
                runtime.store.close()
                logger.error("Quarterly pipeline auto-backfill failed")
                return 1

            available_pipeline_quarters = _count_pipeline_trend_quarters(store=runtime.store, as_of_quarter=pipeline_quarter)

        if available_pipeline_quarters < required_pipeline_quarters:
            runtime.store.close()
            logger.error(
                "Quarterly pipeline precheck failed: insufficient trend quarters",
                extra={
                    "available_quarters": available_pipeline_quarters,
                    "required_quarters": required_pipeline_quarters,
                    "pipeline_quarter": pipeline_quarter,
                },
            )
            return 1

        pipeline_result = run_quarterly_pipeline(
            store=runtime.store,
            history_gateway=runtime.history_gateway,
            symbol_map_file=Path(args.trend_live_prices_symbols_file),
            pipeline=config.pipeline,
            as_of_quarter=pipeline_quarter,
            dry_run_report=pipeline_dry_run_report,
        )
        logger.info(
            "Quarterly pipeline status",
            extra={
                "status": pipeline_result.status,
                "as_of_quarter": pipeline_result.as_of_quarter,
                "run_id": pipeline_result.run_id,
                "quality_status": pipeline_result.quality_status,
                "report_dir": str(pipeline_result.report_dir) if pipeline_result.report_dir else None,
                "dry_run_report": pipeline_dry_run_report,
            },
        )
        if pipeline_result.report_dir is not None:
            print()
            print(f"Quarterly report: {pipeline_result.report_dir}")
            if pipeline_result.quality_status:
                print(f"Quality status: {pipeline_result.quality_status}")

        pipeline_exit_code = _pipeline_exit_code(
            status=pipeline_result.status,
            quality_status=pipeline_result.quality_status,
            fail_on_quality=fail_on_quality,
        )
        if pipeline_exit_code != 0:
            logger.error(
                "Quarterly pipeline quality gate failed",
                extra={
                    "status": pipeline_result.status,
                    "quality_status": pipeline_result.quality_status,
                    "fail_on_quality": sorted(fail_on_quality),
                },
            )

    runtime.store.close()
    logger.info(
        "Tracker run finished",
        extra={
            "finished_at_local": format_local_datetime(now_kyiv()),
            "managers_count": len(config.managers),
            "dry_run": args.dry_run,
        },
    )
    return pipeline_exit_code
