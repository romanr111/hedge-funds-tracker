#!/usr/bin/env python3
"""Backfill trend signals using only managers with sufficient historical data."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from tracker.application.use_cases.run_trend_engine import (
    COMPUTE_MODE_BACKFILL,
    run_trend_engine_for_target_quarter,
)
from tracker.composition import build_runtime
from tracker.config import load_config
from tracker.infrastructure.storage.sqlite_state_repository import StateStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill trends for managers with sufficient history.")
    parser.add_argument("--db", default="data/tracker.sqlite3", help="SQLite DB path.")
    parser.add_argument("--min-quarters", type=int, default=30, help="Min quarters required per manager.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)

    config = load_config()
    runtime = build_runtime(config, dry_run=False, test_notification=False)
    store = StateStore(Path(args.db))

    # Find managers with sufficient data
    full_managers = []
    for manager in config.managers:
        cur = store._conn.cursor()
        cur.execute(
            "SELECT COUNT(DISTINCT report_quarter) FROM manager_quarter_snapshot WHERE cik = ?",
            (manager.cik,),
        )
        count = cur.fetchone()[0]
        if count >= args.min_quarters:
            full_managers.append(manager)
            logger.info(f"{manager.name}: {count} quarters ✓")
        else:
            logger.info(f"{manager.name}: {count} quarters (skipped)")

    if not full_managers:
        logger.error("No managers with sufficient historical data.")
        store.close()
        return 1

    # Find common quarters
    ciks = [m.cik for m in full_managers]
    placeholders = ",".join("?" * len(ciks))
    cur = store._conn.cursor()
    cur.execute(
        f"""
        SELECT report_quarter, COUNT(DISTINCT cik) as manager_count
        FROM manager_quarter_snapshot
        WHERE cik IN ({placeholders})
        GROUP BY report_quarter
        HAVING manager_count = ?
        ORDER BY report_quarter
        """,
        ciks + [len(ciks)],
    )
    common_quarters = [r[0] for r in cur.fetchall()]
    logger.info(f"Common quarters: {len(common_quarters)} ({common_quarters[0]} → {common_quarters[-1]})")

    # Compute trends for each common quarter (need 4-quarter window, so start from 4th quarter)
    computed = 0
    for quarter in common_quarters[3:]:
        result = run_trend_engine_for_target_quarter(
            full_managers,
            store,
            target_quarter=quarter,
            dry_run=False,
            blend_mode="tactical",
            force_recompute=True,
            compute_mode=COMPUTE_MODE_BACKFILL,
            backfill_batch_id="historical-backfill",
            logger=logger,
        )
        logger.info(f"{quarter}: {result.status} ({result.signals_count} signals)")
        if result.status == "computed":
            computed += 1

    logger.info(f"Backfill complete. Computed {computed} quarters.")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
