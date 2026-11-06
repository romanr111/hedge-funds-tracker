from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from signals.application.use_cases.notify_quarterly_reports_completion import (
    notify_if_all_reports_published_for_current_quarter,
)
from signals.application.use_cases.notify_reporting_source_reviews import (
    notify_unresolved_reporting_source_reviews,
)
from signals.application.use_cases.notify_trend_analysis_summary import (
    notify_trend_analysis_summary,
)
from signals.application.use_cases.run_trend_engine import run_trend_engine_for_latest_completed_quarter
from signals.application.use_cases.sync_quarter_snapshots import sync_quarter_snapshots
from signals.application.use_cases.track_manager import process_manager
from signals.config import FilingSourcePeriod, ManagerConfig
from signals.infrastructure.storage.sqlite_state_repository import StateStore


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _submissions(records: list[tuple[str, str, str]]) -> dict[str, Any]:
    return {
        "filings": {
            "recent": {
                "accessionNumber": [record[0] for record in records],
                "form": ["13F-HR" for _ in records],
                "filingDate": [record[2] for record in records],
                "reportDate": [record[1] for record in records],
                "acceptanceDateTime": [
                    f"{record[2].replace('-', '')}120000" for record in records
                ],
            }
        }
    }


def _info_table_xml(alpha_value: int, beta_value: int) -> str:
    return (
        "<informationTable>"
        "<infoTable><nameOfIssuer>Alpha</nameOfIssuer><titleOfClass>COM</titleOfClass>"
        "<cusip>111111111</cusip>"
        f"<value>{alpha_value}</value>"
        f"<shrsOrPrnAmt><sshPrnamt>{alpha_value}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
        "</infoTable>"
        "<infoTable><nameOfIssuer>Beta</nameOfIssuer><titleOfClass>COM</titleOfClass>"
        "<cusip>222222222</cusip>"
        f"<value>{beta_value}</value>"
        f"<shrsOrPrnAmt><sshPrnamt>{beta_value}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
        "</infoTable>"
        "</informationTable>"
    )


class _SecFixture:
    def __init__(self) -> None:
        self._submissions = {
            "0000000001": _submissions(
                [
                    ("direct-q2", "2026-06-30", "2026-08-14"),
                    ("direct-q1", "2026-03-31", "2026-05-15"),
                ]
            ),
            "0001336528": _submissions(
                [("pershing-q1", "2026-03-31", "2026-05-15")]
            ),
            "0002026053": _submissions(
                [("parent-q2", "2026-06-30", "2026-08-14")]
            ),
        }
        self._xml = {
            "direct-q1": _info_table_xml(100, 900),
            "direct-q2": _info_table_xml(300, 700),
            "pershing-q1": _info_table_xml(50, 950),
            "parent-q2": _info_table_xml(200, 800),
        }

    def get_submissions(self, cik: str) -> dict[str, Any]:
        return self._submissions[cik]

    def get_all_submissions(self, cik: str) -> dict[str, Any]:
        return self.get_submissions(cik)

    def find_information_table_url(self, cik: str, accession: str) -> str:
        return f"https://example.invalid/{cik}/{accession}.xml"

    def get_text(self, url: str) -> str:
        accession = url.rsplit("/", 1)[-1].removesuffix(".xml")
        return self._xml[accession]


class _CapturingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.messages.append((subject, body))


def test_mapped_successor_reaches_trend_and_summary_notifications_end_to_end(tmp_path: Path) -> None:
    managers = [
        ManagerConfig(name="Direct Fund", cik="0000000001", weight=1.0),
        ManagerConfig(
            name="Pershing Square",
            cik="0001336528",
            weight=1.0,
            filing_source_periods=(
                FilingSourcePeriod(through_quarter="2026Q1", source_ciks=("0001336528",)),
                FilingSourcePeriod(from_quarter="2026Q2", source_ciks=("0002026053",)),
            ),
        ),
    ]
    store = StateStore(tmp_path / "signals.sqlite3")
    client = _SecFixture()
    notifier = _CapturingNotifier()
    symbols_file = tmp_path / "symbols.json"
    symbols_file.write_text('{"111111111":"ALPHA","222222222":"BETA"}')
    try:
        for manager in managers:
            process_manager(
                manager,
                store,
                client,
                [],
                notify_initial=False,
                dry_run=False,
                max_filing_age_days=180,
                now_fn=lambda: NOW,
            )

        assert sync_quarter_snapshots(
            managers,
            store,
            client,
            dry_run=False,
            max_filing_age_days=180,
            now_fn=lambda: NOW,
        ) == 4
        pershing_q2 = store.get_manager_quarter_snapshot("0001336528", "2026Q2")
        assert pershing_q2 is not None
        assert [source.source_cik for source in pershing_q2.source_filings] == ["0002026053"]
        assert store.list_open_reporting_source_reviews() == []

        trend_result = run_trend_engine_for_latest_completed_quarter(managers, store, dry_run=False)
        assert trend_result.status == "computed"
        assert trend_result.report_quarter == "2026Q2"
        assert trend_result.signals_count > 0

        notify_unresolved_reporting_source_reviews(store, [notifier], dry_run=False)
        notify_if_all_reports_published_for_current_quarter(
            managers,
            store,
            [notifier],
            dry_run=False,
            now_fn=lambda: NOW,
        )
        notify_trend_analysis_summary(
            store,
            [notifier],
            dry_run=False,
            trend_status=trend_result.status,
            report_quarter=trend_result.report_quarter,
            manager_ciks=[manager.cik for manager in managers],
            min_conf=0.0,
            symbols_file=str(symbols_file),
            now_fn=lambda: NOW,
        )

        assert [subject for subject, _ in notifier.messages] == [
            "✅ All tracked funds reported for Q2 2026",
            "📈 Signals Trend Analysis 2026Q2",
        ]
        assert "Managers 2/2" in notifier.messages[1][1]
    finally:
        store.close()
