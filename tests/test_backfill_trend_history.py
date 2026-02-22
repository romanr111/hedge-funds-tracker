from __future__ import annotations

from pathlib import Path

from tracker.application.use_cases.backfill_trend_history import run_backfill_trend_history
from tracker.application.use_cases.run_trend_engine import run_trend_engine_for_target_quarter
from tracker.config import ManagerConfig
from tracker.domain.quarters import parse_report_quarter
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



def _quarter_dates(quarter: str) -> tuple[str, str, str]:
    parsed = parse_report_quarter(quarter)
    assert parsed is not None
    year, quarter_idx = parsed
    if quarter_idx == 1:
        report_date = f"{year}-03-31"
        filing_date = f"{year}-05-15"
    elif quarter_idx == 2:
        report_date = f"{year}-06-30"
        filing_date = f"{year}-08-14"
    elif quarter_idx == 3:
        report_date = f"{year}-09-30"
        filing_date = f"{year}-11-14"
    else:
        report_date = f"{year}-12-31"
        filing_date = f"{year + 1}-02-14"
    acceptance = filing_date.replace("-", "") + "120000"
    return report_date, filing_date, acceptance



def _seed_snapshot(
    store: StateStore,
    *,
    cik: str,
    name: str,
    quarter: str,
    accession: str,
    positions: list[dict[str, object]],
) -> None:
    report_date, filing_date, acceptance = _quarter_dates(quarter)
    store.upsert_manager_quarter_snapshot(
        cik=cik,
        manager_name=name,
        report_quarter=quarter,
        report_date=report_date,
        filing_date=filing_date,
        acceptance_datetime=acceptance,
        accession=accession,
        source_form="13F-HR",
        positions=positions,
        aum_value_k=sum(int(item["value"]) for item in positions),
    )



def _quarters(start_year: int, start_q: int, count: int) -> list[str]:
    out: list[str] = []
    year = start_year
    quarter = start_q
    for _ in range(count):
        out.append(f"{year}Q{quarter}")
        quarter += 1
        if quarter > 4:
            year += 1
            quarter = 1
    return out



def _seed_two_managers_for_quarters(store: StateStore, quarters: list[str]) -> None:
    for quarter in quarters:
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter=quarter,
            accession=f"a-{quarter}",
            positions=_positions(100, 900),
        )
        _seed_snapshot(
            store,
            cik="0000000002",
            name="Fund B",
            quarter=quarter,
            accession=f"b-{quarter}",
            positions=_positions(200, 800),
        )



def test_backfill_default_range_uses_last_9_and_excludes_latest(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        quarters = _quarters(2023, 1, 11)
        _seed_two_managers_for_quarters(store, quarters)

        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]
        result = run_backfill_trend_history(
            managers,
            store,
            dry_run=False,
            include_latest=False,
            force_recompute=False,
        )
        assert result.status == "completed"
        assert result.quarters_requested == 9
        assert result.computed == 9

        detailed_quarters = [item.report_quarter for item in result.details]
        assert quarters[-1] not in detailed_quarters
        assert detailed_quarters == quarters[-10:-1]
    finally:
        store.close()



def test_backfill_marks_insufficient_history_as_non_error(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        quarters = _quarters(2023, 4, 9)
        _seed_two_managers_for_quarters(store, quarters)
        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]

        result = run_backfill_trend_history(
            managers,
            store,
            dry_run=False,
            include_latest=False,
            force_recompute=False,
        )
        assert result.status == "completed"
        assert result.quarters_requested == 8
        assert result.computed == 7
        assert result.failed == 0

        first_result = next(item for item in result.details if item.report_quarter == quarters[0])
        assert first_result.status == "pending_insufficient_history"
    finally:
        store.close()


def test_backfill_skip_existing_without_force(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        quarters = _quarters(2024, 1, 4)
        _seed_two_managers_for_quarters(store, quarters)
        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]

        seeded = run_trend_engine_for_target_quarter(
            managers,
            store,
            target_quarter="2024Q3",
            dry_run=False,
            compute_mode="backfill",
            backfill_batch_id="batch-seeded",
        )
        assert seeded.status == "computed"

        result = run_backfill_trend_history(
            managers,
            store,
            dry_run=False,
            from_quarter="2024Q3",
            to_quarter="2024Q3",
            include_latest=True,
            force_recompute=False,
        )
        assert result.status == "completed"
        assert result.quarters_requested == 1
        assert result.computed == 0
        assert result.skipped_existing == 1
        assert result.details[0].status == "skipped_existing_quarter"
    finally:
        store.close()



def test_backfill_force_recompute_updates_batch_id(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        quarters = _quarters(2024, 1, 4)
        _seed_two_managers_for_quarters(store, quarters)
        managers = [
            ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
            ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ]

        first = run_trend_engine_for_target_quarter(
            managers,
            store,
            target_quarter="2024Q3",
            dry_run=False,
            compute_mode="backfill",
            backfill_batch_id="batch-old",
        )
        assert first.status == "computed"

        result = run_backfill_trend_history(
            managers,
            store,
            dry_run=False,
            from_quarter="2024Q3",
            to_quarter="2024Q3",
            include_latest=True,
            force_recompute=True,
        )
        assert result.status == "completed"
        assert result.computed == 1

        trend_run = store.get_trend_run("2024Q3")
        assert trend_run is not None
        assert trend_run.is_backfill is True
        assert trend_run.backfill_batch_id == result.batch_id
    finally:
        store.close()
