from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from signals.domain.models import TrendStockSignal
from signals.infrastructure.storage.sqlite_state_repository import StateStore

cli_main_module = importlib.import_module("signals.interfaces.cli.main")


def _sample_position(*, cusip: str, value: int, shares: int) -> list[dict[str, int | str]]:
    return [
        {
            "name": "Sample Issuer",
            "title": "COM",
            "cusip": cusip,
            "value": value,
            "shares": shares,
        }
    ]


def _seed_trend_and_snapshot_data(db_path: Path) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    store = StateStore(db_path)
    try:
        store.replace_trend_stock_signals(
            "2025Q4",
            [
                TrendStockSignal(
                    report_quarter="2025Q4",
                    instrument_key="111111111",
                    cusip="111111111",
                    put_call=None,
                    issuer_name="Alpha Corp",
                    title="COM",
                    np_raw=0.12,
                    np_adj=0.12,
                    impulse_score=0.10,
                    accumulation_score=0.09,
                    confidence=0.80,
                    trend_ewma=0.09,
                    trend_delta=0.03,
                    breadth_buy_weight=0.20,
                    breadth_sell_weight=0.01,
                    buy_managers=4,
                    sell_managers=1,
                    crowding_hhi=0.20,
                    persistence_buy=2,
                    persistence_sell=0,
                    regime="STRONG_BUY",
                    contributors_json=json.dumps(
                        [
                            {
                                "manager_name": "TCI Fund Management Ltd",
                                "signal_value": 0.40,
                                "manager_weight_configured": 1.0,
                            },
                            {"manager_name": "Opposing Fund", "signal_value": -0.35},
                            {
                                "manager_name": "Coatue Management LLC",
                                "signal_value": 0.30,
                                "manager_weight_configured": 1.0,
                            },
                            {
                                "manager_name": "Appaloosa Management LP",
                                "signal_value": 0.20,
                                "manager_weight_configured": 1.5,
                            },
                            {"manager_name": "Fund Four", "signal_value": 0.10},
                        ],
                        separators=(",", ":"),
                    ),
                    computed_at=now_iso,
                    freshness_multiplier=1.0,
                    freshness_ok=True,
                ),
                TrendStockSignal(
                    report_quarter="2025Q4",
                    instrument_key="222222222",
                    cusip="222222222",
                    put_call=None,
                    issuer_name="Beta Corp",
                    title="COM",
                    np_raw=-0.11,
                    np_adj=-0.11,
                    impulse_score=-0.09,
                    accumulation_score=-0.08,
                    confidence=0.75,
                    trend_ewma=-0.07,
                    trend_delta=-0.02,
                    breadth_buy_weight=0.01,
                    breadth_sell_weight=0.18,
                    buy_managers=1,
                    sell_managers=4,
                    crowding_hhi=0.22,
                    persistence_buy=0,
                    persistence_sell=2,
                    regime="STRONG_SELL",
                    contributors_json="[]",
                    computed_at=now_iso,
                    freshness_multiplier=1.0,
                    freshness_ok=True,
                ),
            ],
        )

        share_totals_by_quarter = {
            "2025Q3": {"0000000001": 1_000, "0000000002": 2_000, "0000000003": 3_000},
            "2025Q4": {"0000000001": 1_200, "0000000002": 2_050, "0000000003": 2_500, "0000000004": 900},
        }
        for quarter, values in (
            (
                "2025Q3",
                {"0000000001": 100_000_000_000, "0000000002": 200_000_000_000, "0000000003": 300_000_000_000},
            ),
            (
                "2025Q4",
                {"0000000001": 125_000_000_000, "0000000002": 202_000_000_000, "0000000003": 250_000_000_000, "0000000004": 90_000_000_000},
            ),
        ):
            for cik, aum_value_k in values.items():
                store.upsert_manager_quarter_snapshot(
                    cik=cik,
                    manager_name=f"Fund {cik[-1]}",
                    report_quarter=quarter,
                    report_date="2025-12-31",
                    filing_date="2026-02-14",
                    acceptance_datetime="2026-02-14T10:00:00Z",
                    accession=f"{cik}-{quarter}",
                    source_form="13F-HR",
                    positions=_sample_position(
                        cusip="111111111",
                        value=10_000,
                        shares=share_totals_by_quarter[quarter][cik],
                    ),
                    aum_value_k=aum_value_k,
                )
    finally:
        store.close()


def test_compute_portfolio_value_trend_summary_classifies_growth_hold_reduction(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    _seed_trend_and_snapshot_data(db_path)

    store = StateStore(db_path)
    try:
        summary = cli_main_module._compute_portfolio_value_trend_summary(
            store,
            "2025Q4",
            ["0000000001", "0000000002", "0000000003", "0000000004"],
        )
    finally:
        store.close()

    assert summary is not None
    assert summary.previous_quarter == "2025Q3"
    assert summary.selected_managers == 4
    assert summary.analyzed_managers == 3
    assert summary.missing_current == 0
    assert summary.missing_previous == 1
    assert summary.growing_managers == 1
    assert summary.holding_managers == 1
    assert summary.reducing_managers == 1
    assert summary.previous_total_value_k == 600_000_000_000
    assert summary.current_total_value_k == 577_000_000_000
    assert summary.shares_analyzed_managers == 3
    assert summary.shares_growing_managers == 1
    assert summary.shares_holding_managers == 1
    assert summary.shares_reducing_managers == 1
    assert summary.previous_total_shares == 6_000
    assert summary.current_total_shares == 5_750


def test_print_raw_trend_table_includes_portfolio_value_trend_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text('{"111111111":"AAA","222222222":"BBB"}')
    _seed_trend_and_snapshot_data(db_path)

    store = StateStore(db_path)
    try:
        cli_main_module._print_detailed_trend_table(
            store,
            "2025Q4",
            min_conf=0.5,
            limit=8,
            show_reversals=False,
            symbols_file=str(symbols_path),
            manager_ciks=["0000000001", "0000000002", "0000000003", "0000000004"],
            view="raw",
        )
    finally:
        store.close()

    captured = capsys.readouterr()
    assert "Top Buy Trends" in captured.out
    assert "Top Sell Trends" in captured.out
    assert "Signals Portfolio Value Trend (QoQ)" in captured.out
    assert "Compared quarters: 2025Q3 -> 2025Q4" in captured.out
    assert "Managers analyzed: 3/4" in captured.out
    assert "Aggregate portfolio value: $600B -> $577B (-3.8% Holding)" in captured.out
    assert "Aggregate portfolio shares: 6,000 -> 5,750 (-4.2% Holding)" in captured.out
    assert "Value Direction Breakdown" in captured.out
    assert "Shares Direction Breakdown" in captured.out
    assert "Managers analyzed (Shares):" not in captured.out
    assert "Growing" in captured.out
    assert "Holding" in captured.out
    assert "Reducing" in captured.out


def test_print_shortlist_trend_table_includes_portfolio_value_trend_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text('{"111111111":"AAA","222222222":"BBB"}')
    _seed_trend_and_snapshot_data(db_path)

    store = StateStore(db_path)
    try:
        cli_main_module._print_detailed_trend_table(
            store,
            "2025Q4",
            min_conf=0.5,
            limit=8,
            show_reversals=False,
            symbols_file=str(symbols_path),
            manager_ciks=["0000000001", "0000000002", "0000000003", "0000000004"],
        )
    finally:
        store.close()

    captured = capsys.readouterr()
    assert "Top Buy Ideas" in captured.out
    assert "Top Reduction Trends" in captured.out
    assert "Stored signals: 2" in captured.out
    assert "Directional candidates: Buy 1 | Reduction 1" in captured.out
    assert "[TCI, Coatue, ✅ Appaloosa]" in captured.out
    assert "Opposing Fund" not in captured.out
    assert "Multi-manager support" not in captured.out
    assert "Signals Portfolio Value Trend (QoQ)" in captured.out
    assert "Compared quarters: 2025Q3 -> 2025Q4" in captured.out
    assert "Aggregate portfolio value: $600B -> $577B (-3.8% Holding)" in captured.out
    assert "Aggregate portfolio shares: 6,000 -> 5,750 (-4.2% Holding)" in captured.out
    assert "Value Direction Breakdown" in captured.out
    assert "Shares Direction Breakdown" in captured.out


def test_print_shortlist_trend_table_includes_option_sections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text('{"111111111":"AAA"}')
    _seed_trend_and_snapshot_data(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    store = StateStore(db_path)
    try:
        store.replace_trend_option_signals(
            "2025Q4",
            [
                TrendStockSignal(
                    report_quarter="2025Q4",
                    instrument_key="111111111|CALL",
                    cusip="111111111",
                    put_call="CALL",
                    issuer_name="Alpha Corp",
                    title="OPTION",
                    np_raw=0.08,
                    np_adj=0.08,
                    impulse_score=0.08,
                    accumulation_score=0.07,
                    confidence=0.80,
                    trend_ewma=0.07,
                    trend_delta=0.02,
                    breadth_buy_weight=0.20,
                    breadth_sell_weight=0.01,
                    buy_managers=3,
                    sell_managers=0,
                    crowding_hhi=0.20,
                    persistence_buy=2,
                    persistence_sell=0,
                    regime="STRONG_BUY",
                    contributors_json=json.dumps([{"manager_name": "Fund 1", "signal_value": 0.08}], separators=(",", ":")),
                    computed_at=now_iso,
                    freshness_multiplier=1.0,
                    freshness_ok=None,
                )
            ],
        )
        cli_main_module._print_detailed_trend_table(
            store,
            "2025Q4",
            min_conf=0.5,
            limit=8,
            show_reversals=False,
            symbols_file=str(symbols_path),
            manager_ciks=["0000000001", "0000000002", "0000000003", "0000000004"],
        )
    finally:
        store.close()

    captured = capsys.readouterr()
    assert "Top Call Option Trends" in captured.out
    assert "AAA CALL" in captured.out
    assert "Adding" in captured.out
    assert "Top Put Option Trends" in captured.out


def test_print_trend_explanation_resolves_mapped_symbol(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text('{"111111111":"AAA"}')
    _seed_trend_and_snapshot_data(db_path)

    store = StateStore(db_path)
    try:
        cli_main_module._print_detailed_trend_table(
            store,
            "2025Q4",
            min_conf=0.5,
            limit=8,
            symbols_file=str(symbols_path),
            explain="AAA",
        )
    finally:
        store.close()

    captured = capsys.readouterr()
    assert "Trend explanation: AAA" in captured.out
    assert "Selector state: Promoted" in captured.out
    assert "Regime: STRONG_BUY" in captured.out
    assert "Impulse / Accumulation:" in captured.out
    assert "Top contributors:" in captured.out
