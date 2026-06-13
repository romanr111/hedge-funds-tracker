#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

try:
    from signals.infrastructure.storage.sqlite_state_repository import StateStore
    from signals.domain.trend_presentation import (
        action_for_signal,
        conviction_target,
        directional_contributor_names,
        freshness_icon,
        setup_for_regime,
        target_confidence_for_regime,
    )
    from signals.domain.trend_ideas import TrendIdeaDecision, select_trend_ideas
except ModuleNotFoundError as exc:
    if exc.name != "signals":
        raise
    # Keep script execution stable in CI where the repository root may be absent from sys.path.
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from signals.infrastructure.storage.sqlite_state_repository import StateStore
    from signals.domain.trend_presentation import (
        action_for_signal,
        conviction_target,
        directional_contributor_names,
        freshness_icon,
        setup_for_regime,
        target_confidence_for_regime,
    )
    from signals.domain.trend_ideas import TrendIdeaDecision, select_trend_ideas

SELL_TABLE_MIN_CONF = 0.35
BUY_TABLE_MIN_TREND = 0.001
IDEAS_OUTPUT_MAX_ROWS = 8
OPTIONS_OUTPUT_MAX_ROWS = 5


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


def _instrument_for_signal(signal: Any, symbol_map: dict[str, str]) -> str:
    ticker = _ticker_for_signal(signal, symbol_map)
    if ticker not in {str(signal.instrument_key or "").strip().upper(), str(signal.cusip or "").strip().upper()}:
        return ticker
    identifier = str(signal.instrument_key or signal.cusip or "UNKNOWN").strip().upper()
    issuer = str(signal.issuer_name or "Unknown issuer").strip()
    return f"{issuer} [unmapped: {identifier}]"


def _option_for_signal(signal: Any, symbol_map: dict[str, str]) -> str:
    put_call = str(signal.put_call or "").strip().upper()
    ticker = _ticker_for_signal(signal, symbol_map)
    identifiers = {
        str(signal.instrument_key or "").strip().upper(),
        str(signal.cusip or "").strip().upper(),
    }
    if ticker in identifiers:
        ticker = _instrument_for_signal(signal, symbol_map)
    return f"{ticker} {put_call}".strip()


def _is_option_signal(signal: Any) -> bool:
    return bool(str(getattr(signal, "put_call", "") or "").strip())


def _stock_only_signals(signals: list[Any]) -> list[Any]:
    return [item for item in signals if not _is_option_signal(item)]


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


def _freshness_text(signal: Any) -> str:
    freshness_ok = getattr(signal, "freshness_ok", None)
    if freshness_ok is None:
        return "No quote"
    return "Fresh" if freshness_ok else "Stale"


def _shortlist_row(decision: TrendIdeaDecision, symbol_map: dict[str, str]) -> list[str]:
    signal = decision.signal
    direction = decision.direction.value if decision.direction is not None else ""
    return [
        _instrument_for_signal(signal, symbol_map),
        _setup_for_signal(signal),
        f"{decision.idea_score:.4f}",
        f"{decision.directional_managers}/{decision.opposite_managers}",
        f"{round(float(signal.confidence) * 100)}%",
        _freshness_text(signal),
        directional_contributor_names(signal.contributors_json, direction),
    ]


def _option_flow_for_signal(signal: Any) -> str:
    return "Adding" if float(signal.trend_ewma) >= 0 else "Reducing"


def _option_row(signal: Any, symbol_map: dict[str, str]) -> list[str]:
    direction = "BUY" if float(signal.trend_ewma) >= 0 else "REDUCTION"
    return [
        _option_for_signal(signal, symbol_map),
        _option_flow_for_signal(signal),
        _setup_for_signal(signal),
        f"{abs(float(signal.trend_ewma)) * float(signal.confidence):.4f}",
        f"{signal.buy_managers}/{signal.sell_managers}",
        f"{round(float(signal.confidence) * 100)}%",
        directional_contributor_names(signal.contributors_json, direction),
    ]


def _select_option_signals(signals: list[Any], put_call: str) -> list[Any]:
    normalized = put_call.strip().upper()
    return sorted(
        [item for item in signals if str(item.put_call or "").strip().upper() == normalized],
        key=lambda item: (
            -(abs(float(item.trend_ewma)) * float(item.confidence)),
            -abs(float(item.trend_ewma)),
            str(item.instrument_key),
        ),
    )[:OPTIONS_OUTPUT_MAX_ROWS]


def _print_option_trend_sections(option_signals: list[Any], symbol_map: dict[str, str]) -> None:
    headers = ["Option", "Flow", "Setup", "Idea Score", "Support", "Confidence", "Top Contributors"]
    calls = _select_option_signals(option_signals, "CALL")
    puts = _select_option_signals(option_signals, "PUT")
    _print_section("Top Call Option Trends", headers, [_option_row(item, symbol_map) for item in calls])
    _print_section("Top Put Option Trends", headers, [_option_row(item, symbol_map) for item in puts])


def _print_shortlist(
    *,
    quarter: str,
    signals: list[Any],
    symbol_map: dict[str, str],
    min_conf: float,
    limit: int,
    option_signals: list[Any],
) -> None:
    selection = select_trend_ideas(signals, min_conf=min_conf, limit=limit)
    print(f"Report quarter: {quarter}")
    print(f"Stored signals: {len(signals)}")
    print(
        "Directional candidates: "
        f"Buy {selection.buy_candidates_count} | Reduction {selection.reduction_candidates_count}"
    )
    print(
        "Promoted shortlist: "
        f"Buy {len(selection.promoted_buy)} | Reduction {len(selection.promoted_reduction)} "
        f"| Monitored {len(selection.monitored)}"
    )
    headers = ["Instrument", "Setup", "Idea Score", "Support", "Confidence", "Freshness", "Top Contributors"]
    _print_section("Top Buy Ideas", headers, [_shortlist_row(item, symbol_map) for item in selection.promoted_buy])
    _print_section(
        "Top Reduction Trends",
        headers,
        [_shortlist_row(item, symbol_map) for item in selection.promoted_reduction],
    )
    _print_option_trend_sections(option_signals, symbol_map)


def _print_raw_trends(
    *,
    quarter: str,
    signals: list[Any],
    symbol_map: dict[str, str],
    min_conf: float,
    limit: int,
    show_reversals: bool,
    option_signals: list[Any],
) -> None:
    effective_limit = max(1, min(limit, IDEAS_OUTPUT_MAX_ROWS))
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
    sell_min_conf = min(min_conf, SELL_TABLE_MIN_CONF)
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
        return [
            _ticker_for_signal(item, symbol_map),
            _action_for_signal(item),
            _setup_for_signal(item),
            _conviction_target_for_signal(item),
            f"{item.trend_ewma:.4f}",
            f"{item.buy_managers}/{item.sell_managers}",
            _freshness_icon(item),
        ]

    _print_section("Top Buy Trends", headers, [_row(item) for item in buy])
    _print_section("Top Sell Trends", headers, [_row(item) for item in sell])
    _print_option_trend_sections(option_signals, symbol_map)
    if show_reversals:
        _print_section("Reversals", headers, [_row(item) for item in reversals])


def main() -> int:
    parser = argparse.ArgumentParser(description="Print trend signals by quarter.")
    parser.add_argument("--db", default="data/signals.sqlite3", help="Path to SQLite DB (default: data/signals.sqlite3)")
    parser.add_argument("--quarter", help="Report quarter in format YYYYQn (example: 2025Q4).")
    parser.add_argument("--limit", type=int, default=IDEAS_OUTPUT_MAX_ROWS, help="Max rows per section (default: 8, max: 8).")
    parser.add_argument(
        "--min-conf",
        type=float,
        default=0.45,
        help="Buy/reversal confidence threshold (default: 0.45; reductions use min(threshold, 0.35)).",
    )
    parser.add_argument(
        "--show-reversals",
        action="store_true",
        help="Print raw reversals section (disabled by default).",
    )
    parser.add_argument(
        "--symbols-file",
        default="config/cusip_tickers.json",
        help="JSON map for symbols: {\"CUSIP\": \"TICKER\"} or {\"CUSIP|PUTCALL\": \"TICKER\"}.",
    )
    parser.add_argument(
        "--view",
        choices=["shortlist", "raw"],
        default="shortlist",
        help="Output view: long-term shortlist (default) or raw diagnostics.",
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
        option_signals = store.list_trend_option_signals(quarter)
        if not signals and not option_signals:
            print(f"No trend signals found for {quarter}.")
            return 0
        signals = _stock_only_signals(signals)

        if args.view == "raw":
            _print_raw_trends(
                quarter=quarter,
                signals=signals,
                symbol_map=symbol_map,
                min_conf=args.min_conf,
                limit=args.limit,
                show_reversals=args.show_reversals,
                option_signals=option_signals,
            )
        else:
            _print_shortlist(
                quarter=quarter,
                signals=signals,
                symbol_map=symbol_map,
                min_conf=args.min_conf,
                limit=max(1, min(args.limit, IDEAS_OUTPUT_MAX_ROWS)),
                option_signals=option_signals,
            )
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
