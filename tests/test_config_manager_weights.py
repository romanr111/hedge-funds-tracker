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
