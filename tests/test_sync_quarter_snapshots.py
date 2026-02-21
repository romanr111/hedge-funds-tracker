from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracker.application.use_cases.sync_quarter_snapshots import sync_quarter_snapshots
from tracker.domain.models import Manager
from tracker.infrastructure.storage.sqlite_state_repository import StateStore


def _info_table_xml(values: list[tuple[str, int]]) -> str:
    blocks: list[str] = []
    for idx, (cusip, value) in enumerate(values, start=1):
        blocks.append(
            (
                "<infoTable>"
                f"<nameOfIssuer>Issuer {idx}</nameOfIssuer>"
                "<titleOfClass>COM</titleOfClass>"
                f"<cusip>{cusip}</cusip>"
                f"<value>{value}</value>"
                "<shrsOrPrnAmt><sshPrnamt>10</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
                "</infoTable>"
            )
        )
    return f"<informationTable>{''.join(blocks)}</informationTable>"


class _Client:
    def __init__(self) -> None:
        self.submissions_calls: list[str] = []
        self.url_calls: list[str] = []

    def get_submissions(self, cik: str) -> dict[str, Any]:
        self.submissions_calls.append(cik)
        return {
            "filings": {
                "recent": {
                    "accessionNumber": ["old-accession", "new-accession"],
                    "form": ["13F-HR", "13F-HR/A"],
                    "filingDate": ["2026-02-10", "2026-02-11"],
                    "reportDate": ["2025-12-31", "2025-12-31"],
                    "acceptanceDateTime": ["20260210120000", "20260211120000"],
                }
            }
        }

    def find_information_table_url(self, cik: str, accession: str) -> str:
        del cik
        self.url_calls.append(accession)
        return f"https://example.invalid/{accession}.xml"

    def get_text(self, url: str) -> str:
        if url.endswith("/new-accession.xml"):
            return _info_table_xml([("111111111", 100), ("111111111", 200), ("222222222", 400)])
        if url.endswith("/old-accession.xml"):
            return _info_table_xml([("333333333", 10)])
        raise AssertionError(f"Unexpected URL: {url}")


def test_sync_quarter_snapshots_selects_latest_and_aggregates_duplicates(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        manager = Manager(name="Fund A", cik="0000000001")
        client = _Client()

        rows = sync_quarter_snapshots([manager], store, client, max_quarters=4, dry_run=False)
        assert rows == 1
        assert client.url_calls == ["new-accession"]

        snapshot = store.get_manager_quarter_snapshot("0000000001", "2025Q4")
        assert snapshot is not None
        assert snapshot.accession == "new-accession"
        assert snapshot.positions_count == 2
        assert snapshot.aum_value_k == 700

        by_cusip = {position["cusip"]: position for position in snapshot.positions}
        assert by_cusip["111111111"]["value"] == 300
        assert by_cusip["222222222"]["value"] == 400

        # Re-running should keep one logical row for the quarter (idempotent upsert).
        rows_second = sync_quarter_snapshots([manager], store, client, max_quarters=4, dry_run=False)
        assert rows_second == 1
        all_rows = store.list_snapshots_for_quarters(["2025Q4"], ["0000000001"])
        assert len(all_rows) == 1
    finally:
        store.close()


class _ClientWithOldFiling:
    def get_submissions(self, cik: str) -> dict[str, Any]:
        del cik
        return {
            "filings": {
                "recent": {
                    "accessionNumber": ["very-old", "recent"],
                    "form": ["13F-HR", "13F-HR"],
                    "filingDate": ["2024-01-10", "2026-02-11"],
                    "reportDate": ["2023-12-31", "2025-12-31"],
                    "acceptanceDateTime": ["20240110120000", "20260211120000"],
                }
            }
        }

    def find_information_table_url(self, cik: str, accession: str) -> str:
        del cik
        return f"https://example.invalid/{accession}.xml"

    def get_text(self, url: str) -> str:
        if url.endswith("/recent.xml"):
            return _info_table_xml([("111111111", 100)])
        if url.endswith("/very-old.xml"):
            return _info_table_xml([("999999999", 100)])
        raise AssertionError(f"Unexpected URL: {url}")


def test_sync_quarter_snapshots_applies_filing_age_filter(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "tracker.sqlite3")
    try:
        manager = Manager(name="Fund A", cik="0000000001")
        client = _ClientWithOldFiling()

        rows = sync_quarter_snapshots(
            [manager],
            store,
            client,
            max_quarters=4,
            max_filing_age_days=180,
            dry_run=False,
            now_fn=lambda: datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc),
        )
        assert rows == 1
        assert store.get_manager_quarter_snapshot("0000000001", "2025Q4") is not None
        assert store.get_manager_quarter_snapshot("0000000001", "2023Q4") is None
    finally:
        store.close()
