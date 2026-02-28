#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

try:
    from tracker.application.use_cases.analyze_portfolio_positions_trends import (
        PortfolioPositionsTrendResult,
        PortfolioTickerTrendRow,
        analyze_portfolio_positions_trends,
    )
    from tracker.config import load_managers
    from tracker.infrastructure.market import StooqPriceGateway
    from tracker.domain.trend_presentation import freshness_icon
    from tracker.domain.trend_telegram_message import load_symbol_map
    from tracker.infrastructure.storage.sqlite_state_repository import StateStore
except ModuleNotFoundError as exc:
    if exc.name != "tracker":
        raise
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from tracker.application.use_cases.analyze_portfolio_positions_trends import (
        PortfolioPositionsTrendResult,
        PortfolioTickerTrendRow,
        analyze_portfolio_positions_trends,
    )
    from tracker.config import load_managers
    from tracker.infrastructure.market import StooqPriceGateway
    from tracker.domain.trend_presentation import freshness_icon
    from tracker.domain.trend_telegram_message import load_symbol_map
    from tracker.infrastructure.storage.sqlite_state_repository import StateStore


CHINA_ADR_TICKER_MAP: dict[str, str] = {
    "9988": "BABA",
    "BRK B": "BRK.B",
}


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def _line(parts: list[str]) -> str:
        return " | ".join(part.ljust(widths[idx]) for idx, part in enumerate(parts))

    separator = "-+-".join("-" * width for width in widths)
    output = [_line(headers), separator]
    output.extend(_line(row) for row in rows)
    return "\n".join(output)


def _normalize_ticker(raw: str) -> str:
    ticker = raw.strip().upper()
    return CHINA_ADR_TICKER_MAP.get(ticker, ticker)


def _ticker_lookup_key(raw_ticker: str) -> str:
    ticker = raw_ticker.strip().upper()
    parts = [part for part in re.split(r"[./\s]+", ticker) if part]
    if len(parts) >= 2:
        return "/".join(parts)
    return ticker


def _normalize_tickers(items: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in items:
        ticker = _normalize_ticker(raw)
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        normalized.append(ticker)
    return normalized


def _looks_like_ticker_key(raw_key: str) -> bool:
    key = raw_key.strip().upper()
    if not key:
        return False
    if "TOTAL" in key or "VALUE" in key:
        return False
    if all(ch.isdigit() for ch in key):
        return len(key) in {4, 5, 6}
    return any(ch.isalpha() for ch in key)


def _extract_tickers_from_stocks_value(value: Any) -> list[str]:
    if isinstance(value, dict):
        extracted: list[str] = []
        for key, nested in value.items():
            if isinstance(key, str) and isinstance(nested, (int, float)) and _looks_like_ticker_key(key):
                extracted.append(key)
            extracted.extend(_extract_tickers_from_stocks_value(nested))
        return extracted
    if isinstance(value, list):
        extracted: list[str] = []
        for item in value:
            if isinstance(item, str) and _looks_like_ticker_key(item):
                extracted.append(item)
            extracted.extend(_extract_tickers_from_stocks_value(item))
        return extracted
    return []


def _extract_tickers_from_stocks_sections(payload: Any) -> list[str]:
    extracted: list[str] = []
    found_stocks_key = False

    def _walk(node: Any) -> None:
        nonlocal found_stocks_key
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and "stocks" in key.lower():
                    found_stocks_key = True
                    extracted.extend(_extract_tickers_from_stocks_value(value))
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    if not found_stocks_key:
        return []
    return extracted


def _load_positions_file(path: Path) -> list[str]:
    if not path.exists():
        raise ValueError(f"Positions file not found: {path}")
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid positions JSON file: {path}") from exc
    if isinstance(payload, list):
        if any(not isinstance(item, str) for item in payload):
            raise ValueError("Positions file must contain only string tickers")
        tickers = _normalize_tickers(payload)
        if not tickers:
            raise ValueError("Positions file must contain at least one non-empty ticker")
        return tickers

    tickers = _normalize_tickers(_extract_tickers_from_stocks_sections(payload))
    if tickers:
        return tickers
    raise ValueError(
        "Positions file must be either a JSON array of string tickers "
        "or an object containing at least one key with 'Stocks' and ticker values"
    )


def _trend_score(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.4f}"


def _action_priority(action: str) -> int:
    order = {
        "BUY": 0,
        "SELL": 1,
        "IDEA_BUY": 2,
        "IDEA_SELL": 3,
        "IDEA_NEUTRAL": 4,
        "MONITOR_BUY": 5,
        "MONITOR_SELL": 6,
        "MONITOR_NEUTRAL": 7,
    }
    return order.get(action, 8)


def _sorted_result_rows(result: PortfolioPositionsTrendResult) -> list[PortfolioTickerTrendRow]:
    indexed_rows = list(enumerate(result.rows))

    def _key(item: tuple[int, PortfolioTickerTrendRow]) -> tuple[object, ...]:
        idx, row = item
        if row.status != "OK":
            return (1, idx)
        trend_abs = abs(row.trend.score) if row.trend.score is not None else 0.0
        return (
            0,
            _action_priority(row.presentation.action),
            -trend_abs,
            row.ticker,
        )

    return [item[1] for item in sorted(indexed_rows, key=_key)]


def _result_to_rows(result: PortfolioPositionsTrendResult) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in _sorted_result_rows(result):
        if row.status == "OK":
            action = row.presentation.action
            setup = row.presentation.setup
            conviction = row.presentation.conviction_target
            trend = _trend_score(row.trend.score)
            consensus = f"{row.presentation.consensus_buy}/{row.presentation.consensus_sell}"
            data_fresh = freshness_icon(row.presentation.data_fresh)
            note = row.note or "-"
        else:
            action = "NO_DATA"
            setup = "-"
            conviction = "-"
            trend = "-"
            consensus = "-"
            data_fresh = "-"
            note = row.note or "-"
        rows.append(
            [
                row.ticker,
                action,
                setup,
                conviction,
                trend,
                consensus,
                data_fresh,
                note,
            ]
        )
    return rows


def _write_output_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=False) + "\n")


def _load_live_latest_prices(symbol_map: dict[str, str], tickers: list[str]) -> dict[str, float] | None:
    if not symbol_map or not tickers:
        return None
    requested_ticker_keys = {_ticker_lookup_key(ticker) for ticker in tickers if isinstance(ticker, str) and ticker.strip()}
    if not requested_ticker_keys:
        return None

    key_to_ticker = {
        key.strip().upper(): ticker.strip().upper()
        for key, ticker in symbol_map.items()
        if (
            isinstance(key, str)
            and isinstance(ticker, str)
            and key.strip()
            and ticker.strip()
            and _ticker_lookup_key(ticker) in requested_ticker_keys
        )
    }
    if not key_to_ticker:
        return None

    gateway = StooqPriceGateway()
    ticker_prices = gateway.get_latest_prices(sorted(set(key_to_ticker.values())))
    if not ticker_prices:
        return None

    latest_prices: dict[str, float] = {}
    for key, ticker in key_to_ticker.items():
        price = ticker_prices.get(ticker)
        if price is not None:
            latest_prices[key] = price
    return latest_prices or None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze trend behavior for portfolio positions JSON using existing DB snapshots."
    )
    parser.add_argument(
        "--positions-file",
        required=True,
        help=(
            "JSON file with either a tickers list "
            "(example: [\"AAPL\",\"MSFT\"]) or a nested object with keys containing 'Stocks'"
        ),
    )
    parser.add_argument("--db", default="data/tracker.sqlite3", help="Path to SQLite DB (default: data/tracker.sqlite3)")
    parser.add_argument("--symbols-file", default="config/cusip_tickers.json", help="CUSIP/instrument_key to ticker mapping JSON")
    parser.add_argument("--quarter", help="Optional target quarter in format YYYYQn")
    parser.add_argument("--output-json", help="Optional path to write analysis result JSON")
    parser.add_argument("--managers-file", default="config/managers.json", help="Managers file path (default: config/managers.json)")
    parser.add_argument(
        "--skip-live-prices",
        action="store_true",
        help="Skip live price lookup for Data Fresh calculation.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    try:
        tickers = _load_positions_file(Path(args.positions_file))
    except ValueError as exc:
        print(str(exc))
        return 1

    try:
        managers = load_managers(Path(args.managers_file), None)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Failed to load managers file: {exc}")
        return 1

    symbol_map = load_symbol_map(Path(args.symbols_file))
    latest_prices = None if args.skip_live_prices else _load_live_latest_prices(symbol_map, tickers)
    store = StateStore(db_path)
    try:
        try:
            result = analyze_portfolio_positions_trends(
                store=store,
                managers=managers,
                tickers=tickers,
                symbol_map=symbol_map,
                target_quarter=args.quarter,
                latest_prices=latest_prices,
            )
        except ValueError as exc:
            print(str(exc))
            return 1

        print(f"Report quarter: {result.report_quarter}")
        print(f"Previous quarter: {result.previous_quarter}")
        print(f"Status: {result.status}")

        headers = [
            "Ticker",
            "Action",
            "Setup (Regime)",
            "Conviction / Target",
            "Trend",
            "Consensus (+/-)",
            "Data Fresh",
            "Note",
        ]
        table_rows = _result_to_rows(result)
        print()
        print(_format_table(headers, table_rows))

        if args.output_json:
            output_path = Path(args.output_json)
            _write_output_json(output_path, result.to_dict())
            print()
            print(f"JSON output written: {output_path}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
