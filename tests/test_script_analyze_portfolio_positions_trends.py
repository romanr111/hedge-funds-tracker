from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tracker.infrastructure.storage.sqlite_state_repository import StateStore


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "analyze_portfolio_positions_trends.py"


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_script_path()), *args],
        capture_output=True,
        text=True,
        check=False,
    )


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
        accession=f"{cik}-{quarter}",
        source_form="13F-HR",
        positions=positions,
        aum_value_k=sum(int(item["value"]) for item in positions),
    )


def _seed_db(path: Path) -> None:
    store = StateStore(path)
    try:
        manager_names = {
            "0000000001": "Fund A",
            "0000000002": "Fund B",
            "0000000003": "Fund C",
        }
        data = {
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
        for quarter in sorted(data.keys()):
            for cik, positions in data[quarter].items():
                _seed_snapshot(
                    store,
                    cik=cik,
                    name=manager_names[cik],
                    quarter=quarter,
                    positions=positions,
                )
    finally:
        store.close()


def test_script_runs_and_writes_output_json(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    _seed_db(db_path)

    positions_path = tmp_path / "positions.json"
    positions_path.write_text(json.dumps(["AAA", "GM", "BBB", "UNKNOWN"], ensure_ascii=True))

    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text(
        json.dumps(
            {
                "111111111": "AAA",
                "333333333": "GM",
                "333333334": "GM",
                "222222222": "BBB",
            },
            ensure_ascii=True,
        )
    )

    managers_path = tmp_path / "managers.json"
    managers_path.write_text(
        json.dumps(
            [
                {"name": "Fund A", "cik": "0000000001", "weight": 1.0},
                {"name": "Fund B", "cik": "0000000002", "weight": 1.0},
                {"name": "Fund C", "cik": "0000000003", "weight": 1.0},
            ],
            ensure_ascii=True,
        )
    )

    output_json = tmp_path / "analysis" / "result.json"
    result = _run_script(
        "--positions-file",
        str(positions_path),
        "--db",
        str(db_path),
        "--symbols-file",
        str(symbols_path),
        "--managers-file",
        str(managers_path),
        "--output-json",
        str(output_json),
    )

    assert result.returncode == 0
    assert "Report quarter: 2025Q4" in result.stdout
    assert "Ticker" in result.stdout
    assert "NO_DATA" in result.stdout
    assert "AAA" in result.stdout
    assert "GM" in result.stdout
    assert output_json.exists()

    payload = json.loads(output_json.read_text())
    assert payload["report_quarter"] == "2025Q4"
    assert payload["previous_quarter"] == "2025Q3"
    assert payload["status"] == "OK"
    assert isinstance(payload["rows"], list)
    assert any(item["ticker"] == "BBB" and item["status"] == "NO_DATA" for item in payload["rows"])


def test_script_returns_error_on_invalid_input_and_quarter(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    _seed_db(db_path)

    managers_path = tmp_path / "managers.json"
    managers_path.write_text(
        json.dumps(
            [
                {"name": "Fund A", "cik": "0000000001", "weight": 1.0},
                {"name": "Fund B", "cik": "0000000002", "weight": 1.0},
                {"name": "Fund C", "cik": "0000000003", "weight": 1.0},
            ],
            ensure_ascii=True,
        )
    )

    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text(json.dumps({"111111111": "AAA"}, ensure_ascii=True))

    bad_positions = tmp_path / "bad_positions.json"
    bad_positions.write_text(json.dumps({"positions": ["AAA"]}, ensure_ascii=True))

    invalid_json_result = _run_script(
        "--positions-file",
        str(bad_positions),
        "--db",
        str(db_path),
        "--symbols-file",
        str(symbols_path),
        "--managers-file",
        str(managers_path),
    )
    assert invalid_json_result.returncode == 1
    assert "JSON array of string tickers" in invalid_json_result.stdout

    good_positions = tmp_path / "positions.json"
    good_positions.write_text(json.dumps(["AAA"], ensure_ascii=True))
    invalid_quarter_result = _run_script(
        "--positions-file",
        str(good_positions),
        "--db",
        str(db_path),
        "--symbols-file",
        str(symbols_path),
        "--managers-file",
        str(managers_path),
        "--quarter",
        "2025-4",
    )
    assert invalid_quarter_result.returncode == 1
    assert "YYYYQn" in invalid_quarter_result.stdout


def test_script_extracts_stocks_from_nested_json_and_maps_china_tickers(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    _seed_db(db_path)

    positions_path = tmp_path / "positions.json"
    positions_path.write_text(
        json.dumps(
            {
                "IB_DATA": {
                    "China Stocks": {
                        "9988": 9120,
                        "KWEB": 4976,
                    },
                    "Stocks": {
                        "AAA": 200,
                        "GM": 100,
                        "BRK B": 50,
                    },
                    "Total Stocks Value": 14296,
                }
            },
            ensure_ascii=True,
        )
    )

    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text(
        json.dumps(
            {
                "111111111": "AAA",
                "333333333": "GM",
                "333333334": "GM",
            },
            ensure_ascii=True,
        )
    )

    managers_path = tmp_path / "managers.json"
    managers_path.write_text(
        json.dumps(
            [
                {"name": "Fund A", "cik": "0000000001", "weight": 1.0},
                {"name": "Fund B", "cik": "0000000002", "weight": 1.0},
                {"name": "Fund C", "cik": "0000000003", "weight": 1.0},
            ],
            ensure_ascii=True,
        )
    )

    output_json = tmp_path / "analysis" / "result.json"
    result = _run_script(
        "--positions-file",
        str(positions_path),
        "--db",
        str(db_path),
        "--symbols-file",
        str(symbols_path),
        "--managers-file",
        str(managers_path),
        "--output-json",
        str(output_json),
    )

    assert result.returncode == 0
    assert output_json.exists()

    payload = json.loads(output_json.read_text())
    tickers = [item["ticker"] for item in payload["rows"]]
    assert "9988" not in tickers
    assert "BABA" in tickers
    assert "KWEB" in tickers
    assert "AAA" in tickers
    assert "GM" in tickers
    assert "BRK B" not in tickers
    assert "BRK.B" in tickers


def test_script_returns_error_when_db_missing(tmp_path: Path) -> None:
    positions_path = tmp_path / "positions.json"
    positions_path.write_text(json.dumps(["AAA"], ensure_ascii=True))

    result = _run_script(
        "--positions-file",
        str(positions_path),
        "--db",
        str(tmp_path / "missing.sqlite3"),
    )

    assert result.returncode == 1
    assert "Database not found" in result.stdout
