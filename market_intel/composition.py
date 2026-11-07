"""Dependency injection: builds MarketIntelRuntime (store, fetchers, publisher).

The concrete vintage store and series gateways are owned by the parallel
data agent; they are imported lazily inside `build_runtime` so this module
stays importable in branch states where those adapters are not landed yet
(offline tests inject fake fetchers and stores instead).
"""
from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from market_intel.application.ports.sheets_publisher import SheetsPublisher
from market_intel.application.ports.series_fetcher import SeriesFetcher
from market_intel.application.ports.vintage_repository import VintageRepository
from market_intel.config import MarketIntelConfig
from market_intel.infrastructure.export.gog_sheets_publisher import (
    SETTING_SPREADSHEET_ID,
    GogSheetsPublisher,
)

FETCHER_SOURCE_KEYS: Final[tuple[str, ...]] = (
    "fred",
    "yahoo",
    "multpl",
    "finra",
    "cnn_fg",
    "factset",
    "damodaran",
)


@dataclass(frozen=True)
class MarketIntelRuntime:
    """Concrete components one CLI invocation runs against."""

    store: VintageRepository
    fetchers: Mapping[str, SeriesFetcher]
    publisher: SheetsPublisher | None


def build_runtime(config: MarketIntelConfig, *, dry_run: bool) -> MarketIntelRuntime:
    """Build the runtime from config.

    In dry-run mode the store points at a throwaway SQLite database so the
    real data file stays untouched, and the publisher is disabled entirely.
    """
    # Agent A adapters (deferred import: they land via integration).
    from market_intel.infrastructure.storage.vintage_store import VintageStore

    if dry_run:
        db_path = Path(tempfile.mkdtemp(prefix="market-intel-dry-run-")) / "dry-run.sqlite3"
    else:
        db_path = Path(config.db_path)
    store: VintageRepository = VintageStore(db_path)

    fetchers = _build_fetchers(config)

    publisher: SheetsPublisher | None = None
    if not dry_run:
        publisher = GogSheetsPublisher(
            store,
            folder_id=config.sheets_folder_id or None,
        )

    # Seed the spreadsheet id from the environment config when provided so
    # the publisher resolves the same document across invocations; the store
    # setting remains the single source of truth afterwards.
    if config.sheets_spreadsheet_id and not dry_run:
        store.set_setting(SETTING_SPREADSHEET_ID, config.sheets_spreadsheet_id)

    return MarketIntelRuntime(store=store, fetchers=fetchers, publisher=publisher)


def _build_fetchers(config) -> Mapping[str, SeriesFetcher]:
    """Build one gateway per data source with its configured throttle interval.

    Deferred imports: gateway modules are owned by the parallel data agent
    (flat under market_intel/infrastructure/). If a module is missing, the
    resulting ImportError names the adapter explicitly.
    """
    from market_intel.infrastructure.fred_gateway import FredCsvGateway
    from market_intel.infrastructure.yahoo_gateway import YahooChartGateway
    from market_intel.infrastructure.multpl_gateway import MultplGateway
    from market_intel.infrastructure.finra_gateway import FinraGateway
    from market_intel.infrastructure.cnn_gateway import CnnFgGateway
    from market_intel.infrastructure.factset_gateway import FactsetGateway
    from market_intel.infrastructure.damodaran_gateway import DamodaranGateway

    return {
        "fred": FredCsvGateway(min_interval_seconds=config.fred_interval_seconds),
        "yahoo": YahooChartGateway(min_interval_seconds=config.yahoo_interval_seconds),
        "multpl": MultplGateway(min_interval_seconds=config.http_interval_seconds),
        "finra": FinraGateway(min_interval_seconds=config.http_interval_seconds),
        "cnn_fg": CnnFgGateway(min_interval_seconds=config.http_interval_seconds),
        "factset": FactsetGateway(min_interval_seconds=config.http_interval_seconds),
        "damodaran": DamodaranGateway(min_interval_seconds=config.http_interval_seconds),
    }
