from __future__ import annotations

from datetime import datetime, timezone

from tracker.application.use_cases.notify_quarterly_reports_completion import (
    QUARTERLY_COMPLETION_STATE_CIK,
    notify_if_all_reports_published_for_current_quarter,
)
from tracker.domain.models import Manager, ManagerState, Position


def _fixed_now() -> datetime:
    return datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc)


class _CapturingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.sent.append((subject, body))


class _Store:
    def __init__(self, states: dict[str, ManagerState]) -> None:
        self._states = dict(states)
        self.upserts: list[dict[str, str | None | list[Position]]] = []

    def get_state(self, cik: str) -> ManagerState | None:
        return self._states.get(cik)

    def upsert_state(
        self,
        *,
        cik: str,
        name: str,
        last_accession: str | None,
        last_filing_date: str | None,
        last_report_date: str | None,
        last_positions: list[Position] | None,
        last_notified_accession: str | None,
    ) -> None:
        payload: dict[str, str | None | list[Position]] = {
            "cik": cik,
            "name": name,
            "last_accession": last_accession,
            "last_filing_date": last_filing_date,
            "last_report_date": last_report_date,
            "last_notified_accession": last_notified_accession,
            "last_positions": last_positions,
        }
        self.upserts.append(payload)
        self._states[cik] = ManagerState(
            cik=cik,
            name=name,
            last_accession=last_accession,
            last_filing_date=last_filing_date,
            last_report_date=last_report_date,
            last_positions=last_positions,
            last_notified_accession=last_notified_accession,
        )

    def close(self) -> None:
        return None


def _state(cik: str, name: str, report_date: str, *, last_notified_accession: str | None = None) -> ManagerState:
    return ManagerState(
        cik=cik,
        name=name,
        last_accession="accession",
        last_filing_date=report_date,
        last_report_date=report_date,
        last_positions=[],
        last_notified_accession=last_notified_accession,
    )


def test_all_managers_with_current_quarter_reports_send_notification() -> None:
    managers = [
        Manager(name="Fund A", cik="1"),
        Manager(name="Fund B", cik="2"),
    ]
    store = _Store(
        {
            "1": _state("1", "Fund A", "2025-10-15"),
            "2": _state("2", "Fund B", "2025-12-31"),
        }
    )
    notifier = _CapturingNotifier()

    notify_if_all_reports_published_for_current_quarter(
        managers,
        store,
        [notifier],
        dry_run=False,
        now_fn=_fixed_now,
    )

    assert len(notifier.sent) == 1
    subject, body = notifier.sent[0]
    assert subject == "✅ All tracked funds reported for Q4 2025"
    assert "All tracked funds (2) have published 13F reports for Q4 2025." in body
    assert len(store.upserts) == 1
    assert store.upserts[0]["cik"] == QUARTERLY_COMPLETION_STATE_CIK
    assert store.upserts[0]["last_notified_accession"] == "Q4 2025"


def test_missing_current_quarter_report_skips_notification() -> None:
    managers = [
        Manager(name="Fund A", cik="1"),
        Manager(name="Fund B", cik="2"),
    ]
    store = _Store(
        {
            "1": _state("1", "Fund A", "2025-10-15"),
            "2": _state("2", "Fund B", "2025-09-30"),
        }
    )
    notifier = _CapturingNotifier()

    notify_if_all_reports_published_for_current_quarter(
        managers,
        store,
        [notifier],
        dry_run=False,
        now_fn=_fixed_now,
    )

    assert notifier.sent == []
    assert store.upserts == []


def test_already_notified_for_current_quarter_skips_duplicate() -> None:
    managers = [
        Manager(name="Fund A", cik="1"),
        Manager(name="Fund B", cik="2"),
    ]
    store = _Store(
        {
            "1": _state("1", "Fund A", "2025-10-15"),
            "2": _state("2", "Fund B", "2025-12-31"),
            QUARTERLY_COMPLETION_STATE_CIK: _state(
                QUARTERLY_COMPLETION_STATE_CIK,
                "system",
                "2026-02-20",
                last_notified_accession="Q4 2025",
            ),
        }
    )
    notifier = _CapturingNotifier()

    notify_if_all_reports_published_for_current_quarter(
        managers,
        store,
        [notifier],
        dry_run=False,
        now_fn=_fixed_now,
    )

    assert notifier.sent == []
    assert store.upserts == []
