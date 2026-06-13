from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskFilteredSignal:
    report_quarter: str
    instrument_key: str
    ticker: str
    sector: str
    country: str
    score_raw: float
    score_risk: float
    target_weight: float
    weight_capped: float
    passed_filters: bool
    filter_reasons: list[str]


@dataclass(frozen=True)
class TargetPosition:
    report_quarter: str
    instrument_key: str
    ticker: str
    target_weight: float
    weight_capped: float


@dataclass(frozen=True)
class PortfolioVintage:
    report_quarter: str
    entry_date: str
    exit_date: str
    holdings: dict[str, float]


@dataclass(frozen=True)
class PortfolioSnapshot:
    as_of_quarter: str
    positions: list[TargetPosition]


@dataclass(frozen=True)
class PipelineKPI:
    metric: str
    scope: str
    scope_key: str | None
    value: float
