from __future__ import annotations

from datetime import datetime, timezone

from signals.domain.models import TrendStockSignal
from signals.domain.trend_telegram_message import (
    _issuer_display_name,
    _looks_like_cusip,
    _ticker_for_signal,
    build_trend_message_payload,
    render_trend_telegram_notification,
)


def _signal(*, instrument_key: str, cusip: str | None = None, issuer_name: str | None = "Issuer") -> TrendStockSignal:
    return TrendStockSignal(
        report_quarter="2026Q3",
        instrument_key=instrument_key,
        cusip=cusip,
        put_call=None,
        issuer_name=issuer_name,
        title="COM",
        np_raw=0.1,
        np_adj=0.1,
        impulse_score=0.9,
        accumulation_score=0.9,
        confidence=1.0,
        trend_ewma=0.0822,
        trend_delta=0.01,
        breadth_buy_weight=0.9,
        breadth_sell_weight=0.0,
        buy_managers=2,
        sell_managers=0,
        crowding_hhi=0.1,
        persistence_buy=2,
        persistence_sell=0,
        regime="STRONG_BUY",
        contributors_json="[]",
        computed_at=datetime.now(timezone.utc).isoformat(),
        freshness_multiplier=1.0,
        freshness_ok=True,
    )


def test_looks_like_cusip_detects_nine_character_identifiers() -> None:
    assert _looks_like_cusip("84615Q103") is True
    assert _looks_like_cusip("META") is False
    assert _looks_like_cusip("BRK.B") is False


def test_ticker_for_signal_uses_symbol_map_entry() -> None:
    signal = _signal(instrument_key="84615Q103", cusip="84615Q103", issuer_name="SPACE EXPLORATION TECHN CORP")
    assert _ticker_for_signal(signal, {"84615Q103": "SPCX"}) == "SPCX"


def test_ticker_for_signal_falls_back_to_issuer_name_when_cusip_unmapped() -> None:
    signal = _signal(instrument_key="84615Q103", cusip="84615Q103", issuer_name="SPACE EXPLORATION TECHN CORP")
    resolved = _ticker_for_signal(signal, {})
    assert "84615Q103" not in resolved
    assert resolved.startswith("Space Exploration")
    assert _looks_like_cusip(resolved) is False


def test_ticker_for_signal_keeps_plain_ticker_keys() -> None:
    signal = _signal(instrument_key="META", cusip=None, issuer_name="META PLATFORMS INC")
    assert _ticker_for_signal(signal, {}) == "META"


def test_issuer_display_name_strips_trailing_class_suffix() -> None:
    assert _issuer_display_name("ALLY FINL INC COM") == "Ally Finl Inc"
    assert _issuer_display_name("SPACE EXPLORATION TECHN CORP").startswith("Space Exploration")


def test_rendered_message_never_shows_raw_cusip() -> None:
    signal = _signal(instrument_key="84615Q103", cusip="84615Q103", issuer_name="SPACE EXPLORATION TECHN CORP")
    payload = build_trend_message_payload(
        report_quarter="2026Q3",
        signals=[signal],
        symbol_map={"84615Q103": "SPCX"},
        limit=8,
    )
    subject, body = render_trend_telegram_notification(payload=payload, portfolio_summary=None)
    text = f"{subject}\n{body}"
    assert "SPCX" in text
    assert "84615Q103" not in text
