from __future__ import annotations

from pathlib import Path

import pytest
from tracker.application.use_cases.export_trend_summary import export_trend_summary_if_changed
from tracker.infrastructure.export.xlsx_exporter import (
    TrendSummaryWorkbookData,
    TrendTable,
)


def _sample_data(fingerprint: str = "fp1") -> TrendSummaryWorkbookData:
    return TrendSummaryWorkbookData(
        report_quarter="2025Q4",
        view_mode="shortlist",
        min_conf=0.45,
        limit=8,
        top_buy=TrendTable(title="Top Buy Ideas", headers=["A"], rows=[["1"]]),
        top_sell=TrendTable(title="Top Reduction Trends", headers=["A"], rows=[["2"]]),
        reversals=None,
        portfolio_value_trend=None,
        content_fingerprint=fingerprint,
    )


def test_writes_file_when_not_exists(tmp_path: Path) -> None:
    path = tmp_path / "trend_summary_2025Q4.xlsx"
    data = _sample_data("fp1")
    result = export_trend_summary_if_changed(path, data, dry_run=False)

    assert result.status == "written"
    assert result.path == path
    assert path.exists()


def test_skips_when_fingerprint_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "trend_summary_2025Q4.xlsx"
    data = _sample_data("fp1")
    export_trend_summary_if_changed(path, data, dry_run=False)

    result = export_trend_summary_if_changed(path, data, dry_run=False)
    assert result.status == "skipped_unchanged"
    assert result.path == path


def test_writes_when_fingerprint_changes(tmp_path: Path) -> None:
    path = tmp_path / "trend_summary_2025Q4.xlsx"
    data1 = _sample_data("fp1")
    export_trend_summary_if_changed(path, data1, dry_run=False)

    data2 = _sample_data("fp2")
    result = export_trend_summary_if_changed(path, data2, dry_run=False)
    assert result.status == "written"


def test_skips_on_dry_run(tmp_path: Path) -> None:
    path = tmp_path / "trend_summary_2025Q4.xlsx"
    data = _sample_data("fp1")
    result = export_trend_summary_if_changed(path, data, dry_run=True)

    assert result.status == "skipped_dry_run"
    assert result.path is None
    assert not path.exists()


def test_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "trend_summary_2025Q4.xlsx"
    data = _sample_data("fp1")
    result = export_trend_summary_if_changed(path, data, dry_run=False)

    assert result.status == "written"
    assert path.exists()
