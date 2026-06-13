from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from signals.application.use_cases.notify_trend_analysis_summary import (
    TREND_ANALYSIS_SUMMARY_STATE_CIK,
    notify_trend_analysis_summary,
)
from signals.domain.models import TrendStockSignal
from signals.domain.trend_telegram_message import build_trend_message_payload
from signals.infrastructure.storage.sqlite_state_repository import StateStore


class _CapturingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.sent.append((subject, body))


def _signal(
    *,
    instrument_key: str,
    regime: str,
    confidence: float,
    trend_ewma: float,
    buy_managers: int = 2,
    sell_managers: int = 1,
    persistence_buy: int = 1,
    persistence_sell: int = 0,
) -> TrendStockSignal:
    return TrendStockSignal(
        report_quarter="2025Q4",
        instrument_key=instrument_key,
        cusip=instrument_key,
        put_call=None,
        issuer_name="Issuer",
        title="COM",
        np_raw=0.1,
        np_adj=0.1,
        impulse_score=0.1,
        accumulation_score=0.1,
        confidence=confidence,
        trend_ewma=trend_ewma,
        trend_delta=0.01,
        breadth_buy_weight=0.1,
        breadth_sell_weight=0.0,
        buy_managers=buy_managers,
        sell_managers=sell_managers,
        crowding_hhi=0.1,
        persistence_buy=persistence_buy,
        persistence_sell=persistence_sell,
        regime=regime,
        contributors_json="[]",
        computed_at=datetime.now(timezone.utc).isoformat(),
        freshness_multiplier=1.0,
        freshness_ok=True,
    )


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
                    contributors_json="[]",
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
                {
                    "0000000001": 125_000_000_000,
                    "0000000002": 202_000_000_000,
                    "0000000003": 250_000_000_000,
                    "0000000004": 90_000_000_000,
                },
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


def test_notify_trend_analysis_summary_sends_message_and_deduplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text('{"111111111":"AAA","222222222":"BBB"}')
    _seed_trend_and_snapshot_data(db_path)

    notifier = _CapturingNotifier()
    store = StateStore(db_path)
    try:
        notify_trend_analysis_summary(
            store,
            [notifier],
            dry_run=False,
            trend_status="computed",
            report_quarter="2025Q4",
            manager_ciks=["0000000001", "0000000002", "0000000003", "0000000004"],
            min_conf=0.5,
            limit=8,
            show_reversals=True,
            symbols_file=str(symbols_path),
            now_fn=lambda: datetime(2026, 2, 20, tzinfo=timezone.utc),
        )
        notify_trend_analysis_summary(
            store,
            [notifier],
            dry_run=False,
            trend_status="computed",
            report_quarter="2025Q4",
            manager_ciks=["0000000001", "0000000002", "0000000003", "0000000004"],
            min_conf=0.5,
            limit=8,
            show_reversals=True,
            symbols_file=str(symbols_path),
            now_fn=lambda: datetime(2026, 2, 20, tzinfo=timezone.utc),
        )
        marker = store.get_state(TREND_ANALYSIS_SUMMARY_STATE_CIK)
    finally:
        store.close()

    assert len(notifier.sent) == 1
    subject, body = notifier.sent[0]
    assert subject == "📈 Signals Trend Analysis 2025Q4"
    assert "Quarter: 2025Q4" in body
    assert "Signals analyzed: 2" in body
    assert "Legend: Confidence=current vs target, Managers=buy/sell manager count" not in body
    assert "🟢 Top Buy Ideas (1)" in body
    assert "🔴 Top Reduction Trends (1)" in body
    assert "🟠 Reversals (0)" in body
    assert "1) AAA — ✅ BUY (Strong)" in body
    assert "1) BBB — ⛔ REDUCE (Strong)" in body
    assert "Confidence: 80%" in body
    assert "vs target" not in body
    assert "Managers: Buy 4 / Sell 1" in body
    assert "📊 Portfolio Value Trend (QoQ)" in body
    assert "2025Q3 -> 2025Q4 | Managers 3/4" in body
    assert "Value: $600B -> $577B (-3.8%, Holding)" in body
    assert "Value direction (manager behavior by total portfolio value):" in body
    assert "- Increasing: 1 (33.3%)" in body
    assert "- Holding: 1 (33.3%)" in body
    assert "- Reducing: 1 (33.3%)" in body
    assert "Shares: 6,000 -> 5,750 (-4.2%, Holding)\n\nShares direction (manager behavior by total reported shares):" in body
    assert "Shares direction (manager behavior by total reported shares):" in body

    assert marker is not None
    assert marker.last_notified_accession == "2025Q4"


def test_notify_trend_analysis_summary_skips_pending_trend_status(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text('{"111111111":"AAA","222222222":"BBB"}')
    _seed_trend_and_snapshot_data(db_path)

    notifier = _CapturingNotifier()
    store = StateStore(db_path)
    try:
        notify_trend_analysis_summary(
            store,
            [notifier],
            dry_run=False,
            trend_status="pending_incomplete_snapshot_matrix",
            report_quarter="2025Q4",
            manager_ciks=["0000000001", "0000000002", "0000000003", "0000000004"],
            min_conf=0.5,
            limit=8,
            symbols_file=str(symbols_path),
        )
        marker = store.get_state(TREND_ANALYSIS_SUMMARY_STATE_CIK)
    finally:
        store.close()

    assert notifier.sent == []
    assert marker is None


def test_notify_trend_analysis_summary_force_send_ignores_dedup_marker(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.sqlite3"
    symbols_path = tmp_path / "symbols.json"
    symbols_path.write_text('{"111111111":"AAA","222222222":"BBB"}')
    _seed_trend_and_snapshot_data(db_path)

    notifier = _CapturingNotifier()
    store = StateStore(db_path)
    try:
        notify_trend_analysis_summary(
            store,
            [notifier],
            dry_run=False,
            trend_status="computed",
            report_quarter="2025Q4",
            manager_ciks=["0000000001", "0000000002", "0000000003", "0000000004"],
            min_conf=0.5,
            limit=8,
            symbols_file=str(symbols_path),
            now_fn=lambda: datetime(2026, 2, 20, tzinfo=timezone.utc),
        )
        notify_trend_analysis_summary(
            store,
            [notifier],
            dry_run=False,
            trend_status="computed",
            report_quarter="2025Q4",
            manager_ciks=["0000000001", "0000000002", "0000000003", "0000000004"],
            min_conf=0.5,
            limit=8,
            symbols_file=str(symbols_path),
            force_send=True,
            now_fn=lambda: datetime(2026, 2, 21, tzinfo=timezone.utc),
        )
    finally:
        store.close()

    assert len(notifier.sent) == 2


def test_build_trend_message_payload_uses_idea_and_to_monitor_labels() -> None:
    payload = build_trend_message_payload(
        report_quarter="2025Q4",
        signals=[
            _signal(
                instrument_key="111111111",
                regime="EMERGING_BUY",
                confidence=0.48,
                trend_ewma=0.02,
            ),
            _signal(
                instrument_key="222222222",
                regime="STRONG_BUY",
                confidence=0.40,
                trend_ewma=0.03,
            ),
        ],
        symbol_map={},
        min_conf=0.40,
        limit=8,
    )

    actions = [row.action for row in payload.buy_rows]
    assert "IDEA" in actions
    assert "TO_MONITOR" in actions
    assert "INTERESTING_IDEA" not in actions
    assert "MONITOR" not in actions


def test_build_trend_message_payload_uses_promoted_shortlist_selection() -> None:
    payload = build_trend_message_payload(
        report_quarter="2025Q4",
        signals=[
            _signal(
                instrument_key="111111111",
                regime="REVERSAL_BUY",
                confidence=0.80,
                trend_ewma=0.02,
                buy_managers=2,
            ),
            _signal(
                instrument_key="222222222",
                regime="REVERSAL_BUY",
                confidence=0.90,
                trend_ewma=0.08,
                buy_managers=1,
                sell_managers=0,
            ),
        ],
        symbol_map={"111111111": "AAA", "222222222": "BBB"},
    )

    assert [row.ticker for row in payload.buy_rows] == ["AAA"]
