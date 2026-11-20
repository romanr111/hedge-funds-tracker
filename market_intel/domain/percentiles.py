"""Percentile-rank helpers for gauge normalization (report sec. 6).

Percentile semantics: share of the comparison values that are strictly below
``x`` (rank 0..100). Values equal to ``x`` count toward the rank when strictly
below handling would undercount ties; concrete rule: percentile = 100 * (#below
+ 0.5 * #equal) / total, clipped to [0, cap]. Ties land mid-rank so a member
sitting exactly at its own historical midpoint gets ~50, not a biased extreme.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Sequence

_MIN_WINDOW_VALUES = 30
_DAY = timedelta(days=1)


def percentile_rank(values: Sequence[float], x: float, *, cap: float = 99.99) -> float | None:
    """Percentile of ``x`` inside ``values`` (0..cap), None when too thin.

    Thin = fewer than 30 values -> None (the series cannot be honestly ranked).
    """
    total = len(values)
    if total < _MIN_WINDOW_VALUES:
        return None
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    pct = (below + 0.5 * equal) / total * 100.0
    return min(pct, cap)


def percentile_from_history(
    history: Sequence[tuple[date, float]],
    x: float,
    *,
    window_days: int,
    invert: bool = False,
) -> float | None:
    """Percentile of ``x`` within the trailing ``window_days`` of history.

    The window is measured back from the latest observation date in ``history``
    (not from today), so point-in-time reads and synthetic tests are stable.
    Returns None when the window holds fewer than 30 values (too thin) or is
    empty. ``invert=True`` flips the risk direction: the underlying value is
    "risky when LOW" (e.g. ERP, ECY), so the percentile is mirrored around 50.
    """
    if not history:
        return None
    latest_obs = max(obs for obs, _ in history)
    cutoff = latest_obs - (window_days - 1) * _DAY
    window = [v for obs, v in history if obs >= cutoff]
    rank = percentile_rank(window, x)
    if rank is None:
        return None
    if invert:
        rank = 100.0 - rank
    return min(rank, 99.99)
