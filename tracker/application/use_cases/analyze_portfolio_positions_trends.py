from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from statistics import mean
from typing import Any, Protocol, Sequence

from tracker.application.use_cases.run_trend_engine import WINDOW_QUARTERS
from tracker.domain.models import ManagerQuarterSnapshot
from tracker.domain.quarters import parse_report_quarter, quarter_sort_key
from tracker.domain.trends import (
    _trade_flow_delta,
    compute_trend_signals,
    instrument_key,
)


class _WeightedManagerLike(Protocol):
    cik: str
    weight: float


@dataclass(frozen=True)
class PortfolioTickerTrend:
    score: float | None
    delta: float | None
    confidence: float | None
    regime: str | None


@dataclass(frozen=True)
class PortfolioTickerFundBehavior:
    buy: int
    sell: int
    hold: int
    analyzed: int
    total: int
    dominant: str | None


@dataclass(frozen=True)
class PortfolioTickerTrendRow:
    ticker: str
    status: str
    mapped_keys: list[str]
    trend: PortfolioTickerTrend
    fund_behavior: PortfolioTickerFundBehavior
    note: str | None


@dataclass(frozen=True)
class PortfolioPositionsTrendResult:
    report_quarter: str
    previous_quarter: str
    status: str
    rows: list[PortfolioTickerTrendRow]

    def to_dict(self) -> dict[str, object]:
        return {
            "report_quarter": self.report_quarter,
            "previous_quarter": self.previous_quarter,
            "status": self.status,
            "rows": [asdict(row) for row in self.rows],
        }


@dataclass(frozen=True)
class _WeightEntry:
    weight: float
    shares: int


def _ticker_lookup_key(raw_ticker: str) -> str:
    ticker = raw_ticker.strip().upper()
    parts = [part for part in re.split(r"[./\s]+", ticker) if part]
    if len(parts) >= 2:
        return "/".join(parts)
    return ticker


def _normalize_tickers(tickers: Sequence[str]) -> list[str]:
    if not isinstance(tickers, Sequence):
        raise ValueError("tickers must be a sequence of strings")
    normalized: list[str] = []
    seen_lookup_keys: set[str] = set()
    for raw in tickers:
        if not isinstance(raw, str):
            raise ValueError("tickers must contain only strings")
        ticker = raw.strip().upper()
        if not ticker:
            continue
        lookup_key = _ticker_lookup_key(ticker)
        if lookup_key in seen_lookup_keys:
            continue
        seen_lookup_keys.add(lookup_key)
        normalized.append(ticker)
    if not normalized:
        raise ValueError("tickers list must contain at least one non-empty ticker")
    return normalized


def _build_ticker_to_keys(symbol_map: dict[str, str]) -> dict[str, list[str]]:
    by_ticker: dict[str, set[str]] = {}
    for raw_key, raw_ticker in symbol_map.items():
        if not isinstance(raw_key, str) or not isinstance(raw_ticker, str):
            continue
        key = raw_key.strip().upper()
        ticker = _ticker_lookup_key(raw_ticker)
        if not key or not ticker:
            continue
        by_ticker.setdefault(ticker, set()).add(key)
    return {ticker: sorted(keys) for ticker, keys in by_ticker.items()}


def _weights_by_instrument(snapshot: ManagerQuarterSnapshot) -> dict[str, _WeightEntry]:
    total_value = 0
    value_by_key: dict[str, int] = {}
    shares_by_key: dict[str, int] = {}

    for position in snapshot.positions:
        raw_cusip = position.get("cusip")
        if not isinstance(raw_cusip, str):
            continue
        cusip = raw_cusip.strip()
        if not cusip:
            continue

        raw_put_call = position.get("put_call")
        put_call = raw_put_call.strip() if isinstance(raw_put_call, str) else None
        key = instrument_key(cusip, put_call)

        value = position.get("value")
        if not isinstance(value, int) or value <= 0:
            continue
        value_by_key[key] = value_by_key.get(key, 0) + value
        total_value += value

        shares = position.get("shares")
        if isinstance(shares, int) and shares > 0:
            shares_by_key[key] = shares_by_key.get(key, 0) + shares

    if total_value <= 0:
        return {}

    return {
        key: _WeightEntry(weight=(float(value) / float(total_value)), shares=shares_by_key.get(key, 0))
        for key, value in value_by_key.items()
    }


def _snapshot_index(snapshots: list[ManagerQuarterSnapshot]) -> dict[str, dict[str, ManagerQuarterSnapshot]]:
    by_quarter: dict[str, dict[str, ManagerQuarterSnapshot]] = {}
    for snapshot in snapshots:
        by_quarter.setdefault(snapshot.report_quarter, {})[snapshot.cik] = snapshot
    return by_quarter


def _aggregate_manager_behavior(
    *,
    manager_ciks: list[str],
    prev_weights_by_manager: dict[str, dict[str, _WeightEntry]],
    curr_weights_by_manager: dict[str, dict[str, _WeightEntry]],
    keys: list[str],
) -> PortfolioTickerFundBehavior:
    buy = 0
    sell = 0
    hold = 0
    analyzed = 0

    for manager_cik in manager_ciks:
        prev_weights = prev_weights_by_manager.get(manager_cik, {})
        curr_weights = curr_weights_by_manager.get(manager_cik, {})

        prev_weight = sum(prev_weights.get(key, _WeightEntry(weight=0.0, shares=0)).weight for key in keys)
        curr_weight = sum(curr_weights.get(key, _WeightEntry(weight=0.0, shares=0)).weight for key in keys)
        prev_shares = sum(prev_weights.get(key, _WeightEntry(weight=0.0, shares=0)).shares for key in keys)
        curr_shares = sum(curr_weights.get(key, _WeightEntry(weight=0.0, shares=0)).shares for key in keys)

        if prev_weight <= 0 and curr_weight <= 0 and prev_shares <= 0 and curr_shares <= 0:
            continue

        analyzed += 1
        trade_dw = _trade_flow_delta(
            prev_weight=prev_weight,
            curr_weight=curr_weight,
            prev_shares=prev_shares,
            curr_shares=curr_shares,
        )
        if trade_dw > 1e-12:
            buy += 1
        elif trade_dw < -1e-12:
            sell += 1
        else:
            hold += 1

    dominant: str | None
    if analyzed <= 0:
        dominant = None
    else:
        dominant = sorted(
            [("BUY", buy), ("SELL", sell), ("HOLD", hold)],
            key=lambda item: (-item[1], item[0]),
        )[0][0]

    return PortfolioTickerFundBehavior(
        buy=buy,
        sell=sell,
        hold=hold,
        analyzed=analyzed,
        total=len(manager_ciks),
        dominant=dominant,
    )


def _aggregate_ticker_trend(signals: list[Any]) -> PortfolioTickerTrend:
    if not signals:
        return PortfolioTickerTrend(score=None, delta=None, confidence=None, regime=None)

    score = sum(float(signal.trend_ewma) for signal in signals)
    delta = sum(float(signal.trend_delta) for signal in signals)

    denominator = sum(abs(float(signal.trend_ewma)) for signal in signals)
    if denominator > 0:
        confidence = sum(abs(float(signal.trend_ewma)) * float(signal.confidence) for signal in signals) / denominator
    else:
        confidence = mean(float(signal.confidence) for signal in signals)

    regime_source = sorted(signals, key=lambda item: (-abs(float(item.trend_ewma)), item.instrument_key))[0]
    return PortfolioTickerTrend(score=score, delta=delta, confidence=confidence, regime=str(regime_source.regime))


def _resolve_target_quarter(common_quarters: list[str], target_quarter: str | None) -> str:
    if target_quarter is None:
        return common_quarters[-1]
    normalized = target_quarter.strip().upper()
    if parse_report_quarter(normalized) is None:
        raise ValueError("target_quarter must use format YYYYQn")
    if normalized not in common_quarters:
        raise ValueError("target_quarter is not available in common manager quarters")
    return normalized


def analyze_portfolio_positions_trends(
    *,
    store: Any,
    managers: Sequence[_WeightedManagerLike],
    tickers: Sequence[str],
    symbol_map: dict[str, str],
    target_quarter: str | None = None,
    blend_mode: str = "tactical",
) -> PortfolioPositionsTrendResult:
    normalized_tickers = _normalize_tickers(tickers)

    active_managers = [manager for manager in managers if float(manager.weight) > 0]
    if not active_managers:
        raise ValueError("managers must contain at least one positive weight")

    manager_weights = {manager.cik: float(manager.weight) for manager in active_managers}
    manager_ciks = [manager.cik for manager in active_managers]

    common_quarters = sorted(store.list_common_report_quarters(manager_ciks), key=quarter_sort_key)
    if not common_quarters:
        raise ValueError("No common completed quarters found for selected managers")

    resolved_target_quarter = _resolve_target_quarter(common_quarters, target_quarter)
    target_idx = common_quarters.index(resolved_target_quarter)
    quarter_window = common_quarters[max(0, target_idx - (WINDOW_QUARTERS - 1)) : target_idx + 1]
    if len(quarter_window) < 2:
        raise ValueError("At least 2 common quarters are required to analyze ticker trends")

    snapshots = store.list_snapshots_for_quarters(quarter_window, manager_ciks)
    snapshots_by_quarter = _snapshot_index(snapshots)
    for quarter in quarter_window:
        for manager in active_managers:
            if manager.cik not in snapshots_by_quarter.get(quarter, {}):
                raise ValueError(
                    "Incomplete snapshot matrix for selected managers and quarter window"
                )

    computed = compute_trend_signals(
        quarters=quarter_window,
        snapshots_by_quarter=snapshots_by_quarter,
        manager_weights=manager_weights,
        blend_mode=blend_mode,
    )
    signals_by_key = {signal.instrument_key: signal for signal in computed.signals}

    previous_quarter = quarter_window[-2]
    prev_snapshots = snapshots_by_quarter[previous_quarter]
    curr_snapshots = snapshots_by_quarter[resolved_target_quarter]
    prev_weights_by_manager = {
        manager_cik: _weights_by_instrument(snapshot)
        for manager_cik, snapshot in prev_snapshots.items()
    }
    curr_weights_by_manager = {
        manager_cik: _weights_by_instrument(snapshot)
        for manager_cik, snapshot in curr_snapshots.items()
    }

    ticker_to_keys = _build_ticker_to_keys(symbol_map)
    rows: list[PortfolioTickerTrendRow] = []

    for ticker in normalized_tickers:
        mapped_keys = ticker_to_keys.get(_ticker_lookup_key(ticker), [])
        if not mapped_keys:
            rows.append(
                PortfolioTickerTrendRow(
                    ticker=ticker,
                    status="NO_DATA",
                    mapped_keys=[],
                    trend=PortfolioTickerTrend(score=None, delta=None, confidence=None, regime=None),
                    fund_behavior=PortfolioTickerFundBehavior(
                        buy=0,
                        sell=0,
                        hold=0,
                        analyzed=0,
                        total=len(manager_ciks),
                        dominant=None,
                    ),
                    note="Ticker is not mapped in symbols file",
                )
            )
            continue

        behavior = _aggregate_manager_behavior(
            manager_ciks=manager_ciks,
            prev_weights_by_manager=prev_weights_by_manager,
            curr_weights_by_manager=curr_weights_by_manager,
            keys=mapped_keys,
        )
        matched_signals = [signals_by_key[key] for key in mapped_keys if key in signals_by_key]

        if behavior.analyzed <= 0:
            rows.append(
                PortfolioTickerTrendRow(
                    ticker=ticker,
                    status="NO_DATA",
                    mapped_keys=mapped_keys,
                    trend=PortfolioTickerTrend(score=None, delta=None, confidence=None, regime=None),
                    fund_behavior=behavior,
                    note="No manager positions found for selected quarter comparison",
                )
            )
            continue

        if not matched_signals:
            rows.append(
                PortfolioTickerTrendRow(
                    ticker=ticker,
                    status="NO_DATA",
                    mapped_keys=mapped_keys,
                    trend=PortfolioTickerTrend(score=None, delta=None, confidence=None, regime=None),
                    fund_behavior=behavior,
                    note="No trend signals computed for mapped keys in selected quarter",
                )
            )
            continue

        rows.append(
            PortfolioTickerTrendRow(
                ticker=ticker,
                status="OK",
                mapped_keys=mapped_keys,
                trend=_aggregate_ticker_trend(matched_signals),
                fund_behavior=behavior,
                note=None,
            )
        )

    overall_status = "OK" if any(row.status == "OK" for row in rows) else "NO_DATA"
    return PortfolioPositionsTrendResult(
        report_quarter=resolved_target_quarter,
        previous_quarter=previous_quarter,
        status=overall_status,
        rows=rows,
    )
