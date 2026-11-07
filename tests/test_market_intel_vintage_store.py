from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from market_intel.domain.exceptions import MarketIntelStorageError
from market_intel.domain.models import GaugeHistoryPoint, SeriesObservation
from market_intel.infrastructure.storage.vintage_store import VintageStore


def _obs(
    series_id: str,
    obs_date: date,
    value: float,
    fetched_at: datetime,
    source: str = "fred",
) -> SeriesObservation:
    return SeriesObservation(
        series_id=series_id,
        obs_date=obs_date,
        value=value,
        source=source,
        fetched_at=fetched_at,
    )


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture()
def store(tmp_path: Path) -> VintageStore:
    opened = VintageStore(tmp_path / "mi" / "vintages.sqlite3")
    yield opened
    opened.close()


def test_append_observation_is_idempotent_on_unique_triple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "a" / "b" / "vintages.sqlite3"
    opened = VintageStore(db_path)
    try:
        first = _obs("hy_oas", date(2026, 11, 4), 3.32, _utc(2026, 11, 7, 7))
        assert opened.append_observation(first) is True
        assert opened.append_observation(first) is False
        revised = _obs("hy_oas", date(2026, 11, 4), 3.48, _utc(2026, 11, 7, 19))
        assert opened.append_observation(revised) is True
        assert len(opened.read_history("hy_oas")) == 2
    finally:
        opened.close()


def test_append_observations_counts_only_new_rows(store: VintageStore) -> None:
    batch = [
        _obs("spx", date(2026, 11, 3), 6800.0, _utc(2026, 11, 7, 7)),
        _obs("spx", date(2026, 11, 4), 6821.0, _utc(2026, 11, 7, 7)),
        _obs("vix", date(2026, 11, 4), 16.61, _utc(2026, 11, 7, 7)),
    ]
    assert store.append_observations(batch) == 3
    assert store.append_observations(batch) == 0
    assert len(store.read_history("spx")) == 2


def test_point_in_time_prefers_latest_obs_date_then_first_print(store: VintageStore) -> None:
    # Same obs_date printed twice with different values: earliest fetch wins.
    assert store.append_observation(_obs("vix", date(2026, 11, 4), 19.0, _utc(2026, 11, 5, 7)))
    assert store.append_observation(_obs("vix", date(2026, 11, 4), 16.61, _utc(2026, 11, 5, 19)))
    # Older obs_date printed later.
    assert store.append_observation(_obs("vix", date(2026, 11, 3), 15.5, _utc(2026, 11, 6, 7)))
    # Newer obs_date printed even later.
    assert store.append_observation(_obs("vix", date(2026, 11, 5), 15.9, _utc(2026, 11, 6, 19)))

    point = store.read_point_in_time("vix", date(2026, 11, 5))
    assert point is not None
    assert point.obs_date == date(2026, 11, 5)
    assert point.value == 15.9
    assert point.fetched_at == _utc(2026, 11, 6, 19)

    older = store.read_point_in_time("vix", date(2026, 11, 4))
    assert older is not None
    assert older.obs_date == date(2026, 11, 4)
    assert older.value == 19.0  # first print of that obs_date
    assert older.fetched_at == _utc(2026, 11, 5, 7)

    assert store.read_point_in_time("vix", date(2026, 11, 1)) is None
    assert store.read_point_in_time("nfci", date(2026, 11, 5)) is None


def test_read_history_orders_and_filters_by_dates(store: VintageStore) -> None:
    points = [
        _obs("cape", date(2025, 12, 31), 38.79, _utc(2026, 1, 2)),
        _obs("cape", date(2026, 1, 31), 38.9, _utc(2026, 1, 2)),
        _obs("cape", date(2026, 2, 28), 39.1, _utc(2026, 1, 2)),
        _obs("cape", date(2026, 2, 28), 39.5, _utc(2026, 1, 3)),
    ]
    store.append_observations(points)

    history = store.read_history("cape")
    assert [o.obs_date for o in history] == [
        date(2025, 12, 31),
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 2, 28),
    ]
    assert [o.value for o in history] == [38.79, 38.9, 39.1, 39.5]
    assert all(o.source == "fred" for o in history)

    bounded = store.read_history("cape", start=date(2026, 1, 1), end=date(2026, 1, 31))
    assert [(o.obs_date, o.value) for o in bounded] == [(date(2026, 1, 31), 38.9)]
    assert store.read_history("unknown") == []


def test_gauge_history_upserts_per_run_date_and_module(store: VintageStore) -> None:
    first = GaugeHistoryPoint(
        run_date=date(2026, 11, 6),
        module_key="valuation",
        score=None,
        state_key="state3",
        members_used=3,
        members_total=4,
        computed_at=_utc(2026, 11, 6, 12),
    )
    store.upsert_gauge_history(first)
    same_run_revised = GaugeHistoryPoint(
        run_date=date(2026, 11, 6),
        module_key="valuation",
        score=88.0,
        state_key="state5",
        members_used=4,
        members_total=4,
        computed_at=_utc(2026, 11, 6, 13),
    )
    store.upsert_gauge_history(same_run_revised)
    other_module = GaugeHistoryPoint(
        run_date=date(2026, 11, 6),
        module_key="credit",
        score=25.0,
        state_key="state2",
        members_used=4,
        members_total=4,
        computed_at=_utc(2026, 11, 6, 13, 5),
    )
    store.upsert_gauge_history(other_module)

    history = store.read_gauge_history()
    assert len(history) == 2
    assert {p.module_key for p in history} == {"credit", "valuation"}
    valuation_row = next(p for p in history if p.module_key == "valuation")
    assert valuation_row.score == 88.0
    assert valuation_row.state_key == "state5"
    assert valuation_row.members_used == 4
    assert valuation_row.computed_at == _utc(2026, 11, 6, 13)


def test_settings_roundtrip_and_update(store: VintageStore) -> None:
    assert store.get_setting("missing") is None
    store.set_setting("sheets_fingerprint", "abc123")
    assert store.get_setting("sheets_fingerprint") == "abc123"
    store.set_setting("sheets_fingerprint", "def456")
    assert store.get_setting("sheets_fingerprint") == "def456"
    row = store._conn.execute(
        "SELECT updated_at FROM settings WHERE key = ?", ("sheets_fingerprint",)
    ).fetchone()
    assert row["updated_at"]


def test_publish_run_record_latest_by_id(store: VintageStore) -> None:
    assert store.last_publish_run() is None
    store.record_publish_run(
        spreadsheet_id="sheet-1",
        fingerprint="fp-1",
        status="failed",
        started_at=_utc(2026, 11, 6, 7),
        finished_at=_utc(2026, 11, 6, 7, 1),
        detail="gog missing",
    )
    store.record_publish_run(
        spreadsheet_id="sheet-2",
        fingerprint="fp-2",
        status="ok",
        started_at=None,
        finished_at=None,
        detail="",
    )
    latest = store.last_publish_run()
    assert latest is not None
    assert latest.spreadsheet_id == "sheet-2"
    assert latest.fingerprint == "fp-2"
    assert latest.status == "ok"
    assert latest.started_at is not None
    assert latest.finished_at is not None
    assert latest.detail == ""


def test_store_wraps_sqlite_errors_as_market_intel_storage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_connect(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(
        "market_intel.infrastructure.storage.vintage_store.sqlite3.connect", broken_connect
    )
    with pytest.raises(MarketIntelStorageError, match="Failed to initialize SQLite vintage store"):
        VintageStore(tmp_path / "broken.sqlite3")


def test_store_wraps_runtime_sqlite_errors(store: VintageStore, monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenConn:
        def execute(self, *args: object, **kwargs: object) -> None:
            raise sqlite3.ProgrammingError("statement broken")

    monkeypatch.setattr(store, "_conn", _BrokenConn())
    obs = _obs("hy_oas", date(2026, 11, 4), 3.32, _utc(2026, 11, 7, 7))
    with pytest.raises(MarketIntelStorageError):
        store.append_observation(obs)
    with pytest.raises(MarketIntelStorageError):
        store.read_history("hy_oas")
    with pytest.raises(MarketIntelStorageError):
        store.get_setting("any")
    with pytest.raises(MarketIntelStorageError):
        store.read_gauge_history()
    with pytest.raises(MarketIntelStorageError):
        store.last_publish_run()
