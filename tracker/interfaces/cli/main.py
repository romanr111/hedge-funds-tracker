from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tracker.application.ports.notifier import NotifierPort
from tracker.application.use_cases.notify_quarterly_reports_completion import (
    notify_if_all_reports_published_for_current_quarter,
)
from tracker.application.use_cases.run_trend_engine import run_trend_engine_for_latest_completed_quarter
from tracker.application.use_cases.sync_quarter_snapshots import sync_quarter_snapshots
from tracker.application.use_cases.track_manager import process_manager
from tracker.composition import build_notifier_list, build_runtime
from tracker.config import load_config
from tracker.domain.exceptions import StateStoreError
from tracker.domain.models import Manager
from tracker.domain.timing import format_local_datetime, now_kyiv
from tracker.infrastructure.logging import configure_logging, log_context, new_trace_id
from tracker.infrastructure.market import StooqPriceGateway

SELL_TABLE_MIN_CONF = 0.35


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


def _contributors_preview(contributors_json: str) -> str:
    try:
        payload = json.loads(contributors_json)
    except json.JSONDecodeError:
        return "-"
    if not isinstance(payload, list) or not payload:
        return "-"
    first = payload[0]
    if not isinstance(first, dict):
        return "-"
    manager_name = first.get("manager_name")
    signal_value = first.get("signal_value")
    if isinstance(manager_name, str):
        return f"{manager_name} ({signal_value})"
    return "-"


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
    regime = (signal.regime or "").upper()
    confidence = float(signal.confidence)
    if regime == "STRONG_BUY" and confidence >= 0.65:
        return "ACTION_BUY"
    if regime in {"REVERSAL_BUY", "EMERGING_BUY"} and confidence >= 0.45:
        return "WATCH_BUY"
    if regime == "WEAKENING_BUY":
        return "WEAKENING_BUY"
    if regime == "STRONG_SELL" and confidence >= 0.65:
        return "ACTION_SELL"
    if regime in {"REVERSAL_SELL", "EMERGING_SELL"} and confidence >= SELL_TABLE_MIN_CONF:
        return "WATCH_SELL"
    if regime == "WEAKENING_SELL":
        return "WEAKENING_SELL"
    return "MONITOR"


def _print_section(title: str, headers: list[str], rows: list[list[str]]) -> None:
    print()
    print(title)
    if not rows:
        print("(empty)")
        return
    print(_format_table(headers, rows))


def _print_trend_table(store: Any, report_quarter: str, *, min_conf: float = 0.45, limit: int = 10) -> None:
    signals = store.list_trend_stock_signals(report_quarter)
    if not signals:
        return
    sell_min_conf = min(min_conf, SELL_TABLE_MIN_CONF)
    buy = sorted(
        [
            item
            for item in signals
            if "BUY" in item.regime and item.regime != "REVERSAL_SELL" and item.confidence >= min_conf
        ],
        key=lambda item: item.trend_ewma,
        reverse=True,
    )[:limit]
    sell = sorted(
        [
            item
            for item in signals
            if "SELL" in item.regime and item.regime != "REVERSAL_BUY" and item.confidence >= sell_min_conf
        ],
        key=lambda item: item.trend_ewma,
    )[:limit]
    headers = [
        "action",
        "instrument",
        "regime",
        "trend",
        "delta",
        "conf",
        "breadth(+/-)",
    ]
    freshness_enabled = any(getattr(item, "freshness_ok", None) is not None for item in signals)
    if freshness_enabled:
        headers.append("freshness")

    def row(item: Any) -> list[str]:
        line = [
            _action_for_signal(item),
            (item.instrument_key or "").strip() or "UNKNOWN",
            item.regime,
            f"{item.trend_ewma:.6f}",
            f"{item.trend_delta:.6f}",
            f"{item.confidence:.3f}",
            f"{item.buy_managers}/{item.sell_managers}",
        ]
        if freshness_enabled:
            freshness_ok = getattr(item, "freshness_ok", None)
            line.append("-" if freshness_ok is None else ("✅" if bool(freshness_ok) else "❌"))
        return line

    print()
    print(f"Trend table ({report_quarter})")
    _print_section("Top Buy Trends", headers, [row(item) for item in buy])
    _print_section("Top Sell Trends", headers, [row(item) for item in sell])


def _print_detailed_trend_table(
    store: Any,
    report_quarter: str,
    *,
    min_conf: float = 0.45,
    limit: int = 15,
    show_reversals: bool = False,
    symbols_file: str = "config/cusip_tickers.json",
) -> None:
    symbol_map = _load_symbol_map(Path(symbols_file))
    signals = store.list_trend_stock_signals(report_quarter)
    if not signals:
        print(f"No trend signals found for {report_quarter}.")
        return

    sell_min_conf = min(min_conf, SELL_TABLE_MIN_CONF)
    buy = sorted(
        [
            item
            for item in signals
            if "BUY" in item.regime and item.regime != "REVERSAL_SELL" and item.confidence >= min_conf
        ],
        key=lambda item: item.trend_ewma,
        reverse=True,
    )[:limit]
    sell = sorted(
        [
            item
            for item in signals
            if "SELL" in item.regime and item.regime != "REVERSAL_BUY" and item.confidence >= sell_min_conf
        ],
        key=lambda item: item.trend_ewma,
    )[:limit]
    reversals = sorted(
        [
            item
            for item in signals
            if item.regime in {"REVERSAL_BUY", "REVERSAL_SELL"} and item.confidence >= min_conf
        ],
        key=lambda item: abs(item.trend_delta),
        reverse=True,
    )[:limit]

    print()
    print(f"Report quarter: {report_quarter}")
    print(f"Signals total: {len(signals)}")

    headers = [
        "action",
        "ticker",
        "regime",
        "trend",
        "delta",
        "impulse",
        "accum",
        "conf",
        "breadth(+/-)",
        "issuer",
        "top contributor",
    ]
    freshness_enabled = any(getattr(item, "freshness_ok", None) is not None for item in signals)
    if freshness_enabled:
        headers.append("freshness indicator")

    def _row(item: Any) -> list[str]:
        row = [
            _action_for_signal(item),
            _ticker_for_signal(item, symbol_map),
            item.regime,
            f"{item.trend_ewma:.6f}",
            f"{item.trend_delta:.6f}",
            f"{item.impulse_score:.6f}",
            f"{item.accumulation_score:.6f}",
            f"{item.confidence:.3f}",
            f"{item.buy_managers}/{item.sell_managers}",
            item.issuer_name or "-",
            _contributors_preview(item.contributors_json),
        ]
        if freshness_enabled:
            freshness_ok = getattr(item, "freshness_ok", None)
            row.append("-" if freshness_ok is None else ("✅" if bool(freshness_ok) else "❌"))
        return row

    _print_section("Top Buy Trends", headers, [_row(item) for item in buy])
    _print_section("Top Sell Trends", headers, [_row(item) for item in sell])
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
        help="Print detailed trends table (similar to scripts/show_trends.py) after tracker run.",
    )
    parser.add_argument(
        "--trends-quarter",
        help="Quarter for --show-trends-detailed in format YYYYQn. Default: computed/latest quarter.",
    )
    parser.add_argument(
        "--trends-min-conf",
        type=float,
        default=0.45,
        help="Buy/reversal confidence for --show-trends-detailed (default: 0.45; sells use min(threshold, 0.35)).",
    )
    parser.add_argument(
        "--trends-limit",
        type=int,
        default=15,
        help="Rows per section for --show-trends-detailed (default: 15).",
    )
    parser.add_argument(
        "--trends-show-reversals",
        action="store_true",
        help="Include reversals section in --show-trends-detailed output.",
    )
    parser.add_argument(
        "--trends-symbols-file",
        default="config/cusip_tickers.json",
        help="Symbol map JSON for --show-trends-detailed.",
    )
    parser.add_argument(
        "--trend-live-prices-symbols-file",
        default=os.environ.get("TREND_LIVE_PRICES_SYMBOLS_FILE", "config/cusip_tickers.json"),
        help="Symbol map JSON for live prices from stooq (key: CUSIP/instrument_key, value: ticker).",
    )
    args = parser.parse_args()

    if args.test_notification and args.dry_run:
        logger.error("Cannot combine --test-notification with --dry-run")
        return 2
    if args.test_notification and args.clean_state == "clean_state":
        logger.error("Cannot combine --test-notification with clean_state")
        return 2
    if args.dry_run and args.clean_state == "clean_state":
        logger.error("Cannot combine --dry-run with clean_state")
        return 2
    if args.trends_min_conf < 0 or args.trends_min_conf > 1:
        logger.error("--trends-min-conf must be between 0 and 1")
        return 2
    if args.trends_limit <= 0:
        logger.error("--trends-limit must be > 0")
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
        max_quarters=4,
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
            )
    elif _should_print_trend_table(
        dry_run=args.dry_run,
        status=trend_result.status,
        report_quarter=trend_result.report_quarter,
    ):
        _print_trend_table(runtime.store, trend_result.report_quarter)

    notify_if_all_reports_published_for_current_quarter(
        managers,
        runtime.store,
        runtime.notifiers,
        dry_run=args.dry_run,
        logger=logger,
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
    return 0
