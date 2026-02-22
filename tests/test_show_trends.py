from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tracker.domain.models import TrendStockSignal
from tracker.infrastructure.storage.sqlite_state_repository import StateStore


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
                    contributors_json="[]",
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
            ],
        )
    finally:
        store.close()


def test_show_trends_applies_default_min_conf_and_hides_cusip_column(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text(
        json.dumps(
            {
                "111111111": "AAA",
                "222222222": "BBB",
                "333333333": "CCC",
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
    assert " | cusip " not in result.stdout
    assert "freshness indicator" not in result.stdout


def test_show_trends_supports_custom_min_conf_and_rejects_invalid(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text(json.dumps({"111111111": "AAA", "333333333": "CCC"}, ensure_ascii=True))
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
    db_path = tmp_path / "tracker.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text(json.dumps({"111111111": "AAA", "333333333": "CCC"}, ensure_ascii=True))
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
    assert "freshness indicator" in result.stdout
    assert "✅" in result.stdout
    assert "❌" in result.stdout
