from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tracker.domain.models import ManagerState, Position
from tracker.main import _filter_by_filing_age, _format_local_datetime, process_manager


def _iso_days_ago(days_ago: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days_ago)).isoformat()


def test_filter_by_filing_age_drops_old_filings() -> None:
    filings = [
        {"accession": "new", "filing_date": _iso_days_ago(10)},
        {"accession": "old", "filing_date": _iso_days_ago(220)},
    ]

    filtered = _filter_by_filing_age(filings, 180)

    assert [entry["accession"] for entry in filtered] == ["new"]


def test_format_local_datetime_hides_timezone_suffix() -> None:
    dt = datetime(2026, 2, 6, 19, 28, 3, tzinfo=ZoneInfo("Europe/Kyiv"))
    assert _format_local_datetime(dt) == "2026-02-06 19:28:03"


@dataclass
class _Manager:
    name: str
    cik: str


class _Store:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def get_state(self, cik: str) -> ManagerState | None:
        return None

    def upsert_state(
        self,
        *,
        cik: str,
        name: str,
        last_accession: str | None,
        last_filing_date: str | None,
        last_report_date: str | None,
        last_positions: list[Position] | None,
    ) -> None:
        del cik, name, last_filing_date, last_report_date, last_positions
        self.writes.append(last_accession or "")

    def close(self) -> None:
        return None


class _Client:
    def __init__(self) -> None:
        self.accessions: list[str] = []

    def get_submissions(self, cik: str) -> dict[str, Any]:
        del cik
        return {
            "filings": {
                "recent": {
                    "accessionNumber": ["newest", "older"],
                    "form": ["13F-HR", "13F-HR"],
                    "filingDate": [_iso_days_ago(5), _iso_days_ago(20)],
                    "reportDate": [_iso_days_ago(90), _iso_days_ago(120)],
                }
            }
        }

    def find_information_table_url(self, cik: str, accession: str) -> str:
        self.accessions.append(accession)
        return f"https://example.invalid/{accession}.xml"

    def get_text(self, url: str) -> str:
        return "<xml />"


def test_initial_run_uses_only_latest_filing(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _Manager(name="Test Fund", cik="0000000000")
    store = _Store()
    client = _Client()

    def _fake_parse(_: str) -> list[Position]:
        return [{"cusip": "x"}]

    monkeypatch.setattr("tracker.main.parse_infotable", _fake_parse)

    process_manager(
        manager,
        store,
        client,
        notifiers=[],
        notify_initial=False,
        dry_run=False,
        max_filing_age_days=180,
    )

    assert client.accessions == ["newest"]
    assert store.writes == ["newest"]
