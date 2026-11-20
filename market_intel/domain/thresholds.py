"""Numeric thresholds shared by compute dashboard and forward-return rows.

The same economic events are evaluated twice - as live module-verdict inputs
in ``compute_dashboard`` and as activation predicates for the forward-return
rows - and the two copies must agree by construction, so the values live in
one place. Changing a shared threshold changes every predicate that uses it.

Distinct names are distinct predicates even at the same value: the VIX pair
below intentionally differs - >30 is the short-term vol alarm, >40 is the
forward-return row's condition bar.
"""
from __future__ import annotations

from typing import Final

CLAIMS_YOY_ALARM: Final[float] = 20.0
MARGIN_YOY_EUPHORIA: Final[float] = 30.0
RSI_OVERSOLD: Final[float] = 30.0
VIX_CAPITULATION: Final[float] = 30.0
VIX_PANIC: Final[float] = 40.0
