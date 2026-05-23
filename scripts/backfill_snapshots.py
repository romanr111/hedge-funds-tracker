#!/usr/bin/env python3
"""Backfill historical quarter snapshots from SEC EDGAR for all configured managers."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from tracker.application.use_cases.sync_quarter_snapshots import sync_quarter_snapshots
from tracker.composition import build_runtime
from tracker.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill historical quarter snapshots from SEC.")
    parser.add_argument("--max-quarters", type=int, default=40, help="Number of quarters to fetch (default: 40 = 10 years)")
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing to DB")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)

    config = load_config()
    runtime = build_runtime(config, dry_run=args.dry_run, test_notification=False)

    logger.info("Starting historical snapshot backfill")
    logger.info(f"Managers: {len(config.managers)}, Max quarters: {args.max_quarters}")

    upserted = sync_quarter_snapshots(
        managers=config.managers,
        store=runtime.store,
        client=runtime.client,
        max_quarters=args.max_quarters,
        max_filing_age_days=99999,  # disable age filter for historical fetch
        dry_run=args.dry_run,
        logger=logger,
    )

    logger.info(f"Backfill complete. Upserted {upserted} quarter snapshots.")
    runtime.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
