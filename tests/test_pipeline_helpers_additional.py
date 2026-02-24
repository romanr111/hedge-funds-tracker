from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracker.application.use_cases.generate_kpi_report import _quality_status, generate_kpi_report
from tracker.application.use_cases.run_quarterly_pipeline import (
    _default_signal_available_day,
    _signal_available_day,
    run_quarterly_pipeline,
)
from tracker.config import PipelineConfig
from tracker.domain.portfolio import PipelineKPI


def _pipeline(tmp_path: Path, *, min_oos_quarters: int = 1) -> PipelineConfig:
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{}")
    return PipelineConfig(
        top_k=2,
        min_conf=0.4,
        min_oos_quarters=min_oos_quarters,
        hold_quarters=1,
        position_cap=0.7,
        sector_cap=0.8,
        adv20_usd_min=3_000_000,
        price_min=5,
        cost_bps_per_side=10,
        report_dir=tmp_path / "reports",
        symbol_metadata_file=metadata,
    )


def test_default_signal_available_day_validates_and_maps_quarters() -> None:
    assert _default_signal_available_day("2025Q1") == date(2025, 5, 15)
    assert _default_signal_available_day("2025Q2") == date(2025, 8, 14)
    assert _default_signal_available_day("2025Q3") == date(2025, 11, 14)
    assert _default_signal_available_day("2025Q4") == date(2026, 2, 14)
    with pytest.raises(ValueError, match="Invalid report quarter"):
        _default_signal_available_day("bad")


def test_signal_available_day_uses_default_and_max_snapshot_filing() -> None:
    class _Store:
        def __init__(self, rows: list[SimpleNamespace]) -> None:
            self._rows = rows

        def list_snapshots_for_quarter(self, quarter: str) -> list[SimpleNamespace]:
            del quarter
            return self._rows

    assert _signal_available_day(store=_Store([]), quarter="2025Q1") == date(2025, 5, 15)
    assert _signal_available_day(
        store=_Store([SimpleNamespace(filing_date="bad"), SimpleNamespace(filing_date=None)]),
        quarter="2025Q1",
    ) == date(2025, 5, 15)
    assert _signal_available_day(
        store=_Store([SimpleNamespace(filing_date="2025-05-10"), SimpleNamespace(filing_date="2025-05-12")]),
        quarter="2025Q1",
    ) == date(2025, 5, 13)


def test_quality_status_returns_insufficient_pass_watch_and_fail() -> None:
    overall_pass = [
        PipelineKPI(metric="excess_return_vs_spy", scope="overall", scope_key=None, value=0.10),
        PipelineKPI(metric="mean_ic", scope="overall", scope_key=None, value=0.05),
        PipelineKPI(metric="hit_rate_vs_spy", scope="overall", scope_key=None, value=0.55),
        PipelineKPI(metric="max_drawdown", scope="overall", scope_key=None, value=0.01),
    ]
    quarter_kpis = [
        PipelineKPI(metric="x", scope="quarter", scope_key="2024Q1", value=1.0),
        PipelineKPI(metric="x", scope="quarter", scope_key="2024Q2", value=1.0),
    ]
    return_rows = [{"benchmark_return": 0.05}, {"benchmark_return": -0.02}]

    status, reasons = _quality_status(
        min_oos_quarters=3,
        overall_kpis=overall_pass,
        quarter_kpis=quarter_kpis,
        return_rows=return_rows,
    )
    assert status == "INSUFFICIENT_SAMPLE"
    assert reasons == ["oos_quarters_below_3"]

    status, reasons = _quality_status(
        min_oos_quarters=2,
        overall_kpis=overall_pass,
        quarter_kpis=quarter_kpis,
        return_rows=return_rows,
    )
    assert status == "PASS"
    assert reasons == []

    overall_watch = [
        PipelineKPI(metric="excess_return_vs_spy", scope="overall", scope_key=None, value=-0.01),
        PipelineKPI(metric="mean_ic", scope="overall", scope_key=None, value=-0.10),
        PipelineKPI(metric="hit_rate_vs_spy", scope="overall", scope_key=None, value=0.55),
        PipelineKPI(metric="max_drawdown", scope="overall", scope_key=None, value=0.01),
    ]
    status, reasons = _quality_status(
        min_oos_quarters=2,
        overall_kpis=overall_watch,
        quarter_kpis=quarter_kpis,
        return_rows=return_rows,
    )
    assert status == "WATCH"
    assert len(reasons) == 2

    overall_fail = [
        PipelineKPI(metric="excess_return_vs_spy", scope="overall", scope_key=None, value=-0.01),
        PipelineKPI(metric="mean_ic", scope="overall", scope_key=None, value=-0.10),
        PipelineKPI(metric="hit_rate_vs_spy", scope="overall", scope_key=None, value=0.30),
        PipelineKPI(metric="max_drawdown", scope="overall", scope_key=None, value=0.90),
    ]
    status, reasons = _quality_status(
        min_oos_quarters=2,
        overall_kpis=overall_fail,
        quarter_kpis=quarter_kpis,
        return_rows=return_rows,
    )
    assert status == "FAIL"
    assert len(reasons) >= 3


def test_generate_kpi_report_writes_expected_artifacts(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, min_oos_quarters=1)
    report_dir, quality = generate_kpi_report(
        report_root=tmp_path / "reports-root",
        run_id="run-123",
        as_of_quarter="2025Q1",
        pipeline=pipeline,
        return_rows=[{"date": "2025-01-02", "strategy_gross_return": 0.1, "strategy_net_return": 0.09, "benchmark_return": 0.02, "turnover": 0.1}],
        overall_kpis=[PipelineKPI(metric="excess_return_vs_spy", scope="overall", scope_key=None, value=0.1)],
        quarter_kpis=[PipelineKPI(metric="hit_rate_vs_spy", scope="quarter", scope_key="2025Q1", value=1.0)],
        risk_signals_by_quarter={},
        positions_by_quarter={},
    )
    assert quality in {"PASS", "WATCH", "FAIL", "INSUFFICIENT_SAMPLE"}
    assert (report_dir / "kpi_overall.csv").exists()
    assert (report_dir / "kpi_by_quarter.csv").exists()
    assert (report_dir / "return_series.csv").exists()
    assert (report_dir / "filter_diagnostics.csv").exists()
    config_payload = json.loads((report_dir / "config_snapshot.json").read_text())
    assert config_payload["as_of_quarter"] == "2025Q1"
    assert "pipeline" in config_payload
    assert (report_dir / "summary.md").exists()


def test_run_quarterly_pipeline_returns_early_for_input_validation(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, min_oos_quarters=2)

    class _Store:
        def __init__(self, quarters: list[str]) -> None:
            self._quarters = quarters

        def list_trend_quarters(self) -> list[str]:
            return list(self._quarters)

    class _Gateway:
        def get_benchmark_series(self, *_args: object, **_kwargs: object) -> dict[date, float]:
            return {}

        def get_eod_prices(self, *_args: object, **_kwargs: object) -> dict[str, dict[date, float]]:
            return {}

        def get_adv20_usd(self, *_args: object, **_kwargs: object) -> float | None:
            return None

    no_data = run_quarterly_pipeline(
        store=_Store([]),
        history_gateway=_Gateway(),
        symbol_map_file=tmp_path / "symbols.json",
        pipeline=pipeline,
        as_of_quarter=None,
        dry_run_report=True,
    )
    assert no_data.status == "no_trend_data"

    invalid_as_of = run_quarterly_pipeline(
        store=_Store(["2024Q1", "2024Q2"]),
        history_gateway=_Gateway(),
        symbol_map_file=tmp_path / "symbols.json",
        pipeline=pipeline,
        as_of_quarter="2025Q1",
        dry_run_report=True,
    )
    assert invalid_as_of.status == "invalid_as_of_quarter"

    insufficient = run_quarterly_pipeline(
        store=_Store(["2024Q1"]),
        history_gateway=_Gateway(),
        symbol_map_file=tmp_path / "symbols.json",
        pipeline=pipeline,
        as_of_quarter=None,
        dry_run_report=True,
    )
    assert insufficient.status == "insufficient_input_quarters"


def test_run_quarterly_pipeline_updates_status_when_backtest_not_ok(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, min_oos_quarters=1)
    calls: dict[str, object] = {}

    class _Store:
        def list_trend_quarters(self) -> list[str]:
            return ["2025Q1"]

        def list_snapshots_for_quarter(self, quarter: str) -> list[SimpleNamespace]:
            del quarter
            return []

        def list_trend_stock_signals(self, quarter: str) -> list[object]:
            del quarter
            return []

        def upsert_quarterly_pipeline_run(self, **kwargs: object) -> None:
            calls["upsert"] = kwargs

        def update_quarterly_pipeline_run_status(self, **kwargs: object) -> None:
            calls["update"] = kwargs

        def replace_quarterly_portfolio_positions(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("Should not replace positions when backtest is partial")

        def replace_quarterly_return_series(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("Should not replace return series when backtest is partial")

        def replace_quarterly_kpis(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("Should not replace KPI rows when backtest is partial")

    class _Gateway:
        def get_benchmark_series(self, *_args: object, **_kwargs: object) -> dict[date, float]:
            return {date(2025, 1, 1): 100.0, date(2025, 1, 2): 101.0}

        def get_eod_prices(self, *_args: object, **_kwargs: object) -> dict[str, dict[date, float]]:
            return {}

        def get_adv20_usd(self, *_args: object, **_kwargs: object) -> float | None:
            return None

    result = run_quarterly_pipeline(
        store=_Store(),
        history_gateway=_Gateway(),
        symbol_map_file=tmp_path / "symbols.json",
        pipeline=pipeline,
        as_of_quarter=None,
        dry_run_report=False,
    )
    assert result.status == "partial_data"
    assert result.report_dir is None
    assert "upsert" in calls
    assert calls["update"] is not None
