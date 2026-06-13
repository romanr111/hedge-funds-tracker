from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from signals.domain.models import TrendStockSignal
from signals.infrastructure.storage.sqlite_state_repository import StateStore


def _run_show_trends(*args: str) -> subprocess.CompletedProcess[str]:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "show_trends.py"
    return subprocess.run(
        [sys.executable, str(script_path), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _seed_trend_signals(db_path: Path, *, with_freshness: bool = False) -> None:
    store = StateStore(db_path)
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
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
                    np_raw=0.10,
                    np_adj=0.10,
                    impulse_score=0.11,
                    accumulation_score=0.09,
                    confidence=0.60,
                    trend_ewma=0.08,
                    trend_delta=0.02,
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
                    freshness_multiplier=0.82 if with_freshness else 1.0,
                    freshness_ok=True if with_freshness else None,
                ),
                TrendStockSignal(
                    report_quarter="2025Q4",
                    instrument_key="222222222",
                    cusip="222222222",
                    put_call=None,
                    issuer_name="Beta Corp",
                    title="COM",
                    np_raw=0.03,
                    np_adj=0.03,
                    impulse_score=0.03,
                    accumulation_score=0.02,
                    confidence=0.40,
                    trend_ewma=0.02,
                    trend_delta=0.01,
                    breadth_buy_weight=0.08,
                    breadth_sell_weight=0.00,
                    buy_managers=1,
                    sell_managers=0,
                    crowding_hhi=1.00,
                    persistence_buy=1,
                    persistence_sell=0,
                    regime="REVERSAL_BUY",
                    contributors_json="[]",
                    computed_at=now_iso,
                    freshness_multiplier=0.90 if with_freshness else 1.0,
                    freshness_ok=True if with_freshness else None,
                ),
                TrendStockSignal(
                    report_quarter="2025Q4",
                    instrument_key="333333333",
                    cusip="333333333",
                    put_call=None,
                    issuer_name="Gamma Corp",
                    title="COM",
                    np_raw=-0.10,
                    np_adj=-0.10,
                    impulse_score=-0.11,
                    accumulation_score=-0.09,
                    confidence=0.70,
                    trend_ewma=-0.06,
                    trend_delta=-0.03,
                    breadth_buy_weight=0.00,
                    breadth_sell_weight=0.22,
                    buy_managers=0,
                    sell_managers=4,
                    crowding_hhi=0.18,
                    persistence_buy=0,
                    persistence_sell=2,
                    regime="STRONG_SELL",
                    contributors_json="[]",
                    computed_at=now_iso,
                    freshness_multiplier=0.35 if with_freshness else 1.0,
                    freshness_ok=False if with_freshness else None,
                ),
                TrendStockSignal(
                    report_quarter="2025Q4",
                    instrument_key="444444444",
                    cusip="444444444",
                    put_call=None,
                    issuer_name="Delta Corp",
                    title="COM",
                    np_raw=0.02,
                    np_adj=0.02,
                    impulse_score=0.02,
                    accumulation_score=0.01,
                    confidence=0.90,
                    trend_ewma=0.0009,
                    trend_delta=0.0005,
                    breadth_buy_weight=0.11,
                    breadth_sell_weight=0.00,
                    buy_managers=2,
                    sell_managers=0,
                    crowding_hhi=0.30,
                    persistence_buy=1,
                    persistence_sell=0,
                    regime="STRONG_BUY",
                    contributors_json="[]",
                    computed_at=now_iso,
                    freshness_multiplier=0.92 if with_freshness else 1.0,
                    freshness_ok=True if with_freshness else None,
                ),
            ],
        )
    finally:
        store.close()


def _section_rows(output: str, title: str) -> list[str]:
    lines = output.splitlines()
    start = lines.index(title)
    rows: list[str] = []
    for line in lines[start + 3 :]:
        if not line.strip():
            break
        rows.append(line)
    return rows


def _option_signal(idx: int, *, put_call: str, trend: float) -> TrendStockSignal:
    now_iso = datetime.now(timezone.utc).isoformat()
    prefix = "CALL" if put_call == "CALL" else "PUT"
    return TrendStockSignal(
        report_quarter="2025Q4",
        instrument_key=f"{prefix}{idx:06d}|{put_call}",
        cusip=f"{prefix}{idx:06d}",
        put_call=put_call,
        issuer_name=f"{prefix.title()} Corp {idx}",
        title="OPTION",
        np_raw=trend,
        np_adj=trend,
        impulse_score=trend,
        accumulation_score=trend,
        confidence=0.80,
        trend_ewma=trend,
        trend_delta=trend,
        breadth_buy_weight=0.20 if trend > 0 else 0.01,
        breadth_sell_weight=0.01 if trend > 0 else 0.20,
        buy_managers=3 if trend > 0 else 0,
        sell_managers=0 if trend > 0 else 3,
        crowding_hhi=0.20,
        persistence_buy=2 if trend > 0 else 0,
        persistence_sell=0 if trend > 0 else 2,
        regime="STRONG_BUY" if trend > 0 else "STRONG_SELL",
        contributors_json=json.dumps([{"manager_name": "Fund A", "signal_value": trend}], separators=(",", ":")),
        computed_at=now_iso,
        freshness_multiplier=1.0,
        freshness_ok=None,
    )


def test_show_trends_defaults_to_long_term_shortlist(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text(
        json.dumps(
            {
                "111111111": "AAA",
                "222222222": "BBB",
                "333333333": "CCC",
                "444444444": "DDD",
            },
            ensure_ascii=True,
        )
    )
    _seed_trend_signals(db_path)

    result = _run_show_trends(
        "--db",
        str(db_path),
        "--quarter",
        "2025Q4",
        "--symbols-file",
        str(symbols_path),
        "--limit",
        "10",
    )

    assert result.returncode == 0
    assert "AAA" in result.stdout
    assert "CCC" in result.stdout
    assert "BBB" not in result.stdout  # confidence 0.40 < default threshold 0.50
    assert "DDD" not in result.stdout  # trend 0.0009 < buy-table threshold 0.001
    assert "Stored signals: 4" in result.stdout
    assert "Directional candidates: Buy 1 | Reduction 1" in result.stdout
    assert "Promoted shortlist: Buy 1 | Reduction 1 | Monitored 0" in result.stdout
    assert "Instrument" in result.stdout
    assert "Idea Score" in result.stdout
    assert "Freshness" in result.stdout
    assert "No quote" in result.stdout
    assert "[TCI, Coatue, ✅ Appaloosa]" in result.stdout
    assert "Opposing Fund" not in result.stdout
    assert "Multi-manager support" not in result.stdout
    output_rows = _section_rows(result.stdout, "Top Buy Ideas") + _section_rows(result.stdout, "Top Reduction Trends")
    assert output_rows
    assert all("CUSIP" not in row for row in output_rows)
    assert "Top Sell Trends" not in result.stdout
    assert "Consensus (+/-)" not in result.stdout
    assert "delta" not in result.stdout
    assert "impulse" not in result.stdout
    assert "Conviction / Target" not in result.stdout


def test_show_trends_prints_capped_option_sections(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text("{}")
    _seed_trend_signals(db_path)
    store = StateStore(db_path)
    try:
        store.replace_trend_option_signals(
            "2025Q4",
            [
                *[_option_signal(idx, put_call="CALL", trend=0.10 - (idx * 0.001)) for idx in range(6)],
                *[_option_signal(idx, put_call="PUT", trend=-0.10 + (idx * 0.001)) for idx in range(6)],
            ],
        )
    finally:
        store.close()

    result = _run_show_trends(
        "--db",
        str(db_path),
        "--quarter",
        "2025Q4",
        "--symbols-file",
        str(symbols_path),
    )

    assert result.returncode == 0
    assert "Top Call Option Trends" in result.stdout
    assert "Top Put Option Trends" in result.stdout
    assert "Flow" in result.stdout
    assert "Adding" in result.stdout
    assert "Reducing" in result.stdout
    assert len(_section_rows(result.stdout, "Top Call Option Trends")) == 5
    assert len(_section_rows(result.stdout, "Top Put Option Trends")) == 5


def test_show_trends_raw_view_preserves_diagnostic_table(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text('{"111111111":"AAA","222222222":"BBB","333333333":"CCC","444444444":"DDD"}')
    _seed_trend_signals(db_path)

    result = _run_show_trends(
        "--db",
        str(db_path),
        "--quarter",
        "2025Q4",
        "--symbols-file",
        str(symbols_path),
        "--view",
        "raw",
    )

    assert result.returncode == 0
    assert "Top Buy Trends" in result.stdout
    assert "Top Sell Trends" in result.stdout
    assert "Ticker" in result.stdout
    assert "Conviction / Target" in result.stdout


def test_show_trends_caps_buy_and_sell_ideas_to_eight_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    signals: list[TrendStockSignal] = []
    symbol_map: dict[str, str] = {}
    now_iso = datetime.now(timezone.utc).isoformat()

    for idx in range(12):
        key = f"1{idx:08d}"
        symbol_map[key] = f"BUY{idx:02d}"
        signals.append(
            TrendStockSignal(
                report_quarter="2025Q4",
                instrument_key=key,
                cusip=key,
                put_call=None,
                issuer_name=f"Buy Corp {idx}",
                title="COM",
                np_raw=0.12,
                np_adj=0.12,
                impulse_score=0.11,
                accumulation_score=0.09,
                confidence=0.80,
                trend_ewma=0.10 - (idx * 0.001),
                trend_delta=0.02,
                breadth_buy_weight=0.20,
                breadth_sell_weight=0.01,
                buy_managers=4,
                sell_managers=1,
                crowding_hhi=0.20,
                persistence_buy=2,
                persistence_sell=0,
                regime="STRONG_BUY",
                contributors_json="[]",
                computed_at=now_iso,
                freshness_multiplier=1.0,
                freshness_ok=True,
            )
        )

    for idx in range(12):
        key = f"2{idx:08d}"
        symbol_map[key] = f"SEL{idx:02d}"
        signals.append(
            TrendStockSignal(
                report_quarter="2025Q4",
                instrument_key=key,
                cusip=key,
                put_call=None,
                issuer_name=f"Sell Corp {idx}",
                title="COM",
                np_raw=-0.12,
                np_adj=-0.12,
                impulse_score=-0.11,
                accumulation_score=-0.09,
                confidence=0.80,
                trend_ewma=-0.10 + (idx * 0.001),
                trend_delta=-0.02,
                breadth_buy_weight=0.01,
                breadth_sell_weight=0.20,
                buy_managers=1,
                sell_managers=4,
                crowding_hhi=0.20,
                persistence_buy=0,
                persistence_sell=2,
                regime="STRONG_SELL",
                contributors_json="[]",
                computed_at=now_iso,
                freshness_multiplier=1.0,
                freshness_ok=True,
            )
        )

    store = StateStore(db_path)
    try:
        store.replace_trend_stock_signals("2025Q4", signals)
    finally:
        store.close()

    symbols_path.write_text(json.dumps(symbol_map, ensure_ascii=True))

    result = _run_show_trends(
        "--db",
        str(db_path),
        "--quarter",
        "2025Q4",
        "--symbols-file",
        str(symbols_path),
        "--limit",
        "50",
    )

    assert result.returncode == 0
    assert len(_section_rows(result.stdout, "Top Buy Ideas")) == 8
    assert len(_section_rows(result.stdout, "Top Reduction Trends")) == 8


def test_show_trends_supports_custom_min_conf_and_rejects_invalid(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text(json.dumps({"111111111": "AAA", "333333333": "CCC", "444444444": "DDD"}, ensure_ascii=True))
    _seed_trend_signals(db_path)

    stricter = _run_show_trends(
        "--db",
        str(db_path),
        "--quarter",
        "2025Q4",
        "--symbols-file",
        str(symbols_path),
        "--min-conf",
        "0.65",
    )
    assert stricter.returncode == 0
    assert "CCC" in stricter.stdout
    assert "AAA" not in stricter.stdout
    assert "DDD" not in stricter.stdout

    invalid = _run_show_trends(
        "--db",
        str(db_path),
        "--quarter",
        "2025Q4",
        "--symbols-file",
        str(symbols_path),
        "--min-conf",
        "1.2",
    )
    assert invalid.returncode == 1
    assert "--min-conf must be between 0 and 1" in invalid.stdout


def test_show_trends_shows_freshness_indicator_column_when_available(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text(json.dumps({"111111111": "AAA", "333333333": "CCC", "444444444": "DDD"}, ensure_ascii=True))
    _seed_trend_signals(db_path, with_freshness=True)

    result = _run_show_trends(
        "--db",
        str(db_path),
        "--quarter",
        "2025Q4",
        "--symbols-file",
        str(symbols_path),
        "--min-conf",
        "0.5",
    )

    assert result.returncode == 0
    assert "Freshness" in result.stdout
    assert "Fresh" in result.stdout
    assert "Stale" in result.stdout
    assert "DDD" not in result.stdout
