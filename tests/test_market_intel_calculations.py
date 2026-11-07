"""Tests for the promoted domain calculations (live series math).

These helpers are the exact bodies compute_dashboard runs in production:
first-print dedupe, bisect lookups, weekly resampling, Wilder RSI series,
SMA-deviation and sliding-drawdown series.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from market_intel.domain.calculations import (
    SortedSeries,
    dedup_first,
    drawdown_series,
    latest_at_or_before,
    nearest_point,
    pct_ma_series,
    weekly_close_series,
    weekly_rsi_series,
)


class _Obs:
    """Minimal observation duck-type for dedup_first."""

    def __init__(self, obs_date: date, value: float) -> None:
        self.obs_date = obs_date
        self.value = value


def _pairs(values: list[float], *, start: date = date(2026, 1, 1), step_days: int = 1) -> list[tuple[date, float]]:
    return [(start + timedelta(days=step_days * i), v) for i, v in enumerate(values)]


def test_dedup_first_keeps_the_first_print_per_date() -> None:
    observations = [
        _Obs(date(2026, 1, 1), 10.0),
        _Obs(date(2026, 1, 1), 12.0),  # revised print must NOT win
        _Obs(date(2026, 1, 2), 8.0),
        _Obs(date(2025, 12, 31), 3.0),
    ]
    assert dedup_first(observations) == [
        (date(2025, 12, 31), 3.0),
        (date(2026, 1, 1), 10.0),
        (date(2026, 1, 2), 8.0),
    ]
    assert dedup_first([]) == []


def test_nearest_point_tie_breaks_to_earlier_date() -> None:
    points = _pairs([1.0, 2.0, 3.0], start=date(2026, 1, 1), step_days=2)
    # target exactly midway between two points -> earlier date wins
    assert nearest_point(points, date(2026, 1, 2), 1) == (date(2026, 1, 1), 1.0)
    assert nearest_point(points, date(2026, 1, 5), 1) == (date(2026, 1, 5), 3.0)
    assert nearest_point(points, date(2026, 2, 1), 1) is None
    assert nearest_point([], date(2026, 1, 1), 5) is None


def test_latest_at_or_before_scans_up_to_the_clock() -> None:
    points = _pairs([1.0, 2.0, 3.0], start=date(2026, 1, 1))
    assert latest_at_or_before(points, date(2026, 1, 2)) == (date(2026, 1, 2), 2.0)
    assert latest_at_or_before(points, date(2025, 12, 31)) is None
    assert latest_at_or_before(points, date(2027, 1, 1)) == (date(2026, 1, 3), 3.0)


def test_sorted_series_rolling_mean_inclusive_window() -> None:
    weekly = [(date(2026, 10, d), float(v)) for d, v in ((10, 100.0), (17, 200.0), (24, 300.0), (31, 400.0))]
    series = SortedSeries(weekly)
    # as_of 2026-11-06, window 28 days: cutoff 2026-10-09 -> all four points
    assert series.rolling_mean(28, date(2026, 11, 6)) == 250.0
    # window 21: cutoff 2026-10-16 -> the last three weeks only
    assert series.rolling_mean(21, date(2026, 11, 6)) == 300.0
    assert series.rolling_mean(28, date(2026, 10, 9)) is None


def test_sorted_series_yoy_matches_nearest_anniversary() -> None:
    monthly = [
        (date(2025, 1, 1), 100.0),
        (date(2025, 2, 1), 110.0),
        (date(2026, 1, 10), 121.0),
    ]
    series = SortedSeries(monthly)
    # as of 2026-02-01: now = 2026-01-10 (121); target 2025-01-10;
    # nearest year-ago obs = 2025-01-01 (9 days) -> +21%
    assert series.yoy(date(2026, 2, 1)) == pytest.approx(21.0)
    # year-ago farther than 35 days -> None (honest blank)
    assert SortedSeries([(date(2025, 1, 1), 100.0), (date(2026, 5, 1), 121.0)]).yoy(date(2026, 5, 1)) is None
    # zero base is not a valid growth anchor
    assert SortedSeries([(date(2025, 1, 15), 0.0), (date(2026, 1, 15), 50.0)]).yoy(date(2026, 1, 15)) is None
    # fewer than two points on or before as_of
    assert series.yoy(date(2025, 3, 1)) is None
    assert SortedSeries([]).yoy(date(2026, 1, 1)) is None


def test_weekly_close_series_takes_last_close_per_iso_week() -> None:
    # Tue and Wed are the SAME ISO week; Mon starts the next one
    daily = (
        _pairs([10.0], start=date(2026, 1, 5))          # Tue
        + _pairs([14.0], start=date(2026, 1, 6))        # Wed: week last close
        + _pairs([12.0], start=date(2026, 1, 12))       # Mon
        + _pairs([20.0], start=date(2026, 1, 13))       # Tue: week last close
        + _pairs([16.0], start=date(2026, 1, 19))       # Mon
    )
    assert weekly_close_series(daily) == [
        (date(2026, 1, 6), 14.0),
        (date(2026, 1, 13), 20.0),
        (date(2026, 1, 19), 16.0),
    ]


def test_weekly_rsi_series_hand_computed_wilder_chain() -> None:
    closes = _pairs([10.0, 12.0, 11.0, 13.0, 12.0])
    # period 2: seed deltas [2, -1] -> avg_gain=1, avg_loss=0.5 (rs = 2)
    # +2 -> (1.5, 0.25); -1 -> (0.75, 0.625) -> rs = 1.2
    points = weekly_rsi_series(closes, period=2)
    assert [d for d, _ in points] == [closes[2][0], closes[3][0], closes[4][0]]
    assert [v for _, v in points] == pytest.approx(
        [100.0 - 100.0 / 3.0, 100.0 - 100.0 / 7.0, 100.0 - 100.0 / 2.2]
    )


def test_weekly_rsi_series_extremes_and_warmup() -> None:
    up = _pairs([float(i + 1) for i in range(6)])
    down = list(reversed(up))
    assert weekly_rsi_series(up, period=4)[-1][1] == 100.0
    assert weekly_rsi_series(down, period=4)[-1][1] == 0.0
    flat = _pairs([7.0] * 6)
    assert weekly_rsi_series(flat, period=4)[-1][1] == 50.0
    # needs period + 1 closes before the first RSI point exists
    assert weekly_rsi_series(_pairs([1.0, 2.0, 3.0]), period=4) == []


def test_pct_ma_series_daily_sma_deviation() -> None:
    points = _pairs([1.0, 2.0, 3.0, 4.0, 5.0])
    out = pct_ma_series(points, 3)
    assert [d for d, _ in out] == [points[2][0], points[3][0], points[4][0]]
    assert [v for _, v in out] == pytest.approx([50.0, 100.0 / 3.0, 25.0])
    assert pct_ma_series(points, 10) == []
    assert pct_ma_series([], 3) == []


def test_drawdown_series_sliding_peak_within_lookback() -> None:
    points = _pairs([1.0, 2.0, 3.0, 4.0, 3.0])
    out = drawdown_series(points, 5)
    assert [v for _, v in out] == pytest.approx([0.0, 0.0, 0.0, 0.0, -25.0])
    assert [d for d, _ in out] == [p[0] for p in points]


def test_drawdown_series_drops_peak_leaving_the_window() -> None:
    # peak 9 leaves the 2-day window before the later points start rising
    points = _pairs([9.0, 1.0, 1.0, 2.0])
    out = drawdown_series(points, 2)
    assert [v for _, v in out] == pytest.approx([0.0, -88.888888, 0.0, 0.0])
    assert drawdown_series([], 5) == []
