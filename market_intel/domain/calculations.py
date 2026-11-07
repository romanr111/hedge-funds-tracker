"""Pure technical/statistical calculations on dated series.

A "series" is an ordered list of ``(obs_date, value)`` pairs (ascending by
date). The compute layer feeds point-in-time windows already collapsed to one
print per date via :func:`dedup_first` (first stored print wins - a revised
print is a new row), so the series helpers iterate inputs as given without
re-deduplicating. All functions are pure: no I/O, no clock access, no
randomness.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import deque
from datetime import date, timedelta
from typing import Sequence

_YOY_MATCH_TOLERANCE_DAYS = 35


def dedup_first(observations: Sequence) -> list[tuple[date, float]]:
    """(obs_date, value) per date keeping the FIRST print, ascending by date.

    Point-in-time discipline: a revised print is a new row; series logic uses
    first prints, so duplicates collapse to the earliest stored row per date.
    """
    by_date: dict[date, float] = {}
    for obs in observations:
        if obs.obs_date not in by_date:
            by_date[obs.obs_date] = float(obs.value)
    return sorted(by_date.items())


def nearest_point(
    points: list[tuple[date, float]],
    target: date,
    tol_days: int,
) -> tuple[date, float] | None:
    """Obs closest to ``target`` within ``tol_days``; earlier date wins ties."""
    if not points:
        return None
    dates = [d for d, _ in points]
    idx = bisect_left(dates, target)
    candidates: list[tuple[int, date, float]] = []
    for i in (idx - 1, idx):
        if 0 <= i < len(points):
            diff = abs((points[i][0] - target).days)
            if diff <= tol_days:
                candidates.append((diff, points[i][0], points[i][1]))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, d, v = candidates[0]
    return (d, v)


def latest_at_or_before(points: list[tuple[date, float]], as_of: date) -> tuple[date, float] | None:
    """Latest observation dated on or before ``as_of``; None when all later."""
    latest: tuple[date, float] | None = None
    for d, v in points:
        if d > as_of:
            break
        latest = (d, v)
    return latest


def _mean(points: Sequence[tuple[date, float]]) -> float | None:
    if not points:
        return None
    return sum(v for _, v in points) / len(points)


def _rsi_point(d: date, avg_gain: float, avg_loss: float) -> tuple[date, float]:
    if avg_loss == 0.0:
        value = 100.0 if avg_gain > 0.0 else 50.0
    else:
        rs = avg_gain / avg_loss
        value = 100.0 - 100.0 / (1.0 + rs)
    return (d, value)


class SortedSeries:
    """Pre-sorted dated series supporting fast windowed lookups via bisect."""

    def __init__(self, points: list[tuple[date, float]]) -> None:
        self.points = points
        self.dates = [d for d, _ in points]

    def _slice(self, start: date, end: date) -> list[tuple[date, float]]:
        lo = bisect_left(self.dates, start)
        hi = bisect_right(self.dates, end)
        return self.points[lo:hi]

    def rolling_mean(self, window_days: int, as_of: date) -> float | None:
        return _mean(self._slice(as_of - timedelta(days=window_days), as_of))

    def yoy(self, as_of: date) -> float | None:
        """% change vs latest point <= as_of against obs near now-365d.

        "Now" is the latest observation on or before ``as_of``; the year-ago
        anchor is the observation nearest to ``now - 365d`` (within 35 days,
        earlier date wins ties). Weekly and monthly series drift off the
        exact anniversary, hence the tolerance.
        """
        hi = bisect_right(self.dates, as_of)
        if hi < 2:
            return None
        window = self.points[:hi]
        now_d, now_v = window[-1]
        target = now_d - timedelta(days=365)
        year_ago = nearest_point(window, target, _YOY_MATCH_TOLERANCE_DAYS)
        if year_ago is None or abs((year_ago[0] - target).days) > _YOY_MATCH_TOLERANCE_DAYS:
            return None
        if year_ago[1] == 0.0:
            return None
        return (now_v / year_ago[1] - 1.0) * 100.0


def weekly_close_series(points: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Last observed close per ISO week, anchored at that close's date."""
    by_week: dict[tuple[int, int], tuple[date, float]] = {}
    for d, v in points:
        iso = d.isocalendar()
        by_week[(iso[0], iso[1])] = (d, v)
    return sorted(by_week.values())


def weekly_rsi_series(closes: list[tuple[date, float]], period: int = 14) -> list[tuple[date, float]]:
    """Wilder RSI evaluated at every weekly close once the warmup completes."""
    if len(closes) < period + 1:
        return []
    changes = [closes[i][1] - closes[i - 1][1] for i in range(1, len(closes))]
    seed = changes[:period]
    avg_gain = sum(c for c in seed if c > 0) / period
    avg_loss = sum(-c for c in seed if c < 0) / period
    out: list[tuple[date, float]] = [_rsi_point(closes[period][0], avg_gain, avg_loss)]
    for offset, change in enumerate(changes[period:], start=period):
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out.append(_rsi_point(closes[offset + 1][0], avg_gain, avg_loss))
    return out


def pct_ma_series(points: list[tuple[date, float]], window: int) -> list[tuple[date, float]]:
    """(last/window-SMA - 1)*100 per day once the SMA window fills."""
    out: list[tuple[date, float]] = []
    running = deque[float]([])
    total = 0.0
    for d, v in points:
        running.append(v)
        total += v
        if len(running) > window:
            total -= running.popleft()
        if len(running) == window and total != 0.0:
            sma = total / window
            out.append((d, (v / sma - 1.0) * 100.0))
    return out


def drawdown_series(points: list[tuple[date, float]], lookback_days: int) -> list[tuple[date, float]]:
    """(last/max-in-lookback - 1)*100 per day; sliding-window max via deque."""
    out: list[tuple[date, float]] = []
    window: deque[tuple[date, float]] = deque()
    maxima: deque[tuple[date, float]] = deque()
    for d, v in points:
        window.append((d, v))
        while maxima and maxima[-1][1] <= v:
            maxima.pop()
        maxima.append((d, v))
        cutoff = d - timedelta(days=lookback_days - 1)
        while window and window[0][0] < cutoff:
            drop_d, drop_v = window.popleft()
            if maxima and maxima[0] == (drop_d, drop_v):
                maxima.popleft()
        if window and maxima and maxima[0][1] != 0.0:
            out.append((d, (window[-1][1] / maxima[0][1] - 1.0) * 100.0))
    return out
