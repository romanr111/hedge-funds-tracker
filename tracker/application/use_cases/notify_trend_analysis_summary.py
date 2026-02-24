from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from tracker.application.ports.notifier import NotifierPort
from tracker.domain.timing import now_kyiv
from tracker.domain.trend_telegram_message import (
    build_trend_message_payload,
    compute_portfolio_value_trend_summary,
    load_symbol_map,
    render_trend_telegram_notification,
)

TREND_ANALYSIS_SUMMARY_STATE_CIK = "__trend_analysis_summary__"
TREND_ANALYSIS_SUMMARY_STATE_NAME = "Trend analysis summary"


def _send_notifications(notifiers: Sequence[NotifierPort], subject: str, body: str) -> None:
    for notifier in notifiers:
        notifier.send(subject, body)


def _is_notifiable_status(status: str) -> bool:
    normalized = (status or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith("pending_"):
        return False
    return normalized not in {"dry_run", "no_managers"}


def notify_trend_analysis_summary(
    store: Any,
    notifiers: Sequence[NotifierPort],
    *,
    dry_run: bool,
    trend_status: str,
    report_quarter: str | None,
    manager_ciks: Sequence[str],
    min_conf: float = 0.45,
    limit: int = 8,
    show_reversals: bool = False,
    symbols_file: str = "config/cusip_tickers.json",
    force_send: bool = False,
    now_fn: Callable[[], datetime] = now_kyiv,
    logger: logging.Logger | None = None,
) -> None:
    app_logger = logger or logging.getLogger(__name__)
    if dry_run or not notifiers or report_quarter is None:
        return

    if not _is_notifiable_status(trend_status):
        app_logger.info(
            "Trend analysis summary notification skipped",
            extra={"status": "non_notifiable_trend_status", "trend_status": trend_status, "report_quarter": report_quarter},
        )
        return

    marker = store.get_state(TREND_ANALYSIS_SUMMARY_STATE_CIK)
    if not force_send and marker and marker.last_notified_accession == report_quarter:
        app_logger.info(
            "Trend analysis summary notification skipped",
            extra={"status": "already_notified", "report_quarter": report_quarter},
        )
        return

    signals = store.list_trend_stock_signals(report_quarter)
    if not signals:
        app_logger.info(
            "Trend analysis summary notification skipped",
            extra={"status": "no_signals", "report_quarter": report_quarter},
        )
        return

    symbol_map = load_symbol_map(Path(symbols_file))
    payload = build_trend_message_payload(
        report_quarter=report_quarter,
        signals=signals,
        symbol_map=symbol_map,
        min_conf=min_conf,
        limit=limit,
    )
    portfolio_summary = compute_portfolio_value_trend_summary(
        store,
        report_quarter,
        manager_ciks,
    )
    subject, body = render_trend_telegram_notification(
        payload=payload,
        portfolio_summary=portfolio_summary,
        show_reversals=show_reversals,
    )
    _send_notifications(notifiers, subject, body)

    now_iso = now_fn().date().isoformat()
    store.upsert_state(
        cik=TREND_ANALYSIS_SUMMARY_STATE_CIK,
        name=TREND_ANALYSIS_SUMMARY_STATE_NAME,
        last_accession=f"trend-analysis-{report_quarter}",
        last_filing_date=now_iso,
        last_report_date=now_iso,
        last_positions=None,
        last_notified_accession=report_quarter,
    )
    app_logger.info(
        "Trend analysis summary notification sent",
        extra={"report_quarter": report_quarter, "signals_total": payload.signals_total},
    )
