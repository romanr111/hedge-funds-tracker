#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

try:
    from tracker.infrastructure.storage.sqlite_state_repository import StateStore
    from tracker.domain.trend_presentation import (
        action_for_signal,
        conviction_target,
        freshness_icon,
        setup_for_regime,
        target_confidence_for_regime,
    )
except ModuleNotFoundError as exc:
    if exc.name != "tracker":
        raise
    # Keep script execution stable in CI where the repository root may be absent from sys.path.
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from tracker.infrastructure.storage.sqlite_state_repository import StateStore
    from tracker.domain.trend_presentation import (
        action_for_signal,
        conviction_target,
        freshness_icon,
        setup_for_regime,
        target_confidence_for_regime,
    )

SELL_TABLE_MIN_CONF = 0.35
BUY_TABLE_MIN_TREND = 0.001
IDEAS_OUTPUT_MAX_ROWS = 8


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def render_line(parts: list[str]) -> str:
        return " | ".join(part.ljust(widths[idx]) for idx, part in enumerate(parts))

    separator = "-+-".join("-" * width for width in widths)
    output = [render_line(headers), separator]
    output.extend(render_line(row) for row in rows)
    return "\n".join(output)


def _load_symbol_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload: Any = json.loads(path.read_text())
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


def _print_section(title: str, headers: list[str], rows: list[list[str]]) -> None:
    print()
    print(title)
    if not rows:
        print("(empty)")
        return
    print(_format_table(headers, rows))


def _action_for_signal(signal: Any) -> str:
    return action_for_signal(str(signal.regime or ""), float(signal.confidence))


def _setup_for_signal(signal: Any) -> str:
    return setup_for_regime(str(signal.regime or ""))


def _target_confidence_for_signal(signal: Any) -> float:
    return target_confidence_for_regime(str(signal.regime or ""))


def _conviction_target_for_signal(signal: Any) -> str:
    return conviction_target(float(signal.confidence), str(signal.regime or ""))


def _freshness_icon(signal: Any) -> str:
    return freshness_icon(getattr(signal, "freshness_ok", None))


def main() -> int:
    parser = argparse.ArgumentParser(description="Print trend signals by quarter.")
    parser.add_argument("--db", default="data/tracker.sqlite3", help="Path to SQLite DB (default: data/tracker.sqlite3)")
    parser.add_argument("--quarter", help="Report quarter in format YYYYQn (example: 2025Q4).")
    parser.add_argument("--limit", type=int, default=IDEAS_OUTPUT_MAX_ROWS, help="Max rows per section (default: 8, max: 8).")
    parser.add_argument(
        "--min-conf",
        type=float,
        default=0.45,
        help="Buy/reversal confidence threshold (default: 0.45; sells use min(threshold, 0.35)).",
    )
    parser.add_argument(
        "--show-reversals",
        action="store_true",
        help="Print reversals section (disabled by default).",
    )
    parser.add_argument(
        "--symbols-file",
        default="config/cusip_tickers.json",
        help="JSON map for symbols: {\"CUSIP\": \"TICKER\"} or {\"CUSIP|PUTCALL\": \"TICKER\"}.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1
    if args.limit <= 0:
        print("--limit must be > 0")
        return 1
    if args.min_conf < 0 or args.min_conf > 1:
        print("--min-conf must be between 0 and 1")
        return 1
    symbol_map = _load_symbol_map(Path(args.symbols_file))

    store = StateStore(db_path)
    try:
        quarter = args.quarter or store.get_latest_trend_quarter()
        if quarter is None:
            print("No trend signals found.")
            return 0

        signals = store.list_trend_stock_signals(quarter)
        if not signals:
            print(f"No trend signals found for {quarter}.")
            return 0

        effective_limit = max(1, min(args.limit, IDEAS_OUTPUT_MAX_ROWS))

        buy = sorted(
            [
                item
                for item in signals
                if (
                    "BUY" in item.regime
                    and item.regime != "REVERSAL_SELL"
                    and item.confidence >= args.min_conf
                    and item.trend_ewma >= BUY_TABLE_MIN_TREND
                )
            ],
            key=lambda item: item.trend_ewma,
            reverse=True,
        )[:effective_limit]
        sell_min_conf = min(args.min_conf, SELL_TABLE_MIN_CONF)
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
                if item.regime in {"REVERSAL_BUY", "REVERSAL_SELL"} and item.confidence >= args.min_conf
            ],
            key=lambda item: abs(item.trend_delta),
            reverse=True,
        )[:effective_limit]

        print(f"Report quarter: {quarter}")
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

        buy_rows = [
            _row(item)
            for item in buy
        ]
        sell_rows = [
            _row(item)
            for item in sell
        ]
        reversal_rows = [
            _row(item)
            for item in reversals
        ]

        _print_section("Top Buy Trends", headers, buy_rows)
        _print_section("Top Sell Trends", headers, sell_rows)
        if args.show_reversals:
            _print_section("Reversals", headers, reversal_rows)
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
