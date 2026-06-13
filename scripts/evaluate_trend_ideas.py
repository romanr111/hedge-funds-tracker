#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from signals.application.use_cases.evaluate_trend_ideas import evaluate_trend_ideas
from signals.domain.trend_telegram_message import load_symbol_map
from signals.infrastructure.market import StooqHistoryGateway
from signals.infrastructure.storage.sqlite_state_repository import StateStore


def _format_coverage(label: str, coverage: object) -> list[str]:
    return [
        f"{label}: candidates={coverage.candidates} mapped={coverage.mapped_symbols} priced={coverage.priced_candidates}",
        "  Forward return coverage: "
        + " | ".join(f"{window}d={count}" for window, count in coverage.forward_return_coverage.items()),
        "  Benchmark-relative coverage: "
        + " | ".join(f"{window}d={count}" for window, count in coverage.benchmark_relative_coverage.items()),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate promoted trend ideas against forward price coverage.")
    parser.add_argument("--db", default="data/signals.sqlite3", help="Path to SQLite DB.")
    parser.add_argument("--quarters", nargs="+", help="Trend report quarters to evaluate. Default: all stored trend quarters.")
    parser.add_argument("--symbols-file", default="config/cusip_tickers.json", help="CUSIP/instrument key to ticker JSON map.")
    parser.add_argument("--benchmark", default="SPY", help="Benchmark ticker for relative-return coverage.")
    parser.add_argument("--min-conf", type=float, default=0.45, help="Directional candidate confidence threshold.")
    parser.add_argument("--limit", type=int, default=8, help="Promoted rows per buy/reduction side.")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1
    if not 0 <= args.min_conf <= 1:
        print("--min-conf must be between 0 and 1")
        return 1
    if args.limit <= 0:
        print("--limit must be > 0")
        return 1

    store = StateStore(db_path)
    try:
        quarters = args.quarters or store.list_trend_quarters()
        if not quarters:
            print("No trend quarters found.")
            return 0
        evaluation = evaluate_trend_ideas(
            store,
            StooqHistoryGateway(),
            report_quarters=quarters,
            symbol_map=load_symbol_map(Path(args.symbols_file)),
            benchmark_ticker=args.benchmark,
            min_conf=args.min_conf,
            limit=args.limit,
        )
    finally:
        store.close()

    for row in evaluation.quarters:
        print()
        print(f"Report quarter: {row.report_quarter}")
        print(f"Idea availability date: {row.availability_date or 'Unavailable'}")
        for line in _format_coverage("Raw directional candidates", row.raw):
            print(line)
        for line in _format_coverage("Promoted shortlist", row.promoted):
            print(line)
        print(f"Retention by regime: {row.retention_by_regime}")
        print(f"Support summary: {row.support_by_state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
