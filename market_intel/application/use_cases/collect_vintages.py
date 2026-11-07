"""Use case: collect fresh vintages from all registered fetchers.

Per-series outcomes are isolated and carry a machine-readable skip code: a
CollectorError becomes a source_error skip (the run continues), an unexpected
exception is both recorded in errors and skipped with code internal.
Only rows surviving the store's UNIQUE triple count as inserted; a series
with zero new rows still counts as fetched.
"""
from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from datetime import datetime

from market_intel.application.ports.series_fetcher import SeriesFetcher
from market_intel.application.ports.vintage_repository import VintageRepository
from market_intel.domain.exceptions import CollectorError
from market_intel.domain.models import (
    SKIP_COMPUTED,
    SKIP_INTERNAL,
    SKIP_MANUAL,
    SKIP_NO_ADAPTER,
    SKIP_SOURCE_ERROR,
    CollectionResult,
    SeriesSpec,
    Skip,
)

logger = logging.getLogger(__name__)


def collect_vintages(
    store: VintageRepository,
    fetchers: Mapping[str, SeriesFetcher],
    specs: Mapping[str, SeriesSpec],
    *,
    skip_sources: Collection[str] = (),
    now_utc: datetime,
) -> CollectionResult:
    fetched: list[str] = []
    skipped: list[Skip] = []
    errors: list[str] = []

    for key in sorted(specs):
        spec = specs[key]
        if spec.source in skip_sources:
            skipped.append(Skip(key=key, code=SKIP_MANUAL, detail="джерело вимкнено вручну"))
            logger.info("collect_skip", extra={"series_key": key, "reason": "source disabled"})
            continue
        if spec.is_computed:
            skipped.append(Skip(key=key, code=SKIP_COMPUTED, detail="обчислюється пізніше"))
            logger.info("collect_skip", extra={"series_key": key, "reason": "computed"})
            continue
        fetcher = fetchers.get(spec.source)
        if fetcher is None:
            skipped.append(Skip(key=key, code=SKIP_NO_ADAPTER, detail="немає адаптера для джерела"))
            logger.info("collect_skip", extra={"series_key": key, "reason": "no adapter"})
            continue
        try:
            observations = list(fetcher.fetch(spec, now_utc=now_utc))
        except CollectorError as exc:
            skipped.append(Skip(key=key, code=SKIP_SOURCE_ERROR, detail=str(exc)))
            logger.warning("collect_collector_error", extra={"series_key": key, "reason": str(exc)})
            continue
        except Exception as exc:  # unexpected - record and keep the run alive
            errors.append(f"{key}: {exc!r}")
            skipped.append(
                Skip(key=key, code=SKIP_INTERNAL, detail="помилка виконання зібрання (див. errors)")
            )
            logger.exception("collect_unexpected_error", extra={"series_key": key})
            continue
        inserted = store.append_observations(observations)
        fetched.append(key)
        logger.info(
            "collect_fetched",
            extra={"series_key": key, "rows": len(observations), "inserted": inserted},
        )

    result = CollectionResult(fetched=tuple(fetched), skipped=tuple(skipped), errors=tuple(errors))
    logger.info(
        "collect_summary",
        extra={"fetched": len(result.fetched), "skipped": len(result.skipped), "errors": len(result.errors)},
    )
    return result
