from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from signals.domain.models import TrendStockSignal
from signals.infrastructure.storage.sqlite_state_repository import StateStore

cli_main_module = importlib.import_module("signals.interfaces.cli.main")


def _seed_trend_data(db_path: Path) -> None:
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
                                "manager_cik": "0001647251",
                                "manager_weight_configured": 0.80,
                                "manager_weight_base": 0.75,
                                "manager_quality_multiplier": 1.05,
                                "manager_weight_effective": 0.7875,
                                "flow_participation": 0.10,
                                "signal_value": 0.40,
                                "trade_dw": 0.05,
                                "prev_weight": 0.02,
                                "curr_weight": 0.04,
                                "prev_shares": 100000,
                                "curr_shares": 200000,
                                "impulse_multiplier": 2.0,
                                "quarter_price": 150.0,
                                "price_weight": 0.20,
                            },
                            {"manager_name": "Coatue Management LLC", "signal_value": 0.30},
                        ],
                        separators=(",", ":"),
                    ),
                    computed_at=now_iso,
                    freshness_multiplier=1.0,
                    freshness_ok=True,
                ),
            ],
        )
        for quarter, aum_value_k in (
            ("2025Q3", 600_000_000),
            ("2025Q4", 577_000_000),
        ):
            store.upsert_manager_quarter_snapshot(
                cik="0000000001",
                manager_name="Fund 1",
                report_quarter=quarter,
                report_date="2025-12-31",
                filing_date="2026-02-14",
                acceptance_datetime="2026-02-14T10:00:00Z",
                accession=f"0000000001-{quarter}",
                source_form="13F-HR",
                positions=[{"name": "Alpha Corp", "title": "COM", "cusip": "111111111", "value": 10_000, "shares": 1_000}],
                aum_value_k=aum_value_k,
            )
    finally:
        store.close()


def _seed_option_trend_data(db_path: Path) -> None:
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
                ),
            ],
        )
    finally:
        store.close()


def _seed_reversal_trend_data(db_path: Path) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    store = StateStore(db_path)
    try:
        store.replace_trend_stock_signals(
            "2025Q4",
            [
                TrendStockSignal(
                    report_quarter="2025Q4",
                    instrument_key="222222222",
                    cusip="222222222",
                    put_call=None,
                    issuer_name="Beta Corp",
                    title="COM",
                    np_raw=0.05,
                    np_adj=0.05,
                    impulse_score=0.04,
                    accumulation_score=0.03,
                    confidence=0.70,
                    trend_ewma=0.04,
                    trend_delta=0.08,
                    breadth_buy_weight=0.10,
                    breadth_sell_weight=0.05,
                    buy_managers=2,
                    sell_managers=1,
                    crowding_hhi=0.30,
                    persistence_buy=1,
                    persistence_sell=0,
                    regime="REVERSAL_BUY",
                    contributors_json=json.dumps([{"manager_name": "Fund 2", "signal_value": 0.05}], separators=(",", ":")),
                    computed_at=now_iso,
                    freshness_multiplier=1.0,
                    freshness_ok=True,
                ),
            ],
        )
    finally:
        store.close()


def test_build_trend_summary_json_data_returns_expected_structure(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    _seed_trend_data(db_path)
    _seed_option_trend_data(db_path)
    store = StateStore(db_path)
    try:
        signals = store.list_trend_stock_signals("2025Q4")
        option_signals = store.list_trend_option_signals("2025Q4")
        data = cli_main_module._build_trend_summary_json_data(
            store,
            "2025Q4",
            signals=signals,
            option_signals=option_signals,
            symbol_map={"111111111": "AAPL"},
            min_conf=0.45,
            limit=8,
            manager_ciks=["0000000001"],
            view="shortlist",
            show_reversals=False,
        )
    finally:
        store.close()

    payload = data.payload
    assert payload["metadata"]["report_quarter"] == "2025Q4"
    assert payload["metadata"]["view_mode"] == "shortlist"
    assert payload["metadata"]["blend_mode"] == "tactical"
    assert len(data.content_fingerprint) == 64  # SHA-256 hex

    buy_ideas = payload["buy_ideas"]
    assert len(buy_ideas) == 1
    signal_json = buy_ideas[0]
    assert signal_json["ticker"] == "AAPL"
    assert signal_json["regime"] == "STRONG_BUY"
    assert signal_json["setup"] == "Strong"
    assert signal_json["action"] == "BUY"
    assert signal_json["direction"] == "BUY"
    assert signal_json["is_reversal"] is False
    assert "selection" in signal_json
    assert signal_json["selection"]["state"] == "Promoted"

    scores = signal_json["scores"]
    assert scores["trend_ewma"] == pytest.approx(0.09)
    assert scores["confidence"] == pytest.approx(0.80)
    assert scores["idea_score"] == pytest.approx(0.072)

    support = signal_json["support"]
    assert support["buy_managers"] == 4
    assert support["sell_managers"] == 1
    assert support["directional_managers"] == 4
    assert support["opposite_managers"] == 1

    contributors = signal_json["contributors"]
    assert len(contributors) == 2
    tci = contributors[0]
    assert tci["manager_name"] == "TCI Fund Management Ltd"
    assert tci["manager_short_name"] == "TCI"
    assert tci["configured_weight"] == 0.80
    assert tci["is_high_weight"] is False
    assert tci["trade_direction_weight"] == 0.05
    assert tci["previous_shares"] == 100000
    assert tci["current_shares"] == 200000
    assert tci["quarter_price"] == 150.0

    reduction_trends = payload["reduction_trends"]
    assert reduction_trends == []

    assert payload["portfolio_context"] is not None
    assert payload["portfolio_context"]["previous_quarter"] == "2025Q3"

    call_options = payload["call_options"]
    assert len(call_options) == 1
    assert call_options[0]["ticker"] == "AAPL"
    assert call_options[0]["option_flow"] == "Adding"

    put_options = payload["put_options"]
    assert put_options == []

    counts = payload["metadata"]["signal_counts"]
    assert counts["total_stock_signals"] == 1
    assert counts["promoted_buy_ideas"] == 1


def test_build_trend_summary_json_data_respects_raw_view(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    _seed_trend_data(db_path)
    store = StateStore(db_path)
    try:
        signals = store.list_trend_stock_signals("2025Q4")
        option_signals = store.list_trend_option_signals("2025Q4")
        data = cli_main_module._build_trend_summary_json_data(
            store,
            "2025Q4",
            signals=signals,
            option_signals=option_signals,
            symbol_map={"111111111": "AAPL"},
            min_conf=0.45,
            limit=8,
            manager_ciks=["0000000001"],
            view="raw",
            show_reversals=False,
        )
    finally:
        store.close()

    payload = data.payload
    assert payload["metadata"]["view_mode"] == "raw"
    assert len(payload["buy_ideas"]) == 1
    assert payload["buy_ideas"][0]["ticker"] == "AAPL"
    assert payload["reversals"] == []
    assert "selection" not in payload["buy_ideas"][0]


def test_build_trend_summary_json_data_includes_reversals_when_requested(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    _seed_reversal_trend_data(db_path)
    store = StateStore(db_path)
    try:
        signals = store.list_trend_stock_signals("2025Q4")
        option_signals = store.list_trend_option_signals("2025Q4")
        data = cli_main_module._build_trend_summary_json_data(
            store,
            "2025Q4",
            signals=signals,
            option_signals=option_signals,
            symbol_map={"222222222": "BETA"},
            min_conf=0.45,
            limit=8,
            manager_ciks=[],
            view="raw",
            show_reversals=True,
        )
    finally:
        store.close()

    payload = data.payload
    reversals = payload["reversals"]
    assert len(reversals) == 1
    assert reversals[0]["ticker"] == "BETA"
    assert reversals[0]["is_reversal"] is True
    assert reversals[0]["regime"] == "REVERSAL_BUY"


def test_maybe_export_trend_summary_json_writes_file(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    _seed_trend_data(db_path)
    store = StateStore(db_path)
    export_path = tmp_path / "exports"
    logger = cli_main_module.logging.getLogger("test")
    try:
        cli_main_module._maybe_export_trend_summary_json(
            store,
            "2025Q4",
            export_json_path=str(export_path),
            min_conf=0.45,
            limit=8,
            show_reversals=False,
            symbols_file="config/cusip_tickers.json",
            manager_ciks=["0000000001"],
            view="shortlist",
            dry_run=False,
            gdrive_folder_id="",
            logger=logger,
        )
    finally:
        store.close()

    expected_file = export_path / "trend_summary_2025Q4.json"
    assert expected_file.exists()
    payload = json.loads(expected_file.read_text())
    assert payload["metadata"]["report_quarter"] == "2025Q4"
    assert "content_fingerprint" in payload["metadata"]


def test_maybe_export_trend_summary_json_skips_unchanged(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    _seed_trend_data(db_path)
    store = StateStore(db_path)
    export_path = tmp_path / "exports"
    logger = cli_main_module.logging.getLogger("test")
    try:
        cli_main_module._maybe_export_trend_summary_json(
            store,
            "2025Q4",
            export_json_path=str(export_path),
            min_conf=0.45,
            limit=8,
            show_reversals=False,
            symbols_file="config/cusip_tickers.json",
            manager_ciks=["0000000001"],
            view="shortlist",
            dry_run=False,
            gdrive_folder_id="",
            logger=logger,
        )
        expected_file = export_path / "trend_summary_2025Q4.json"
        mtime_after_first = expected_file.stat().st_mtime
        cli_main_module._maybe_export_trend_summary_json(
            store,
            "2025Q4",
            export_json_path=str(export_path),
            min_conf=0.45,
            limit=8,
            show_reversals=False,
            symbols_file="config/cusip_tickers.json",
            manager_ciks=["0000000001"],
            view="shortlist",
            dry_run=False,
            gdrive_folder_id="",
            logger=logger,
        )
    finally:
        store.close()

    assert expected_file.exists()
    assert expected_file.stat().st_mtime == mtime_after_first, "File was rewritten when it should have been skipped as unchanged"


def test_maybe_export_trend_summary_json_respects_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    _seed_trend_data(db_path)
    store = StateStore(db_path)
    export_path = tmp_path / "exports"
    logger = cli_main_module.logging.getLogger("test")
    try:
        cli_main_module._maybe_export_trend_summary_json(
            store,
            "2025Q4",
            export_json_path=str(export_path),
            min_conf=0.45,
            limit=8,
            show_reversals=False,
            symbols_file="config/cusip_tickers.json",
            manager_ciks=["0000000001"],
            view="shortlist",
            dry_run=True,
            gdrive_folder_id="",
            logger=logger,
        )
    finally:
        store.close()

    expected_file = export_path / "trend_summary_2025Q4.json"
    assert not expected_file.exists()


def test_maybe_export_trend_summary_json_uploads_to_gdrive_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    _seed_trend_data(db_path)
    store = StateStore(db_path)
    export_path = tmp_path / "exports"
    logger = cli_main_module.logging.getLogger("test")

    recorded_calls: list[list[str]] = []

    def _fake_subprocess_run(cmd, **kwargs):
        recorded_calls.append(cmd)
        class FakeProc:
            returncode = 0
            stdout = "uploaded"
            stderr = ""
        return FakeProc()

    monkeypatch.setattr(cli_main_module.subprocess, "run", _fake_subprocess_run)
    monkeypatch.setattr(cli_main_module.shutil, "which", lambda _bin: "/usr/local/bin/gog")

    try:
        cli_main_module._maybe_export_trend_summary_json(
            store,
            "2025Q4",
            export_json_path=str(export_path),
            min_conf=0.45,
            limit=8,
            show_reversals=False,
            symbols_file="config/cusip_tickers.json",
            manager_ciks=["0000000001"],
            view="shortlist",
            dry_run=False,
            gdrive_folder_id="folder123",
            logger=logger,
        )
    finally:
        store.close()

    expected_file = export_path / "trend_summary_2025Q4.json"
    assert expected_file.exists()
    assert len(recorded_calls) == 1
    assert recorded_calls[0][0] == "gog"
    assert recorded_calls[0][1] == "drive"
    assert recorded_calls[0][2] == "upload"
    assert recorded_calls[0][4] == "--parent"
    assert recorded_calls[0][5] == "folder123"


def test_upload_json_to_gdrive_skips_when_gog_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(cli_main_module.shutil, "which", lambda _bin: None)
    logger = cli_main_module.logging.getLogger("test")
    test_path = tmp_path / "test.json"
    test_path.write_text("{}")
    cli_main_module._upload_json_to_gdrive(test_path, "folder123", logger)
    assert "gog not found in PATH" in caplog.text


def test_upload_json_to_gdrive_logs_warning_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(cli_main_module.shutil, "which", lambda _bin: "/usr/local/bin/gog")

    def _fake_subprocess_run(cmd, **kwargs):
        class FakeProc:
            returncode = 1
            stdout = ""
            stderr = "auth failed"
        return FakeProc()

    monkeypatch.setattr(cli_main_module.subprocess, "run", _fake_subprocess_run)
    logger = cli_main_module.logging.getLogger("test")
    test_path = tmp_path / "test.json"
    test_path.write_text("{}")
    cli_main_module._upload_json_to_gdrive(test_path, "folder123", logger)
    assert "Google Drive upload failed" in caplog.text
