#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tracker.infrastructure.storage.sqlite_state_repository import StateStore

SELL_TABLE_MIN_CONF = 0.35


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


def _freshness_icon(signal: Any) -> str:
    freshness_ok = getattr(signal, "freshness_ok", None)
    if freshness_ok is None:
        return "-"
    return "✅" if bool(freshness_ok) else "❌"


def main() -> int:
    parser = argparse.ArgumentParser(description="Print trend signals by quarter.")
    parser.add_argument("--db", default="data/tracker.sqlite3", help="Path to SQLite DB (default: data/tracker.sqlite3)")
    parser.add_argument("--quarter", help="Report quarter in format YYYYQn (example: 2025Q4).")
    parser.add_argument("--limit", type=int, default=15, help="Max rows per section (default: 15).")
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

        buy = sorted(
            [
                item
                for item in signals
                if "BUY" in item.regime and item.regime != "REVERSAL_SELL" and item.confidence >= args.min_conf
            ],
            key=lambda item: item.trend_ewma,
            reverse=True,
        )[: args.limit]
        sell_min_conf = min(args.min_conf, SELL_TABLE_MIN_CONF)
        sell = sorted(
            [
                item
                for item in signals
                if "SELL" in item.regime and item.regime != "REVERSAL_BUY" and item.confidence >= sell_min_conf
            ],
            key=lambda item: item.trend_ewma,
        )[: args.limit]
        reversals = sorted(
            [
                item
                for item in signals
                if item.regime in {"REVERSAL_BUY", "REVERSAL_SELL"} and item.confidence >= args.min_conf
            ],
            key=lambda item: abs(item.trend_delta),
            reverse=True,
        )[: args.limit]

        print(f"Report quarter: {quarter}")
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
                row.append(_freshness_icon(item))
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
