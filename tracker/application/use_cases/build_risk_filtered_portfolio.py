from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from tracker.application.ports.historical_price_gateway import HistoricalPriceGateway
from tracker.config import PipelineConfig
from tracker.domain.models import TrendStockSignal
from tracker.domain.portfolio import RiskFilteredSignal, TargetPosition


BUY_REGIMES = {"STRONG_BUY", "EMERGING_BUY", "REVERSAL_BUY", "WEAKENING_BUY"}


@dataclass(frozen=True)
class BuildRiskFilteredPortfolioResult:
    risk_signals: list[RiskFilteredSignal]
    selected_positions: list[TargetPosition]



def _load_symbol_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    resolved: dict[str, str] = {}
    for raw_key, raw_value in payload.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            continue
        key = raw_key.strip().upper()
        value = raw_value.strip().upper()
        if key and value:
            resolved[key] = value
    return resolved



def _load_symbol_metadata(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    resolved: dict[str, dict[str, str]] = {}
    for raw_ticker, raw_item in payload.items():
        if not isinstance(raw_ticker, str) or not isinstance(raw_item, dict):
            continue
        ticker = raw_ticker.strip().upper()
        if not ticker:
            continue
        sector = raw_item.get("sector")
        country = raw_item.get("country")
        resolved[ticker] = {
            "sector": sector.strip().upper() if isinstance(sector, str) and sector.strip() else "UNKNOWN",
            "country": country.strip().upper() if isinstance(country, str) and country.strip() else "UNKNOWN",
        }
    return resolved



def _ticker_for_signal(signal: TrendStockSignal, symbol_map: dict[str, str]) -> str:
    keys = [
        (signal.instrument_key or "").strip().upper(),
        (signal.cusip or "").strip().upper(),
    ]
    for key in keys:
        if key and key in symbol_map:
            return symbol_map[key]
    for key in keys:
        if key:
            return key
    return "UNKNOWN"



def _asof_price(series: dict[date, float], as_of_date: date) -> float | None:
    eligible = [day for day in series if day <= as_of_date]
    if not eligible:
        return None
    latest_day = max(eligible)
    value = series.get(latest_day)
    if value is None or value <= 0:
        return None
    return value



def _normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, value) for value in weights.values())
    if total <= 0:
        return {key: 0.0 for key in weights}
    return {key: max(0.0, value) / total for key, value in weights.items()}


def _sector_bucket(sector: str, instrument_key: str) -> str:
    normalized = (sector or "").strip().upper()
    if normalized and normalized != "UNKNOWN":
        return normalized
    return f"UNKNOWN::{instrument_key}"



def _apply_caps(
    *,
    raw_weights: dict[str, float],
    sector_by_key: dict[str, str],
    position_cap: float,
    sector_cap: float,
    max_iter: int = 10,
) -> dict[str, float]:
    if not raw_weights:
        return {}
    weights = _normalize(raw_weights)

    for _ in range(max_iter):
        prev = dict(weights)

        # Position cap
        excess = 0.0
        for key, value in list(weights.items()):
            if value > position_cap:
                excess += value - position_cap
                weights[key] = position_cap

        if excess > 0:
            free_keys = [
                key
                for key, value in weights.items()
                if value < position_cap and _sector_weight(weights, sector_by_key, sector_by_key.get(key, "UNKNOWN")) < sector_cap
            ]
            _redistribute(weights, free_keys, excess, position_cap, sector_cap, sector_by_key)

        # Sector cap
        sector_totals: dict[str, float] = {}
        for key, value in weights.items():
            sector = sector_by_key.get(key, "UNKNOWN")
            sector_totals[sector] = sector_totals.get(sector, 0.0) + value

        sector_excess = 0.0
        for sector, total in sector_totals.items():
            if total <= sector_cap:
                continue
            shrink = sector_cap / total if total > 0 else 0.0
            for key, value in list(weights.items()):
                if sector_by_key.get(key, "UNKNOWN") != sector:
                    continue
                new_value = value * shrink
                sector_excess += value - new_value
                weights[key] = new_value

        if sector_excess > 0:
            free_keys = [
                key
                for key, value in weights.items()
                if value < position_cap and _sector_weight(weights, sector_by_key, sector_by_key.get(key, "UNKNOWN")) < sector_cap
            ]
            _redistribute(weights, free_keys, sector_excess, position_cap, sector_cap, sector_by_key)

        max_delta = max(abs(weights.get(key, 0.0) - prev.get(key, 0.0)) for key in weights)
        if max_delta < 1e-8:
            break

    for key, value in list(weights.items()):
        if value > position_cap:
            weights[key] = position_cap
        elif value < 0:
            weights[key] = 0.0

    sector_totals: dict[str, float] = {}
    for key, value in weights.items():
        sector = sector_by_key.get(key, "UNKNOWN")
        sector_totals[sector] = sector_totals.get(sector, 0.0) + value
    for sector, total in sector_totals.items():
        if total <= sector_cap:
            continue
        shrink = sector_cap / total if total > 0 else 0.0
        for key, value in list(weights.items()):
            if sector_by_key.get(key, "UNKNOWN") == sector:
                weights[key] = value * shrink

    return weights



def _sector_weight(weights: dict[str, float], sector_by_key: dict[str, str], sector: str) -> float:
    return sum(value for key, value in weights.items() if sector_by_key.get(key, "UNKNOWN") == sector)



def _redistribute(
    weights: dict[str, float],
    keys: list[str],
    extra: float,
    position_cap: float,
    sector_cap: float,
    sector_by_key: dict[str, str],
) -> None:
    if extra <= 0 or not keys:
        return
    room_by_key: dict[str, float] = {}
    room_total = 0.0
    for key in keys:
        current = weights.get(key, 0.0)
        sector = sector_by_key.get(key, "UNKNOWN")
        sector_room = max(0.0, sector_cap - _sector_weight(weights, sector_by_key, sector))
        room = max(0.0, min(position_cap - current, sector_room))
        if room <= 0:
            continue
        room_by_key[key] = room
        room_total += room
    if room_total <= 0:
        return
    alloc = min(extra, room_total)
    for key, room in room_by_key.items():
        weights[key] = weights.get(key, 0.0) + alloc * (room / room_total)



def build_risk_filtered_portfolio(
    *,
    report_quarter: str,
    signals: list[TrendStockSignal],
    as_of_trade_date: date,
    price_gateway: HistoricalPriceGateway,
    pipeline: PipelineConfig,
    symbol_map_file: Path,
) -> BuildRiskFilteredPortfolioResult:
    if not signals:
        return BuildRiskFilteredPortfolioResult(risk_signals=[], selected_positions=[])

    symbol_map = _load_symbol_map(symbol_map_file)
    metadata = _load_symbol_metadata(pipeline.symbol_metadata_file)

    strong_sell_keys = {
        (item.instrument_key or "").strip().upper()
        for item in signals
        if (item.regime or "").upper() == "STRONG_SELL"
    }
    candidates = [
        item
        for item in signals
        if (item.regime or "").upper() in BUY_REGIMES and float(item.confidence) >= pipeline.min_conf
    ]
    if not candidates:
        return BuildRiskFilteredPortfolioResult(risk_signals=[], selected_positions=[])

    ticker_by_key: dict[str, str] = {}
    tickers: set[str] = set()
    for signal in candidates:
        ticker = _ticker_for_signal(signal, symbol_map)
        key = (signal.instrument_key or "").strip().upper()
        ticker_by_key[key] = ticker
        if ticker and ticker != "UNKNOWN":
            tickers.add(ticker)

    lookback_start = as_of_trade_date - timedelta(days=45)
    price_by_ticker = price_gateway.get_eod_prices(sorted(tickers), lookback_start, as_of_trade_date)

    built: list[RiskFilteredSignal] = []
    passed: list[RiskFilteredSignal] = []

    for signal in candidates:
        key = (signal.instrument_key or "").strip().upper()
        ticker = ticker_by_key.get(key, "UNKNOWN")
        reasons: list[str] = []

        if key in strong_sell_keys:
            reasons.append("excluded_strong_sell")

        score_raw = max(0.0, float(signal.trend_ewma)) * float(signal.confidence) * (1.0 - float(signal.crowding_hhi))

        if signal.freshness_ok is False:
            reasons.append("freshness_failed")

        price = _asof_price(price_by_ticker.get(ticker, {}), as_of_trade_date)
        if price is None:
            reasons.append("missing_price")
        elif price < pipeline.price_min:
            reasons.append("price_below_min")

        adv20 = price_gateway.get_adv20_usd(ticker, as_of_trade_date) if ticker != "UNKNOWN" else None
        if adv20 is None:
            reasons.append("missing_adv20")
        elif adv20 < pipeline.adv20_usd_min:
            reasons.append("adv20_below_min")

        if score_raw <= 0:
            reasons.append("non_positive_score")

        passed_filters = len(reasons) == 0
        score_risk = score_raw if passed_filters else 0.0
        meta = metadata.get(ticker, {"sector": "UNKNOWN", "country": "UNKNOWN"})
        risk_signal = RiskFilteredSignal(
            report_quarter=report_quarter,
            instrument_key=key,
            ticker=ticker,
            sector=meta["sector"],
            country=meta["country"],
            score_raw=score_raw,
            score_risk=score_risk,
            target_weight=0.0,
            weight_capped=0.0,
            passed_filters=passed_filters,
            filter_reasons=reasons,
        )
        built.append(risk_signal)
        if passed_filters:
            passed.append(risk_signal)

    ranked = sorted(passed, key=lambda item: item.score_risk, reverse=True)
    selected = ranked[: pipeline.top_k]
    selected_set = {(item.instrument_key, item.ticker) for item in selected}

    raw_weights = {item.instrument_key: item.score_risk for item in selected}
    normalized = _normalize(raw_weights)
    sector_by_key = {
        item.instrument_key: _sector_bucket(item.sector, item.instrument_key)
        for item in selected
    }
    capped = _apply_caps(
        raw_weights=normalized,
        sector_by_key=sector_by_key,
        position_cap=pipeline.position_cap,
        sector_cap=pipeline.sector_cap,
    )

    final_risk_signals: list[RiskFilteredSignal] = []
    selected_positions: list[TargetPosition] = []
    for item in built:
        key = item.instrument_key
        is_selected = (item.instrument_key, item.ticker) in selected_set
        target_weight = normalized.get(key, 0.0) if is_selected else 0.0
        weight_capped = capped.get(key, 0.0) if is_selected else 0.0
        reasons = list(item.filter_reasons)
        if item.passed_filters and not is_selected:
            reasons.append("excluded_top_k")

        finalized = RiskFilteredSignal(
            report_quarter=item.report_quarter,
            instrument_key=item.instrument_key,
            ticker=item.ticker,
            sector=item.sector,
            country=item.country,
            score_raw=item.score_raw,
            score_risk=item.score_risk,
            target_weight=target_weight,
            weight_capped=weight_capped,
            passed_filters=item.passed_filters,
            filter_reasons=reasons,
        )
        final_risk_signals.append(finalized)

        if is_selected and weight_capped > 0:
            selected_positions.append(
                TargetPosition(
                    report_quarter=report_quarter,
                    instrument_key=item.instrument_key,
                    ticker=item.ticker,
                    target_weight=target_weight,
                    weight_capped=weight_capped,
                )
            )

    selected_positions.sort(key=lambda item: item.weight_capped, reverse=True)
    return BuildRiskFilteredPortfolioResult(risk_signals=final_risk_signals, selected_positions=selected_positions)
