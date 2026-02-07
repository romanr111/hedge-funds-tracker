from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tracker import config as _config  # noqa: F401  # loads .env into os.environ for optional integration check
from tracker.domain.models import ManagerState, Position
from tracker.main import _filter_by_filing_age, _format_local_datetime, process_manager
from tracker.notifiers import build_notifiers


def _iso_days_ago(days_ago: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days_ago)).isoformat()


def _quarter_label(iso_date: str) -> str:
    dt = datetime.strptime(iso_date, "%Y-%m-%d").date()
    quarter = ((dt.month - 1) // 3) + 1
    return f"Q{quarter} {dt.year}"


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
        del cik
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


class _StoreWithExistingState:
    def __init__(self, state: ManagerState) -> None:
        self._state = state
        self.writes: list[dict[str, Any]] = []

    def get_state(self, cik: str) -> ManagerState | None:
        del cik
        return self._state

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
        payload: dict[str, Any] = {
            "cik": cik,
            "name": name,
            "last_accession": last_accession,
            "last_filing_date": last_filing_date,
            "last_report_date": last_report_date,
            "last_positions": last_positions,
        }
        self.writes.append(payload)
        self._state = ManagerState(
            cik=cik,
            name=name,
            last_accession=last_accession,
            last_filing_date=last_filing_date,
            last_report_date=last_report_date,
            last_positions=last_positions,
        )

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
        del cik
        self.accessions.append(accession)
        return f"https://example.invalid/{accession}.xml"

    def get_text(self, url: str) -> str:
        del url
        return "<xml />"


class _ClientWithNewFiling:
    def __init__(
        self,
        *,
        new_filing_date: str,
        known_filing_date: str,
        new_report_date: str,
        known_report_date: str,
        new_report_xml: str,
    ) -> None:
        self._new_filing_date = new_filing_date
        self._known_filing_date = known_filing_date
        self._new_report_date = new_report_date
        self._known_report_date = known_report_date
        self._new_report_xml = new_report_xml
        self.accessions: list[str] = []

    def get_submissions(self, cik: str) -> dict[str, Any]:
        del cik
        return {
            "filings": {
                "recent": {
                    "accessionNumber": ["new-report", "known-old"],
                    "form": ["13F-HR", "13F-HR"],
                    "filingDate": [self._new_filing_date, self._known_filing_date],
                    "reportDate": [self._new_report_date, self._known_report_date],
                }
            }
        }

    def find_information_table_url(self, cik: str, accession: str) -> str:
        del cik
        self.accessions.append(accession)
        return f"https://example.invalid/{accession}.xml"

    def get_text(self, url: str) -> str:
        if url.endswith("/new-report.xml"):
            return self._new_report_xml
        raise AssertionError(f"Unexpected URL: {url}")


class _CapturingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.messages.append((subject, body))


def _fake_information_table_xml(positions: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for position in positions:
        blocks.append(
            (
                "<infoTable>"
                f"<nameOfIssuer>{position['name']}</nameOfIssuer>"
                f"<titleOfClass>{position['title']}</titleOfClass>"
                f"<cusip>{position['cusip']}</cusip>"
                f"<value>{position['value']}</value>"
                "<shrsOrPrnAmt>"
                f"<sshPrnamt>{position['shares']}</sshPrnamt>"
                "<sshPrnamtType>SH</sshPrnamtType>"
                "</shrsOrPrnAmt>"
                "</infoTable>"
            )
        )
    return f"<informationTable>{''.join(blocks)}</informationTable>"


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


def test_new_filing_sends_notification_and_updates_tracking_state() -> None:
    manager = _Manager(name="Test Fund", cik="0000000000")
    new_filing_date = _iso_days_ago(4)
    new_report_date = _iso_days_ago(95)
    known_filing_date = _iso_days_ago(30)
    known_report_date = _iso_days_ago(120)

    previous_positions: list[Position] = [
        {
            "name": "Alpha Corp",
            "title": "COM",
            "cusip": "000000001",
            "value": 100,
            "shares": 10,
        }
    ]
    state = ManagerState(
        cik=manager.cik,
        name=manager.name,
        last_accession="known-old",
        last_filing_date=known_filing_date,
        last_report_date=known_report_date,
        last_positions=previous_positions,
    )

    new_report_xml = _fake_information_table_xml(
        [
            {
                "name": "Alpha Corp",
                "title": "COM",
                "cusip": "000000001",
                "value": 150,
                "shares": 15,
            },
            {
                "name": "Beta Holdings",
                "title": "COM",
                "cusip": "000000002",
                "value": 75,
                "shares": 20,
            },
        ]
    )
    store = _StoreWithExistingState(state)
    client = _ClientWithNewFiling(
        new_filing_date=new_filing_date,
        known_filing_date=known_filing_date,
        new_report_date=new_report_date,
        known_report_date=known_report_date,
        new_report_xml=new_report_xml,
    )
    notifier = _CapturingNotifier()

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    require_real_telegram = os.environ.get("REQUIRE_REAL_TELEGRAM_TEST") == "1"

    notifiers = [notifier]
    if require_real_telegram:
        assert telegram_token and telegram_chat_id, (
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to run with real Telegram delivery."
        )
        real_notifier = build_notifiers(
            ["telegram"],
            telegram_bot_token=telegram_token,
            telegram_chat_id=telegram_chat_id,
        )[0]
        notifiers = [real_notifier, notifier]

    process_manager(
        manager,
        store,
        client,
        notifiers=notifiers,
        notify_initial=False,
        dry_run=False,
        max_filing_age_days=180,
    )

    assert client.accessions == ["new-report"]
    assert len(notifier.messages) == 1

    subject, body = notifier.messages[0]
    assert subject == "🔥 Test Fund 13F update"
    assert f"Period: {_quarter_label(new_report_date)}" in body
    assert f"Filed {new_filing_date}." in body
    assert "Accession" not in body
    assert "Report date" not in body
    assert body.index("Period:") < body.index("Filed")
    assert "New positions (1):" in body
    assert "Increased positions (1):" in body

    assert len(store.writes) == 1
    saved = store.writes[0]
    assert saved["last_accession"] == "new-report"
    assert saved["last_filing_date"] == new_filing_date
    assert saved["last_report_date"] == new_report_date
    assert {position.get("cusip") for position in (saved["last_positions"] or [])} == {"000000001", "000000002"}
