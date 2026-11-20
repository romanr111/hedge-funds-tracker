"""Runtime composition tests: persisted spreadsheet id and the real collect seam."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from market_intel.application.use_cases.collect_vintages import collect_vintages
from market_intel.composition import build_runtime
from market_intel.config import MarketIntelConfig
from market_intel.domain.models import SeriesObservation, SeriesSpec
from market_intel.infrastructure.export.gog_sheets_publisher import (
    SETTING_SPREADSHEET_ID,
)
from market_intel.infrastructure.storage.vintage_store import VintageStore
from market_intel.registries import SERIES_SPECS


def _config(db_path: Path, spreadsheet_id: str) -> MarketIntelConfig:
    return MarketIntelConfig(
        db_path=db_path,
        sheets_spreadsheet_id=spreadsheet_id,
        sheets_folder_id="",
        fred_interval_seconds=1.0,
        yahoo_interval_seconds=1.0,
        http_interval_seconds=1.0,
    )


def test_env_seed_does_not_override_persisted_spreadsheet_id(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite3"

    first = build_runtime(_config(db_path, "ENV_FIRST"), dry_run=False)
    try:
        assert first.store.get_setting(SETTING_SPREADSHEET_ID) == "ENV_FIRST"
    finally:
        first.store.close()

    # A later env value must not reclaim the setting: after the first publish
    # the store is the single source of truth, so a replacement id survives.
    second = build_runtime(_config(db_path, "ENV_SECOND"), dry_run=False)
    try:
        assert second.store.get_setting(SETTING_SPREADSHEET_ID) == "ENV_FIRST"
    finally:
        second.store.close()


class _StaticFetcher:
    def fetch(
        self, spec: SeriesSpec, *, now_utc: datetime
    ) -> list[SeriesObservation]:
        return [
            SeriesObservation(
                series_id=spec.key,
                obs_date=date(2026, 11, 5),
                value=3.32,
                source=spec.source,
                fetched_at=now_utc,
            )
        ]


def test_collect_vintages_persists_real_registry_spec_into_store(
    tmp_path: Path,
) -> None:
    store = VintageStore(tmp_path / "collect.sqlite3")
    fetched_at = datetime(2026, 11, 7, 7, tzinfo=timezone.utc)
    try:
        result = collect_vintages(
            store,
            {"fred": _StaticFetcher()},
            {"hy_oas": SERIES_SPECS["hy_oas"]},
            skip_sources=(),
            now_utc=fetched_at,
        )

        assert result.fetched == ("hy_oas",)
        history = store.read_history("hy_oas")
        assert [(o.obs_date, o.value) for o in history] == [
            (date(2026, 11, 5), 3.32)
        ]
        assert history[0].fetched_at == fetched_at
    finally:
        store.close()
