#!/usr/bin/env python3
"""Backtest trend signals against forward price returns using Yahoo Finance."""
from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from signals.domain.trend_ideas import TrendIdeaState, select_trend_ideas
from signals.domain.trend_telegram_message import load_symbol_map
from signals.infrastructure.market.yfinance_history_gateway import YFinanceHistoryGateway
from signals.infrastructure.storage.sqlite_state_repository import StateStore


FORWARD_WINDOWS = (30, 90, 180)


@dataclass(frozen=True)
class BacktestResult:
    quarter: str
    availability_date: date | None
    strategy: str
    candidates: int
    priced_count: int
    returns: list[float]
    benchmark_returns: list[float]
    excess_returns: list[float]


def _first_price_near_date(series: dict[date, float], target: date, max_lookahead: int = 7) -> float | None:
    max_date = target + timedelta(days=max_lookahead)
    for day in sorted(series):
        if target <= day <= max_date and series[day] > 0:
            return series[day]
    return None


def _symbol_for_signal(signal, symbol_map: dict[str, str]) -> str | None:
    keys = [
        str(signal.instrument_key or "").strip().upper(),
        str(signal.cusip or "").strip().upper(),
    ]
    for key in keys:
        ticker = symbol_map.get(key)
        if isinstance(ticker, str) and ticker.strip():
            # Yahoo Finance uses hyphens for class shares (BRK-B, HEI-A)
            return ticker.strip().upper().replace("/", "-")
    return None


def _parse_acceptance_date(raw_value: str | None) -> date | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    if len(value) == 14 and value.isdigit():
        try:
            from datetime import datetime
            return datetime.strptime(value, "%Y%m%d%H%M%S").date()
        except ValueError:
            return None
    normalized = value.replace("Z", "+00:00")
    try:
        from datetime import datetime
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        return None


def _latest_availability_date(store, report_quarter: str) -> date | None:
    dates = [
        parsed
        for snapshot in store.list_snapshots_for_quarter(report_quarter)
        for parsed in [_parse_acceptance_date(snapshot.acceptance_datetime)]
        if parsed is not None
    ]
    return max(dates) if dates else None


def _run_backtest_for_quarter(
    store,
    gateway,
    report_quarter: str,
    symbol_map: dict[str, str],
    min_conf: float,
    limit: int,
    strategy: str,
) -> BacktestResult | None:
    signals = store.list_trend_stock_signals(report_quarter)
    if not signals:
        return None

    availability_date = _latest_availability_date(store, report_quarter)
    if availability_date is None:
        return None

    if strategy == "promoted":
        selection = select_trend_ideas(signals, min_conf=min_conf, limit=limit)
        candidates = list(selection.promoted_buy) + list(selection.promoted_reduction)
    elif strategy == "top_trend_ewma":
        buy_signals = [s for s in signals if s.trend_ewma > 0]
        candidates_sorted = sorted(buy_signals, key=lambda s: -s.trend_ewma)[:limit]
        candidates = candidates_sorted
    elif strategy == "random":
        import random
        buy_signals = [s for s in signals if s.trend_ewma > 0]
        random.seed(hash(report_quarter))
        sampled = random.sample(buy_signals, min(limit, len(buy_signals))) if buy_signals else []
        candidates = sampled
    elif strategy == "all_buy":
        buy_signals = [s for s in signals if s.trend_ewma > 0]
        candidates = buy_signals
    else:
        return None

    tickers = sorted({
        t for c in candidates
        for t in [_symbol_for_signal(c.signal if hasattr(c, "signal") else c, symbol_map)]
        if t
    })

    if not tickers:
        return BacktestResult(
            quarter=report_quarter,
            availability_date=availability_date,
            strategy=strategy,
            candidates=len(candidates),
            priced_count=0,
            returns=[],
            benchmark_returns=[],
            excess_returns=[],
        )

    end_date = availability_date + timedelta(days=max(FORWARD_WINDOWS) + 7)
    price_series = gateway.get_eod_prices(tickers, availability_date, end_date)
    benchmark_series = gateway.get_benchmark_series("SPY", availability_date, end_date)

    benchmark_starts = {w: _first_price_near_date(benchmark_series, availability_date + timedelta(days=w)) for w in FORWARD_WINDOWS}
    benchmark_start = _first_price_near_date(benchmark_series, availability_date)

    returns: list[float] = []
    benchmark_returns: list[float] = []
    excess_returns: list[float] = []
    priced_count = 0

    for candidate in candidates:
        signal = candidate.signal if hasattr(candidate, "signal") else candidate
        ticker = _symbol_for_signal(signal, symbol_map)
        if not ticker:
            continue
        series = price_series.get(ticker, {})
        start_price = _first_price_near_date(series, availability_date)
        if start_price is None:
            continue
        priced_count += 1

        # Use longest available window for the return calculation
        for window in sorted(FORWARD_WINDOWS, reverse=True):
            end_price = _first_price_near_date(series, availability_date + timedelta(days=window))
            if end_price is not None:
                ret = (end_price - start_price) / start_price
                returns.append(ret)

                if benchmark_start and benchmark_starts.get(window):
                    b_ret = (benchmark_starts[window] - benchmark_start) / benchmark_start
                    benchmark_returns.append(b_ret)
                    excess_returns.append(ret - b_ret)
                break

    return BacktestResult(
        quarter=report_quarter,
        availability_date=availability_date,
        strategy=strategy,
        candidates=len(candidates),
        priced_count=priced_count,
        returns=returns,
        benchmark_returns=benchmark_returns,
        excess_returns=excess_returns,
    )


def _format_result(r: BacktestResult) -> list[str]:
    lines = [
        f"  {r.quarter} ({r.availability_date}) — {r.strategy}",
        f"    Candidates: {r.candidates} | Priced: {r.priced_count}",
    ]
    if r.returns:
        mean_ret = statistics.mean(r.returns) * 100
        hit_rate = sum(1 for x in r.returns if x > 0) / len(r.returns) * 100
        lines.append(f"    Mean return: {mean_ret:+.2f}% | Hit rate: {hit_rate:.0f}% | N={len(r.returns)}")
    if r.excess_returns:
        mean_exc = statistics.mean(r.excess_returns) * 100
        lines.append(f"    Excess vs SPY: {mean_exc:+.2f}%")
    else:
        lines.append("    No return data")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest trend signals against forward returns.")
    parser.add_argument("--db", default="data/signals.sqlite3", help="SQLite DB path.")
    parser.add_argument("--symbols-file", default="config/cusip_tickers.json", help="Symbol map JSON.")
    parser.add_argument("--quarters", nargs="+", help="Specific quarters to test. Default: all.")
    parser.add_argument("--min-conf", type=float, default=0.45)
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1

    store = StateStore(db_path)
    gateway = YFinanceHistoryGateway()
    symbol_map = load_symbol_map(Path(args.symbols_file))

    try:
        quarters = args.quarters or store.list_trend_quarters()
        if not quarters:
            print("No trend quarters found.")
            return 0

        strategies = ["promoted", "top_trend_ewma", "random", "all_buy"]
        all_results: list[BacktestResult] = []

        for quarter in quarters:
            print(f"Processing {quarter}...")
            for strategy in strategies:
                result = _run_backtest_for_quarter(
                    store, gateway, quarter, symbol_map, args.min_conf, args.limit, strategy
                )
                if result:
                    all_results.append(result)

        print("\n" + "=" * 60)
        print("BACKTEST RESULTS")
        print("=" * 60)

        for strategy in strategies:
            strategy_results = [r for r in all_results if r.strategy == strategy]
            print(f"\n## Strategy: {strategy}")
            for r in strategy_results:
                for line in _format_result(r):
                    print(line)

            # Aggregate
            all_returns = [ret for r in strategy_results for ret in r.returns]
            all_excess = [exc for r in strategy_results for exc in r.excess_returns]
            if all_returns:
                mean_ret = statistics.mean(all_returns) * 100
                hit_rate = sum(1 for x in all_returns if x > 0) / len(all_returns) * 100
                print(f"\n  AGGREGATE — Mean return: {mean_ret:+.2f}% | Hit rate: {hit_rate:.0f}% | Total N={len(all_returns)}")
            if all_excess:
                mean_exc = statistics.mean(all_excess) * 100
                print(f"  AGGREGATE — Excess vs SPY: {mean_exc:+.2f}%")

    finally:
        store.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
