from __future__ import annotations

import logging
from collections.abc import Sequence

from signals.application.ports.notifier import NotifierPort
from signals.domain.models import ReportingSourceReview
from signals.infrastructure.storage.sqlite_state_repository import StateStore


def _render_review(review: ReportingSourceReview) -> str:
    candidate_ciks = ", ".join(review.candidate_ciks) if review.candidate_ciks else "not identified"
    return (
        f"- {review.manager_name} ({review.manager_cik}), {review.report_quarter}\n"
        f"  13F notice: {review.notice_accession}; candidate source CIKs: {candidate_ciks}"
    )


def notify_unresolved_reporting_source_reviews(
    store: StateStore,
    notifiers: Sequence[NotifierPort],
    *,
    dry_run: bool,
    logger: logging.Logger | None = None,
) -> None:
    """Alert once for each unresolved 13F reporting-source transition."""

    app_logger = logger or logging.getLogger(__name__)
    if dry_run or not notifiers:
        return

    reviews_reader = getattr(store, "list_unnotified_reporting_source_reviews", None)
    if not callable(reviews_reader):
        return
    reviews = reviews_reader()
    if not reviews:
        return

    subject = "⚠️ Reporting source review required"
    body = (
        "Trend analysis is paused for the affected reporting quarter until the filing source is "
        "explicitly mapped in config/managers.json.\n\n"
        + "\n".join(_render_review(review) for review in reviews)
    )
    for notifier in notifiers:
        notifier.send(subject, body)
    store.mark_reporting_source_reviews_notified(reviews)
    app_logger.info(
        "Reporting source review notification sent",
        extra={"reviews_count": len(reviews)},
    )
