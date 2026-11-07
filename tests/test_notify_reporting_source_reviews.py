from __future__ import annotations

from pathlib import Path

from signals.application.use_cases.notify_reporting_source_reviews import (
    notify_unresolved_reporting_source_reviews,
)
from signals.infrastructure.storage.sqlite_state_repository import StateStore


class _Notifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.messages.append((subject, body))


def test_unresolved_source_review_is_notified_once_and_keeps_trend_gate_open(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "signals.sqlite3")
    notifier = _Notifier()
    try:
        store.upsert_reporting_source_review(
            manager_cik="0001336528",
            manager_name="Pershing Square",
            report_quarter="2026Q2",
            notice_accession="notice-q2",
            notice_filing_date="2026-08-14",
            candidate_ciks=("0002026053",),
        )

        notify_unresolved_reporting_source_reviews(store, [notifier], dry_run=False)
        notify_unresolved_reporting_source_reviews(store, [notifier], dry_run=False)

        assert len(notifier.messages) == 1
        subject, body = notifier.messages[0]
        assert subject == "⚠️ Reporting source review required"
        assert "Pershing Square" in body
        assert "2026Q2" in body
        assert "0002026053" in body
        assert store.has_open_reporting_source_reviews(["0001336528"], ["2026Q2"])
        assert store.list_unnotified_reporting_source_reviews() == []
    finally:
        store.close()
