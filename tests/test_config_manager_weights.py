from __future__ import annotations

import pytest

from signals.config import load_managers


def test_load_managers_requires_weight() -> None:
    with pytest.raises(ValueError, match="must include numeric 'weight'"):
        load_managers(
            None,
            '[{"name":"Fund A","cik":"0000000001"}]',
        )


def test_load_managers_rejects_non_positive_weight() -> None:
    with pytest.raises(ValueError, match="must have 'weight' > 0"):
        load_managers(
            None,
            '[{"name":"Fund A","cik":"0000000001","weight":0}]',
        )


def test_load_managers_parses_valid_weight() -> None:
    managers = load_managers(
        None,
        '[{"name":"Fund A","cik":"0000000001","weight":"1.5"}]',
    )
    assert len(managers) == 1
    assert managers[0].weight == 1.5


def test_load_managers_requires_explicit_sum_for_composite_filing_sources() -> None:
    with pytest.raises(ValueError, match="overlap_policy 'sum'"):
        load_managers(
            None,
            """[
                {
                    "name": "Fund A",
                    "cik": "0000000001",
                    "weight": 1,
                    "filing_source_periods": [
                        {
                            "from_quarter": "2026Q2",
                            "source_ciks": ["0000000001", "0000000002"]
                        }
                    ]
                }
            ]""",
        )


def test_load_managers_rejects_non_string_filing_source_overlap_policy() -> None:
    with pytest.raises(ValueError, match="unsupported filing source overlap policy"):
        load_managers(
            None,
            """[
                {
                    "name": "Fund A",
                    "cik": "0000000001",
                    "weight": 1,
                    "filing_source_periods": [
                        {
                            "from_quarter": "2026Q2",
                            "source_ciks": ["0000000001"],
                            "overlap_policy": 1
                        }
                    ]
                }
            ]""",
        )


def test_load_managers_rejects_overlapping_filing_source_periods() -> None:
    with pytest.raises(ValueError, match="overlapping filing source periods"):
        load_managers(
            None,
            """[
                {
                    "name": "Fund A",
                    "cik": "0000000001",
                    "weight": 1,
                    "filing_source_periods": [
                        {
                            "through_quarter": "2026Q2",
                            "source_ciks": ["0000000001"]
                        },
                        {
                            "from_quarter": "2026Q2",
                            "source_ciks": ["0000000002"]
                        }
                    ]
                }
            ]""",
        )


def test_load_managers_accepts_adjacent_filing_source_periods() -> None:
    managers = load_managers(
        None,
        """[
            {
                "name": "Fund A",
                "cik": "0000000001",
                "weight": 1,
                "filing_source_periods": [
                    {
                        "through_quarter": "2026Q1",
                        "source_ciks": ["0000000001"]
                    },
                    {
                        "from_quarter": "2026Q2",
                        "source_ciks": ["0000000002"]
                    }
                ]
            }
        ]""",
    )

    assert len(managers) == 1
    assert managers[0].source_ciks_for_quarter("2026Q1") == ("0000000001",)
    assert managers[0].source_ciks_for_quarter("2026Q2") == ("0000000002",)
