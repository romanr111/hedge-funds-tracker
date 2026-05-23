from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tracker.application.ports.notifier import NotifierPort
from tracker.application.use_cases.notify_quarterly_reports_completion import (
    notify_if_all_reports_published_for_current_quarter,
)
from tracker.application.use_cases.notify_trend_analysis_summary import notify_trend_analysis_summary
from tracker.application.use_cases.backfill_trend_history import run_backfill_trend_history
from tracker.application.use_cases.run_trend_engine import run_trend_engine_for_latest_completed_quarter
from tracker.application.use_cases.sync_quarter_snapshots import sync_quarter_snapshots
from tracker.application.use_cases.export_trend_summary import export_trend_summary_if_changed
from tracker.application.use_cases.track_manager import process_manager
from tracker.composition import build_notifier_list, build_runtime
from tracker.config import load_config
from tracker.domain.exceptions import StateStoreError
from tracker.domain.models import Manager
from tracker.domain.quarters import parse_report_quarter
from tracker.domain.trend_presentation import (
    action_for_signal,
    conviction_target,
    directional_contributor_names,
    freshness_icon,
    setup_for_regime,
    target_confidence_for_regime,
)
from tracker.domain.trend_ideas import TrendIdeaDecision, select_trend_ideas
from tracker.domain.timing import format_local_datetime, now_kyiv
from tracker.infrastructure.export.xlsx_exporter import (
    PortfolioValueTrendData,
    TrendSummaryWorkbookData,
    TrendTable,
)
from tracker.infrastructure.logging import configure_logging, log_context, new_trace_id
from tracker.infrastructure.market import StooqPriceGateway

SELL_TABLE_MIN_CONF = 0.35
BUY_TABLE_MIN_TREND = 0.001
IDEAS_OUTPUT_MAX_ROWS = 8
PORTFOLIO_VALUE_HOLD_BAND = 0.05
PORTFOLIO_SHARES_HOLD_BAND = 0.05


@dataclass(frozen=True)
class _PortfolioValueTrendSummary:
    report_quarter: str
    previous_quarter: str
    selected_managers: int
    analyzed_managers: int
    missing_current: int
    missing_previous: int
    growing_managers: int
    holding_managers: int
    reducing_managers: int
    previous_total_value_k: int
    current_total_value_k: int
    shares_analyzed_managers: int
    shares_growing_managers: int
    shares_holding_managers: int
    shares_reducing_managers: int
    previous_total_shares: int
    current_total_shares: int


def _send_notifications(notifiers: Sequence[NotifierPort], subject: str, body: str) -> None:
    for notifier in notifiers:
        notifier.send(subject, body)


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def render(parts: list[str]) -> str:
        return " | ".join(part.ljust(widths[idx]) for idx, part in enumerate(parts))

    separator = "-+-".join("-" * width for width in widths)
    output = [render(headers), separator]
    output.extend(render(row) for row in rows)
    return "\n".join(output)


def _load_symbol_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
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
    identifiers = {
        str(signal.instrument_key or "").strip().upper(),
        str(signal.cusip or "").strip().upper(),
    }
    if ticker not in identifiers:
        return ticker
    identifier = str(signal.instrument_key or signal.cusip or "UNKNOWN").strip().upper()
    issuer = str(signal.issuer_name or "Unknown issuer").strip()
    return f"{issuer} [unmapped: {identifier}]"


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


def _print_section(title: str, headers: list[str], rows: list[list[str]]) -> None:
    print()
    print(title)
    if not rows:
        print("(empty)")
        return
    print(_format_table(headers, rows))


def _previous_report_quarter(report_quarter: str) -> str | None:
    parsed = parse_report_quarter(report_quarter)
    if parsed is None:
        return None
    year, quarter = parsed
    if quarter == 1:
        return f"{year - 1}Q4"
    return f"{year}Q{quarter - 1}"


def _portfolio_value_change_ratio(previous_value_k: int, current_value_k: int) -> float:
    if previous_value_k <= 0:
        return 0.0 if current_value_k <= 0 else 1.0
    return (current_value_k - previous_value_k) / previous_value_k


def _portfolio_value_direction(change_ratio: float) -> str:
    if change_ratio > PORTFOLIO_VALUE_HOLD_BAND:
        return "Growing"
    if change_ratio < -PORTFOLIO_VALUE_HOLD_BAND:
        return "Reducing"
    return "Holding"


def _portfolio_shares_direction(change_ratio: float) -> str:
    if change_ratio > PORTFOLIO_SHARES_HOLD_BAND:
        return "Growing"
    if change_ratio < -PORTFOLIO_SHARES_HOLD_BAND:
        return "Reducing"
    return "Holding"


def _format_value_k(value_k: int) -> str:
    value_billions = round(value_k / 1_000_000_000)
    return f"${value_billions:,}B"


def _format_int(value: int) -> str:
    return f"{value:,}"


def _format_signed_ratio(value: float) -> str:
    return f"{value * 100:+.1f}%"


def _format_ratio(value: float) -> str:
    return f"{value * 100:.1f}%"


def _compute_portfolio_value_trend_summary(
    store: Any,
    report_quarter: str,
    manager_ciks: list[str],
) -> _PortfolioValueTrendSummary | None:
    selected_ciks = [cik.strip() for cik in manager_ciks if cik and cik.strip()]
    if not selected_ciks:
        return None

    previous_quarter = _previous_report_quarter(report_quarter)
    if previous_quarter is None:
        return None

    snapshots = store.list_snapshots_for_quarters([previous_quarter, report_quarter], selected_ciks)
    snapshot_by_key = {(snapshot.cik, snapshot.report_quarter): snapshot for snapshot in snapshots}

    analyzed_managers = 0
    missing_current = 0
    missing_previous = 0
    growing_managers = 0
    holding_managers = 0
    reducing_managers = 0
    previous_total_value_k = 0
    current_total_value_k = 0
    shares_analyzed_managers = 0
    shares_growing_managers = 0
    shares_holding_managers = 0
    shares_reducing_managers = 0
    previous_total_shares = 0
    current_total_shares = 0

    def _total_snapshot_shares(snapshot: Any) -> int | None:
        total_shares = 0
        has_shares = False
        for position in snapshot.positions:
            shares = position.get("shares")
            if isinstance(shares, int) and shares > 0:
                total_shares += shares
                has_shares = True
        return total_shares if has_shares else None

    for cik in selected_ciks:
        current_snapshot = snapshot_by_key.get((cik, report_quarter))
        if current_snapshot is None:
            missing_current += 1
            continue
        previous_snapshot = snapshot_by_key.get((cik, previous_quarter))
        if previous_snapshot is None:
            missing_previous += 1
            continue

        analyzed_managers += 1
        previous_total_value_k += previous_snapshot.aum_value_k
        current_total_value_k += current_snapshot.aum_value_k

        change_ratio = _portfolio_value_change_ratio(previous_snapshot.aum_value_k, current_snapshot.aum_value_k)
        direction = _portfolio_value_direction(change_ratio)
        if direction == "Growing":
            growing_managers += 1
        elif direction == "Reducing":
            reducing_managers += 1
        else:
            holding_managers += 1

        previous_shares = _total_snapshot_shares(previous_snapshot)
        current_shares = _total_snapshot_shares(current_snapshot)
        if previous_shares is not None and current_shares is not None:
            shares_analyzed_managers += 1
            previous_total_shares += previous_shares
            current_total_shares += current_shares
            shares_change_ratio = _portfolio_value_change_ratio(previous_shares, current_shares)
            shares_direction = _portfolio_shares_direction(shares_change_ratio)
            if shares_direction == "Growing":
                shares_growing_managers += 1
            elif shares_direction == "Reducing":
                shares_reducing_managers += 1
            else:
                shares_holding_managers += 1

    return _PortfolioValueTrendSummary(
        report_quarter=report_quarter,
        previous_quarter=previous_quarter,
        selected_managers=len(selected_ciks),
        analyzed_managers=analyzed_managers,
        missing_current=missing_current,
        missing_previous=missing_previous,
        growing_managers=growing_managers,
        holding_managers=holding_managers,
        reducing_managers=reducing_managers,
        previous_total_value_k=previous_total_value_k,
        current_total_value_k=current_total_value_k,
        shares_analyzed_managers=shares_analyzed_managers,
        shares_growing_managers=shares_growing_managers,
        shares_holding_managers=shares_holding_managers,
        shares_reducing_managers=shares_reducing_managers,
        previous_total_shares=previous_total_shares,
        current_total_shares=current_total_shares,
    )


def _print_portfolio_value_trend_summary(
    store: Any,
    report_quarter: str,
    *,
    manager_ciks: list[str] | None,
) -> None:
    if not manager_ciks:
        return
    summary = _compute_portfolio_value_trend_summary(store, report_quarter, manager_ciks)
    if summary is None:
        return

    print()
    print("Hedge Funds Portfolio Value Trend (QoQ)")
    print(f"Compared quarters: {summary.previous_quarter} -> {summary.report_quarter}")
    print(f"Managers analyzed: {summary.analyzed_managers}/{summary.selected_managers}")

    if summary.analyzed_managers == 0:
        print("Not enough comparable snapshots to determine portfolio value direction.")
        return

    aggregate_change_ratio = _portfolio_value_change_ratio(
        summary.previous_total_value_k,
        summary.current_total_value_k,
    )
    aggregate_direction = _portfolio_value_direction(aggregate_change_ratio)
    print(
        "Aggregate portfolio value: "
        f"{_format_value_k(summary.previous_total_value_k)} -> {_format_value_k(summary.current_total_value_k)} "
        f"({_format_signed_ratio(aggregate_change_ratio)} {aggregate_direction})"
    )
    if summary.shares_analyzed_managers > 0:
        aggregate_shares_change_ratio = _portfolio_value_change_ratio(
            summary.previous_total_shares,
            summary.current_total_shares,
        )
        aggregate_shares_direction = _portfolio_shares_direction(aggregate_shares_change_ratio)
        print(
            "Aggregate portfolio shares: "
            f"{_format_int(summary.previous_total_shares)} -> {_format_int(summary.current_total_shares)} "
            f"({_format_signed_ratio(aggregate_shares_change_ratio)} {aggregate_shares_direction})"
        )

    direction_headers = ["Direction", "Managers", "Share"]
    value_rows = [
        [
            "Growing",
            str(summary.growing_managers),
            _format_ratio(summary.growing_managers / summary.analyzed_managers),
        ],
        [
            "Holding",
            str(summary.holding_managers),
            _format_ratio(summary.holding_managers / summary.analyzed_managers),
        ],
        [
            "Reducing",
            str(summary.reducing_managers),
            _format_ratio(summary.reducing_managers / summary.analyzed_managers),
        ],
    ]
    print()
    print("Value Direction Breakdown")
    print(_format_table(direction_headers, value_rows))

    if summary.shares_analyzed_managers <= 0:
        return
    if summary.shares_analyzed_managers != summary.analyzed_managers:
        print(f"Shares coverage: {summary.shares_analyzed_managers}/{summary.analyzed_managers}")
    shares_rows = [
        [
            "Growing",
            str(summary.shares_growing_managers),
            _format_ratio(summary.shares_growing_managers / summary.shares_analyzed_managers),
        ],
        [
            "Holding",
            str(summary.shares_holding_managers),
            _format_ratio(summary.shares_holding_managers / summary.shares_analyzed_managers),
        ],
        [
            "Reducing",
            str(summary.shares_reducing_managers),
            _format_ratio(summary.shares_reducing_managers / summary.shares_analyzed_managers),
        ],
    ]
    print()
    print("Shares Direction Breakdown")
    print(_format_table(direction_headers, shares_rows))


def _build_trend_summary_workbook_data(
    store: Any,
    report_quarter: str,
    *,
    signals: list[Any],
    symbol_map: dict[str, str],
    min_conf: float,
    limit: int,
    manager_ciks: list[str] | None,
    view: str = "shortlist",
    show_reversals: bool = False,
) -> TrendSummaryWorkbookData:
    effective_limit = max(1, min(limit, IDEAS_OUTPUT_MAX_ROWS))

    if view == "raw":
        sell_min_conf = min(min_conf, SELL_TABLE_MIN_CONF)
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
        sell = sorted(
            [item for item in signals if "SELL" in item.regime and item.regime != "REVERSAL_BUY" and item.confidence >= sell_min_conf],
            key=lambda item: item.trend_ewma,
        )[:effective_limit]
        reversals = sorted(
            [item for item in signals if item.regime in {"REVERSAL_BUY", "REVERSAL_SELL"} and item.confidence >= min_conf],
            key=lambda item: abs(item.trend_delta),
            reverse=True,
        )[:effective_limit]

        headers = ["Ticker", "Action", "Setup (Regime)", "Conviction / Target", "Trend", "Consensus (+/-)", "Data Fresh"]

        def _raw_row(item: Any) -> list[str]:
            return [
                _ticker_for_signal(item, symbol_map),
                _action_for_signal(item),
                _setup_for_signal(item),
                _conviction_target_for_signal(item),
                f"{item.trend_ewma:.4f}",
                f"{item.buy_managers}/{item.sell_managers}",
                _freshness_icon(item),
            ]

        top_buy = TrendTable(title="Top Buy Trends", headers=headers, rows=[_raw_row(item) for item in buy])
        top_sell = TrendTable(title="Top Sell Trends", headers=headers, rows=[_raw_row(item) for item in sell])
        rev_table: TrendTable | None = None
        if show_reversals and reversals:
            rev_table = TrendTable(title="Reversals", headers=headers, rows=[_raw_row(item) for item in reversals])
    else:
        selection = select_trend_ideas(signals, min_conf=min_conf, limit=limit)
        headers = ["Instrument", "Setup", "Idea Score", "Support", "Confidence", "Freshness", "Top Contributors"]
        top_buy = TrendTable(
            title="Top Buy Ideas",
            headers=headers,
            rows=[_shortlist_row(item, symbol_map) for item in selection.promoted_buy],
        )
        top_sell = TrendTable(
            title="Top Reduction Trends",
            headers=headers,
            rows=[_shortlist_row(item, symbol_map) for item in selection.promoted_reduction],
        )
        rev_table = None

    summary = _compute_portfolio_value_trend_summary(store, report_quarter, manager_ciks or [])

    fingerprint_payload = {
        "report_quarter": report_quarter,
        "view": view,
        "min_conf": min_conf,
        "limit": limit,
        "top_buy": top_buy.rows,
        "top_sell": top_sell.rows,
        "reversals": rev_table.rows if rev_table else None,
        "portfolio_summary": summary.__dict__ if summary else None,
    }
    content_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, default=str).encode()
    ).hexdigest()

    portfolio_data: PortfolioValueTrendData | None = None
    if summary is not None:
        portfolio_data = PortfolioValueTrendData(
            report_quarter=summary.report_quarter,
            previous_quarter=summary.previous_quarter,
            selected_managers=summary.selected_managers,
            analyzed_managers=summary.analyzed_managers,
            missing_current=summary.missing_current,
            missing_previous=summary.missing_previous,
            growing_managers=summary.growing_managers,
            holding_managers=summary.holding_managers,
            reducing_managers=summary.reducing_managers,
            previous_total_value_k=summary.previous_total_value_k,
            current_total_value_k=summary.current_total_value_k,
            shares_analyzed_managers=summary.shares_analyzed_managers,
            shares_growing_managers=summary.shares_growing_managers,
            shares_holding_managers=summary.shares_holding_managers,
            shares_reducing_managers=summary.shares_reducing_managers,
            previous_total_shares=summary.previous_total_shares,
            current_total_shares=summary.current_total_shares,
        )

    return TrendSummaryWorkbookData(
        report_quarter=report_quarter,
        view_mode=view,
        min_conf=min_conf,
        limit=limit,
        top_buy=top_buy,
        top_sell=top_sell,
        reversals=rev_table,
        portfolio_value_trend=portfolio_data,
        content_fingerprint=content_fingerprint,
    )


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


def _print_shortlist_trend_table(
    store: Any,
    report_quarter: str,
    *,
    signals: list[Any],
    symbol_map: dict[str, str],
    min_conf: float,
    limit: int,
    manager_ciks: list[str] | None,
) -> None:
    selection = select_trend_ideas(signals, min_conf=min_conf, limit=limit)
    print()
    print(f"Report quarter: {report_quarter}")
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
    _print_portfolio_value_trend_summary(store, report_quarter, manager_ciks=manager_ciks)


def _resolve_explained_signal(
    signals: list[Any],
    symbol_map: dict[str, str],
    explain: str,
) -> Any | None:
    query = explain.strip().upper()
    if not query:
        return None
    for item in signals:
        keys = {
            str(item.instrument_key or "").strip().upper(),
            str(item.cusip or "").strip().upper(),
        }
        symbols = {symbol_map[key] for key in keys if key and key in symbol_map}
        if query in keys or query in symbols:
            return item
    return None


def _print_trend_explanation(
    *,
    signals: list[Any],
    symbol_map: dict[str, str],
    explain: str,
    min_conf: float,
) -> None:
    signal = _resolve_explained_signal(signals, symbol_map, explain)
    if signal is None:
        print(f"No trend signal found for {explain}.")
        return

    selection = select_trend_ideas(signals, min_conf=min_conf)
    decision = selection.by_instrument[signal.instrument_key]
    target_pct = round(_target_confidence_for_signal(signal) * 100)
    contributors: list[Any]
    try:
        parsed_contributors = json.loads(signal.contributors_json)
    except (TypeError, json.JSONDecodeError):
        parsed_contributors = []
    contributors = parsed_contributors if isinstance(parsed_contributors, list) else []

    print()
    print(f"Trend explanation: {_instrument_for_signal(signal, symbol_map)}")
    print(f"Selector state: {decision.state.value}")
    print(f"Selector reason: {decision.reason}")
    print(f"Instrument key: {signal.instrument_key}")
    print(f"Issuer: {signal.issuer_name or 'Unknown'}")
    print(f"Regime: {signal.regime} | Setup: {_setup_for_signal(signal)}")
    print(f"Raw trend / Delta: {signal.trend_ewma:+.4f} / {signal.trend_delta:+.4f}")
    print(f"Impulse / Accumulation: {signal.impulse_score:+.4f} / {signal.accumulation_score:+.4f}")
    print(f"Confidence / Target: {round(float(signal.confidence) * 100)}% / {target_pct}%")
    print(f"Buy / Reduction manager support: {signal.buy_managers} / {signal.sell_managers}")
    print(f"Persistence Buy / Reduction: {signal.persistence_buy} / {signal.persistence_sell}")
    print(f"Freshness: {_freshness_text(signal)} | Multiplier: {signal.freshness_multiplier:.2f}")
    print(f"Crowding HHI: {signal.crowding_hhi:.2f}")
    print("Top contributors:")
    if not contributors:
        print("- none stored")
        return
    for item in contributors:
        if not isinstance(item, dict):
            continue
        manager = str(item.get("manager_name") or item.get("manager_cik") or "Unknown manager")
        signal_value = item.get("signal_value")
        trade_dw = item.get("trade_dw")
        print(f"- {manager}: signal {signal_value} | trade weight delta {trade_dw}")


def _print_raw_trend_table(
    store: Any,
    report_quarter: str,
    *,
    min_conf: float = 0.45,
    limit: int = IDEAS_OUTPUT_MAX_ROWS,
    show_reversals: bool = False,
    symbols_file: str = "config/cusip_tickers.json",
    manager_ciks: list[str] | None = None,
) -> None:
    symbol_map = _load_symbol_map(Path(symbols_file))
    signals = store.list_trend_stock_signals(report_quarter)
    if not signals:
        print(f"No trend signals found for {report_quarter}.")
        return

    effective_limit = max(1, min(limit, IDEAS_OUTPUT_MAX_ROWS))
    sell_min_conf = min(min_conf, SELL_TABLE_MIN_CONF)
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

    print()
    print(f"Report quarter: {report_quarter}")
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

    _print_section("Top Buy Trends", headers, [_row(item) for item in buy])
    _print_section("Top Sell Trends", headers, [_row(item) for item in sell])
    _print_portfolio_value_trend_summary(
        store,
        report_quarter,
        manager_ciks=manager_ciks,
    )
    if show_reversals:
        _print_section("Reversals", headers, [_row(item) for item in reversals])


def _print_detailed_trend_table(
    store: Any,
    report_quarter: str,
    *,
    min_conf: float = 0.45,
    limit: int = IDEAS_OUTPUT_MAX_ROWS,
    show_reversals: bool = False,
    symbols_file: str = "config/cusip_tickers.json",
    manager_ciks: list[str] | None = None,
    view: str = "shortlist",
    explain: str | None = None,
) -> None:
    symbol_map = _load_symbol_map(Path(symbols_file))
    signals = store.list_trend_stock_signals(report_quarter)
    if not signals:
        print(f"No trend signals found for {report_quarter}.")
        return
    if explain:
        _print_trend_explanation(signals=signals, symbol_map=symbol_map, explain=explain, min_conf=min_conf)
        return
    effective_limit = max(1, min(limit, IDEAS_OUTPUT_MAX_ROWS))
    if view == "raw":
        _print_raw_trend_table(
            store,
            report_quarter,
            min_conf=min_conf,
            limit=effective_limit,
            show_reversals=show_reversals,
            symbols_file=symbols_file,
            manager_ciks=manager_ciks,
        )
        return
    _print_shortlist_trend_table(
        store,
        report_quarter,
        signals=signals,
        symbol_map=symbol_map,
        min_conf=min_conf,
        limit=effective_limit,
        manager_ciks=manager_ciks,
    )


def _should_print_trend_table(*, dry_run: bool, status: str, report_quarter: str | None) -> bool:
    if dry_run or report_quarter is None:
        return False
    if status.startswith("pending_"):
        return False
    return sys.stdout.isatty()


def _load_live_latest_prices(
    *,
    symbols_file: str,
    logger: logging.Logger,
) -> dict[str, float] | None:
    key_to_ticker = _load_symbol_map(Path(symbols_file))
    if not key_to_ticker:
        logger.warning(
            "Live prices symbols file is empty or invalid; live freshness input skipped",
            extra={"symbols_file": symbols_file},
        )
        return None

    gateway = StooqPriceGateway()
    ticker_prices = gateway.get_latest_prices(sorted(set(key_to_ticker.values())))
    if not ticker_prices:
        logger.warning(
            "Live prices source returned no quotes; live freshness input skipped",
            extra={"source": "stooq"},
        )
        return None

    latest_prices: dict[str, float] = {}
    for key, ticker in key_to_ticker.items():
        price = ticker_prices.get(ticker)
        if price is not None:
            latest_prices[key] = price
    if not latest_prices:
        logger.warning(
            "Live prices fetched but no symbol keys matched; live freshness input skipped",
            extra={"source": "stooq", "symbols_file": symbols_file},
        )
        return None
    logger.info(
        "Live prices loaded for freshness decay",
        extra={
            "source": "stooq",
            "symbols_file": symbols_file,
            "mapped_keys": len(latest_prices),
            "available_tickers": len(ticker_prices),
            "configured_keys": len(key_to_ticker),
        },
    )
    return latest_prices


def _snapshot_sync_max_quarters(*, max_filing_age_days: int) -> int:
    # Ensure snapshot sync covers the filing-age horizon, plus warm-up and safety buffer.
    estimated_quarters_from_age = int(math.ceil(max_filing_age_days / 92.0)) + 2
    return max(9, estimated_quarters_from_age)


def _maybe_export_trend_summary(
    store: Any,
    report_quarter: str,
    *,
    export_xlsx_path: str,
    min_conf: float,
    limit: int,
    show_reversals: bool,
    symbols_file: str,
    manager_ciks: list[str] | None,
    view: str,
    dry_run: bool,
    logger: logging.Logger,
) -> None:
    symbol_map = _load_symbol_map(Path(symbols_file))
    signals = store.list_trend_stock_signals(report_quarter)
    if not signals:
        logger.info("No trend signals to export", extra={"report_quarter": report_quarter})
        return

    data = _build_trend_summary_workbook_data(
        store,
        report_quarter,
        signals=signals,
        symbol_map=symbol_map,
        min_conf=min_conf,
        limit=limit,
        manager_ciks=manager_ciks,
        view=view,
        show_reversals=show_reversals,
    )

    path = Path(export_xlsx_path)
    if path.suffix != ".xlsx":
        path = path / f"trend_summary_{report_quarter}.xlsx"

    result = export_trend_summary_if_changed(path, data, dry_run=dry_run)
    logger.info(
        "Trend summary export",
        extra={
            "status": result.status,
            "path": str(result.path) if result.path else None,
            "report_quarter": report_quarter,
            "content_fingerprint": result.content_fingerprint,
        },
    )
    if result.status == "written":
        print(f"Exported trend summary to {result.path}")


def main() -> int:
    configure_logging()
    logger = logging.getLogger(__name__)
    trace_id = new_trace_id()

    with log_context(trace_id=trace_id):
        return _main(logger)


def _main(logger: logging.Logger) -> int:
    parser = argparse.ArgumentParser(description="Track 13F filings and send notifications.")
    parser.add_argument("--notify_on_first_start", action="store_true", help="Notify on initial baseline set")
    parser.add_argument(
        "clean_state",
        nargs="?",
        choices=["clean_state"],
        help="Clear persisted manager state before running checks.",
    )
    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="Send a test notification and exit (without SEC checks).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not send notifications or write state")
    parser.add_argument(
        "--force-trend-recompute",
        action="store_true",
        help="Force trend engine recomputation even when input and top fingerprints are unchanged.",
    )
    parser.add_argument(
        "--show-trends-detailed",
        action="store_true",
        help="Always print trend output after tracker run (also in non-interactive output).",
    )
    parser.add_argument(
        "--show-trends-only",
        action="store_true",
        help="Print trend output from existing DB signals and exit (no sync/trend recompute).",
    )
    parser.add_argument(
        "--send-trend-summary-from-db",
        action="store_true",
        help="Send trend Telegram summary from existing DB signals and exit (no sync/trend recompute).",
    )
    parser.add_argument(
        "--send-trend-summary-force",
        action="store_true",
        help="Force resend trend Telegram summary even if this quarter was already notified.",
    )
    parser.add_argument(
        "--trends-quarter",
        help="Quarter for --show-trends-detailed/--show-trends-only in format YYYYQn. Default: computed/latest quarter.",
    )
    parser.add_argument(
        "--trends-min-conf",
        type=float,
        default=0.45,
        help="Buy/reversal confidence for --show-trends-detailed/--show-trends-only (default: 0.45; reductions use min(threshold, 0.35)).",
    )
    parser.add_argument(
        "--trends-limit",
        type=int,
        default=IDEAS_OUTPUT_MAX_ROWS,
        help="Rows per section for --show-trends-detailed/--show-trends-only (default: 8, max: 8).",
    )
    parser.add_argument(
        "--trends-show-reversals",
        action="store_true",
        help="Include reversals section in raw trend output.",
    )
    parser.add_argument(
        "--trends-view",
        choices=["shortlist", "raw"],
        default="shortlist",
        help="Trend output view: long-term shortlist (default) or raw diagnostics.",
    )
    parser.add_argument(
        "--trends-explain",
        help="Explain one trend signal by mapped ticker, CUSIP, or instrument key.",
    )
    parser.add_argument(
        "--trends-symbols-file",
        default="config/cusip_tickers.json",
        help="Symbol map JSON for --show-trends-detailed/--show-trends-only.",
    )
    parser.add_argument(
        "--trend-live-prices-symbols-file",
        default=os.environ.get("TREND_LIVE_PRICES_SYMBOLS_FILE", "config/cusip_tickers.json"),
        help="Symbol map JSON for live prices from stooq (key: CUSIP/instrument_key, value: ticker).",
    )
    parser.add_argument(
        "--backfill-trend-history",
        action="store_true",
        help="Run backfill trend computation for historical quarters as a separate mode.",
    )
    parser.add_argument(
        "--backfill-from-quarter",
        help="Optional start quarter for backfill mode in format YYYYQn.",
    )
    parser.add_argument(
        "--backfill-to-quarter",
        help="Optional end quarter for backfill mode in format YYYYQn.",
    )
    parser.add_argument(
        "--backfill-force",
        action="store_true",
        help="Recompute backfill quarters even if trend signals already exist.",
    )
    parser.add_argument(
        "--backfill-include-latest",
        action="store_true",
        help="Include latest completed quarter into backfill run.",
    )
    parser.add_argument(
        "--export-xlsx",
        action="store_true",
        help="Export trend summary tables to an Excel file after computation.",
    )
    parser.add_argument(
        "--export-xlsx-path",
        default="data/exports",
        help="Directory or file path for --export-xlsx output (default: data/exports).",
    )
    args = parser.parse_args()
    show_trends_only_flag = bool(getattr(args, "show_trends_only", False))
    send_trend_summary_from_db_flag = bool(getattr(args, "send_trend_summary_from_db", False))
    send_trend_summary_force_flag = bool(getattr(args, "send_trend_summary_force", False))
    backfill_trend_history_flag = bool(getattr(args, "backfill_trend_history", False))
    backfill_from_quarter = getattr(args, "backfill_from_quarter", None)
    backfill_to_quarter = getattr(args, "backfill_to_quarter", None)
    backfill_force = bool(getattr(args, "backfill_force", False))
    backfill_include_latest = bool(getattr(args, "backfill_include_latest", False))
    trends_view = getattr(args, "trends_view", "shortlist")
    trends_explain = getattr(args, "trends_explain", None)

    if args.test_notification and args.dry_run:
        logger.error("Cannot combine --test-notification with --dry-run")
        return 2
    if args.test_notification and args.clean_state == "clean_state":
        logger.error("Cannot combine --test-notification with clean_state")
        return 2
    if args.dry_run and args.clean_state == "clean_state":
        logger.error("Cannot combine --dry-run with clean_state")
        return 2
    if show_trends_only_flag and args.test_notification:
        logger.error("Cannot combine --show-trends-only with --test-notification")
        return 2
    if send_trend_summary_from_db_flag and args.test_notification:
        logger.error("Cannot combine --send-trend-summary-from-db with --test-notification")
        return 2
    if show_trends_only_flag and args.clean_state == "clean_state":
        logger.error("Cannot combine --show-trends-only with clean_state")
        return 2
    if send_trend_summary_from_db_flag and args.clean_state == "clean_state":
        logger.error("Cannot combine --send-trend-summary-from-db with clean_state")
        return 2
    if show_trends_only_flag and args.force_trend_recompute:
        logger.error("Cannot combine --show-trends-only with --force-trend-recompute")
        return 2
    if send_trend_summary_from_db_flag and args.force_trend_recompute:
        logger.error("Cannot combine --send-trend-summary-from-db with --force-trend-recompute")
        return 2
    if send_trend_summary_force_flag and not send_trend_summary_from_db_flag:
        logger.error("--send-trend-summary-force requires --send-trend-summary-from-db")
        return 2
    if args.trends_min_conf < 0 or args.trends_min_conf > 1:
        logger.error("--trends-min-conf must be between 0 and 1")
        return 2
    if args.trends_limit <= 0:
        logger.error("--trends-limit must be > 0")
        return 2
    if backfill_from_quarter and parse_report_quarter(backfill_from_quarter) is None:
        logger.error("--backfill-from-quarter must use YYYYQn format")
        return 2
    if backfill_to_quarter and parse_report_quarter(backfill_to_quarter) is None:
        logger.error("--backfill-to-quarter must use YYYYQn format")
        return 2
    if backfill_from_quarter and backfill_to_quarter:
        if parse_report_quarter(backfill_from_quarter) > parse_report_quarter(backfill_to_quarter):
            logger.error("--backfill-from-quarter must be <= --backfill-to-quarter")
            return 2
    if show_trends_only_flag and backfill_trend_history_flag:
        logger.error("Cannot combine --show-trends-only with --backfill-trend-history")
        return 2
    if send_trend_summary_from_db_flag and backfill_trend_history_flag:
        logger.error("Cannot combine --send-trend-summary-from-db with --backfill-trend-history")
        return 2
    if show_trends_only_flag and send_trend_summary_from_db_flag:
        logger.error("Cannot combine --show-trends-only with --send-trend-summary-from-db")
        return 2
    if not backfill_trend_history_flag and (
        backfill_from_quarter or backfill_to_quarter or backfill_force or backfill_include_latest
    ):
        logger.error("Backfill options require --backfill-trend-history")
        return 2
    export_xlsx_flag = bool(getattr(args, "export_xlsx", False))
    export_xlsx_path = getattr(args, "export_xlsx_path", "data/exports")

    try:
        config = load_config(notify_initial=args.notify_on_first_start)
    except (ValueError, FileNotFoundError) as exc:
        logger.error("Configuration validation failed", extra={"error": str(exc)})
        return 1

    if not config.notifiers:
        logger.warning("No notifiers configured, running without notifications")
    logger.info(
        "Tracker run started",
        extra={
            "managers_count": len(config.managers),
            "dry_run": args.dry_run,
            "test_notification": args.test_notification,
            "clean_state": args.clean_state == "clean_state",
        },
    )
    manager_ciks = [manager.cik for manager in config.managers]

    if args.test_notification:
        try:
            notifiers = build_notifier_list(config, dry_run=args.dry_run, test_notification=True)
        except ValueError as exc:
            logger.error("Notifier initialization failed", extra={"error": str(exc)})
            return 1
        if not notifiers:
            logger.error("No notifiers configured for test notification")
            return 1
        subject = "13F Tracker test notification"
        body = f"Test notification sent at {format_local_datetime(now_kyiv())}."
        _send_notifications(notifiers, subject, body)
        logger.info("Test notification sent")
        return 0

    try:
        runtime = build_runtime(config, dry_run=args.dry_run, test_notification=args.test_notification)
    except (ValueError, StateStoreError) as exc:
        logger.error("Runtime initialization failed", extra={"error": str(exc)})
        return 1

    if show_trends_only_flag:
        quarter_for_table = args.trends_quarter or runtime.store.get_latest_trend_quarter()
        if quarter_for_table is None:
            print("No trend signals found.")
        else:
            _print_detailed_trend_table(
                runtime.store,
                quarter_for_table,
                min_conf=args.trends_min_conf,
                limit=args.trends_limit,
                show_reversals=args.trends_show_reversals,
                symbols_file=args.trends_symbols_file,
                manager_ciks=manager_ciks,
                view=trends_view,
                explain=trends_explain,
            )
        runtime.store.close()
        logger.info(
            "Tracker run finished",
            extra={
                "finished_at_local": format_local_datetime(now_kyiv()),
                "managers_count": len(config.managers),
                "dry_run": args.dry_run,
                "mode": "show_trends_only",
                "report_quarter": quarter_for_table,
            },
        )
        return 0

    if send_trend_summary_from_db_flag:
        quarter_for_summary = args.trends_quarter or runtime.store.get_latest_trend_quarter()
        if quarter_for_summary is None:
            print("No trend signals found.")
        else:
            managers = [Manager(name=manager_config.name, cik=manager_config.cik) for manager_config in config.managers]
            notify_if_all_reports_published_for_current_quarter(
                managers,
                runtime.store,
                runtime.notifiers,
                dry_run=args.dry_run,
                logger=logger,
            )
            notify_trend_analysis_summary(
                runtime.store,
                runtime.notifiers,
                dry_run=args.dry_run,
                trend_status="from_db",
                report_quarter=quarter_for_summary,
                manager_ciks=manager_ciks,
                min_conf=args.trends_min_conf,
                limit=args.trends_limit,
                show_reversals=args.trends_show_reversals,
                symbols_file=args.trends_symbols_file,
                force_send=send_trend_summary_force_flag,
                logger=logger,
            )
        runtime.store.close()
        logger.info(
            "Tracker run finished",
            extra={
                "finished_at_local": format_local_datetime(now_kyiv()),
                "managers_count": len(config.managers),
                "dry_run": args.dry_run,
                "mode": "send_trend_summary_from_db",
                "report_quarter": quarter_for_summary,
                "force_send": send_trend_summary_force_flag,
            },
        )
        return 0

    if backfill_trend_history_flag:
        if args.clean_state == "clean_state":
            runtime.store.close()
            logger.error("Cannot combine clean_state with --backfill-trend-history")
            return 2
        trend_blend_mode = os.environ.get("TREND_BLEND_MODE", "tactical").strip().lower()
        trend_latest_prices = _load_live_latest_prices(
            symbols_file=args.trend_live_prices_symbols_file,
            logger=logger,
        )
        try:
            backfill_result = run_backfill_trend_history(
                list(config.managers),
                runtime.store,
                dry_run=args.dry_run,
                blend_mode=trend_blend_mode,
                latest_prices=trend_latest_prices,
                from_quarter=backfill_from_quarter,
                to_quarter=backfill_to_quarter,
                include_latest=backfill_include_latest,
                force_recompute=backfill_force,
                logger=logger,
            )
        except ValueError as exc:
            runtime.store.close()
            logger.error("Backfill trend history configuration failed", extra={"error": str(exc)})
            return 1

        logger.info(
            "Backfill trend history status",
            extra={
                "status": backfill_result.status,
                "batch_id": backfill_result.batch_id,
                "quarters_requested": backfill_result.quarters_requested,
                "computed": backfill_result.computed,
                "skipped_existing": backfill_result.skipped_existing,
                "failed": backfill_result.failed,
                "force_recompute": backfill_force,
                "include_latest": backfill_include_latest,
                "from_quarter": backfill_from_quarter,
                "to_quarter": backfill_to_quarter,
            },
        )
        print()
        print(
            "Backfill summary: "
            f"requested={backfill_result.quarters_requested}, "
            f"computed={backfill_result.computed}, "
            f"skipped_existing={backfill_result.skipped_existing}, "
            f"failed={backfill_result.failed}, "
            f"status={backfill_result.status}, "
            f"batch_id={backfill_result.batch_id}"
        )

        runtime.store.close()
        logger.info(
            "Tracker run finished",
            extra={
                "finished_at_local": format_local_datetime(now_kyiv()),
                "managers_count": len(config.managers),
                "dry_run": args.dry_run,
                "mode": "backfill",
            },
        )
        return 1 if backfill_result.status == "failed" else 0

    if args.clean_state == "clean_state":
        cleared_rows = runtime.store.clear_state()
        logger.info("State store cleared before run", extra={"rows_deleted": cleared_rows})

    managers = [Manager(name=manager_config.name, cik=manager_config.cik) for manager_config in config.managers]
    for manager in managers:
        process_manager(
            manager,
            runtime.store,
            runtime.client,
            runtime.notifiers,
            notify_initial=config.notify_initial,
            dry_run=args.dry_run,
            max_filing_age_days=config.max_filing_age_days,
            logger=logger,
        )

    sync_quarter_snapshots(
        managers,
        runtime.store,
        runtime.client,
        max_quarters=_snapshot_sync_max_quarters(max_filing_age_days=config.max_filing_age_days),
        max_filing_age_days=config.max_filing_age_days,
        dry_run=args.dry_run,
        logger=logger,
    )
    trend_blend_mode = os.environ.get("TREND_BLEND_MODE", "tactical").strip().lower()
    trend_latest_prices = _load_live_latest_prices(
        symbols_file=args.trend_live_prices_symbols_file,
        logger=logger,
    )
    try:
        trend_result = run_trend_engine_for_latest_completed_quarter(
            list(config.managers),
            runtime.store,
            dry_run=args.dry_run,
            blend_mode=trend_blend_mode,
            latest_prices=trend_latest_prices,
            force_recompute=args.force_trend_recompute,
            logger=logger,
        )
    except ValueError as exc:
        runtime.store.close()
        logger.error("Trend engine configuration failed", extra={"error": str(exc), "blend_mode": trend_blend_mode})
        return 1
    logger.info(
        "Trend engine status",
        extra={
            "status": trend_result.status,
            "report_quarter": trend_result.report_quarter,
            "signals_count": trend_result.signals_count,
            "blend_mode": trend_blend_mode,
            "force_trend_recompute": args.force_trend_recompute,
        },
    )
    if args.show_trends_detailed:
        quarter_for_table = args.trends_quarter or trend_result.report_quarter or runtime.store.get_latest_trend_quarter()
        if quarter_for_table is None:
            print("No trend signals found.")
        else:
            _print_detailed_trend_table(
                runtime.store,
                quarter_for_table,
                min_conf=args.trends_min_conf,
                limit=args.trends_limit,
                show_reversals=args.trends_show_reversals,
                symbols_file=args.trends_symbols_file,
                manager_ciks=manager_ciks,
                view=trends_view,
                explain=trends_explain,
            )
    elif _should_print_trend_table(
        dry_run=args.dry_run,
        status=trend_result.status,
        report_quarter=trend_result.report_quarter,
    ):
        _print_detailed_trend_table(
            runtime.store,
            trend_result.report_quarter,
            min_conf=args.trends_min_conf,
            limit=args.trends_limit,
            show_reversals=args.trends_show_reversals,
            symbols_file=args.trends_symbols_file,
            manager_ciks=manager_ciks,
            view=trends_view,
            explain=trends_explain,
        )

    if export_xlsx_flag:
        export_quarter = trend_result.report_quarter or runtime.store.get_latest_trend_quarter()
        if export_quarter:
            _maybe_export_trend_summary(
                runtime.store,
                export_quarter,
                export_xlsx_path=export_xlsx_path,
                min_conf=args.trends_min_conf,
                limit=args.trends_limit,
                show_reversals=args.trends_show_reversals,
                symbols_file=args.trends_symbols_file,
                manager_ciks=manager_ciks,
                view=trends_view,
                dry_run=args.dry_run,
                logger=logger,
            )

    notify_if_all_reports_published_for_current_quarter(
        managers,
        runtime.store,
        runtime.notifiers,
        dry_run=args.dry_run,
        logger=logger,
    )

    notify_trend_analysis_summary(
        runtime.store,
        runtime.notifiers,
        dry_run=args.dry_run,
        trend_status=trend_result.status,
        report_quarter=trend_result.report_quarter,
        manager_ciks=manager_ciks,
        min_conf=args.trends_min_conf,
        limit=args.trends_limit,
        show_reversals=args.trends_show_reversals,
        symbols_file=args.trends_symbols_file,
        force_send=False,
        logger=logger,
    )
    runtime.store.close()
    logger.info(
        "Tracker run finished",
        extra={
            "finished_at_local": format_local_datetime(now_kyiv()),
            "managers_count": len(config.managers),
            "dry_run": args.dry_run,
        },
    )
    return 0
