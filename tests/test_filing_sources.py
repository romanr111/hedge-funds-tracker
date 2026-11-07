from __future__ import annotations

from types import SimpleNamespace

from signals.config import FilingSourcePeriod, ManagerConfig
from signals.domain.filing_sources import (
    all_possible_source_ciks,
    approved_source_ciks,
    composite_accession,
)
from signals.domain.models import Filing


def test_approved_source_ciks_falls_back_to_canonical_cik() -> None:
    manager = SimpleNamespace(name="Fund A", cik="0001234567")

    assert approved_source_ciks(manager, "2026Q1") == ("0001234567",)


def test_approved_source_ciks_honors_config_resolver() -> None:
    manager = ManagerConfig(
        name="Pershing",
        cik="0001336528",
        weight=1.0,
        filing_source_periods=(
            FilingSourcePeriod(source_ciks=("0002026053",), from_quarter="2026Q2"),
        ),
    )

    assert approved_source_ciks(manager, "2026Q2") == ("0002026053",)
    assert approved_source_ciks(manager, "2026Q1") == ("0001336528",)


def test_all_possible_source_ciks_merges_periods_and_dedupes() -> None:
    manager = ManagerConfig(
        name="Pershing",
        cik="0001336528",
        weight=1.0,
        filing_source_periods=(
            FilingSourcePeriod(source_ciks=("0002026053",), from_quarter="2026Q2"),
            FilingSourcePeriod(source_ciks=("0001336528", "0002026053"), through_quarter="2025Q4"),
        ),
    )

    assert all_possible_source_ciks(manager) == ("0001336528", "0002026053")


def test_composite_accession_is_order_independent_and_discriminates() -> None:
    alpha = ("0001336528", Filing(accession="0001336528-26-000001", form="13F-HR", filing_date="2026-05-15", report_date="2026-03-31"))
    beta = ("0002026053", Filing(accession="0002026053-26-000002", form="13F-HR", filing_date="2026-05-18", report_date="2026-03-31"))

    forward = composite_accession([alpha, beta])
    reversed_pairs = composite_accession([beta, alpha])
    assert forward == reversed_pairs
    assert composite_accession([alpha, alpha]) != forward
    assert forward.startswith("composite:")
