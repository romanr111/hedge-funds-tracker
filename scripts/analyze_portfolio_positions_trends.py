#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

try:
    from tracker.application.use_cases.analyze_portfolio_positions_trends import (
        PortfolioPositionsTrendResult,
        analyze_portfolio_positions_trends,
    )
    from tracker.config import load_managers
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
        analyze_portfolio_positions_trends,
    )
    from tracker.config import load_managers
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


def _format_float(value: float | None, digits: int) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _trend_score(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.4f}"


def _result_to_rows(result: PortfolioPositionsTrendResult) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in result.rows:
        mapped = ",".join(row.mapped_keys) if row.mapped_keys else "-"
        behavior = f"{row.fund_behavior.buy}/{row.fund_behavior.sell}/{row.fund_behavior.hold}"
        coverage = f"{row.fund_behavior.analyzed}/{row.fund_behavior.total}"
        dominant = row.fund_behavior.dominant or "-"
        rows.append(
            [
                row.ticker,
                row.status,
                _trend_score(row.trend.score),
                _format_float(row.trend.delta, 4),
                _format_float(row.trend.confidence, 3),
                row.trend.regime or "-",
                behavior,
                coverage,
                dominant,
                mapped,
                row.note or "-",
            ]
        )
    return rows


def _write_output_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=False) + "\n")


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
    store = StateStore(db_path)
    try:
        try:
            result = analyze_portfolio_positions_trends(
                store=store,
                managers=managers,
                tickers=tickers,
                symbol_map=symbol_map,
                target_quarter=args.quarter,
            )
        except ValueError as exc:
            print(str(exc))
            return 1

        print(f"Report quarter: {result.report_quarter}")
        print(f"Previous quarter: {result.previous_quarter}")
        print(f"Status: {result.status}")

        headers = [
            "Ticker",
            "Status",
            "Trend",
            "Delta",
            "Conf",
            "Regime",
            "Buy/Sell/Hold",
            "Coverage",
            "Dominant",
            "Mapped Keys",
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
