"""Exception hierarchy rooted at MarketIntelError.

market_intel is exception-isolated from the signals package by design (only
signals.infrastructure.logging.* and signals.domain.timing may be imported).
Config errors intentionally use ValueError (mirrors signals/config.py).
"""
from __future__ import annotations


class MarketIntelError(Exception):
    """Root of the market_intel exception hierarchy."""


class CollectorError(MarketIntelError):
    """Raised by gateways/collectors; captured per source, never fatal alone."""


class GaugeComputeError(MarketIntelError):
    """Raised for invalid gauge inputs (unknown band key, empty members, ...)."""


class MarketIntelStorageError(MarketIntelError):
    """Wraps sqlite3.Error raised by the vintage store."""


class MarketIntelPublishError(MarketIntelError):
    """Raised when an existing spreadsheet update hard-fails."""
