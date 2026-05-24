from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tracker.domain.exceptions import StateStoreError
from tracker.domain.models import TrendStockSignal
from tracker.infrastructure.storage.sqlite_state_repository import StateStore


def _seed_signal(quarter: str = "2025Q1", key: str = "AAA") -> TrendStockSignal:
    return TrendStockSignal(
        report_quarter=quarter,
        instrument_key=key,
        cusip=key,
        put_call=None,
        issuer_name="Issuer",
        title="COM",
        np_raw=1.0,
        np_adj=1.0,
        impulse_score=1.0,
        accumulation_score=1.0,
        confidence=0.8,
        trend_ewma=0.5,
        trend_delta=0.1,
        breadth_buy_weight=0.2,
        breadth_sell_weight=0.1,
        buy_managers=2,
        sell_managers=0,
        crowding_hhi=0.2,
        persistence_buy=2,
        persistence_sell=0,
        regime="STRONG_BUY",
        contributors_json="[]",
        computed_at=datetime.now(timezone.utc).isoformat(),
        freshness_multiplier=1.0,
        freshness_ok=True,
    )


def _manager_state_columns(conn: sqlite3.Connection) -> set[str]:
    return {
        row[1]
        for row in conn.execute("PRAGMA table_info(manager_state)").fetchall()
    }


def test_state_store_wraps_sqlite_init_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_connect(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr("tracker.infrastructure.storage.sqlite_state_repository.sqlite3.connect", fail_connect)

    with pytest.raises(StateStoreError, match="Failed to initialize SQLite state store"):
        StateStore(tmp_path / "state.sqlite3")


def test_recover_interrupted_migration_renames_temp_table(tmp_path: Path) -> None:
    db_path = tmp_path / "recover-rename.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
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
    conn.execute(
        """
        INSERT INTO manager_state_new (
            cik, name, last_accession, last_filing_date, last_report_date, last_positions_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("0000000001", "Fund A", "acc-1", "2025-01-01", "2024-12-31", "[]", "2025-01-02T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    store = StateStore(db_path)
    try:
        row = store.get_state("0000000001")
        assert row is not None
        assert row.last_notified_accession is None
    finally:
        store.close()


def test_recover_interrupted_migration_drops_stale_temp_table(tmp_path: Path) -> None:
    db_path = tmp_path / "recover-drop.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE manager_state (
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
    conn.execute("CREATE TABLE manager_state_new (cik TEXT PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.commit()
    conn.close()

    store = StateStore(db_path)
    try:
        exists_temp = store._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='manager_state_new'"
        ).fetchone()
        assert exists_temp is None
    finally:
        store.close()


def test_migrate_schema_adds_last_notified_column_for_old_table(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-add-column.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE manager_state (
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
    conn.commit()
    conn.close()

    store = StateStore(db_path)
    try:
        columns = _manager_state_columns(store._conn)
        assert "last_notified_accession" in columns
    finally:
        store.close()


def test_migrate_schema_rewrites_legacy_last_filing_time_column(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-rewrite.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE manager_state (
            cik TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            last_accession TEXT,
            last_filing_date TEXT,
            last_filing_time_human TEXT,
            last_report_date TEXT,
            last_positions_json TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO manager_state (
            cik, name, last_accession, last_filing_date, last_filing_time_human, last_report_date, last_positions_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "0000000001",
            "Fund A",
            "acc-1",
            "2025-01-01",
            "2025-01-01 14:30:00",
            "2024-12-31",
            "[]",
            "2025-01-02T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    store = StateStore(db_path)
    try:
        columns = _manager_state_columns(store._conn)
        assert "last_filing_time_human" not in columns
        assert "last_notified_accession" in columns
        state = store.get_state("0000000001")
        assert state is not None
        assert state.last_accession == "acc-1"
    finally:
        store.close()


def test_ensure_trend_columns_for_legacy_tables(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "legacy-trend.sqlite3")
    try:
        store._conn.execute("DROP TABLE trend_stock_signal")
        store._conn.execute(
            """
            CREATE TABLE trend_stock_signal (
                report_quarter TEXT NOT NULL,
                instrument_key TEXT NOT NULL,
                cusip TEXT,
                put_call TEXT,
                issuer_name TEXT,
                title TEXT,
                np_raw REAL NOT NULL,
                np_adj REAL NOT NULL,
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
                PRIMARY KEY (report_quarter, instrument_key)
            )
            """
        )
        store._conn.execute("DROP TABLE trend_run")
        store._conn.execute(
            """
            CREATE TABLE trend_run (
                report_quarter TEXT PRIMARY KEY,
                input_fingerprint TEXT NOT NULL,
                top_fingerprint TEXT,
                status TEXT NOT NULL,
                computed_at TEXT NOT NULL,
                notes_json TEXT
            )
            """
        )
        store._ensure_trend_stock_signal_columns()
        store._ensure_trend_run_columns()
        stock_cols = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(trend_stock_signal)").fetchall()
        }
        run_cols = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(trend_run)").fetchall()
        }
        assert {"impulse_score", "accumulation_score", "confidence", "freshness_multiplier", "freshness_ok"} <= stock_cols
        assert {"is_backfill", "backfill_batch_id"} <= stock_cols
        assert {"is_backfill", "backfill_batch_id"} <= run_cols
    finally:
        store.close()


def test_get_state_rejects_invalid_position_payloads(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state-invalid-positions.sqlite3")
    try:
        store._conn.execute(
            """
            INSERT INTO manager_state (
                cik, name, last_accession, last_filing_date, last_report_date, last_positions_json, last_notified_accession, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("0000000001", "Fund A", None, None, None, "{}", None, datetime.now(timezone.utc).isoformat()),
        )
        store._conn.commit()
        with pytest.raises(StateStoreError, match="Invalid positions payload type"):
            store.get_state("0000000001")

        store._conn.execute("UPDATE manager_state SET last_positions_json = ? WHERE cik = ?", ("{", "0000000001"))
        store._conn.commit()
        with pytest.raises(StateStoreError, match="Failed to read state for CIK 0000000001"):
            store.get_state("0000000001")
    finally:
        store.close()


def test_upsert_methods_wrap_serialization_errors(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "serialize-errors.sqlite3")
    try:
        with pytest.raises(StateStoreError, match="Failed to upsert state"):
            store.upsert_state(
                cik="0000000001",
                name="Fund A",
                last_accession=None,
                last_filing_date=None,
                last_report_date=None,
                last_positions=[{"bad": {1, 2, 3}}],
                last_notified_accession=None,
            )

        with pytest.raises(StateStoreError, match="Failed to upsert manager quarter snapshot"):
            store.upsert_manager_quarter_snapshot(
                cik="0000000001",
                manager_name="Fund A",
                report_quarter="2025Q1",
                report_date="2025-03-31",
                filing_date="2025-05-15",
                acceptance_datetime="20250515120000",
                accession="acc-1",
                source_form="13F-HR",
                positions=[{"bad": {1, 2, 3}}],
                aum_value_k=100,
            )
    finally:
        store.close()


def test_snapshot_row_validation_errors(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "snapshot-errors.sqlite3")
    try:
        row_template = (
            "0000000001",
            "Fund A",
            "2025Q1",
            "2025-03-31",
            "2025-05-15",
            "20250515120000",
            "acc-1",
            "13F-HR",
            None,
            100,
            1,
            datetime.now(timezone.utc).isoformat(),
        )
        store._conn.execute(
            """
            INSERT INTO manager_quarter_snapshot (
                cik, manager_name, report_quarter, report_date, filing_date, acceptance_datetime,
                accession, source_form, positions_json, aum_value_k, positions_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row_template[:-4] + ("{",) + row_template[-3:],
        )
        store._conn.commit()
        with pytest.raises(StateStoreError, match="Failed to decode positions_json"):
            store.get_manager_quarter_snapshot("0000000001", "2025Q1")

        store._conn.execute("UPDATE manager_quarter_snapshot SET positions_json = ? WHERE cik = ?", ("{}", "0000000001"))
        store._conn.commit()
        with pytest.raises(StateStoreError, match="Invalid positions payload type"):
            store.get_manager_quarter_snapshot("0000000001", "2025Q1")
    finally:
        store.close()


def test_list_helpers_return_empty_when_no_input(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "list-empty.sqlite3")
    try:
        assert store.list_common_report_quarters([]) == []
        assert store.list_snapshots_for_quarters([], ["0000000001"]) == []
        assert store.list_snapshots_for_quarters(["2025Q1"], []) == []
    finally:
        store.close()


def test_trend_tables_and_clear_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "trend.sqlite3")
    try:
        assert store.get_latest_trend_quarter() is None
        assert store.get_latest_trend_run_quarter() is None

        store.replace_trend_stock_signals("2025Q1", [_seed_signal()])
        store.upsert_trend_run(
            report_quarter="2025Q1",
            input_fingerprint="input",
            top_fingerprint="top",
            status="ok",
            computed_at=datetime.now(timezone.utc).isoformat(),
            notes_json=None,
            is_backfill=True,
            backfill_batch_id="batch-1",
        )
        assert store.get_latest_trend_quarter() == "2025Q1"
        assert store.get_latest_trend_run_quarter() == "2025Q1"

        assert store.clear_state() > 0
    finally:
        store.close()


def test_get_latest_trend_quarter_with_multiple_quarters_returns_max(tmp_path: Path) -> None:
    """Regression guard: ensure DESC ordering works when multiple quarters exist."""
    store = StateStore(tmp_path / "multi-quarter.sqlite3")
    try:
        # Insert quarters out of chronological order
        store.replace_trend_stock_signals("2022Q2", [_seed_signal("2022Q2", "A")])
        store.replace_trend_stock_signals("2024Q4", [_seed_signal("2024Q4", "B")])
        store.replace_trend_stock_signals("2023Q1", [_seed_signal("2023Q1", "C")])
        store.replace_trend_stock_signals("2026Q1", [_seed_signal("2026Q1", "D")])

        assert store.get_latest_trend_quarter() == "2026Q1"
    finally:
        store.close()


def test_list_trend_quarters_returns_ascending_order(tmp_path: Path) -> None:
    """Ensure quarters are returned oldest-first (chronological order)."""
    store = StateStore(tmp_path / "list-quarters.sqlite3")
    try:
        store.replace_trend_stock_signals("2022Q2", [_seed_signal("2022Q2", "A")])
        store.replace_trend_stock_signals("2025Q4", [_seed_signal("2025Q4", "B")])
        store.replace_trend_stock_signals("2024Q1", [_seed_signal("2024Q1", "C")])

        quarters = store.list_trend_quarters()
        assert quarters == ["2022Q2", "2024Q1", "2025Q4"]
    finally:
        store.close()


@pytest.mark.parametrize(
    "caller",
    [
        lambda s: s.replace_trend_stock_signals("2025Q1", [_seed_signal()]),
        lambda s: s.list_trend_stock_signals("2025Q1"),
        lambda s: s.get_latest_trend_quarter(),
        lambda s: s.list_trend_quarters(),
        lambda s: s.has_trend_signals_for_quarter("2025Q1"),
        lambda s: s.clear_state(),
    ],
)
def test_methods_raise_state_store_error_when_connection_closed(
    tmp_path: Path,
    caller: object,
) -> None:
    store = StateStore(tmp_path / "closed.sqlite3")
    store.close()
    with pytest.raises(StateStoreError):
        casted = caller  # avoid mypy-style complaints in static contexts
        casted(store)


def test_close_wraps_sqlite_errors(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "close-error.sqlite3")

    class _FailingConn:
        def close(self) -> None:
            raise sqlite3.OperationalError("close failed")

    store._conn = _FailingConn()  # type: ignore[assignment]
    with pytest.raises(StateStoreError, match="Failed to close SQLite connection"):
        store.close()
