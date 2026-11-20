from __future__ import annotations

from datetime import date, timedelta

import pytest

from market_intel.domain.percentiles import percentile_from_history, percentile_rank


def _pairs(values: list[float], start: date = date(2026, 1, 1), step_days: int = 1):
    return [(start + timedelta(days=step_days * i), v) for i, v in enumerate(values)]


def test_percentile_rank_positions_ties_midrank() -> None:
    values = [float(v) for v in range(30)]  # 0..29
    # x=10: 10 below, 1 equal -> (10 + 0.5) / 30 * 100 = 35.0
    assert percentile_rank(values, 10.0) == 35.0
    # strictly between two ranks: below = 15 -> 50.0
    assert percentile_rank(values, 14.5) == 50.0


def test_percentile_rank_caps_top_but_not_bottom() -> None:
    values = [float(v) for v in range(30)]
    assert percentile_rank(values, 99.0) == 99.99
    assert percentile_rank(values, -5.0) == 0.0
    # custom cap is honored
    assert percentile_rank(values, 99.0, cap=97.0) == 97.0


def test_percentile_rank_thin_window_returns_none() -> None:
    assert percentile_rank([1.0, 2.0, 3.0], 2.0) is None
    assert percentile_rank([1.0] * 29, 1.0) is None
    assert percentile_rank([], 1.0) is None
    # exactly 30 values rank fine (all ties -> 50)
    assert percentile_rank([7.0] * 30, 7.0) == 50.0


def test_percentile_from_history_uses_trailing_window() -> None:
    # 60 daily values 0..59; a 30-day window holds 30..59
    history = _pairs([float(v) for v in range(60)])
    result = percentile_from_history(history, 44.5, window_days=30)
    # below 44.5 inside window: 30..44 (15 values) -> 50.0
    assert result == 50.0
    # a value present in its own sample lands mid-rank: x=45 (equal=1)
    assert percentile_from_history(history, 45.0, window_days=30) == pytest.approx(51.66666666666667)
    # x above everything in the window -> capped, not 100
    assert percentile_from_history(history, 500.0, window_days=30) == 99.99


def test_percentile_from_history_ignores_values_outside_window() -> None:
    # 100 daily values 0..99; window 40 keeps 60..99; x=55 sits below ALL window
    # values (but mid-rank on the full history) -> 0.0 proves window filtering
    history = _pairs([float(v) for v in range(100)])
    assert percentile_from_history(history, 55.0, window_days=40) == 0.0
    # widening the window past the full history gives the true rank: below = 55
    full = percentile_from_history(history, 66.0, window_days=1000)
    assert full == (66 + 0.5) / 100 * 100.0


def test_percentile_from_history_thin_window_returns_none() -> None:
    # 100 points total, but only 20 inside the trailing 25-day window
    history = [(date(2026, 1, 1) + timedelta(days=i), float(i)) for i in range(80)]
    history += [(date(2026, 5, 1) + timedelta(days=i), float(i)) for i in range(20)]
    # ensure ascending order overall: rebuild cleanly instead
    history = sorted(history)
    assert percentile_from_history(history, 10.0, window_days=25) is None
    # the same dense-enough window ranks fine: window keeps 10..39, x=24.5
    # has 15 window values below it -> 50.0
    dense = _pairs([float(v) for v in range(40)])
    assert percentile_from_history(dense, 24.5, window_days=30) == 50.0


def test_percentile_from_history_invert_flips_and_caps() -> None:
    history = _pairs([float(v) for v in range(50)])
    # x=14.5 -> 15 of 50 below -> rank 30 -> invert to 70
    assert percentile_from_history(history, 14.5, window_days=50, invert=True) == 70.0
    # x below everything -> rank 0 -> invert 100 -> capped 99.99
    assert percentile_from_history(history, -1.0, window_days=50, invert=True) == 99.99
    # without invert the same x is a plain 0.0
    assert percentile_from_history(history, -1.0, window_days=50) == 0.0


def test_percentile_from_history_empty_returns_none() -> None:
    assert percentile_from_history([], 10.0, window_days=30) is None
