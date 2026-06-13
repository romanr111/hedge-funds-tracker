from __future__ import annotations

from dataclasses import dataclass
from typing import Any


Position = dict[str, Any]


@dataclass(frozen=True)
class Manager:
    name: str
    cik: str


@dataclass(frozen=True)
class Filing:
    accession: str
    form: str
    filing_date: str | None
    report_date: str | None
    acceptance_datetime: str | None = None


@dataclass(frozen=True)
class ManagerState:
    cik: str
    name: str
    last_accession: str | None
    last_filing_date: str | None
    last_report_date: str | None
    last_positions: list[Position] | None
    last_notified_accession: str | None = None


@dataclass(frozen=True)
class DiffResult:
    new_positions: list[Position]
    exited_positions: list[Position]
    increased_positions: list[Position]
    decreased_positions: list[Position]


@dataclass(frozen=True)
class ManagerQuarterSnapshot:
    cik: str
    manager_name: str
    report_quarter: str
    report_date: str | None
    filing_date: str | None
    acceptance_datetime: str | None
    accession: str
    source_form: str
    positions: list[Position]
    aum_value_k: int
    positions_count: int
    updated_at: str | None = None


@dataclass(frozen=True)
class TrendRun:
    report_quarter: str
    input_fingerprint: str
    top_fingerprint: str | None
    status: str
    computed_at: str
    notes_json: str | None = None
    is_backfill: bool = False
    backfill_batch_id: str | None = None


@dataclass(frozen=True)
class TrendStockSignal:
    report_quarter: str
    instrument_key: str
    cusip: str | None
    put_call: str | None
    issuer_name: str | None
    title: str | None
    np_raw: float
    np_adj: float
    impulse_score: float
    accumulation_score: float
    confidence: float
    trend_ewma: float
    trend_delta: float
    breadth_buy_weight: float
    breadth_sell_weight: float
    buy_managers: int
    sell_managers: int
    crowding_hhi: float
    persistence_buy: int
    persistence_sell: int
    regime: str
    contributors_json: str
    computed_at: str
    freshness_multiplier: float = 1.0
    freshness_ok: bool | None = None
    is_backfill: bool = False
    backfill_batch_id: str | None = None
