from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from signals.application.ports.historical_price_gateway import HistoricalPriceGateway
from signals.domain.trend_ideas import TrendIdeaDecision, TrendIdeaState, select_trend_ideas


DEFAULT_FORWARD_WINDOWS = (30, 90, 180)
PRICE_LOOKAHEAD_DAYS = 7


@dataclass(frozen=True)
class TrendIdeaCandidateCoverage:
    candidates: int
    mapped_symbols: int
    priced_candidates: int
    forward_return_coverage: dict[int, int]
    benchmark_relative_coverage: dict[int, int]


@dataclass(frozen=True)
class TrendIdeaQuarterEvaluation:
    report_quarter: str
    availability_date: date | None
    raw: TrendIdeaCandidateCoverage
    promoted: TrendIdeaCandidateCoverage
    retention_by_regime: dict[str, dict[str, int]]
    support_by_state: dict[str, int]


@dataclass(frozen=True)
class TrendIdeaEvaluation:
    quarters: list[TrendIdeaQuarterEvaluation]


def _parse_acceptance_date(raw_value: str | None) -> date | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    if len(value) == 14 and value.isdigit():
        try:
            return datetime.strptime(value, "%Y%m%d%H%M%S").date()
        except ValueError:
            return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        return None


def _latest_availability_date(store: Any, report_quarter: str) -> date | None:
    dates = [
        parsed
        for snapshot in store.list_snapshots_for_quarter(report_quarter)
        for parsed in [_parse_acceptance_date(snapshot.acceptance_datetime)]
        if parsed is not None
    ]
    return max(dates) if dates else None


def _symbol_for_decision(decision: TrendIdeaDecision, symbol_map: Mapping[str, str]) -> str | None:
    keys = [
        str(decision.signal.instrument_key or "").strip().upper(),
        str(decision.signal.cusip or "").strip().upper(),
    ]
    for key in keys:
        ticker = symbol_map.get(key)
        if isinstance(ticker, str) and ticker.strip():
            return ticker.strip().upper()
    return None


def _first_price_near_date(series: Mapping[date, float], target: date) -> float | None:
    max_date = target + timedelta(days=PRICE_LOOKAHEAD_DAYS)
    for day in sorted(series):
        if target <= day <= max_date and float(series[day]) > 0:
            return float(series[day])
    return None


def _coverage_for_decisions(
    decisions: Sequence[TrendIdeaDecision],
    *,
    symbol_map: Mapping[str, str],
    availability_date: date | None,
    price_series: Mapping[str, Mapping[date, float]],
    benchmark_series: Mapping[date, float],
    windows: tuple[int, ...],
) -> TrendIdeaCandidateCoverage:
    mapped = [(decision, ticker) for decision in decisions for ticker in [_symbol_for_decision(decision, symbol_map)] if ticker]
    forward = {window: 0 for window in windows}
    benchmark = {window: 0 for window in windows}
    if availability_date is None:
        return TrendIdeaCandidateCoverage(
            candidates=len(decisions),
            mapped_symbols=len(mapped),
            priced_candidates=0,
            forward_return_coverage=forward,
            benchmark_relative_coverage=benchmark,
        )

    priced_candidates = 0
    benchmark_start = _first_price_near_date(benchmark_series, availability_date)
    for _, ticker in mapped:
        series = price_series.get(ticker, {})
        start_price = _first_price_near_date(series, availability_date)
        if start_price is None:
            continue
        priced_candidates += 1
        for window in windows:
            target_date = availability_date + timedelta(days=window)
            end_price = _first_price_near_date(series, target_date)
            if end_price is None:
                continue
            forward[window] += 1
            if benchmark_start is not None and _first_price_near_date(benchmark_series, target_date) is not None:
                benchmark[window] += 1

    return TrendIdeaCandidateCoverage(
        candidates=len(decisions),
        mapped_symbols=len(mapped),
        priced_candidates=priced_candidates,
        forward_return_coverage=forward,
        benchmark_relative_coverage=benchmark,
    )


def _regime_retention(decisions: Sequence[TrendIdeaDecision]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for decision in decisions:
        regime = str(decision.signal.regime or "UNKNOWN")
        state_counts = counts.setdefault(regime, {})
        state_counts[decision.state.value] = state_counts.get(decision.state.value, 0) + 1
    return counts


def _support_summary(decisions: Sequence[TrendIdeaDecision]) -> dict[str, int]:
    counts = {
        "promoted_multi_manager": 0,
        "promoted_persistence": 0,
        "monitored_single_manager": 0,
    }
    for decision in decisions:
        if decision.state == TrendIdeaState.PROMOTED and decision.directional_managers >= 2:
            counts["promoted_multi_manager"] += 1
        elif decision.state == TrendIdeaState.PROMOTED and decision.directional_persistence >= 2:
            counts["promoted_persistence"] += 1
        elif decision.state == TrendIdeaState.MONITOR and decision.directional_managers < 2:
            counts["monitored_single_manager"] += 1
    return counts


def evaluate_trend_ideas(
    store: Any,
    history_gateway: HistoricalPriceGateway,
    *,
    report_quarters: Sequence[str],
    symbol_map: Mapping[str, str],
    benchmark_ticker: str = "SPY",
    min_conf: float = 0.45,
    limit: int = 8,
    windows: tuple[int, ...] = DEFAULT_FORWARD_WINDOWS,
) -> TrendIdeaEvaluation:
    rows: list[TrendIdeaQuarterEvaluation] = []
    for report_quarter in report_quarters:
        signals = store.list_trend_stock_signals(report_quarter)
        selection = select_trend_ideas(signals, min_conf=min_conf)
        raw_decisions = list(selection.promoted_buy) + list(selection.promoted_reduction) + list(selection.monitored)
        promoted_decisions = list(selection.promoted_buy[:limit]) + list(selection.promoted_reduction[:limit])
        availability_date = _latest_availability_date(store, report_quarter)

        price_series: dict[str, dict[date, float]] = {}
        benchmark_series: dict[date, float] = {}
        mapped_tickers = sorted(
            {
                ticker
                for decision in raw_decisions
                for ticker in [_symbol_for_decision(decision, symbol_map)]
                if ticker
            }
        )
        if availability_date is not None and mapped_tickers:
            end_date = availability_date + timedelta(days=max(windows) + PRICE_LOOKAHEAD_DAYS)
            price_series = history_gateway.get_eod_prices(mapped_tickers, availability_date, end_date)
            benchmark_series = history_gateway.get_benchmark_series(benchmark_ticker, availability_date, end_date)

        rows.append(
            TrendIdeaQuarterEvaluation(
                report_quarter=report_quarter,
                availability_date=availability_date,
                raw=_coverage_for_decisions(
                    raw_decisions,
                    symbol_map=symbol_map,
                    availability_date=availability_date,
                    price_series=price_series,
                    benchmark_series=benchmark_series,
                    windows=windows,
                ),
                promoted=_coverage_for_decisions(
                    promoted_decisions,
                    symbol_map=symbol_map,
                    availability_date=availability_date,
                    price_series=price_series,
                    benchmark_series=benchmark_series,
                    windows=windows,
                ),
                retention_by_regime=_regime_retention(raw_decisions),
                support_by_state=_support_summary(raw_decisions),
            )
        )
    return TrendIdeaEvaluation(quarters=rows)
