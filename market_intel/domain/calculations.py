"""Pure technical/statistical calculations on dated series.

A "series" is an ordered sequence of ``(obs_date, value)`` pairs (ascending by
date; duplicates tolerated, last wins per date for resampling). All functions
are pure: no I/O, no clock access, no randomness.
"""
from __future__ import annotations

from bisect import bisect_left

from datetime import date, timedelta
from typing import Sequence

_YEARDAY = 365
_YOY_MATCH_TOLERANCE_DAYS = 35


def _sorted_unique_days(series: Sequence[tuple[date, float]]) -> list[tuple[date, float]]:
    """Last value per date, ordered ascending."""
    by_date: dict[date, float] = {}
    for obs, value in series:
        by_date[obs] = value
    return sorted(by_date.items())


def _weekly_close_series(series: Sequence[tuple[date, float]]) -> list[tuple[date, float]]:
    """Resample to one point per ISO week: the LAST observed close of the week.

    Weeks are keyed by the ISO year-week of each observation date; the anchor
    date carried forward is the observation date of that last close.
    """
    by_week: dict[tuple[int, int], tuple[date, float]] = {}
    for obs, value in _sorted_unique_days(series):
        iso_year, iso_week, _ = obs.isocalendar()
        by_week[(iso_year, iso_week)] = (obs, value)
    return sorted(by_week.values())


def rsi(series: Sequence[tuple[date, float]], period: int, *, weekly: bool = False) -> float | None:
    """Wilder-smoothed RSI over ``period`` deltas; None when not enough data.

    Needs ``period + 1`` points to produce ``period`` deltas. Seed: average
    gain/loss over the first ``period`` deltas; subsequent deltas use Wilder's
    smoothing: avg = (prev_avg * (period - 1) + current) / period.
    """
    if period <= 0:
        return None
    points = _weekly_close_series(series) if weekly else _sorted_unique_days(series)
    values = [v for _, v in points]
    if len(values) < period + 1:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    seed_changes = changes[:period]
    avg_gain = sum(c for c in seed_changes if c > 0) / period
    avg_loss = sum(-c for c in seed_changes if c < 0) / period
    for change in changes[period:]:
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def simple_moving_average(series: Sequence[tuple[date, float]], window: int) -> float | None:
    """Mean of the LAST ``window`` distinct-date values; None when fewer."""
    if window <= 0:
        return None
    values = [v for _, v in _sorted_unique_days(series)][-window:]
    if len(values) < window:
        return None
    return sum(values) / window


def price_vs_ma_pct(series: Sequence[tuple[date, float]], window: int) -> float | None:
    """(last close / window-SMA - 1) * 100; None when SMA unavailable."""
    values = [v for _, v in _sorted_unique_days(series)]
    if not values:
        return None
    sma = simple_moving_average(series, window)
    if sma is None or sma == 0.0:
        return None
    return (values[-1] / sma - 1.0) * 100.0


def current_drawdown_from_peak(series: Sequence[tuple[date, float]], lookback_days: int) -> float | None:
    """(last / max within lookback - 1) * 100; 0.0 when the last IS the peak.

    The lookback window covers observations dated within the last
    ``lookback_days`` calendar days of the latest observation date.
    """
    if lookback_days <= 0:
        return None
    points = _sorted_unique_days(series)
    if not points:
        return None
    latest_obs = points[-1][0]
    cutoff = latest_obs - (lookback_days - 1) * timedelta(days=1)
    window_values = [v for obs, v in points if obs >= cutoff]
    if not window_values:
        return None
    peak = max(window_values)
    last = points[-1][1]
    if peak == 0.0:
        return None
    return (last / peak - 1.0) * 100.0


def _nearest_obs_on_or_before(series: Sequence[tuple[date, float]], target: date) -> tuple[date, float] | None:
    nearest: tuple[date, float] | None = None
    for obs, value in series:
        if obs > target:
            break
        nearest = (obs, value)
    return nearest


def _nearest_obs_to_target(points: list[tuple[date, float]], target: date, tol_days: int) -> tuple[date, float] | None:
    """Observation within ``tol_days`` of ``target`` that is closest to it.

    Earlier date wins ties. ``points`` must be ascending by date; the search
    is a bisect over the date list, never a scan over values.
    """
    if not points:
        return None
    dates = [obs for obs, _ in points]
    idx = bisect_left(dates, target)
    best: tuple[int, date, float] | None = None
    for i in (idx - 1, idx):
        if 0 <= i < len(points):
            diff = abs((points[i][0] - target).days)
            if diff <= tol_days and (best is None or (diff, points[i][0]) < (best[0], best[1])):
                best = (diff, points[i][0], points[i][1])
    return (best[1], best[2]) if best is not None else None


def year_over_year(history: Sequence[tuple[date, float]], as_of: date) -> float | None:
    """% change vs the observation nearest to ``as_of - 365d`` (within 35 days).

    Both anchors are point-in-time: "now" is the latest observation on or
    before ``as_of``, "year ago" is the observation nearest to ``now - 365d``
    (must be within 35 days of that target, earlier date wins ties). Weekly
    and monthly series drift off the exact anniversary, hence the tolerance.
    """
    points = _sorted_unique_days(history)
    if len(points) < 2:
        return None
    now_point = _nearest_obs_on_or_before(points, as_of)
    if now_point is None:
        return None
    target = now_point[0] - timedelta(days=_YEARDAY)
    year_ago = _nearest_obs_to_target(points, target, _YOY_MATCH_TOLERANCE_DAYS)
    if year_ago is None:
        return None
    if year_ago[1] == 0.0:
        return None
    return (now_point[1] / year_ago[1] - 1.0) * 100.0


def rolling_mean(history: Sequence[tuple[date, float]], window_days: int, as_of: date) -> float | None:
    """Mean of observations dated in ``[as_of - window_days, as_of]``.

    The window is inclusive on both ends (28-day trailing mean of weekly
    claims still catches the as_of week). None when the window is empty.
    """
    if window_days <= 0:
        return None
    cutoff = as_of - timedelta(days=window_days)
    window_values = [v for obs, v in _sorted_unique_days(history) if cutoff <= obs <= as_of]
    if not window_values:
        return None
    return sum(window_values) / len(window_values)
