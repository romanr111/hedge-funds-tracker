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


def _load_positions_file(path: Path) -> list[str]:
    if not path.exists():
        raise ValueError(f"Positions file not found: {path}")
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid positions JSON file: {path}") from exc
    if not isinstance(payload, list):
        raise ValueError("Positions file must be a JSON array of tickers")
    if any(not isinstance(item, str) for item in payload):
        raise ValueError("Positions file must contain only string tickers")
    return payload


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
        description="Analyze trend behavior for a portfolio JSON tickers list using existing DB snapshots."
    )
    parser.add_argument("--positions-file", required=True, help="JSON file with tickers list, for example: [\"AAPL\",\"MSFT\"]")
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
