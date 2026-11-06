from __future__ import annotations

from types import SimpleNamespace

from signals.application.use_cases.run_trend_engine import run_trend_engine_for_latest_completed_quarter


class _ReviewBlockedStore:
    def has_open_reporting_source_reviews(
        self,
        manager_ciks: list[str],
        report_quarters: list[str] | None = None,
    ) -> bool:
        assert manager_ciks == ["0001336528"]
        assert report_quarters is None
        return True

    def list_common_report_quarters(self, ciks: list[str]) -> list[str]:
        raise AssertionError(f"Trend selection must stop before using stale common quarters: {ciks}")


def test_open_reporting_source_review_blocks_latest_trend_run() -> None:
    result = run_trend_engine_for_latest_completed_quarter(
        [SimpleNamespace(cik="0001336528", weight=1.0)],
        _ReviewBlockedStore(),
        dry_run=False,
    )

    assert result.status == "pending_reporting_source_review"
    assert result.report_quarter is None
