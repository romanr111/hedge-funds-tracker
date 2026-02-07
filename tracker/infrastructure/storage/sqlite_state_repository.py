from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from tracker.domain.exceptions import StateStoreError
from tracker.domain.models import ManagerState, Position


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._initialize()
        except sqlite3.Error as exc:
            raise StateStoreError(f"Failed to initialize SQLite state store at {self._db_path}: {exc}") from exc

    def _initialize(self) -> None:
        self._recover_interrupted_migration()
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manager_state (
                cik TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                last_accession TEXT,
                last_filing_date TEXT,
                last_report_date TEXT,
                last_positions_json TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._migrate_schema()
        self._conn.commit()

    def _table_exists(self, table_name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _recover_interrupted_migration(self) -> None:
        has_main = self._table_exists("manager_state")
        has_new = self._table_exists("manager_state_new")

        if has_new and not has_main:
            # Previous migration likely crashed after creating/copying into temp table.
            self._conn.execute("ALTER TABLE manager_state_new RENAME TO manager_state")
        elif has_new and has_main:
            # Stale temp table from interrupted migration; keep canonical table only.
            self._conn.execute("DROP TABLE manager_state_new")

    def _migrate_schema(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(manager_state)").fetchall()
        }
        if "last_filing_time_human" in columns:
            self._conn.execute("DROP TABLE IF EXISTS manager_state_new")
            self._conn.execute(
                """
                CREATE TABLE manager_state_new (
                    cik TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    last_accession TEXT,
                    last_filing_date TEXT,
                    last_report_date TEXT,
                    last_positions_json TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                INSERT INTO manager_state_new (
                    cik, name, last_accession, last_filing_date, last_report_date, last_positions_json, updated_at
                )
                SELECT
                    cik, name, last_accession, last_filing_date, last_report_date, last_positions_json, updated_at
                FROM manager_state
                """
            )
            self._conn.execute("DROP TABLE manager_state")
            self._conn.execute("ALTER TABLE manager_state_new RENAME TO manager_state")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_manager_state_last_filing_date ON manager_state(last_filing_date)"
            )

    def get_state(self, cik: str) -> ManagerState | None:
        try:
            cursor = self._conn.execute("SELECT * FROM manager_state WHERE cik = ?", (cik,))
            row = cursor.fetchone()
            if not row:
                return None
            last_positions_raw = json.loads(row["last_positions_json"]) if row["last_positions_json"] else None
            if last_positions_raw is None:
                last_positions = None
            elif isinstance(last_positions_raw, list):
                last_positions = cast(list[Position], last_positions_raw)
            else:
                raise StateStoreError(f"Invalid positions payload type for CIK {cik}")
            return ManagerState(
                cik=row["cik"],
                name=row["name"],
                last_accession=row["last_accession"],
                last_filing_date=row["last_filing_date"],
                last_report_date=row["last_report_date"],
                last_positions=last_positions,
            )
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            raise StateStoreError(f"Failed to read state for CIK {cik}: {exc}") from exc

    def upsert_state(
        self,
        *,
        cik: str,
        name: str,
        last_accession: str | None,
        last_filing_date: str | None,
        last_report_date: str | None,
        last_positions: list[Position] | None,
    ) -> None:
        try:
            updated_at = datetime.now(timezone.utc).isoformat()
            last_positions_json = json.dumps(last_positions) if last_positions is not None else None
            self._conn.execute(
                """
                INSERT INTO manager_state (
                    cik, name, last_accession, last_filing_date, last_report_date, last_positions_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cik) DO UPDATE SET
                    name = excluded.name,
                    last_accession = excluded.last_accession,
                    last_filing_date = excluded.last_filing_date,
                    last_report_date = excluded.last_report_date,
                    last_positions_json = excluded.last_positions_json,
                    updated_at = excluded.updated_at
                """,
                (
                    cik,
                    name,
                    last_accession,
                    last_filing_date,
                    last_report_date,
                    last_positions_json,
                    updated_at,
                ),
            )
            self._conn.commit()
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise StateStoreError(f"Failed to upsert state for CIK {cik}: {exc}") from exc

    def clear_state(self) -> int:
        try:
            cursor = self._conn.execute("DELETE FROM manager_state")
            self._conn.commit()
            return cursor.rowcount
        except sqlite3.Error as exc:
            raise StateStoreError(f"Failed to clear state store at {self._db_path}: {exc}") from exc

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error as exc:
            raise StateStoreError(f"Failed to close SQLite connection: {exc}") from exc
