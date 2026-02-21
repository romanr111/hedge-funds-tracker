from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence

from tracker.application.ports.notifier import NotifierPort
from tracker.application.use_cases.notify_quarterly_reports_completion import (
    notify_if_all_reports_published_for_current_quarter,
)
from tracker.application.use_cases.run_trend_engine import run_trend_engine_for_latest_completed_quarter
from tracker.application.use_cases.sync_quarter_snapshots import sync_quarter_snapshots
from tracker.application.use_cases.track_manager import process_manager
from tracker.composition import build_notifier_list, build_runtime
from tracker.config import load_config
from tracker.domain.exceptions import StateStoreError
from tracker.domain.models import Manager
from tracker.domain.timing import format_local_datetime, now_kyiv
from tracker.infrastructure.logging import configure_logging, log_context, new_trace_id


def _send_notifications(notifiers: Sequence[NotifierPort], subject: str, body: str) -> None:
    for notifier in notifiers:
        notifier.send(subject, body)


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
    args = parser.parse_args()

    if args.test_notification and args.dry_run:
        logger.error("Cannot combine --test-notification with --dry-run")
        return 2
    if args.test_notification and args.clean_state == "clean_state":
        logger.error("Cannot combine --test-notification with clean_state")
        return 2
    if args.dry_run and args.clean_state == "clean_state":
        logger.error("Cannot combine --dry-run with clean_state")
        return 2

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
        max_quarters=4,
        max_filing_age_days=config.max_filing_age_days,
        dry_run=args.dry_run,
        logger=logger,
    )
    trend_blend_mode = os.environ.get("TREND_BLEND_MODE", "tactical").strip().lower()
    try:
        trend_result = run_trend_engine_for_latest_completed_quarter(
            list(config.managers),
            runtime.store,
            dry_run=args.dry_run,
            blend_mode=trend_blend_mode,
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
        },
    )

    notify_if_all_reports_published_for_current_quarter(
        managers,
        runtime.store,
        runtime.notifiers,
        dry_run=args.dry_run,
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
