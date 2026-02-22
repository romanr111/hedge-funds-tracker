from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from tracker.domain.exceptions import StateStoreError
from tracker.domain.models import ManagerQuarterSnapshot, ManagerState, Position, TrendRun, TrendStockSignal


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
                last_notified_accession TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._migrate_schema()
        self._initialize_trend_schema()
        self._conn.commit()

    def _initialize_trend_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manager_quarter_snapshot (
                cik TEXT NOT NULL,
                manager_name TEXT NOT NULL,
                report_quarter TEXT NOT NULL,
                report_date TEXT,
                filing_date TEXT,
                acceptance_datetime TEXT,
                accession TEXT NOT NULL,
                source_form TEXT NOT NULL,
                positions_json TEXT NOT NULL,
                aum_value_k INTEGER NOT NULL,
                positions_count INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (cik, report_quarter)
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_snapshot_report_quarter
            ON manager_quarter_snapshot(report_quarter)
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_run (
                report_quarter TEXT PRIMARY KEY,
                input_fingerprint TEXT NOT NULL,
                top_fingerprint TEXT,
                status TEXT NOT NULL,
                computed_at TEXT NOT NULL,
                notes_json TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_stock_signal (
                report_quarter TEXT NOT NULL,
                instrument_key TEXT NOT NULL,
                cusip TEXT,
                put_call TEXT,
                issuer_name TEXT,
                title TEXT,
                np_raw REAL NOT NULL,
                np_adj REAL NOT NULL,
                impulse_score REAL NOT NULL,
                accumulation_score REAL NOT NULL,
                confidence REAL NOT NULL,
                trend_ewma REAL NOT NULL,
                trend_delta REAL NOT NULL,
                breadth_buy_weight REAL NOT NULL,
                breadth_sell_weight REAL NOT NULL,
                buy_managers INTEGER NOT NULL,
                sell_managers INTEGER NOT NULL,
                crowding_hhi REAL NOT NULL,
                persistence_buy INTEGER NOT NULL,
                persistence_sell INTEGER NOT NULL,
                regime TEXT NOT NULL,
                contributors_json TEXT NOT NULL,
                computed_at TEXT NOT NULL,
                freshness_multiplier REAL NOT NULL DEFAULT 1.0,
                freshness_ok INTEGER,
                PRIMARY KEY (report_quarter, instrument_key)
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trend_stock_signal_quarter_regime
            ON trend_stock_signal(report_quarter, regime)
            """
        )
        self._ensure_trend_stock_signal_columns()

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(row["name"] == column_name for row in rows)

    def _ensure_trend_stock_signal_columns(self) -> None:
        if not self._column_exists("trend_stock_signal", "impulse_score"):
            self._conn.execute(
                "ALTER TABLE trend_stock_signal ADD COLUMN impulse_score REAL NOT NULL DEFAULT 0.0"
            )
        if not self._column_exists("trend_stock_signal", "accumulation_score"):
            self._conn.execute(
                "ALTER TABLE trend_stock_signal ADD COLUMN accumulation_score REAL NOT NULL DEFAULT 0.0"
            )
        if not self._column_exists("trend_stock_signal", "confidence"):
            self._conn.execute(
                "ALTER TABLE trend_stock_signal ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0"
            )
        if not self._column_exists("trend_stock_signal", "freshness_multiplier"):
            self._conn.execute(
                "ALTER TABLE trend_stock_signal ADD COLUMN freshness_multiplier REAL NOT NULL DEFAULT 1.0"
            )
        if not self._column_exists("trend_stock_signal", "freshness_ok"):
            self._conn.execute("ALTER TABLE trend_stock_signal ADD COLUMN freshness_ok INTEGER")

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
                    last_notified_accession TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                INSERT INTO manager_state_new (
                    cik, name, last_accession, last_filing_date, last_report_date, last_positions_json, last_notified_accession, updated_at
                )
                SELECT
                    cik, name, last_accession, last_filing_date, last_report_date, last_positions_json, NULL, updated_at
                FROM manager_state
                """
            )
            self._conn.execute("DROP TABLE manager_state")
            self._conn.execute("ALTER TABLE manager_state_new RENAME TO manager_state")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_manager_state_last_filing_date ON manager_state(last_filing_date)"
            )
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(manager_state)").fetchall()
            }

        if "last_notified_accession" not in columns:
            self._conn.execute("ALTER TABLE manager_state ADD COLUMN last_notified_accession TEXT")

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
                last_notified_accession=row["last_notified_accession"],
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
        last_notified_accession: str | None,
    ) -> None:
        try:
            updated_at = datetime.now(timezone.utc).isoformat()
            last_positions_json = json.dumps(last_positions) if last_positions is not None else None
            self._conn.execute(
                """
                INSERT INTO manager_state (
                    cik, name, last_accession, last_filing_date, last_report_date, last_positions_json, last_notified_accession, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cik) DO UPDATE SET
                    name = excluded.name,
                    last_accession = excluded.last_accession,
                    last_filing_date = excluded.last_filing_date,
                    last_report_date = excluded.last_report_date,
                    last_positions_json = excluded.last_positions_json,
                    last_notified_accession = excluded.last_notified_accession,
                    updated_at = excluded.updated_at
                """,
                (
                    cik,
                    name,
                    last_accession,
                    last_filing_date,
                    last_report_date,
                    last_positions_json,
                    last_notified_accession,
                    updated_at,
                ),
            )
            self._conn.commit()
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise StateStoreError(f"Failed to upsert state for CIK {cik}: {exc}") from exc

    def upsert_manager_quarter_snapshot(
        self,
        *,
        cik: str,
        manager_name: str,
        report_quarter: str,
        report_date: str | None,
        filing_date: str | None,
        acceptance_datetime: str | None,
        accession: str,
        source_form: str,
        positions: list[Position],
        aum_value_k: int,
    ) -> None:
        try:
            updated_at = datetime.now(timezone.utc).isoformat()
            positions_json = json.dumps(positions, separators=(",", ":"), ensure_ascii=True)
            positions_count = len(positions)
            self._conn.execute(
                """
                INSERT INTO manager_quarter_snapshot (
                    cik, manager_name, report_quarter, report_date, filing_date, acceptance_datetime,
                    accession, source_form, positions_json, aum_value_k, positions_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cik, report_quarter) DO UPDATE SET
                    manager_name = excluded.manager_name,
                    report_date = excluded.report_date,
                    filing_date = excluded.filing_date,
                    acceptance_datetime = excluded.acceptance_datetime,
                    accession = excluded.accession,
                    source_form = excluded.source_form,
                    positions_json = excluded.positions_json,
                    aum_value_k = excluded.aum_value_k,
                    positions_count = excluded.positions_count,
                    updated_at = excluded.updated_at
                """,
                (
                    cik,
                    manager_name,
                    report_quarter,
                    report_date,
                    filing_date,
                    acceptance_datetime,
                    accession,
                    source_form,
                    positions_json,
                    aum_value_k,
                    positions_count,
                    updated_at,
                ),
            )
            self._conn.commit()
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise StateStoreError(
                f"Failed to upsert manager quarter snapshot for CIK {cik}, quarter {report_quarter}: {exc}"
            ) from exc

    def _row_to_snapshot(self, row: sqlite3.Row) -> ManagerQuarterSnapshot:
        try:
            positions_raw = json.loads(row["positions_json"])
        except json.JSONDecodeError as exc:
            raise StateStoreError(
                f"Failed to decode positions_json for CIK {row['cik']}, quarter {row['report_quarter']}: {exc}"
            ) from exc
        if not isinstance(positions_raw, list):
            raise StateStoreError(
                f"Invalid positions payload type for CIK {row['cik']}, quarter {row['report_quarter']}"
            )
        return ManagerQuarterSnapshot(
            cik=row["cik"],
            manager_name=row["manager_name"],
            report_quarter=row["report_quarter"],
            report_date=row["report_date"],
            filing_date=row["filing_date"],
            acceptance_datetime=row["acceptance_datetime"],
            accession=row["accession"],
            source_form=row["source_form"],
            positions=cast(list[Position], positions_raw),
            aum_value_k=int(row["aum_value_k"]),
            positions_count=int(row["positions_count"]),
            updated_at=row["updated_at"],
        )

    def get_manager_quarter_snapshot(self, cik: str, report_quarter: str) -> ManagerQuarterSnapshot | None:
        try:
            cursor = self._conn.execute(
                """
                SELECT *
                FROM manager_quarter_snapshot
                WHERE cik = ? AND report_quarter = ?
                """,
                (cik, report_quarter),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_snapshot(row)
        except sqlite3.Error as exc:
            raise StateStoreError(
                f"Failed to read manager quarter snapshot for CIK {cik}, quarter {report_quarter}: {exc}"
            ) from exc

    def list_manager_report_quarters(self, cik: str) -> list[str]:
        try:
            rows = self._conn.execute(
                """
                SELECT report_quarter
                FROM manager_quarter_snapshot
                WHERE cik = ?
                ORDER BY report_quarter ASC
                """,
                (cik,),
            ).fetchall()
            return [cast(str, row["report_quarter"]) for row in rows]
        except sqlite3.Error as exc:
            raise StateStoreError(f"Failed to list report quarters for CIK {cik}: {exc}") from exc

    def list_common_report_quarters(self, ciks: list[str]) -> list[str]:
        if not ciks:
            return []
        placeholders = ",".join("?" for _ in ciks)
        try:
            rows = self._conn.execute(
                f"""
                SELECT report_quarter
                FROM manager_quarter_snapshot
                WHERE cik IN ({placeholders})
                GROUP BY report_quarter
                HAVING COUNT(DISTINCT cik) = ?
                ORDER BY report_quarter ASC
                """,
                tuple(ciks) + (len(ciks),),
            ).fetchall()
            return [cast(str, row["report_quarter"]) for row in rows]
        except sqlite3.Error as exc:
            raise StateStoreError("Failed to list common report quarters: {exc}".format(exc=exc)) from exc

    def list_snapshots_for_quarters(self, quarters: list[str], ciks: list[str]) -> list[ManagerQuarterSnapshot]:
        if not quarters or not ciks:
            return []
        quarter_placeholders = ",".join("?" for _ in quarters)
        cik_placeholders = ",".join("?" for _ in ciks)
        try:
            rows = self._conn.execute(
                f"""
                SELECT *
                FROM manager_quarter_snapshot
                WHERE report_quarter IN ({quarter_placeholders})
                  AND cik IN ({cik_placeholders})
                ORDER BY report_quarter ASC, manager_name ASC
                """,
                tuple(quarters) + tuple(ciks),
            ).fetchall()
            return [self._row_to_snapshot(row) for row in rows]
        except sqlite3.Error as exc:
            raise StateStoreError("Failed to list snapshots for selected quarters: {exc}".format(exc=exc)) from exc

    def get_trend_run(self, report_quarter: str) -> TrendRun | None:
        try:
            row = self._conn.execute(
                """
                SELECT report_quarter, input_fingerprint, top_fingerprint, status, computed_at, notes_json
                FROM trend_run
                WHERE report_quarter = ?
                """,
                (report_quarter,),
            ).fetchone()
            if row is None:
                return None
            return TrendRun(
                report_quarter=row["report_quarter"],
                input_fingerprint=row["input_fingerprint"],
                top_fingerprint=row["top_fingerprint"],
                status=row["status"],
                computed_at=row["computed_at"],
                notes_json=row["notes_json"],
            )
        except sqlite3.Error as exc:
            raise StateStoreError(f"Failed to read trend run for quarter {report_quarter}: {exc}") from exc

    def upsert_trend_run(
        self,
        *,
        report_quarter: str,
        input_fingerprint: str,
        top_fingerprint: str | None,
        status: str,
        computed_at: str,
        notes_json: str | None,
    ) -> None:
        try:
            self._conn.execute(
                """
                INSERT INTO trend_run (
                    report_quarter, input_fingerprint, top_fingerprint, status, computed_at, notes_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_quarter) DO UPDATE SET
                    input_fingerprint = excluded.input_fingerprint,
                    top_fingerprint = excluded.top_fingerprint,
                    status = excluded.status,
                    computed_at = excluded.computed_at,
                    notes_json = excluded.notes_json
                """,
                (report_quarter, input_fingerprint, top_fingerprint, status, computed_at, notes_json),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StateStoreError(f"Failed to upsert trend run for quarter {report_quarter}: {exc}") from exc

    def replace_trend_stock_signals(self, report_quarter: str, signals: list[TrendStockSignal]) -> None:
        try:
            self._conn.execute("DELETE FROM trend_stock_signal WHERE report_quarter = ?", (report_quarter,))
            if signals:
                self._conn.executemany(
                    """
                    INSERT INTO trend_stock_signal (
                        report_quarter, instrument_key, cusip, put_call, issuer_name, title,
                        np_raw, np_adj, impulse_score, accumulation_score, confidence, trend_ewma, trend_delta,
                        breadth_buy_weight, breadth_sell_weight, buy_managers, sell_managers,
                        crowding_hhi, persistence_buy, persistence_sell, regime,
                        contributors_json, computed_at, freshness_multiplier, freshness_ok
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            signal.report_quarter,
                            signal.instrument_key,
                            signal.cusip,
                            signal.put_call,
                            signal.issuer_name,
                            signal.title,
                            signal.np_raw,
                            signal.np_adj,
                            signal.impulse_score,
                            signal.accumulation_score,
                            signal.confidence,
                            signal.trend_ewma,
                            signal.trend_delta,
                            signal.breadth_buy_weight,
                            signal.breadth_sell_weight,
                            signal.buy_managers,
                            signal.sell_managers,
                            signal.crowding_hhi,
                            signal.persistence_buy,
                            signal.persistence_sell,
                            signal.regime,
                            signal.contributors_json,
                            signal.computed_at,
                            signal.freshness_multiplier,
                            (1 if signal.freshness_ok else 0) if signal.freshness_ok is not None else None,
                        )
                        for signal in signals
                    ],
                )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StateStoreError(f"Failed to replace trend stock signals for quarter {report_quarter}: {exc}") from exc

    def list_trend_stock_signals(self, report_quarter: str) -> list[TrendStockSignal]:
        try:
            rows = self._conn.execute(
                """
                SELECT
                    report_quarter, instrument_key, cusip, put_call, issuer_name, title,
                    np_raw, np_adj, impulse_score, accumulation_score, confidence, trend_ewma, trend_delta,
                    breadth_buy_weight, breadth_sell_weight, buy_managers, sell_managers,
                    crowding_hhi, persistence_buy, persistence_sell, regime,
                    contributors_json, computed_at, freshness_multiplier, freshness_ok
                FROM trend_stock_signal
                WHERE report_quarter = ?
                ORDER BY trend_ewma DESC
                """,
                (report_quarter,),
            ).fetchall()
            return [
                TrendStockSignal(
                    report_quarter=row["report_quarter"],
                    instrument_key=row["instrument_key"],
                    cusip=row["cusip"],
                    put_call=row["put_call"],
                    issuer_name=row["issuer_name"],
                    title=row["title"],
                    np_raw=float(row["np_raw"]),
                    np_adj=float(row["np_adj"]),
                    impulse_score=float(row["impulse_score"]),
                    accumulation_score=float(row["accumulation_score"]),
                    confidence=float(row["confidence"]),
                    trend_ewma=float(row["trend_ewma"]),
                    trend_delta=float(row["trend_delta"]),
                    breadth_buy_weight=float(row["breadth_buy_weight"]),
                    breadth_sell_weight=float(row["breadth_sell_weight"]),
                    buy_managers=int(row["buy_managers"]),
                    sell_managers=int(row["sell_managers"]),
                    crowding_hhi=float(row["crowding_hhi"]),
                    persistence_buy=int(row["persistence_buy"]),
                    persistence_sell=int(row["persistence_sell"]),
                    regime=row["regime"],
                    contributors_json=row["contributors_json"],
                    computed_at=row["computed_at"],
                    freshness_multiplier=float(row["freshness_multiplier"]),
                    freshness_ok=(None if row["freshness_ok"] is None else bool(int(row["freshness_ok"]))),
                )
                for row in rows
            ]
        except sqlite3.Error as exc:
            raise StateStoreError(f"Failed to list trend stock signals for quarter {report_quarter}: {exc}") from exc

    def get_latest_trend_quarter(self) -> str | None:
        try:
            row = self._conn.execute(
                """
                SELECT report_quarter
                FROM trend_stock_signal
                GROUP BY report_quarter
                ORDER BY report_quarter DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            return cast(str, row["report_quarter"])
        except sqlite3.Error as exc:
            raise StateStoreError(f"Failed to read latest trend quarter: {exc}") from exc

    def get_latest_trend_run_quarter(self) -> str | None:
        try:
            row = self._conn.execute(
                """
                SELECT report_quarter
                FROM trend_run
                ORDER BY report_quarter DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            return cast(str, row["report_quarter"])
        except sqlite3.Error as exc:
            raise StateStoreError(f"Failed to read latest trend run quarter: {exc}") from exc

    def clear_state(self) -> int:
        try:
            cursor_state = self._conn.execute("DELETE FROM manager_state")
            cursor_snapshot = self._conn.execute("DELETE FROM manager_quarter_snapshot")
            cursor_trend_run = self._conn.execute("DELETE FROM trend_run")
            cursor_trend_signal = self._conn.execute("DELETE FROM trend_stock_signal")
            self._conn.commit()
            return (
                cursor_state.rowcount
                + cursor_snapshot.rowcount
                + cursor_trend_run.rowcount
                + cursor_trend_signal.rowcount
            )
        except sqlite3.Error as exc:
            raise StateStoreError(f"Failed to clear state store at {self._db_path}: {exc}") from exc

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error as exc:
            raise StateStoreError(f"Failed to close SQLite connection: {exc}") from exc
