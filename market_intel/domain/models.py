"""Frozen domain models for the market intelligence pipeline.

Contract (shared by all agents - do not change field names without
updating the orchestrator's approved plan).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final, Mapping

MODULE_KEYS: Final[tuple[str, ...]] = (
    "valuation",
    "sentiment",
    "technique",
    "macro",
    "credit",
)

MODULE_TITLES_UK: Final[Mapping[str, str]] = {
    "valuation": "Валютація",
    "sentiment": "Настрої",
    "technique": "Техніка",
    "macro": "Макро і ліквідність",
    "credit": "Кредит",
}

# Band color tokens -> fill hex. Always paired with the band's word label.
COLOR_TOKEN_HEX: Final[Mapping[str, str]] = {
    "state1": "#7CB342",  # добре / дешево / низький ризик
    "state2": "#AED581",
    "state3": "#FFD54F",  # нейтрально / увага
    "state4": "#FFB74D",
    "state5": "#E57373",  # погано / паніка / високий ризик
}


@dataclass(frozen=True)
class SeriesObservation:
    """One point-in-time reading stored in the append-only vintage store."""

    series_id: str
    obs_date: date
    value: float
    source: str
    fetched_at: datetime


@dataclass(frozen=True)
class SeriesSpec:
    """Declarative definition (or computation) of one series."""

    key: str
    title_uk: str
    source: str
    freq: str
    staleness_days: int
    band_ref: str | None = None
    weight: float | None = None
    module_key: str | None = None
    invert: bool = False
    percentile_window_days: int = 7300
    params: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_computed(self) -> bool:
        return self.source == "computed"


@dataclass(frozen=True)
class BandPoint:
    """One band of a value scale; bands are adjacent half-open intervals."""

    key: str
    label_uk: str
    color_token: str
    lower: float | None
    upper: float | None


@dataclass(frozen=True)
class SeriesSnapshot:
    """Current view of one series for the dashboard."""

    key: str
    title_uk: str
    module_key: str | None
    value: float | None
    obs_date: date | None
    percentile: float | None
    band: BandPoint | None
    is_stale: bool
    stale_reason_uk: str | None
    meaning_uk: str
    source_uk: str
    unit_uk: str


@dataclass(frozen=True)
class ModuleGauge:
    """Dashboard module gauge: composite score mapped to a band."""

    module_key: str
    title_uk: str
    score: float | None
    state_key: str
    chip_label_uk: str
    chip_color: str
    verdict_uk: str
    context_forward_uk: str
    facts_uk: tuple[str, ...]
    members_used: int
    members_total: int
    dropped_uk: tuple[str, ...]


@dataclass(frozen=True)
class ForwardReturnRow:
    """One row of the "conditions -> forward returns" mapping table.

    evidence_level: "ESTABLISHED" | "TENTATIVE" | "TBC" | "NOT ESTABLISHED".
    Rows not computable in MVP carry evaluated_in_mvp=False.
    """

    rule_key: str
    condition_uk: str
    horizon_uk: str
    result_uk: str
    uncertainty_uk: str
    max_dd_uk: str
    n_uk: str
    evidence_level: str
    citation_uk: str
    active_now: bool
    evaluated_in_mvp: bool = True


@dataclass(frozen=True)
class GaugeHistoryPoint:
    """One composite score history row (per run date and module)."""

    run_date: date
    module_key: str
    score: float | None
    state_key: str
    members_used: int
    members_total: int
    computed_at: datetime


@dataclass(frozen=True)
class DashboardSnapshot:
    """Full dashboard state for one run."""

    as_of: date
    generated_at: datetime
    modules: tuple[ModuleGauge, ...]
    indicators: tuple[SeriesSnapshot, ...]
    forward_rows: tuple[ForwardReturnRow, ...]
    system_signal_uk: str
    warnings_uk: tuple[str, ...]


# Structured skip codes for the CLI exit ladder (see Skip / CollectionResult).
# manual / computed / no_adapter are benign: none of them is an upstream fetch
# failure, so they never trigger the all-sources-failed exit. source_error and
# internal always count as an unhandled skip.
SKIP_MANUAL: Final[str] = "manual"
SKIP_COMPUTED: Final[str] = "computed"
SKIP_NO_ADAPTER: Final[str] = "no_adapter"
SKIP_SOURCE_ERROR: Final[str] = "source_error"
SKIP_INTERNAL: Final[str] = "internal"


@dataclass(frozen=True)
class Skip:
    """One series skipped during collection with a machine-readable code."""

    key: str
    code: str
    detail: str


@dataclass(frozen=True)
class CollectionResult:
    """Outcome of collecting vintages from all fetchers."""

    fetched: tuple[str, ...]
    skipped: tuple[Skip, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class PublishRunRecord:
    """Last publish attempt bookkeeping row."""

    spreadsheet_id: str | None
    fingerprint: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    detail: str


@dataclass(frozen=True)
class PublishResult:
    """Outcome of publishing to Google Sheets."""

    status: str
    spreadsheet_id: str | None
    detail: str


@dataclass(frozen=True)
class RunResult:
    """Full CLI run outcome (collect -> compute -> publish)."""

    collected: CollectionResult
    dashboard: DashboardSnapshot
    publish: PublishResult | None
