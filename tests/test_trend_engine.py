from __future__ import annotations

from pathlib import Path

import pytest

import tracker.domain.trends as trends_module
from tracker.application.use_cases.run_trend_engine import (
    _build_top_fingerprint_payload,
    detect_latest_completed_report_quarter,
    run_trend_engine_for_latest_completed_quarter,
    run_trend_engine_for_target_quarter,
)
from tracker.config import ManagerConfig
from tracker.domain.models import ManagerQuarterSnapshot
from tracker.domain.trends import (
    TrendSignalRow,
    _confidence_score,
    _entry_impulse_multiplier,
    _price_freshness_multiplier,
    _trade_flow_delta,
    compute_trend_signals,
)
from tracker.infrastructure.storage.sqlite_state_repository import StateStore


def _positions(alpha_value: int, beta_value: int) -> list[dict[str, object]]:
    return [
        {
            "name": "Alpha",
            "title": "COM",
            "cusip": "111111111",
            "put_call": None,
            "value": alpha_value,
            "shares": alpha_value,
        },
        {
            "name": "Beta",
            "title": "COM",
            "cusip": "222222222",
            "put_call": None,
            "value": beta_value,
            "shares": beta_value,
        },
    ]


def _seed_snapshot(
    store: StateStore,
    *,
    cik: str,
    name: str,
    quarter: str,
    accession: str,
    positions: list[dict[str, object]],
) -> None:
    store.upsert_manager_quarter_snapshot(
        cik=cik,
        manager_name=name,
        report_quarter=quarter,
        report_date="2025-12-31" if quarter == "2025Q4" else "2025-09-30",
        filing_date="2026-02-14" if quarter == "2025Q4" else "2025-11-14",
        acceptance_datetime="20260214120000" if quarter == "2025Q4" else "20251114120000",
        accession=accession,
        source_form="13F-HR",
        positions=positions,
        aum_value_k=sum(int(item["value"]) for item in positions),
    )


def test_detect_latest_completed_report_quarter_uses_intersection(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q3",
            accession="a-q3",
            positions=_positions(100, 900),
        )
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q4",
            accession="a-q4",
            positions=_positions(300, 700),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q4",
            accession="b-q4",
            positions=_positions(200, 800),
        )

        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]
        assert detect_latest_completed_report_quarter(managers, store) == "2025Q4"
    finally:
        store.close()


def test_run_trend_engine_computes_and_persists_signals(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q3",
            accession="a-q3",
            positions=_positions(100, 900),
        )
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q4",
            accession="a-q4",
            positions=_positions(300, 700),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q3",
            accession="b-q3",
            positions=_positions(50, 950),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q4",
            accession="b-q4",
            positions=_positions(200, 800),
        )

        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]
        result = run_trend_engine_for_latest_completed_quarter(managers, store, dry_run=False)
        assert result.status == "computed"
        assert result.report_quarter == "2025Q4"
        assert result.signals_count > 0

        signals = store.list_trend_stock_signals("2025Q4")
        alpha = next((item for item in signals if item.instrument_key == "111111111"), None)
        assert alpha is not None
        assert alpha.trend_ewma > 0
        assert "BUY" in alpha.regime
        assert alpha.impulse_score != 0
        assert alpha.accumulation_score != 0
        assert 0 <= alpha.confidence <= 1
    finally:
        store.close()


def test_run_trend_engine_skips_writing_signals_when_top_unchanged(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q3",
            accession="a-q3",
            positions=_positions(100, 900),
        )
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q4",
            accession="a-q4",
            positions=_positions(300, 700),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q3",
            accession="b-q3",
            positions=_positions(50, 950),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q4",
            accession="b-q4",
            positions=_positions(200, 800),
        )
        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]

        first = run_trend_engine_for_latest_completed_quarter(managers, store, dry_run=False)
        assert first.status == "computed"
        first_alpha = next(item for item in store.list_trend_stock_signals("2025Q4") if item.instrument_key == "111111111")
        first_computed_at = first_alpha.computed_at

        # Amendment-like change: input changes but ranking keys remain the same.
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q4",
            accession="a-q4-amendment",
            positions=_positions(350, 650),
        )
        second = run_trend_engine_for_latest_completed_quarter(managers, store, dry_run=False)
        assert second.status == "skipped_no_top_change"

        second_alpha = next(item for item in store.list_trend_stock_signals("2025Q4") if item.instrument_key == "111111111")
        assert second_alpha.computed_at == first_computed_at
    finally:
        store.close()


def test_run_trend_engine_recomputes_with_latest_prices_even_when_top_unchanged(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q3",
            accession="a-q3",
            positions=_positions(100, 900),
        )
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q4",
            accession="a-q4",
            positions=_positions(300, 700),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q3",
            accession="b-q3",
            positions=_positions(50, 950),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q4",
            accession="b-q4",
            positions=_positions(200, 800),
        )
        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]

        first = run_trend_engine_for_latest_completed_quarter(
            managers,
            store,
            dry_run=False,
            latest_prices={"111111111": 1.05},
        )
        assert first.status == "computed"
        first_alpha = next(item for item in store.list_trend_stock_signals("2025Q4") if item.instrument_key == "111111111")

        second = run_trend_engine_for_latest_completed_quarter(
            managers,
            store,
            dry_run=False,
            latest_prices={"111111111": 5.0},
        )
        assert second.status == "computed"

        second_alpha = next(item for item in store.list_trend_stock_signals("2025Q4") if item.instrument_key == "111111111")
        assert second_alpha.confidence < first_alpha.confidence
    finally:
        store.close()


def test_run_trend_engine_skips_when_input_unchanged_after_reupsert(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q3",
            accession="a-q3",
            positions=_positions(100, 900),
        )
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q4",
            accession="a-q4",
            positions=_positions(300, 700),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q3",
            accession="b-q3",
            positions=_positions(50, 950),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q4",
            accession="b-q4",
            positions=_positions(200, 800),
        )
        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]

        first = run_trend_engine_for_latest_completed_quarter(managers, store, dry_run=False)
        assert first.status == "computed"

        # Simulate idempotent snapshot sync: same content/accession, new updated_at in DB.
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q4",
            accession="a-q4",
            positions=_positions(300, 700),
        )
        second = run_trend_engine_for_latest_completed_quarter(managers, store, dry_run=False)
        assert second.status == "skipped_no_new_completed_quarter"
    finally:
        store.close()


def test_run_trend_engine_force_recompute_bypasses_unchanged_input_skip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q3",
            accession="a-q3",
            positions=_positions(100, 900),
        )
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q4",
            accession="a-q4",
            positions=_positions(300, 700),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q3",
            accession="b-q3",
            positions=_positions(50, 950),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q4",
            accession="b-q4",
            positions=_positions(200, 800),
        )
        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]

        first = run_trend_engine_for_latest_completed_quarter(managers, store, dry_run=False)
        assert first.status == "computed"

        second = run_trend_engine_for_latest_completed_quarter(managers, store, dry_run=False)
        assert second.status == "skipped_no_new_completed_quarter"

        forced = run_trend_engine_for_latest_completed_quarter(
            managers,
            store,
            dry_run=False,
            force_recompute=True,
        )
        assert forced.status == "computed"
        assert forced.signals_count > 0
    finally:
        store.close()


def test_run_trend_engine_recomputes_when_manager_weight_changes(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        # Fund A: bullish Alpha, bearish Beta
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q3",
            accession="a-q3",
            positions=_positions(100, 900),
        )
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q4",
            accession="a-q4",
            positions=_positions(500, 500),
        )
        # Fund B: bearish Alpha, bullish Beta
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q3",
            accession="b-q3",
            positions=_positions(900, 100),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q4",
            accession="b-q4",
            positions=_positions(500, 500),
        )
        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]

        first = run_trend_engine_for_latest_completed_quarter(managers, store, dry_run=False)
        assert first.status == "computed"
        first_alpha = next(item for item in store.list_trend_stock_signals("2025Q4") if item.instrument_key == "111111111")
        first_computed_at = first_alpha.computed_at

        # Same weights → skip
        second = run_trend_engine_for_latest_completed_quarter(managers, store, dry_run=False)
        assert second.status == "skipped_no_new_completed_quarter"

        # Changed weight (drop Fund B to zero) → must recompute without force_recompute
        managers_changed = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=0.0),
        ]
        third = run_trend_engine_for_latest_completed_quarter(managers_changed, store, dry_run=False)
        assert third.status == "computed"
        assert third.signals_count > 0

        third_alpha = next(item for item in store.list_trend_stock_signals("2025Q4") if item.instrument_key == "111111111")
        assert third_alpha.computed_at != first_computed_at
    finally:
        store.close()


def test_run_trend_engine_for_target_quarter_marks_backfill_metadata(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q3",
            accession="a-q3",
            positions=_positions(100, 900),
        )
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q4",
            accession="a-q4",
            positions=_positions(300, 700),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q3",
            accession="b-q3",
            positions=_positions(50, 950),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q4",
            accession="b-q4",
            positions=_positions(200, 800),
        )

        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]
        result = run_trend_engine_for_target_quarter(
            managers,
            store,
            target_quarter="2025Q4",
            dry_run=False,
            compute_mode="backfill",
            backfill_batch_id="batch-001",
        )
        assert result.status == "computed"

        trend_run = store.get_trend_run("2025Q4")
        assert trend_run is not None
        assert trend_run.is_backfill is True
        assert trend_run.backfill_batch_id == "batch-001"

        signals = store.list_trend_stock_signals("2025Q4")
        assert signals
        assert all(item.is_backfill is True for item in signals)
        assert all(item.backfill_batch_id == "batch-001" for item in signals)
    finally:
        store.close()


def test_run_trend_engine_for_target_quarter_skip_if_exists(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q3",
            accession="a-q3",
            positions=_positions(100, 900),
        )
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q4",
            accession="a-q4",
            positions=_positions(300, 700),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q3",
            accession="b-q3",
            positions=_positions(50, 950),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter="2025Q4",
            accession="b-q4",
            positions=_positions(200, 800),
        )
        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]

        first = run_trend_engine_for_latest_completed_quarter(managers, store, dry_run=False)
        assert first.status == "computed"
        first_run = store.get_trend_run("2025Q4")
        assert first_run is not None
        assert first_run.is_backfill is False

        skipped = run_trend_engine_for_target_quarter(
            managers,
            store,
            target_quarter="2025Q4",
            dry_run=False,
            compute_mode="backfill",
            backfill_batch_id="batch-002",
            skip_if_exists=True,
        )
        assert skipped.status == "skipped_existing_quarter"

        second_run = store.get_trend_run("2025Q4")
        assert second_run is not None
        assert second_run.is_backfill is False
    finally:
        store.close()


def test_top_fingerprint_payload_is_order_invariant() -> None:
    row_a = TrendSignalRow(
        instrument_key="111111111",
        cusip="111111111",
        put_call=None,
        issuer_name="A",
        title="COM",
        np_raw=0.1,
        np_adj=0.1,
        impulse_score=0.12,
        accumulation_score=0.08,
        confidence=0.9,
        trend_ewma=0.5,
        trend_delta=0.05,
        breadth_buy_weight=0.2,
        breadth_sell_weight=0.0,
        buy_managers=3,
        sell_managers=0,
        crowding_hhi=0.2,
        persistence_buy=2,
        persistence_sell=0,
        regime="STRONG_BUY",
        contributors=[],
    )
    row_b = TrendSignalRow(
        instrument_key="222222222",
        cusip="222222222",
        put_call=None,
        issuer_name="B",
        title="COM",
        np_raw=-0.1,
        np_adj=-0.1,
        impulse_score=-0.11,
        accumulation_score=-0.07,
        confidence=0.85,
        trend_ewma=-0.4,
        trend_delta=-0.04,
        breadth_buy_weight=0.0,
        breadth_sell_weight=0.2,
        buy_managers=0,
        sell_managers=3,
        crowding_hhi=0.2,
        persistence_buy=0,
        persistence_sell=2,
        regime="STRONG_SELL",
        contributors=[],
    )
    row_c = TrendSignalRow(
        instrument_key="333333333",
        cusip="333333333",
        put_call=None,
        issuer_name="C",
        title="COM",
        np_raw=0.01,
        np_adj=0.01,
        impulse_score=0.05,
        accumulation_score=0.01,
        confidence=0.7,
        trend_ewma=0.02,
        trend_delta=0.3,
        breadth_buy_weight=0.12,
        breadth_sell_weight=0.01,
        buy_managers=3,
        sell_managers=1,
        crowding_hhi=0.3,
        persistence_buy=1,
        persistence_sell=0,
        regime="REVERSAL_BUY",
        contributors=[],
    )

    payload_one = _build_top_fingerprint_payload([row_a, row_b, row_c], limit=20)
    payload_two = _build_top_fingerprint_payload([row_c, row_b, row_a], limit=20)
    assert payload_one == payload_two


def _snapshot_for_compute(*, cik: str, quarter: str, alpha_value: int, beta_value: int) -> ManagerQuarterSnapshot:
    report_date_by_quarter = {"2025Q3": "2025-09-30", "2025Q4": "2025-12-31"}
    filing_date_by_quarter = {"2025Q3": "2025-11-14", "2025Q4": "2026-02-14"}
    acceptance_by_quarter = {"2025Q3": "20251114120000", "2025Q4": "20260214120000"}
    positions = _positions(alpha_value, beta_value)
    return ManagerQuarterSnapshot(
        cik=cik,
        manager_name="Fund A",
        report_quarter=quarter,
        report_date=report_date_by_quarter.get(quarter),
        filing_date=filing_date_by_quarter.get(quarter),
        acceptance_datetime=acceptance_by_quarter.get(quarter),
        accession=f"{cik}-{quarter}",
        source_form="13F-HR",
        positions=positions,
        aum_value_k=alpha_value + beta_value,
        positions_count=len(positions),
    )


def test_entry_impulse_multiplier_targets_new_large_entries() -> None:
    assert _entry_impulse_multiplier(0.0, 0.03) == pytest.approx(2.0)
    assert _entry_impulse_multiplier(0.0, 0.04) == pytest.approx(2.25)
    assert _entry_impulse_multiplier(0.0, 0.05) == pytest.approx(2.5)
    assert _entry_impulse_multiplier(0.04, 0.05) == pytest.approx(1.0)
    assert _entry_impulse_multiplier(0.05, 0.0) == pytest.approx(2.5)


def test_compute_trend_signals_blend_modes_change_weighting() -> None:
    quarters = ["2025Q3", "2025Q4"]
    snapshots_by_quarter = {
        "2025Q3": {"0000000001": _snapshot_for_compute(cik="0000000001", quarter="2025Q3", alpha_value=10, beta_value=990)},
        "2025Q4": {"0000000001": _snapshot_for_compute(cik="0000000001", quarter="2025Q4", alpha_value=40, beta_value=960)},
    }
    manager_weights = {"0000000001": 1.0}

    tactical = compute_trend_signals(
        quarters=quarters,
        snapshots_by_quarter=snapshots_by_quarter,
        manager_weights=manager_weights,
        blend_mode="tactical",
    )
    portfolio = compute_trend_signals(
        quarters=quarters,
        snapshots_by_quarter=snapshots_by_quarter,
        manager_weights=manager_weights,
        blend_mode="portfolio",
    )

    tactical_alpha = next(item for item in tactical.signals if item.instrument_key == "111111111")
    portfolio_alpha = next(item for item in portfolio.signals if item.instrument_key == "111111111")
    assert tactical_alpha.impulse_score > tactical_alpha.accumulation_score
    assert tactical_alpha.trend_ewma > portfolio_alpha.trend_ewma

    with pytest.raises(ValueError, match="Unsupported blend mode"):
        compute_trend_signals(
            quarters=quarters,
            snapshots_by_quarter=snapshots_by_quarter,
            manager_weights=manager_weights,
            blend_mode="invalid",
        )


def test_confidence_directional_and_disagreement_penalty() -> None:
    directional = _confidence_score(
        direction="BUY",
        directional_weight=0.20,
        opposite_weight=0.0,
        directional_managers=4,
        opposite_managers=0,
        crowding_hhi=0.25,
        directional_persistence=2,
        min_managers=3,
        min_weight=0.10,
        magnitude_value=0.05,
        magnitude_scale=0.05,
    )
    conflicted = _confidence_score(
        direction="BUY",
        directional_weight=0.20,
        opposite_weight=0.20,
        directional_managers=4,
        opposite_managers=4,
        crowding_hhi=0.25,
        directional_persistence=2,
        min_managers=3,
        min_weight=0.10,
        magnitude_value=0.05,
        magnitude_scale=0.05,
    )
    heavily_conflicted = _confidence_score(
        direction="BUY",
        directional_weight=0.20,
        opposite_weight=0.40,
        directional_managers=4,
        opposite_managers=4,
        crowding_hhi=0.25,
        directional_persistence=2,
        min_managers=3,
        min_weight=0.10,
        magnitude_value=0.05,
        magnitude_scale=0.05,
    )
    wrong_direction = _confidence_score(
        direction="SELL",
        directional_weight=0.03,
        opposite_weight=0.20,
        directional_managers=1,
        opposite_managers=4,
        crowding_hhi=0.25,
        directional_persistence=1,
        min_managers=3,
        min_weight=0.10,
        magnitude_value=0.05,
        magnitude_scale=0.05,
    )

    assert directional > conflicted
    assert conflicted > heavily_conflicted
    assert wrong_direction < conflicted


def test_confidence_gets_bonus_from_high_conviction_directional_weight() -> None:
    baseline = _confidence_score(
        direction="BUY",
        directional_weight=0.20,
        opposite_weight=0.02,
        directional_managers=4,
        opposite_managers=1,
        crowding_hhi=0.22,
        directional_persistence=2,
        min_managers=3,
        min_weight=0.10,
        magnitude_value=0.05,
        magnitude_scale=0.05,
        directional_high_conviction_weight=0.0,
    )
    boosted = _confidence_score(
        direction="BUY",
        directional_weight=0.20,
        opposite_weight=0.02,
        directional_managers=4,
        opposite_managers=1,
        crowding_hhi=0.22,
        directional_persistence=2,
        min_managers=3,
        min_weight=0.10,
        magnitude_value=0.05,
        magnitude_scale=0.05,
        directional_high_conviction_weight=0.20,
    )
    assert boosted > baseline


def test_confidence_magnitude_mixes_np_and_blended_components() -> None:
    np_only = _confidence_score(
        direction="BUY",
        directional_weight=0.20,
        opposite_weight=0.02,
        directional_managers=4,
        opposite_managers=1,
        crowding_hhi=0.22,
        directional_persistence=2,
        min_managers=3,
        min_weight=0.10,
        magnitude_value=0.01,
        magnitude_scale=0.05,
    )
    mixed = _confidence_score(
        direction="BUY",
        directional_weight=0.20,
        opposite_weight=0.02,
        directional_managers=4,
        opposite_managers=1,
        crowding_hhi=0.22,
        directional_persistence=2,
        min_managers=3,
        min_weight=0.10,
        magnitude_value=0.01,
        magnitude_scale=0.05,
        blended_magnitude_value=0.05,
        blended_magnitude_scale=0.05,
    )
    assert mixed > np_only


def test_price_freshness_multiplier_reduces_confidence_on_large_drift() -> None:
    assert _price_freshness_multiplier(100.0, 104.0) == pytest.approx(1.0)
    assert _price_freshness_multiplier(100.0, 120.0) < 1.0
    assert _price_freshness_multiplier(100.0, 1000.0) == pytest.approx(0.35)


def test_compute_trend_signals_passes_blended_magnitude_inputs_to_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quarters = ["2025Q3", "2025Q4"]
    snapshots_by_quarter = {
        "2025Q3": {
            "0000000001": _snapshot_for_compute(
                cik="0000000001",
                quarter="2025Q3",
                alpha_value=100,
                beta_value=900,
            )
        },
        "2025Q4": {
            "0000000001": _snapshot_for_compute(
                cik="0000000001",
                quarter="2025Q4",
                alpha_value=300,
                beta_value=700,
            )
        },
    }
    manager_weights = {"0000000001": 1.0}

    captured: list[tuple[float | None, float | None]] = []
    original_confidence_score = trends_module._confidence_score

    def _confidence_score_spy(**kwargs):
        captured.append((kwargs.get("blended_magnitude_value"), kwargs.get("blended_magnitude_scale")))
        return original_confidence_score(**kwargs)

    monkeypatch.setattr(trends_module, "_confidence_score", _confidence_score_spy)

    compute_trend_signals(
        quarters=quarters,
        snapshots_by_quarter=snapshots_by_quarter,
        manager_weights=manager_weights,
        blend_mode="tactical",
    )

    assert captured
    assert all(scale is not None and scale > 0 for _, scale in captured)
    assert any(value is not None and abs(value) > 0 for value, _ in captured)


def test_trade_flow_delta_uses_shares_direction_when_available() -> None:
    # Real-world pattern: shares added, but value-weight can decline due market price move.
    flow = _trade_flow_delta(
        prev_weight=0.07759265,
        curr_weight=0.06901949,
        prev_shares=311810,
        curr_shares=317000,
    )
    assert flow > 0

    fallback = _trade_flow_delta(
        prev_weight=0.07759265,
        curr_weight=0.06901949,
        prev_shares=0,
        curr_shares=0,
    )
    assert fallback < 0


def test_trade_flow_delta_ignores_small_shares_noise_up_to_one_point_five_percent() -> None:
    flow_at_one_point_five_percent = _trade_flow_delta(
        prev_weight=0.10,
        curr_weight=0.11,
        prev_shares=10000,
        curr_shares=10150,
    )
    assert flow_at_one_point_five_percent == pytest.approx(0.0)

    flow_below_one_point_five_percent = _trade_flow_delta(
        prev_weight=0.10,
        curr_weight=0.11,
        prev_shares=10000,
        curr_shares=10149,
    )
    assert flow_below_one_point_five_percent == pytest.approx(0.0)

    flow_above_one_point_five_percent = _trade_flow_delta(
        prev_weight=0.10,
        curr_weight=0.11,
        prev_shares=10000,
        curr_shares=10160,
    )
    assert flow_above_one_point_five_percent > 0


def test_trade_flow_delta_handles_split_like_shares_jump_as_weight_delta() -> None:
    flow = _trade_flow_delta(
        prev_weight=0.19211302,
        curr_weight=0.18147970,
        prev_shares=41020231,
        curr_shares=61403089,
    )
    assert flow == pytest.approx(-0.01063332)


def test_compute_trend_signals_does_not_create_conflict_from_price_only_move() -> None:
    quarters = ["2025Q3", "2025Q4"]
    snapshots_by_quarter = {
        "2025Q3": {
            "0000000001": ManagerQuarterSnapshot(
                cik="0000000001",
                manager_name="Fund A",
                report_quarter="2025Q3",
                report_date="2025-09-30",
                filing_date="2025-11-14",
                acceptance_datetime="20251114120000",
                accession="a-q3",
                source_form="13F-HR",
                positions=[
                    {"name": "Alpha", "title": "COM", "cusip": "111111111", "put_call": None, "value": 100, "shares": 100},
                    {"name": "Beta", "title": "COM", "cusip": "222222222", "put_call": None, "value": 900, "shares": 900},
                ],
                aum_value_k=1000,
                positions_count=2,
            ),
            "0000000002": ManagerQuarterSnapshot(
                cik="0000000002",
                manager_name="Fund B",
                report_quarter="2025Q3",
                report_date="2025-09-30",
                filing_date="2025-11-14",
                acceptance_datetime="20251114120000",
                accession="b-q3",
                source_form="13F-HR",
                positions=[
                    {"name": "Alpha", "title": "COM", "cusip": "111111111", "put_call": None, "value": 100, "shares": 100},
                    {"name": "Beta", "title": "COM", "cusip": "222222222", "put_call": None, "value": 900, "shares": 900},
                ],
                aum_value_k=1000,
                positions_count=2,
            ),
        },
        "2025Q4": {
            "0000000001": ManagerQuarterSnapshot(
                cik="0000000001",
                manager_name="Fund A",
                report_quarter="2025Q4",
                report_date="2025-12-31",
                filing_date="2026-02-14",
                acceptance_datetime="20260214120000",
                accession="a-q4",
                source_form="13F-HR",
                positions=[
                    {"name": "Alpha", "title": "COM", "cusip": "111111111", "put_call": None, "value": 110, "shares": 110},
                    {"name": "Beta", "title": "COM", "cusip": "222222222", "put_call": None, "value": 890, "shares": 890},
                ],
                aum_value_k=1000,
                positions_count=2,
            ),
            "0000000002": ManagerQuarterSnapshot(
                cik="0000000002",
                manager_name="Fund B",
                report_quarter="2025Q4",
                report_date="2025-12-31",
                filing_date="2026-02-14",
                acceptance_datetime="20260214120000",
                accession="b-q4",
                source_form="13F-HR",
                positions=[
                    # Same shares, lower value => price move only (must not become SELL flow).
                    {"name": "Alpha", "title": "COM", "cusip": "111111111", "put_call": None, "value": 90, "shares": 100},
                    {"name": "Beta", "title": "COM", "cusip": "222222222", "put_call": None, "value": 910, "shares": 900},
                ],
                aum_value_k=1000,
                positions_count=2,
            ),
        },
    }
    manager_weights = {"0000000001": 1.0, "0000000002": 1.0}

    result = compute_trend_signals(
        quarters=quarters,
        snapshots_by_quarter=snapshots_by_quarter,
        manager_weights=manager_weights,
        blend_mode="tactical",
    )
    alpha = next(item for item in result.signals if item.instrument_key == "111111111")
    assert alpha.buy_managers == 1
    assert alpha.sell_managers == 0


def test_compute_trend_signals_keeps_np_unpenalized_by_crowding() -> None:
    quarters = ["2025Q3", "2025Q4"]
    snapshots_by_quarter = {
        "2025Q3": {"0000000001": _snapshot_for_compute(cik="0000000001", quarter="2025Q3", alpha_value=10, beta_value=990)},
        "2025Q4": {"0000000001": _snapshot_for_compute(cik="0000000001", quarter="2025Q4", alpha_value=40, beta_value=960)},
    }
    manager_weights = {"0000000001": 1.0}

    result = compute_trend_signals(
        quarters=quarters,
        snapshots_by_quarter=snapshots_by_quarter,
        manager_weights=manager_weights,
        blend_mode="tactical",
    )
    alpha = next(item for item in result.signals if item.instrument_key == "111111111")
    assert alpha.np_raw != 0
    assert alpha.np_adj == pytest.approx(alpha.np_raw)


def test_compute_trend_signals_applies_price_freshness_decay() -> None:
    quarters = ["2025Q3", "2025Q4"]
    snapshots_by_quarter = {
        "2025Q3": {"0000000001": _snapshot_for_compute(cik="0000000001", quarter="2025Q3", alpha_value=10, beta_value=990)},
        "2025Q4": {"0000000001": _snapshot_for_compute(cik="0000000001", quarter="2025Q4", alpha_value=40, beta_value=960)},
    }
    manager_weights = {"0000000001": 1.0}

    baseline = compute_trend_signals(
        quarters=quarters,
        snapshots_by_quarter=snapshots_by_quarter,
        manager_weights=manager_weights,
        blend_mode="tactical",
    )
    with_decay = compute_trend_signals(
        quarters=quarters,
        snapshots_by_quarter=snapshots_by_quarter,
        manager_weights=manager_weights,
        blend_mode="tactical",
        latest_prices={"111111111": 52000.0},
    )

    baseline_alpha = next(item for item in baseline.signals if item.instrument_key == "111111111")
    decayed_alpha = next(item for item in with_decay.signals if item.instrument_key == "111111111")
    assert baseline_alpha.freshness_ok is None
    assert decayed_alpha.freshness_ok is False
    assert decayed_alpha.confidence < baseline_alpha.confidence
    assert abs(decayed_alpha.trend_ewma) < abs(baseline_alpha.trend_ewma)
