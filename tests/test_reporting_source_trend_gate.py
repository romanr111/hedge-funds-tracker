from __future__ import annotations

from types import SimpleNamespace

from signals.application.use_cases.run_trend_engine import run_trend_engine_for_latest_completed_quarter


class _Store:
    def __init__(self, *, open_quarters: list[str], common_quarters: list[str]) -> None:
        self._open_quarters = frozenset(open_quarters)
        self._common_quarters = list(common_quarters)
        self.review_checks: list[list[str]] = []

    def has_open_reporting_source_reviews(
        self,
        manager_ciks: list[str],
        report_quarters: list[str] | None = None,
    ) -> bool:
        assert manager_ciks == ["0001336528"]
        assert report_quarters is not None, "review gate must be scoped to the target quarter"
        self.review_checks.append(list(report_quarters))
        return any(quarter in self._open_quarters for quarter in report_quarters)

    def list_common_report_quarters(self, ciks: list[str]) -> list[str]:
        return list(self._common_quarters)


def test_open_reporting_source_review_for_target_quarter_blocks_latest_trend_run() -> None:
    store = _Store(open_quarters=["2026Q2"], common_quarters=["2026Q1", "2026Q2"])

    result = run_trend_engine_for_latest_completed_quarter(
        [SimpleNamespace(cik="0001336528", weight=1.0)],
        store,
        dry_run=False,
    )

    assert result.status == "pending_reporting_source_review"
    assert result.report_quarter is None
    assert store.review_checks == [["2026Q2"]]


def test_open_reporting_source_review_for_older_quarter_does_not_block_latest_trend_run() -> None:
    store = _Store(open_quarters=["2025Q4"], common_quarters=["2026Q1"])

    result = run_trend_engine_for_latest_completed_quarter(
        [SimpleNamespace(cik="0001336528", weight=1.0)],
        store,
        dry_run=False,
    )

    assert result.status == "pending_insufficient_history"
    assert store.review_checks == [["2026Q1"], ["2026Q1"]]
