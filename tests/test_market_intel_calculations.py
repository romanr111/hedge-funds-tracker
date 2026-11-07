from __future__ import annotations

from datetime import date, timedelta

import pytest

from market_intel.domain.calculations import (
    current_drawdown_from_peak,
    price_vs_ma_pct,
    rolling_mean,
    rsi,
    simple_moving_average,
    year_over_year,
)


def _pairs(values: list[float], *, start: date = date(2026, 1, 1), step_days: int = 1):
    return [(start + timedelta(days=step_days * i), v) for i, v in enumerate(values)]


def test_rsi_wilder_smoothing_hand_computed() -> None:
    # values 10,12,11,13,12 -> deltas [2,-1,2,-1]; seed (first 2): avg_gain=1,
    # avg_loss=0.5; step +2 -> (1.5, 0.25); step -1 -> (0.75, 0.625); rs=1.2
    pairs = _pairs([10.0, 12.0, 11.0, 13.0, 12.0])
    assert rsi(pairs, 2) == pytest.approx(100.0 - 100.0 / 2.2)


def test_rsi_seed_only_series() -> None:
    # values 10,12,11 -> deltas [2,-1] = exactly the seed for period 2; rs = 2
    pairs = _pairs([10.0, 12.0, 11.0])
    assert rsi(pairs, 2) == pytest.approx(100.0 - 100.0 / 3.0)


def test_rsi_extremes_and_insufficient_data() -> None:
    up = _pairs([float(i + 1) for i in range(6)])
    down = _pairs([float(6 - i) for i in range(6)])
    assert rsi(up, 4) == 100.0
    assert rsi(down, 4) == 0.0
    flat = _pairs([7.0] * 6)
    assert rsi(flat, 4) == 50.0
    # needs period + 1 points
    assert rsi(_pairs([1.0, 2.0, 3.0]), 4) is None
    assert rsi(_pairs([1.0, 2.0]), 0) is None


def test_rsi_weekly_resamples_to_iso_week_last_close() -> None:
    # two daily points per ISO week: Tue and Wed are the SAME week; Mon next week
    week1 = _pairs([10.0, 14.0], start=date(2026, 1, 5), step_days=1)
    week2 = _pairs([12.0, 20.0], start=date(2026, 1, 12), step_days=1)
    week3 = _pairs([16.0], start=date(2026, 1, 19), step_days=1)
    daily = week1 + week2 + week3
    weekly_closes = _pairs([14.0, 20.0, 16.0], start=date(2026, 1, 7), step_days=7)
    assert rsi(daily, 2, weekly=True) == rsi(weekly_closes, 2)
    # and it differs from treating every day as its own observation
    assert rsi(daily, 2, weekly=True) != rsi(daily, 2)


def test_simple_moving_average_and_price_vs_ma_pct() -> None:
    pairs = _pairs([1.0, 2.0, 3.0, 4.0, 5.0])
    assert simple_moving_average(pairs, 3) == 4.0  # (3+4+5)/3
    assert simple_moving_average(pairs, 10) is None
    assert simple_moving_average(pairs, 0) is None
    assert price_vs_ma_pct(pairs, 3) == pytest.approx(25.0)  # 5/4 - 1
    # duplicate dates collapse to the last value
    dated = pairs + [(pairs[-1][0], 8.0)]
    assert simple_moving_average(dated, 3) == 5.0  # (3 + 4 + 8) / 3
    assert simple_moving_average([], 3) is None
    assert price_vs_ma_pct([], 3) is None


def test_current_drawdown_from_peak_windowed() -> None:
    pairs = _pairs([1.0, 2.0, 3.0, 4.0, 3.0])
    assert current_drawdown_from_peak(pairs, 5) == pytest.approx(-25.0)  # 3 vs peak 4
    assert current_drawdown_from_peak(pairs, 2) == pytest.approx(-25.0)  # [4, 3]
    assert current_drawdown_from_peak(pairs, 1) == 0.0  # last value IS the peak
    # peak outside the lookback is ignored
    long = _pairs([9.0, 1.0, 1.0, 2.0])
    assert current_drawdown_from_peak(long, 2) == 0.0  # window [1,2]: peak = last
    assert current_drawdown_from_peak([], 5) is None
    assert current_drawdown_from_peak(pairs, 0) is None


def test_year_over_year_nearest_observation_with_tolerance() -> None:
    monthly = [
        (date(2025, 1, 1), 100.0),
        (date(2025, 2, 1), 110.0),
        (date(2026, 1, 10), 121.0),
    ]
    # as of 2026-02-01: "now" = 2026-01-10 (121); target = 2025-01-10;
    # nearest year-ago obs = 2025-01-01 (9 days) -> +21%
    assert year_over_year(monthly, date(2026, 2, 1)) == pytest.approx(21.0)
    # year-ago farther than 35 days -> None (honest blank)
    sparse = [(date(2025, 1, 1), 100.0), (date(2026, 5, 1), 121.0)]
    assert year_over_year(sparse, date(2026, 5, 1)) is None
    # zero base is not a valid growth anchor
    zero_base = [(date(2025, 1, 15), 0.0), (date(2026, 1, 15), 50.0)]
    assert year_over_year(zero_base, date(2026, 1, 15)) is None
    assert year_over_year([], date(2026, 1, 1)) is None
    assert year_over_year([(date(2026, 1, 1), 5.0)], date(2026, 1, 1)) is None


def test_rolling_mean_inclusive_window() -> None:
    weekly = [(date(2026, 10, d), float(v)) for d, v in ((10, 100.0), (17, 200.0), (24, 300.0), (31, 400.0))]
    # as_of 2026-11-06, window 28 days: cutoff 2026-10-09 -> all four points
    assert rolling_mean(weekly, 28, date(2026, 11, 6)) == 250.0
    # window 21 drops 2026-10-10 only if it is outside: cutoff 2026-10-16 -> 3 points
    assert rolling_mean(weekly, 21, date(2026, 11, 6)) == 300.0
    assert rolling_mean(weekly, 28, date(2026, 1, 1)) is None
    assert rolling_mean(weekly, 0, date(2026, 11, 6)) is None
