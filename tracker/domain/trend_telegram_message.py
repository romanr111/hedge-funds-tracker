from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from tracker.domain.models import TrendStockSignal
from tracker.domain.quarters import parse_report_quarter

SELL_TABLE_MIN_CONF = 0.35
BUY_TABLE_MIN_TREND = 0.001
DEFAULT_MAX_ROWS = 8
DEFAULT_PORTFOLIO_VALUE_HOLD_BAND = 0.05
DEFAULT_PORTFOLIO_SHARES_HOLD_BAND = 0.05


@dataclass(frozen=True)
class TrendMessageRow:
    ticker: str
    action: str
    setup: str
    confidence_pct: int
    target_pct: int
    trend_ewma: float
    buy_managers: int
    sell_managers: int
    freshness: str


@dataclass(frozen=True)
class TrendMessagePayload:
    report_quarter: str
    signals_total: int
    buy_rows: list[TrendMessageRow]
    sell_rows: list[TrendMessageRow]
    reversals_rows: list[TrendMessageRow]


@dataclass(frozen=True)
class PortfolioValueTrendSummary:
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


def load_symbol_map(path: Path) -> dict[str, str]:
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


def _ticker_for_signal(signal: TrendStockSignal, symbol_map: Mapping[str, str]) -> str:
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


def _target_confidence_for_signal(signal: TrendStockSignal) -> float:
    regime = (signal.regime or "").upper()
    if regime.startswith("STRONG_"):
        return 0.65
    if regime in {"REVERSAL_SELL", "EMERGING_SELL"}:
        return SELL_TABLE_MIN_CONF
    if regime in {"REVERSAL_BUY", "EMERGING_BUY"}:
        return 0.45
    if regime.startswith("WEAKENING_"):
        return 0.50
    return 0.50


def _action_for_signal(signal: TrendStockSignal) -> str:
    target = _target_confidence_for_signal(signal)
    regime = (signal.regime or "").upper()
    confidence = float(signal.confidence)
    target_gap_pp = (target - confidence) * 100.0

    if regime.startswith("STRONG_") and confidence >= target:
        if regime.endswith("_BUY"):
            return "BUY"
        if regime.endswith("_SELL"):
            return "SELL"
    has_direction = regime.endswith("_BUY") or regime.endswith("_SELL")
    if has_direction and target_gap_pp <= 5.0 + 1e-9:
        return "IDEA"
    return "TO_MONITOR"


def _setup_for_signal(signal: TrendStockSignal) -> str:
    regime = (signal.regime or "").upper()
    if regime.startswith("STRONG_"):
        return "Strong"
    if regime.startswith("REVERSAL_"):
        return "Reversal"
    if regime.startswith("EMERGING_"):
        return "Emerging"
    if regime.startswith("WEAKENING_"):
        return "Weakening"
    return "Unknown"


def _confidence_pct_for_signal(signal: TrendStockSignal) -> int:
    return round(float(signal.confidence) * 100)


def _target_pct_for_signal(signal: TrendStockSignal) -> int:
    return round(_target_confidence_for_signal(signal) * 100)


def _freshness_icon(signal: TrendStockSignal) -> str:
    if signal.freshness_ok is None:
        return "❌"
    return "✅" if bool(signal.freshness_ok) else "❌"


def _build_row(signal: TrendStockSignal, symbol_map: Mapping[str, str]) -> TrendMessageRow:
    return TrendMessageRow(
        ticker=_ticker_for_signal(signal, symbol_map),
        action=_action_for_signal(signal),
        setup=_setup_for_signal(signal),
        confidence_pct=_confidence_pct_for_signal(signal),
        target_pct=_target_pct_for_signal(signal),
        trend_ewma=float(signal.trend_ewma),
        buy_managers=int(signal.buy_managers),
        sell_managers=int(signal.sell_managers),
        freshness=_freshness_icon(signal),
    )


def build_trend_message_payload(
    *,
    report_quarter: str,
    signals: Sequence[TrendStockSignal],
    symbol_map: Mapping[str, str],
    min_conf: float = 0.45,
    limit: int = DEFAULT_MAX_ROWS,
) -> TrendMessagePayload:
    effective_limit = max(1, min(limit, DEFAULT_MAX_ROWS))
    sell_min_conf = min(min_conf, SELL_TABLE_MIN_CONF)

    buy_signals = sorted(
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
    sell_signals = sorted(
        [
            item
            for item in signals
            if "SELL" in item.regime and item.regime != "REVERSAL_BUY" and item.confidence >= sell_min_conf
        ],
        key=lambda item: item.trend_ewma,
    )[:effective_limit]
    reversals_signals = sorted(
        [
            item
            for item in signals
            if item.regime in {"REVERSAL_BUY", "REVERSAL_SELL"} and item.confidence >= min_conf
        ],
        key=lambda item: abs(item.trend_delta),
        reverse=True,
    )[:effective_limit]

    return TrendMessagePayload(
        report_quarter=report_quarter,
        signals_total=len(signals),
        buy_rows=[_build_row(item, symbol_map) for item in buy_signals],
        sell_rows=[_build_row(item, symbol_map) for item in sell_signals],
        reversals_rows=[_build_row(item, symbol_map) for item in reversals_signals],
    )


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


def _portfolio_value_direction(change_ratio: float, *, hold_band: float) -> str:
    if change_ratio > hold_band:
        return "Growing"
    if change_ratio < -hold_band:
        return "Reducing"
    return "Holding"


def _portfolio_shares_direction(change_ratio: float, *, hold_band: float) -> str:
    if change_ratio > hold_band:
        return "Growing"
    if change_ratio < -hold_band:
        return "Reducing"
    return "Holding"


def format_value_k(value_k: int) -> str:
    value_billions = round(value_k / 1_000_000_000)
    return f"${value_billions:,}B"


def format_int(value: int) -> str:
    return f"{value:,}"


def format_signed_ratio(value: float) -> str:
    return f"{value * 100:+.1f}%"


def format_ratio(value: float) -> str:
    return f"{value * 100:.1f}%"


def compute_portfolio_value_trend_summary(
    store: Any,
    report_quarter: str,
    manager_ciks: Sequence[str],
    *,
    value_hold_band: float = DEFAULT_PORTFOLIO_VALUE_HOLD_BAND,
    shares_hold_band: float = DEFAULT_PORTFOLIO_SHARES_HOLD_BAND,
) -> PortfolioValueTrendSummary | None:
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
        direction = _portfolio_value_direction(change_ratio, hold_band=value_hold_band)
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
            shares_direction = _portfolio_shares_direction(shares_change_ratio, hold_band=shares_hold_band)
            if shares_direction == "Growing":
                shares_growing_managers += 1
            elif shares_direction == "Reducing":
                shares_reducing_managers += 1
            else:
                shares_holding_managers += 1

    return PortfolioValueTrendSummary(
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


def _render_signal_row(row: TrendMessageRow, *, index: int) -> list[str]:
    trend = f"{row.trend_ewma:+.4f}"
    action_display = {
        "BUY": "✅ BUY",
        "SELL": "⛔ SELL",
        "IDEA": "💡 IDEA",
        "TO_MONITOR": "👀 TO_MONITOR",
    }.get(row.action, row.action)
    return [
        f"{index}) {row.ticker} — {action_display} ({row.setup})",
        (
            f"   Confidence: {row.confidence_pct}%"
            f" | Trend: {trend} | Managers: Buy {row.buy_managers} / Sell {row.sell_managers}"
            f" | Fresh: {row.freshness}"
        ),
    ]


def _render_section(title: str, rows: Sequence[TrendMessageRow]) -> list[str]:
    lines = [f"{title} ({len(rows)})"]
    if not rows:
        lines.append("(empty)")
        return lines
    for idx, row in enumerate(rows, start=1):
        lines.extend(_render_signal_row(row, index=idx))
    return lines


def _render_direction_breakdown_lines(
    *,
    growing: int,
    holding: int,
    reducing: int,
    total: int,
) -> list[str]:
    if total <= 0:
        return [
            "- Increasing: 0 (0.0%)",
            "- Holding: 0 (0.0%)",
            "- Reducing: 0 (0.0%)",
        ]
    return [
        f"- Increasing: {growing} ({format_ratio(growing / total)})",
        f"- Holding: {holding} ({format_ratio(holding / total)})",
        f"- Reducing: {reducing} ({format_ratio(reducing / total)})",
    ]


def _truncate_for_telegram(subject: str, body: str, *, limit: int = 4096) -> str:
    budget = max(0, limit - len(subject) - 2)
    if len(body) <= budget:
        return body
    if budget <= 3:
        return body[:budget]
    return body[: budget - 3].rstrip() + "..."


def render_trend_telegram_notification(
    *,
    payload: TrendMessagePayload,
    portfolio_summary: PortfolioValueTrendSummary | None,
    show_reversals: bool = False,
) -> tuple[str, str]:
    subject = f"📈 Hedge Funds Trend Analysis {payload.report_quarter}"
    lines: list[str] = [
        f"Quarter: {payload.report_quarter}",
        f"Signals analyzed: {payload.signals_total}",
        "",
    ]
    lines.extend(_render_section("🟢 Top Buy Trends", payload.buy_rows))
    lines.append("")
    lines.extend(_render_section("🔴 Top Sell Trends", payload.sell_rows))
    if show_reversals:
        lines.append("")
        lines.extend(_render_section("🟠 Reversals", payload.reversals_rows))

    if portfolio_summary is not None:
        lines.append("")
        lines.append("📊 Portfolio Value Trend (QoQ)")
        lines.append(
            f"{portfolio_summary.previous_quarter} -> {portfolio_summary.report_quarter}"
            f" | Managers {portfolio_summary.analyzed_managers}/{portfolio_summary.selected_managers}"
        )
        if portfolio_summary.analyzed_managers <= 0:
            lines.append("Not enough comparable snapshots to determine portfolio value direction.")
        else:
            aggregate_change_ratio = _portfolio_value_change_ratio(
                portfolio_summary.previous_total_value_k,
                portfolio_summary.current_total_value_k,
            )
            aggregate_direction = _portfolio_value_direction(
                aggregate_change_ratio,
                hold_band=DEFAULT_PORTFOLIO_VALUE_HOLD_BAND,
            )
            lines.append(
                "Value: "
                f"{format_value_k(portfolio_summary.previous_total_value_k)} -> "
                f"{format_value_k(portfolio_summary.current_total_value_k)} "
                f"({format_signed_ratio(aggregate_change_ratio)}, {aggregate_direction})"
            )
            lines.append("Value direction (manager behavior by total portfolio value):")
            lines.extend(
                _render_direction_breakdown_lines(
                    growing=portfolio_summary.growing_managers,
                    holding=portfolio_summary.holding_managers,
                    reducing=portfolio_summary.reducing_managers,
                    total=portfolio_summary.analyzed_managers,
                )
            )

            if portfolio_summary.shares_analyzed_managers > 0:
                aggregate_shares_change_ratio = _portfolio_value_change_ratio(
                    portfolio_summary.previous_total_shares,
                    portfolio_summary.current_total_shares,
                )
                aggregate_shares_direction = _portfolio_shares_direction(
                    aggregate_shares_change_ratio,
                    hold_band=DEFAULT_PORTFOLIO_SHARES_HOLD_BAND,
                )
                lines.append(
                    "Shares: "
                    f"{format_int(portfolio_summary.previous_total_shares)} -> "
                    f"{format_int(portfolio_summary.current_total_shares)} "
                    f"({format_signed_ratio(aggregate_shares_change_ratio)}, {aggregate_shares_direction})"
                )
                lines.append("")
                lines.append("Shares direction (manager behavior by total reported shares):")
                lines.extend(
                    _render_direction_breakdown_lines(
                        growing=portfolio_summary.shares_growing_managers,
                        holding=portfolio_summary.shares_holding_managers,
                        reducing=portfolio_summary.shares_reducing_managers,
                        total=portfolio_summary.shares_analyzed_managers,
                    )
                )

    body = "\n".join(lines).strip()
    return subject, _truncate_for_telegram(subject, body)
