"""SQLite vintage store: append-only series observations + gauge history.

Mirrors the StateStore style (mkdir, Row factory, wrapped sqlite3.Error) but
implements market_intel's VintageRepository port. All writes are idempotent
on natural keys so repeated runs stay safe.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from market_intel.domain.exceptions import MarketIntelStorageError
from market_intel.domain.models import (
    GaugeHistoryPoint,
    PublishRunRecord,
    SeriesObservation,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VintageStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._initialize()
        except sqlite3.Error as exc:
            raise MarketIntelStorageError(
                f"Failed to initialize SQLite vintage store at {self._db_path}: {exc}"
            ) from exc

    def _initialize(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS series_vintage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id TEXT NOT NULL,
                obs_date TEXT NOT NULL,
                value REAL NOT NULL,
                source TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                UNIQUE(series_id, obs_date, value)
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_series_vintage_lookups
            ON series_vintage(series_id, obs_date)
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gauge_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                module_key TEXT NOT NULL,
                score REAL,
                state_key TEXT NOT NULL,
                members_used INTEGER NOT NULL,
                members_total INTEGER NOT NULL,
                computed_at TEXT NOT NULL,
                UNIQUE(run_date, module_key)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS publish_run (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spreadsheet_id TEXT,
                fingerprint TEXT,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                detail TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    # --- Series vintages ---------------------------------------------------

    def append_observation(self, obs: SeriesObservation) -> bool:
        try:
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO series_vintage (series_id, obs_date, value, source, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    obs.series_id,
                    obs.obs_date.isoformat(),
                    float(obs.value),
                    obs.source,
                    obs.fetched_at.isoformat(),
                ),
            )
            inserted = self._conn.execute("SELECT changes()").fetchone()[0]
            self._conn.commit()
        except sqlite3.Error as exc:
            raise MarketIntelStorageError(f"Failed to append observation: {exc}") from exc
        return bool(inserted)

    def append_observations(self, observations: Sequence[SeriesObservation]) -> int:
        fetched_at = _utc_now_iso()
        inserted_total = 0
        try:
            for obs in observations:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO series_vintage (series_id, obs_date, value, source, fetched_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        obs.series_id,
                        obs.obs_date.isoformat(),
                        float(obs.value),
                        obs.source,
                        fetched_at,
                    ),
                )
                inserted_total += self._conn.execute("SELECT changes()").fetchone()[0]
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise MarketIntelStorageError(f"Failed to append observations: {exc}") from exc
        return inserted_total

    def _row_to_observation(self, row: sqlite3.Row) -> SeriesObservation:
        return SeriesObservation(
            series_id=row["series_id"],
            obs_date=date.fromisoformat(row["obs_date"]),
            value=float(row["value"]),
            source=row["source"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )

    def read_point_in_time(self, series_id: str, as_of: date) -> SeriesObservation | None:
        try:
            row = self._conn.execute(
                """
                SELECT series_id, obs_date, value, source, fetched_at
                FROM series_vintage
                WHERE series_id = ? AND obs_date <= ?
                ORDER BY obs_date DESC, fetched_at ASC
                LIMIT 1
                """,
                (series_id, as_of.isoformat()),
            ).fetchone()
        except sqlite3.Error as exc:
            raise MarketIntelStorageError(f"Failed to read point-in-time value: {exc}") from exc
        return self._row_to_observation(row) if row is not None else None

    def read_history(
        self, series_id: str, *, start: date | None = None, end: date | None = None
    ) -> list[SeriesObservation]:
        conditions = ["series_id = ?"]
        params: list[Any] = [series_id]
        if start is not None:
            conditions.append("obs_date >= ?")
            params.append(start.isoformat())
        if end is not None:
            conditions.append("obs_date <= ?")
            params.append(end.isoformat())
        try:
            rows = self._conn.execute(
                f"""
                SELECT series_id, obs_date, value, source, fetched_at
                FROM series_vintage
                WHERE {' AND '.join(conditions)}
                ORDER BY obs_date ASC
                """,
                params,
            ).fetchall()
        except sqlite3.Error as exc:
            raise MarketIntelStorageError(f"Failed to read history: {exc}") from exc
        return [self._row_to_observation(row) for row in rows]

    # --- Gauge history ------------------------------------------------------

    def upsert_gauge_history(self, point: GaugeHistoryPoint) -> None:
        try:
            self._conn.execute(
                """
                INSERT INTO gauge_history (run_date, module_key, score, state_key, members_used, members_total, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_date, module_key) DO UPDATE SET
                    score = excluded.score,
                    state_key = excluded.state_key,
                    members_used = excluded.members_used,
                    members_total = excluded.members_total,
                    computed_at = excluded.computed_at
                """,
                (
                    point.run_date.isoformat(),
                    point.module_key,
                    point.score,
                    point.state_key,
                    point.members_used,
                    point.members_total,
                    point.computed_at.isoformat(),
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise MarketIntelStorageError(f"Failed to upsert gauge history: {exc}") from exc

    def read_gauge_history(self) -> list[GaugeHistoryPoint]:
        try:
            rows = self._conn.execute(
                """
                SELECT run_date, module_key, score, state_key, members_used, members_total, computed_at
                FROM gauge_history
                ORDER BY run_date ASC
                """
            ).fetchall()
        except sqlite3.Error as exc:
            raise MarketIntelStorageError(f"Failed to read gauge history: {exc}") from exc
        return [
            GaugeHistoryPoint(
                run_date=date.fromisoformat(row["run_date"]),
                module_key=row["module_key"],
                score=row["score"],
                state_key=row["state_key"],
                members_used=int(row["members_used"]),
                members_total=int(row["members_total"]),
                computed_at=datetime.fromisoformat(row["computed_at"]),
            )
            for row in rows
        ]

    # --- Settings and publish runs ------------------------------------------

    def get_setting(self, key: str) -> str | None:
        try:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error as exc:
            raise MarketIntelStorageError(f"Failed to read setting '{key}': {exc}") from exc
        return row["value"] if row is not None else None

    def set_setting(self, key: str, value: str) -> None:
        try:
            self._conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, _utc_now_iso()),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise MarketIntelStorageError(f"Failed to write setting '{key}': {exc}") from exc

    def record_publish_run(
        self,
        *,
        spreadsheet_id: str | None,
        fingerprint: str | None,
        status: str,
        started_at: datetime | None,
        finished_at: datetime | None,
        detail: str,
    ) -> None:
        try:
            self._conn.execute(
                """
                INSERT INTO publish_run (spreadsheet_id, fingerprint, status, started_at, finished_at, detail)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    spreadsheet_id,
                    fingerprint,
                    status,
                    (started_at or datetime.now(timezone.utc)).isoformat(),
                    (finished_at or datetime.now(timezone.utc)).isoformat(),
                    detail,
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise MarketIntelStorageError(f"Failed to record publish run: {exc}") from exc

    def last_publish_run(self) -> PublishRunRecord | None:
        try:
            row = self._conn.execute(
                """
                SELECT spreadsheet_id, fingerprint, status, started_at, finished_at, detail
                FROM publish_run
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.Error as exc:
            raise MarketIntelStorageError(f"Failed to read last publish run: {exc}") from exc
        if row is None:
            return None
        return PublishRunRecord(
            spreadsheet_id=row["spreadsheet_id"],
            fingerprint=row["fingerprint"],
            status=row["status"],
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            detail=row["detail"] or "",
        )

    def close(self) -> None:
        self._conn.close()
