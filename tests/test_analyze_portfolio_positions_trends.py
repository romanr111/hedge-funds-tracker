from __future__ import annotations

from pathlib import Path

import pytest

from tracker.application.use_cases.analyze_portfolio_positions_trends import (
    analyze_portfolio_positions_trends,
)
from tracker.config import ManagerConfig
from tracker.domain.trends import compute_trend_signals
from tracker.infrastructure.storage.sqlite_state_repository import StateStore


def _quarter_dates(quarter: str) -> tuple[str, str, str]:
    if quarter.endswith("Q1"):
        report_date = quarter[:4] + "-03-31"
        filing_date = quarter[:4] + "-05-15"
    elif quarter.endswith("Q2"):
        report_date = quarter[:4] + "-06-30"
        filing_date = quarter[:4] + "-08-14"
    elif quarter.endswith("Q3"):
        report_date = quarter[:4] + "-09-30"
        filing_date = quarter[:4] + "-11-14"
    else:
        report_date = quarter[:4] + "-12-31"
        filing_date = f"{int(quarter[:4]) + 1}-02-14"
    acceptance_datetime = filing_date.replace("-", "") + "120000"
    return report_date, filing_date, acceptance_datetime


def _pos(cusip: str, value: int) -> dict[str, object]:
    return {
        "name": f"Issuer {cusip}",
        "title": "COM",
        "cusip": cusip,
        "put_call": None,
        "value": value,
        "shares": value,
    }


def _seed_snapshot(
    store: StateStore,
    *,
    cik: str,
    name: str,
    quarter: str,
    accession: str,
    positions: list[dict[str, object]],
) -> None:
    report_date, filing_date, acceptance_datetime = _quarter_dates(quarter)
    store.upsert_manager_quarter_snapshot(
        cik=cik,
        manager_name=name,
        report_quarter=quarter,
        report_date=report_date,
        filing_date=filing_date,
        acceptance_datetime=acceptance_datetime,
        accession=accession,
        source_form="13F-HR",
        positions=positions,
        aum_value_k=sum(int(item["value"]) for item in positions),
    )


def _seed_base_dataset(store: StateStore, *, with_q2: bool = False) -> None:
    data: dict[str, dict[str, list[dict[str, object]]]] = {
        "2025Q3": {
            "0000000001": [_pos("111111111", 100), _pos("333333333", 200), _pos("333333334", 100), _pos("999999999", 600)],
            "0000000002": [_pos("111111111", 300), _pos("333333333", 100), _pos("999999999", 600)],
            "0000000003": [_pos("999999999", 1000)],
        },
        "2025Q4": {
            "0000000001": [_pos("111111111", 220), _pos("333333333", 260), _pos("333333334", 140), _pos("999999999", 380)],
            "0000000002": [_pos("111111111", 180), _pos("333333333", 170), _pos("333333334", 80), _pos("999999999", 570)],
            "0000000003": [_pos("999999999", 1000)],
        },
    }

    if with_q2:
        data["2025Q2"] = {
            "0000000001": [_pos("111111111", 80), _pos("333333333", 150), _pos("333333334", 50), _pos("999999999", 720)],
            "0000000002": [_pos("111111111", 350), _pos("333333333", 80), _pos("333333334", 20), _pos("999999999", 550)],
            "0000000003": [_pos("999999999", 1000)],
        }

    manager_names = {
        "0000000001": "Fund A",
        "0000000002": "Fund B",
        "0000000003": "Fund C",
    }
    for quarter in sorted(data.keys()):
        for cik, positions in data[quarter].items():
            _seed_snapshot(
                store,
                cik=cik,
                name=manager_names[cik],
                quarter=quarter,
                accession=f"{cik}-{quarter}",
                positions=positions,
            )


def _managers() -> list[ManagerConfig]:
    return [
        ManagerConfig(name="Fund A", cik="0000000001", weight=1.0),
        ManagerConfig(name="Fund B", cik="0000000002", weight=1.0),
        ManagerConfig(name="Fund C", cik="0000000003", weight=1.0),
    ]


def _symbol_map() -> dict[str, str]:
    return {
        "111111111": "AAA",
        "333333333": "GM",
        "333333334": "GM",
        "222222222": "BBB",
    }


def test_analyze_portfolio_positions_trends_happy_path_and_no_data(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_base_dataset(store)
        result = analyze_portfolio_positions_trends(
            store=store,
            managers=_managers(),
            tickers=["AAA", "GM", "UNKNOWN", "BBB"],
            symbol_map=_symbol_map(),
        )
    finally:
        store.close()

    assert result.status == "OK"
    assert result.report_quarter == "2025Q4"
    assert result.previous_quarter == "2025Q3"

    rows = {row.ticker: row for row in result.rows}

    aaa = rows["AAA"]
    assert aaa.status == "OK"
    assert aaa.trend.score is not None
    assert aaa.presentation.action in {
        "BUY",
        "SELL",
        "IDEA_BUY",
        "IDEA_SELL",
        "IDEA_NEUTRAL",
        "MONITOR_BUY",
        "MONITOR_SELL",
        "MONITOR_NEUTRAL",
    }
    assert aaa.presentation.setup in {"Strong", "Reversal", "Emerging", "Weakening", "Unknown"}
    assert "Target:" in aaa.presentation.conviction_target
    assert aaa.presentation.consensus_buy >= 0
    assert aaa.presentation.consensus_sell >= 0
    assert aaa.fund_behavior.total == 3
    assert aaa.fund_behavior.analyzed == 2
    assert aaa.fund_behavior.buy + aaa.fund_behavior.sell + aaa.fund_behavior.hold == 2

    gm = rows["GM"]
    assert gm.status == "OK"
    assert gm.mapped_keys == ["333333333", "333333334"]
    assert gm.trend.score is not None
    assert gm.fund_behavior.analyzed == 2

    unknown = rows["UNKNOWN"]
    assert unknown.status == "NO_DATA"
    assert unknown.presentation.action == "NO_DATA"
    assert unknown.presentation.conviction_target == "-"
    assert "not mapped" in (unknown.note or "").lower()

    bbb = rows["BBB"]
    assert bbb.status == "NO_DATA"
    assert bbb.trend.score is None
    assert bbb.presentation.action == "NO_DATA"
    assert bbb.fund_behavior.analyzed == 0
    assert "no manager positions" in (bbb.note or "").lower()


def test_analyze_portfolio_positions_trends_aggregates_multi_cusip_signals(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_base_dataset(store)
        managers = _managers()
        manager_ciks = [item.cik for item in managers]
        snapshots = store.list_snapshots_for_quarters(["2025Q3", "2025Q4"], manager_ciks)
        snapshots_by_quarter: dict[str, dict[str, object]] = {}
        for snapshot in snapshots:
            snapshots_by_quarter.setdefault(snapshot.report_quarter, {})[snapshot.cik] = snapshot

        expected = compute_trend_signals(
            quarters=["2025Q3", "2025Q4"],
            snapshots_by_quarter=snapshots_by_quarter,
            manager_weights={item.cik: item.weight for item in managers},
        )
        by_key = {item.instrument_key: item for item in expected.signals}

        result = analyze_portfolio_positions_trends(
            store=store,
            managers=managers,
            tickers=["GM"],
            symbol_map=_symbol_map(),
        )
    finally:
        store.close()

    gm = result.rows[0]
    expected_signals = [by_key["333333333"], by_key["333333334"]]
    expected_score = sum(item.trend_ewma for item in expected_signals)
    expected_delta = sum(item.trend_delta for item in expected_signals)
    expected_regime = sorted(expected_signals, key=lambda item: (-abs(item.trend_ewma), item.instrument_key))[0].regime

    assert gm.status == "OK"
    assert gm.trend.score == pytest.approx(expected_score)
    assert gm.trend.delta == pytest.approx(expected_delta)
    assert gm.trend.regime == expected_regime

    manager_signal_by_key: dict[str, dict[str, float]] = {}
    for key in ("333333333", "333333334"):
        manager_signal_by_key[key] = {}
        for contributor in by_key[key].contributors:
            manager_cik = contributor.get("manager_cik")
            signal_value = contributor.get("signal_value")
            if isinstance(manager_cik, str) and isinstance(signal_value, (int, float)):
                manager_signal_by_key[key][manager_cik] = float(signal_value)

    manager_total_signal: dict[str, float] = {}
    for key_contrib in manager_signal_by_key.values():
        for manager_cik, signal_value in key_contrib.items():
            manager_total_signal[manager_cik] = manager_total_signal.get(manager_cik, 0.0) + signal_value
    expected_buy = sum(1 for value in manager_total_signal.values() if value > 0)
    expected_sell = sum(1 for value in manager_total_signal.values() if value < 0)
    assert gm.presentation.consensus_buy == expected_buy
    assert gm.presentation.consensus_sell == expected_sell


def test_analyze_portfolio_positions_trends_single_key_consensus_matches_signal_counts(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_base_dataset(store)
        managers = _managers()
        manager_ciks = [item.cik for item in managers]
        snapshots = store.list_snapshots_for_quarters(["2025Q3", "2025Q4"], manager_ciks)
        snapshots_by_quarter: dict[str, dict[str, object]] = {}
        for snapshot in snapshots:
            snapshots_by_quarter.setdefault(snapshot.report_quarter, {})[snapshot.cik] = snapshot

        expected = compute_trend_signals(
            quarters=["2025Q3", "2025Q4"],
            snapshots_by_quarter=snapshots_by_quarter,
            manager_weights={item.cik: item.weight for item in managers},
            contributor_limit=len(managers),
        )
        by_key = {item.instrument_key: item for item in expected.signals}
        result = analyze_portfolio_positions_trends(
            store=store,
            managers=managers,
            tickers=["AAA"],
            symbol_map=_symbol_map(),
        )
    finally:
        store.close()

    aaa = result.rows[0]
    reference = by_key["111111111"]
    assert aaa.presentation.consensus_buy == reference.buy_managers
    assert aaa.presentation.consensus_sell == reference.sell_managers


def test_no_position_managers_are_excluded_from_behavior_counts(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_base_dataset(store)
        result = analyze_portfolio_positions_trends(
            store=store,
            managers=_managers(),
            tickers=["AAA"],
            symbol_map=_symbol_map(),
        )
    finally:
        store.close()

    row = result.rows[0]
    assert row.fund_behavior.total == 3
    assert row.fund_behavior.analyzed == 2


def test_analyze_portfolio_positions_trends_supports_quarter_override(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_base_dataset(store, with_q2=True)
        latest = analyze_portfolio_positions_trends(
            store=store,
            managers=_managers(),
            tickers=["AAA"],
            symbol_map=_symbol_map(),
        )
        q3 = analyze_portfolio_positions_trends(
            store=store,
            managers=_managers(),
            tickers=["AAA"],
            symbol_map=_symbol_map(),
            target_quarter="2025Q3",
        )
    finally:
        store.close()

    assert latest.report_quarter == "2025Q4"
    assert latest.previous_quarter == "2025Q3"
    assert q3.report_quarter == "2025Q3"
    assert q3.previous_quarter == "2025Q2"


def test_analyze_portfolio_positions_trends_validates_inputs(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_base_dataset(store)

        with pytest.raises(ValueError, match="at least one"):
            analyze_portfolio_positions_trends(
                store=store,
                managers=_managers(),
                tickers=[],
                symbol_map=_symbol_map(),
            )

        with pytest.raises(ValueError, match="only strings"):
            analyze_portfolio_positions_trends(
                store=store,
                managers=_managers(),
                tickers=["AAA", 10],  # type: ignore[list-item]
                symbol_map=_symbol_map(),
            )

        with pytest.raises(ValueError, match="YYYYQn"):
            analyze_portfolio_positions_trends(
                store=store,
                managers=_managers(),
                tickers=["AAA"],
                symbol_map=_symbol_map(),
                target_quarter="2025-4",
            )

        with pytest.raises(ValueError, match="not available"):
            analyze_portfolio_positions_trends(
                store=store,
                managers=_managers(),
                tickers=["AAA"],
                symbol_map=_symbol_map(),
                target_quarter="2021Q1",
            )
    finally:
        store.close()


def test_analyze_portfolio_positions_trends_supports_ticker_alias_separators(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_base_dataset(store)
        result = analyze_portfolio_positions_trends(
            store=store,
            managers=_managers(),
            tickers=["BRK.B", "BRK B", "BRK/B"],
            symbol_map={"111111111": "BRK/B"},
        )
    finally:
        store.close()

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.ticker == "BRK.B"
    assert row.status == "OK"
    assert row.mapped_keys == ["111111111"]
    assert row.presentation.action in {
        "BUY",
        "SELL",
        "IDEA_BUY",
        "IDEA_SELL",
        "IDEA_NEUTRAL",
        "MONITOR_BUY",
        "MONITOR_SELL",
        "MONITOR_NEUTRAL",
    }


def test_analyze_portfolio_positions_trends_uses_latest_prices_for_data_fresh(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_base_dataset(store)
        managers = _managers()
        fresh = analyze_portfolio_positions_trends(
            store=store,
            managers=managers,
            tickers=["AAA"],
            symbol_map=_symbol_map(),
            latest_prices={"111111111": 1.0},
        )
        stale = analyze_portfolio_positions_trends(
            store=store,
            managers=managers,
            tickers=["AAA"],
            symbol_map=_symbol_map(),
            latest_prices={"111111111": 5.0},
        )
    finally:
        store.close()

    assert fresh.rows[0].presentation.data_fresh is True
    assert stale.rows[0].presentation.data_fresh is False


def test_analyze_portfolio_positions_trends_sets_note_for_none_regime(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q3",
            accession="a-2025Q3",
            positions=[_pos("111111111", 1000)],
        )
        _seed_snapshot(
            store,
            cik="0000000001",
            name="Fund A",
            quarter="2025Q4",
            accession="a-2025Q4",
            positions=[_pos("111111111", 1000)],
        )
        result = analyze_portfolio_positions_trends(
            store=store,
            managers=[ManagerConfig(name="Fund A", cik="0000000001", weight=1.0)],
            tickers=["AAA"],
            symbol_map={"111111111": "AAA"},
        )
    finally:
        store.close()

    row = result.rows[0]
    assert row.status == "OK"
    assert row.trend.regime == "NONE"
    assert row.note == "Low confidence for regime"
