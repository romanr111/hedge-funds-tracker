from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

from signals.application.use_cases.sync_quarter_snapshots import sync_quarter_snapshots
from signals.application.use_cases.track_manager import process_manager
from signals.config import FilingSourcePeriod, ManagerConfig
from signals.domain.exceptions import SubmissionsFetchError
from signals.infrastructure.storage.sqlite_state_repository import StateStore


def test_manager_config_resolves_explicit_successor_by_report_quarter() -> None:
    manager = ManagerConfig(
        name="Pershing Square Capital Management",
        cik="0001336528",
        weight=1.0,
        filing_source_periods=(
            FilingSourcePeriod(
                through_quarter="2026Q1",
                source_ciks=("0001336528",),
            ),
            FilingSourcePeriod(
                from_quarter="2026Q2",
                source_ciks=("0002026053",),
            ),
        ),
    )

    assert manager.source_ciks_for_quarter("2026Q1") == ("0001336528",)
    assert manager.source_ciks_for_quarter("2026Q2") == ("0002026053",)


def _submissions(*, accession: str, report_date: str, filing_date: str) -> dict[str, Any]:
    return {
        "filings": {
            "recent": {
                "accessionNumber": [accession],
                "form": ["13F-HR"],
                "filingDate": [filing_date],
                "reportDate": [report_date],
                "acceptanceDateTime": [f"{filing_date.replace('-', '')}120000"],
            }
        }
    }


def _info_table_xml(cusip: str, value: int) -> str:
    return (
        "<informationTable><infoTable>"
        "<nameOfIssuer>Issuer</nameOfIssuer><titleOfClass>COM</titleOfClass>"
        f"<cusip>{cusip}</cusip><value>{value}</value>"
        "<shrsOrPrnAmt><sshPrnamt>10</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
        "</infoTable></informationTable>"
    )


class _SuccessorClient:
    def get_all_submissions(self, cik: str) -> dict[str, Any]:
        if cik == "0001336528":
            return _submissions(
                accession="old-manager-q1",
                report_date="2026-03-31",
                filing_date="2026-05-15",
            )
        if cik == "0002026053":
            return _submissions(
                accession="parent-q2",
                report_date="2026-06-30",
                filing_date="2026-08-14",
            )
        raise AssertionError(f"Unexpected CIK: {cik}")

    def find_information_table_url(self, cik: str, accession: str) -> str:
        return f"https://example.invalid/{cik}/{accession}.xml"

    def get_text(self, url: str) -> str:
        if url.endswith("old-manager-q1.xml"):
            return _info_table_xml("111111111", 100)
        if url.endswith("parent-q2.xml"):
            return _info_table_xml("222222222", 200)
        raise AssertionError(f"Unexpected URL: {url}")


def test_sync_uses_approved_successor_and_preserves_canonical_manager_identity(tmp_path: Path) -> None:
    manager = ManagerConfig(
        name="Pershing Square Capital Management",
        cik="0001336528",
        weight=1.0,
        filing_source_periods=(
            FilingSourcePeriod(
                through_quarter="2026Q1",
                source_ciks=("0001336528",),
            ),
            FilingSourcePeriod(
                from_quarter="2026Q2",
                source_ciks=("0002026053",),
            ),
        ),
    )
    store = StateStore(tmp_path / "signals.sqlite3")
    try:
        rows = sync_quarter_snapshots(
            [manager],
            store,
            _SuccessorClient(),
            dry_run=False,
            now_fn=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc),
        )

        assert rows == 2
        q2_snapshot = store.get_manager_quarter_snapshot("0001336528", "2026Q2")
        assert q2_snapshot is not None
        assert q2_snapshot.cik == "0001336528"
        assert q2_snapshot.accession == "parent-q2"
        assert [source.source_cik for source in q2_snapshot.source_filings] == ["0002026053"]
        assert [source.accession for source in q2_snapshot.source_filings] == ["parent-q2"]
    finally:
        store.close()


class _NoticeClient:
    def get_all_submissions(self, cik: str) -> dict[str, Any]:
        assert cik == "0001336528"
        return {
            "filings": {
                "recent": {
                    "accessionNumber": ["notice-q2"],
                    "form": ["13F-NT"],
                    "filingDate": ["2026-08-14"],
                    "reportDate": ["2026-06-30"],
                    "acceptanceDateTime": ["20260814120000"],
                    "primaryDocument": ["primary_doc.xml"],
                }
            }
        }

    def find_primary_document_url(self, cik: str, accession: str, primary_document: str | None) -> str:
        assert (cik, accession, primary_document) == ("0001336528", "notice-q2", "primary_doc.xml")
        return "https://example.invalid/notice-q2.xml"

    def get_text(self, url: str) -> str:
        assert url.endswith("notice-q2.xml")
        return (
            "<edgarSubmission><formData><form13FInfo><otherManagersInfo>"
            "<otherManager><cik>2026053</cik></otherManager>"
            "</otherManagersInfo></form13FInfo></formData></edgarSubmission>"
        )


def test_unmapped_notice_creates_open_review_with_candidate_cik(tmp_path: Path) -> None:
    manager = ManagerConfig(name="Fund", cik="0001336528", weight=1.0)
    store = StateStore(tmp_path / "signals.sqlite3")
    try:
        rows = sync_quarter_snapshots(
            [manager],
            store,
            _NoticeClient(),
            dry_run=False,
            now_fn=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc),
        )

        assert rows == 0
        reviews = store.list_open_reporting_source_reviews()
        assert len(reviews) == 1
        assert reviews[0].manager_cik == "0001336528"
        assert reviews[0].report_quarter == "2026Q2"
        assert reviews[0].candidate_ciks == ("0002026053",)
        assert store.has_open_reporting_source_reviews([manager.cik], ["2026Q2"])
    finally:
        store.close()


class _CompositeClient:
    def get_all_submissions(self, cik: str) -> dict[str, Any]:
        if cik == "0000000001":
            return _submissions(
                accession="source-a-q2",
                report_date="2026-06-30",
                filing_date="2026-08-14",
            )
        if cik == "0000000002":
            return _submissions(
                accession="source-b-q2",
                report_date="2026-06-30",
                filing_date="2026-08-14",
            )
        raise AssertionError(f"Unexpected CIK: {cik}")

    def find_information_table_url(self, cik: str, accession: str) -> str:
        return f"https://example.invalid/{cik}/{accession}.xml"

    def get_text(self, url: str) -> str:
        if url.endswith("source-a-q2.xml"):
            return _info_table_xml("111111111", 100)
        if url.endswith("source-b-q2.xml"):
            return _info_table_xml("111111111", 200)
        raise AssertionError(f"Unexpected URL: {url}")


def test_sync_aggregates_only_explicit_sum_composite_sources(tmp_path: Path) -> None:
    manager = ManagerConfig(
        name="Composite Fund",
        cik="0000000001",
        weight=1.0,
        filing_source_periods=(
            FilingSourcePeriod(
                from_quarter="2026Q2",
                source_ciks=("0000000001", "0000000002"),
                overlap_policy="sum",
            ),
        ),
    )
    store = StateStore(tmp_path / "signals.sqlite3")
    try:
        rows = sync_quarter_snapshots(
            [manager],
            store,
            _CompositeClient(),
            dry_run=False,
            now_fn=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc),
        )

        assert rows == 1
        snapshot = store.get_manager_quarter_snapshot(manager.cik, "2026Q2")
        assert snapshot is not None
        assert snapshot.source_form == "COMPOSITE"
        assert snapshot.positions[0]["value"] == 300
        assert [source.source_cik for source in snapshot.source_filings] == ["0000000001", "0000000002"]
    finally:
        store.close()


def test_existing_snapshot_database_migrates_to_source_provenance_fallback(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE manager_quarter_snapshot (
                cik TEXT NOT NULL,
                manager_name TEXT NOT NULL,
                report_quarter TEXT NOT NULL,
                report_date TEXT,
                filing_date TEXT,
                acceptance_datetime TEXT,
                accession TEXT NOT NULL,
                source_form TEXT NOT NULL,
                positions_json TEXT NOT NULL,
                aum_value_k INTEGER NOT NULL,
                positions_count INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (cik, report_quarter)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO manager_quarter_snapshot VALUES
            ('0000000001', 'Legacy Fund', '2026Q1', '2026-03-31', '2026-05-15',
             '20260515120000', 'legacy-q1', '13F-HR', '[]', 0, 0, '2026-05-15T12:00:00+00:00')
            """
        )
        connection.commit()
    finally:
        connection.close()

    store = StateStore(db_path)
    try:
        snapshot = store.get_manager_quarter_snapshot("0000000001", "2026Q1")
        assert snapshot is not None
        assert snapshot.source_filings[0].source_cik == "0000000001"
        assert snapshot.source_filings[0].accession == "legacy-q1"
    finally:
        store.close()


class _CurrentSuccessorClient:
    def __init__(self) -> None:
        self.info_source_ciks: list[str] = []

    def get_submissions(self, cik: str) -> dict[str, Any]:
        if cik == "0001336528":
            return _submissions(
                accession="old-manager-q1",
                report_date="2026-03-31",
                filing_date="2026-05-15",
            )
        if cik == "0002026053":
            return _submissions(
                accession="parent-q2",
                report_date="2026-06-30",
                filing_date="2026-08-14",
            )
        raise AssertionError(f"Unexpected CIK: {cik}")

    def find_information_table_url(self, cik: str, accession: str) -> str:
        self.info_source_ciks.append(cik)
        return f"https://example.invalid/{accession}.xml"

    def get_text(self, url: str) -> str:
        assert url.endswith("parent-q2.xml")
        return _info_table_xml("222222222", 200)


def test_position_tracking_uses_approved_successor_source(tmp_path: Path) -> None:
    manager = ManagerConfig(
        name="Pershing Square",
        cik="0001336528",
        weight=1.0,
        filing_source_periods=(
            FilingSourcePeriod(through_quarter="2026Q1", source_ciks=("0001336528",)),
            FilingSourcePeriod(from_quarter="2026Q2", source_ciks=("0002026053",)),
        ),
    )
    store = StateStore(tmp_path / "signals.sqlite3")
    client = _CurrentSuccessorClient()
    try:
        store.upsert_state(
            cik=manager.cik,
            name=manager.name,
            last_accession="old-manager-q1",
            last_filing_date="2026-05-15",
            last_report_date="2026-03-31",
            last_positions=[],
            last_notified_accession="old-manager-q1",
        )

        process_manager(
            manager,
            store,
            client,
            [],
            notify_initial=False,
            dry_run=False,
            max_filing_age_days=180,
            now_fn=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc),
        )

        state = store.get_state(manager.cik)
        assert state is not None
        assert state.last_accession == "parent-q2"
        assert client.info_source_ciks == ["0002026053"]
    finally:
        store.close()


class _CapturingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.messages.append((subject, body))


class _CurrentCompositeClient:
    def get_submissions(self, cik: str) -> dict[str, Any]:
        if cik == "0000000001":
            return _submissions(
                accession="source-a-q2",
                report_date="2026-06-30",
                filing_date="2026-08-14",
            )
        if cik == "0000000002":
            return _submissions(
                accession="source-b-q2",
                report_date="2026-06-30",
                filing_date="2026-08-14",
            )
        raise AssertionError(f"Unexpected CIK: {cik}")

    def find_information_table_url(self, cik: str, accession: str) -> str:
        return f"https://example.invalid/{cik}/{accession}.xml"

    def get_text(self, url: str) -> str:
        if url.endswith("source-a-q2.xml"):
            return _info_table_xml("111111111", 100)
        if url.endswith("source-b-q2.xml"):
            return _info_table_xml("111111111", 200)
        raise AssertionError(f"Unexpected URL: {url}")


def test_position_tracking_aggregates_composite_sources_into_one_notification(tmp_path: Path) -> None:
    manager = ManagerConfig(
        name="Composite Fund",
        cik="0000000001",
        weight=1.0,
        filing_source_periods=(
            FilingSourcePeriod(
                from_quarter="2026Q2",
                source_ciks=("0000000001", "0000000002"),
                overlap_policy="sum",
            ),
        ),
    )
    store = StateStore(tmp_path / "signals.sqlite3")
    notifier = _CapturingNotifier()
    try:
        store.upsert_state(
            cik=manager.cik,
            name=manager.name,
            last_accession="old-manager-q1",
            last_filing_date="2026-05-15",
            last_report_date="2026-03-31",
            last_positions=[{"cusip": "999999999", "value": 1, "shares": 1}],
            last_notified_accession="old-manager-q1",
        )

        process_manager(
            manager,
            store,
            _CurrentCompositeClient(),
            [notifier],
            notify_initial=False,
            dry_run=False,
            max_filing_age_days=180,
            now_fn=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc),
        )

        state = store.get_state(manager.cik)
        assert state is not None
        assert state.last_accession is not None and state.last_accession.startswith("composite:")
        assert len(notifier.messages) == 1
        assert state.last_positions is not None
        assert state.last_positions[0]["value"] == 300
    finally:
        store.close()


class _CompositeAmendmentClient:
    def get_submissions(self, cik: str) -> dict[str, Any]:
        if cik == "0000000001":
            return {
                "filings": {
                    "recent": {
                        "accessionNumber": ["source-a-q2-amend", "q1-hr-old"],
                        "form": ["13F-HR", "13F-HR"],
                        "filingDate": ["2026-08-15", "2026-05-15"],
                        "reportDate": ["2026-06-30", "2026-03-31"],
                        "acceptanceDateTime": ["20260815120000", "20260515120000"],
                    }
                }
            }
        if cik == "0000000002":
            return _submissions(
                accession="source-b-q2-amend",
                report_date="2026-06-30",
                filing_date="2026-08-15",
            )
        raise AssertionError(f"Unexpected CIK: {cik}")

    def find_information_table_url(self, cik: str, accession: str) -> str:
        return f"https://example.invalid/{cik}/{accession}.xml"

    def get_text(self, url: str) -> str:
        if url.endswith("source-a-q2-amend.xml"):
            return _info_table_xml("111111111", 100)
        if url.endswith("source-b-q2-amend.xml"):
            return _info_table_xml("111111111", 200)
        if url.endswith("q1-hr-old.xml"):
            return _info_table_xml("999999999", 100)
        raise AssertionError(f"Unexpected URL: {url}")


def test_position_tracking_does_not_renotify_older_quarters_after_composite_hash_change(
    tmp_path: Path,
) -> None:
    manager = ManagerConfig(
        name="Composite Fund",
        cik="0000000001",
        weight=1.0,
        filing_source_periods=(
            FilingSourcePeriod(through_quarter="2026Q1", source_ciks=("0000000001",)),
            FilingSourcePeriod(
                from_quarter="2026Q2",
                source_ciks=("0000000001", "0000000002"),
                overlap_policy="sum",
            ),
        ),
    )
    store = StateStore(tmp_path / "signals.sqlite3")
    notifier = _CapturingNotifier()
    try:
        store.upsert_state(
            cik=manager.cik,
            name=manager.name,
            last_accession="composite:stale-old",
            last_filing_date="2026-08-14",
            last_report_date="2026-06-30",
            last_positions=[{"cusip": "111111111", "value": 300, "shares": 10}],
            last_notified_accession="composite:stale-old",
        )

        process_manager(
            manager,
            store,
            _CompositeAmendmentClient(),
            [notifier],
            notify_initial=False,
            dry_run=False,
            max_filing_age_days=180,
            now_fn=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc),
        )

        # The Q2 amendment (new composite hash) is reprocessed silently, but the older
        # Q1 batch must never resurface: composite accession churn must not re-notify it.
        assert notifier.messages == []
        state = store.get_state(manager.cik)
        assert state is not None
        assert state.last_accession is not None and state.last_accession.startswith("composite:")
        assert state.last_positions is not None
        assert state.last_positions[0]["value"] == 300
    finally:
        store.close()


class _NoticeWithFailedSourceClient:
    def get_all_submissions(self, cik: str) -> dict[str, Any]:
        if cik == "0000000002":
            raise SubmissionsFetchError("EDGAR submissions feed unavailable")
        assert cik == "0000000001"
        return {
            "filings": {
                "recent": {
                    "accessionNumber": ["notice-q2"],
                    "form": ["13F-NT"],
                    "filingDate": ["2026-08-14"],
                    "reportDate": ["2026-06-30"],
                    "acceptanceDateTime": ["20260814120000"],
                    "primaryDocument": ["primary_doc.xml"],
                }
            }
        }

    def find_primary_document_url(self, cik: str, accession: str, primary_document: str | None) -> str:
        assert (cik, accession, primary_document) == ("0000000001", "notice-q2", "primary_doc.xml")
        return "https://example.invalid/notice-q2.xml"

    def get_text(self, url: str) -> str:
        assert url.endswith("notice-q2.xml")
        return (
            "<edgarSubmission><formData><form13FInfo><otherManagersInfo>"
            "<otherManager><cik>0000000002</cik></otherManager>"
            "</otherManagersInfo></form13FInfo></formData></edgarSubmission>"
        )


def test_sync_does_not_create_review_when_source_submissions_fetch_failed(tmp_path: Path) -> None:
    manager = ManagerConfig(
        name="Composite Fund",
        cik="0000000001",
        weight=1.0,
        filing_source_periods=(
            FilingSourcePeriod(
                from_quarter="2026Q2",
                source_ciks=("0000000001", "0000000002"),
                overlap_policy="sum",
            ),
        ),
    )
    store = StateStore(tmp_path / "signals.sqlite3")
    try:
        rows = sync_quarter_snapshots(
            [manager],
            store,
            _NoticeWithFailedSourceClient(),
            dry_run=False,
            now_fn=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc),
        )

        assert rows == 0
        assert store.list_open_reporting_source_reviews() == []
    finally:
        store.close()
