"""Offline tests for the pure sheet layout spec builder."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
from typing import Any

import pytest

from market_intel.domain.models import (
    MODULE_KEYS,
    MODULE_TITLES_UK,
    BandPoint,
    DashboardSnapshot,
    ForwardReturnRow,
    GaugeHistoryPoint,
    ModuleGauge,
    SeriesSnapshot,
)
from market_intel.infrastructure.export.sheet_layout import (
    CHART_TITLE_UK,
    SPREADSHEET_TITLE_UK,
    TAB_FORWARD_UK,
    TAB_HISTORY_UK,
    TAB_NAMES_UK,
    TAB_OVERVIEW_UK,
    ChartSpec,
    build_sheet_document,
    count_cells,
)

GENERATED_AT = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)


def _module_gauge(
    module_key: str,
    score: float,
    chip_label_uk: str = "Нейтрально",
    chip_color: str = "#FFD54F",
) -> ModuleGauge:
    return ModuleGauge(
        module_key=module_key,
        title_uk=MODULE_TITLES_UK[module_key],
        score=score,
        state_key="state3",
        chip_label_uk=chip_label_uk,
        chip_color=chip_color,
        verdict_uk=f"Вердикт для {module_key}",
        context_forward_uk=f"Контекст для {module_key}",
        facts_uk=(f"Факт 1 ({module_key})", f"Факт 2 ({module_key})"),
        members_used=1,
        members_total=2,
        dropped_uk=(),
    )


def _indicator(module_key: str, value: float = 38.2, percentile: float | None = 0.94) -> SeriesSnapshot:
    return SeriesSnapshot(
        key=f"{module_key}-ind",
        title_uk="CAPE (Shiller)",
        module_key=module_key,
        value=value,
        obs_date=date(2026, 11, 5),
        percentile=percentile,
        band=BandPoint(
            key="band_high",
            label_uk="Дорого",
            color_token="state5",
            lower=30.0,
            upper=None,
        ),
        is_stale=False,
        stale_reason_uk=None,
        meaning_uk="Оцінка ринку",
        source_uk="multpl.com",
        unit_uk="р.",
    )


def _forward_row(rule_key: str, evidence_level: str, active_now: bool = False) -> ForwardReturnRow:
    return ForwardReturnRow(
        rule_key=rule_key,
        condition_uk=f"Умова {rule_key}",
        horizon_uk="6-12 міс.",
        result_uk="+3.1%",
        uncertainty_uk="-2.0..+1.0 в.п.",
        max_dd_uk="-15%",
        n_uk="14",
        evidence_level=evidence_level,
        citation_uk="Джерело: мета-аналіз",
        active_now=active_now,
    )


def _history_rows(dates: Sequence[date] = (date(2026, 11, 5), date(2026, 11, 6))) -> list[GaugeHistoryPoint]:
    rows: list[GaugeHistoryPoint] = []
    for run_date in dates:
        for index, module_key in enumerate(MODULE_KEYS):
            rows.append(
                GaugeHistoryPoint(
                    run_date=run_date,
                    module_key=module_key,
                    score=40.0 + 2.0 * index,
                    state_key="state3",
                    members_used=1,
                    members_total=2,
                    computed_at=GENERATED_AT,
                )
            )
    return rows


def _snapshot(
    *,
    modules: tuple[ModuleGauge, ...] | None = None,
    indicators: Sequence[SeriesSnapshot] = (),
    forward_rows: Sequence[ForwardReturnRow] = (),
    warnings_uk: Sequence[str] = ("Попередження 1",),
) -> DashboardSnapshot:
    if modules is None:
        modules = tuple(
            _module_gauge(module_key, 50.0 + 1.5 * index)
            for index, module_key in enumerate(MODULE_KEYS)
        )
    return DashboardSnapshot(
        as_of=date(2026, 11, 7),
        generated_at=GENERATED_AT,
        modules=modules,
        indicators=tuple(indicators),
        forward_rows=tuple(forward_rows),
        system_signal_uk="Жовтий: дорогі оцінки при сильних трендах",
        warnings_uk=tuple(warnings_uk),
    )


def _tab(spec: Any, name: str) -> Any:
    return next(tab for tab in spec.tabs if tab.name == name)


def _flat_values(tab: Any) -> list[Any]:
    return [cell for op in tab.cell_ops for row in op.values for cell in row]


def _rows_list(tab: Any) -> list[list[Any]]:
    return [list(row) for op in tab.cell_ops for row in op.values]


def test_tabs_are_created_in_exact_fixed_order() -> None:
    spec = build_sheet_document(_snapshot(), _history_rows())
    assert [tab.name for tab in spec.tabs] == list(TAB_NAMES_UK)
    assert [tab.name for tab in spec.tabs] == [
        "Огляд",
        "Валютація",
        "Настрої",
        "Техніка",
        "Макро і ліквідність",
        "Кредит",
        "Форвардна дохідність",
        "Історія",
    ]
    assert spec.title == SPREADSHEET_TITLE_UK


def test_overview_renders_module_rows_with_chip_labels_and_disclaimer() -> None:
    spec = build_sheet_document(_snapshot(), _history_rows())
    overview = _tab(spec, TAB_OVERVIEW_UK)
    rows = _rows_list(overview)

    module_row = next(row for row in rows if row and row[0] == "Валютація")
    assert module_row[1] == "Нейтрально"  # chip label kept beside the module
    assert module_row[2] == 50.0
    assert module_row[3] == "Вердикт для valuation"

    assert any(row and row[0] == "Системний сигнал:" and row[1] == "Жовтий: дорогі оцінки при сильних трендах" for row in rows)
    assert any(cell == "Система не дає фінансових порад." for cell in _flat_values(overview))
    assert any(cell == "Попередження 1" for cell in _flat_values(overview))

    # Header formatting and per-module chip fills are emitted as format ops.
    assert any(
        op.range.startswith("'Огляд'!A4") and "backgroundColor" in op.format_json
        for op in overview.format_ops
    )
    assert any(
        op.range == "'Огляд'!B5" and "backgroundColor" in op.format_json
        for op in overview.format_ops
    )


def test_numeric_cells_keep_real_numbers() -> None:
    spec = build_sheet_document(_snapshot(indicators=[_indicator("valuation")]), _history_rows())
    overview = _tab(spec, TAB_OVERVIEW_UK)
    module_row = next(row for row in _rows_list(overview) if row and row[0] == "Валютація")
    assert isinstance(module_row[2], float)

    for tab in spec.tabs:
        for op in tab.cell_ops:
            for row in op.values:
                for cell in row:
                    assert isinstance(cell, (str, int, float))
                    assert not isinstance(cell, bool)

    # The score column of the overview module table is numeric.
    number_op = next(op for op in overview.number_format_ops if op.range.startswith("'Огляд'!C5"))
    assert number_op.type_ == "NUMBER"
    assert number_op.pattern == "0.0"


def test_forward_tab_carries_evidence_levels() -> None:
    forward_rows = (
        _forward_row("rule_established", "ESTABLISHED", active_now=True),
        _forward_row("rule_tbc", "TBC"),
        _forward_row("rule_not_established", "NOT ESTABLISHED"),
        _forward_row("rule_tentative", "TENTATIVE"),
    )
    spec = build_sheet_document(_snapshot(forward_rows=forward_rows), _history_rows())
    forward = _tab(spec, TAB_FORWARD_UK)
    rows = _rows_list(forward)

    header_row = next(row for row in rows if row and row[0] == "Умова")
    assert "Горизонт" in header_row and "Рівень доказів" in header_row

    established_row = next(
        row for row in rows if len(row) == 9 and row[6] == "ESTABLISHED"
    )
    assert established_row[-1] == "так"
    assert any("ESTABLISHED / TENTATIVE / TBC / NOT ESTABLISHED." in cell for cell in _flat_values(forward))
    assert any(len(row) == 9 and row[6] == "TBC" for row in rows)
    assert any(len(row) == 9 and row[6] == "NOT ESTABLISHED" for row in rows)

    # Active rows get the highlight fill.
    assert any(
        op.range == "'Форвардна дохідність'!A5" and "backgroundColor" in op.format_json
        for op in forward.format_ops
    )
    assert any(
        note.note.startswith("ESTABLISHED - мета-аналіз")
        for note in forward.note_ops
    )


def test_module_tab_renders_indicator_rows() -> None:
    spec = build_sheet_document(
        _snapshot(indicators=[_indicator("valuation"), _indicator("credit")]),
        _history_rows(),
    )
    valuation = _tab(spec, MODULE_TITLES_UK["valuation"])
    rows = _rows_list(valuation)

    header_row = next(row for row in rows if row and row[0] == "Показник")
    assert header_row[-1] == "Джерело"
    indicator_row = next(row for row in rows if row and row[0] == "CAPE (Shiller)")
    assert indicator_row[1] == 38.2
    assert indicator_row[2] == "р."
    assert indicator_row[3] == "2026-11-05"
    assert indicator_row[4] == 0.94
    assert indicator_row[5] == "Дорого"

    # Percentile column carries an explicit "%" suffix format (0-100 scale,
    # so the Sheets-native PERCENT type would wrongly multiply by 100).
    assert any(op.type_ == "NUMBER" and '"%"' in op.pattern and "'!E" in op.range for op in valuation.number_format_ops)
    assert count_cells(spec.tabs) > 0


def test_module_tab_gauge_header_carries_chip_and_score() -> None:
    spec = build_sheet_document(_snapshot(), _history_rows())
    valuation = _tab(spec, MODULE_TITLES_UK["valuation"])
    rows = _rows_list(valuation)
    gauge_row = rows[0]
    assert gauge_row[0] == "Валютація"
    assert gauge_row[1] == "Нейтрально"
    assert gauge_row[2] == 50.0
    # Chip cell fill and notes are attached.
    assert any(op.range == "'Валютація'!B1" and "backgroundColor" in op.format_json for op in valuation.format_ops)
    assert any("0-100" in note.note for note in valuation.note_ops)


def test_history_tab_pivots_by_date() -> None:
    spec = build_sheet_document(_snapshot(), _history_rows())
    history = _tab(spec, TAB_HISTORY_UK)
    rows = _rows_list(history)

    assert rows[0][0] == "Дата"
    assert list(rows[0][1:]) == [MODULE_TITLES_UK[key] for key in MODULE_KEYS]
    first_data_row = rows[1]
    assert first_data_row[0] == "2026-11-05"
    assert all(isinstance(cell, float) for cell in first_data_row[1:])
    assert len(rows) == 3  # header + two dates

    latest_row = rows[-1]
    assert latest_row[0] == "2026-11-06"
    assert latest_row[1] == pytest.approx(40.0)
    assert latest_row[-1] == pytest.approx(48.0)

    # Missing module history renders as empty text, never None/NaN.
    rows_missing_module = _history_rows()[:3]  # only valuation for one date
    sparse_spec = build_sheet_document(_snapshot(), rows_missing_module)
    sparse_history = _tab(sparse_spec, TAB_HISTORY_UK)
    sparse_rows = _rows_list(sparse_history)
    assert any(row and row[0] == "2026-11-05" and row[1] == 40.0 for row in sparse_rows)


def test_freeze_rows_and_column_widths_set() -> None:
    spec = build_sheet_document(_snapshot(), _history_rows())
    for tab in spec.tabs:
        assert tab.freeze_rows == 1
        assert tab.column_widths
        assert all(width > 0 for width in tab.column_widths)


def test_chart_spec_only_with_enough_history() -> None:
    spec = build_sheet_document(_snapshot(), _history_rows())
    assert spec.chart is not None
    assert isinstance(spec.chart, ChartSpec)
    assert spec.chart.title == CHART_TITLE_UK
    assert spec.chart.tab == TAB_HISTORY_UK

    one_day_rows = _history_rows(dates=(date(2026, 11, 6),))
    sparse_spec = build_sheet_document(_snapshot(), one_day_rows)
    assert sparse_spec.chart is None

    empty_spec = build_sheet_document(_snapshot(), [])
    assert empty_spec.chart is None
    empty_history = _tab(empty_spec, TAB_HISTORY_UK)
    assert [row[0] for row in _rows_list(empty_history)] == ["Дата"]


def test_all_cell_ranges_quote_tab_names() -> None:
    spec = build_sheet_document(_snapshot(), _history_rows())
    for tab in spec.tabs:
        for op in tab.cell_ops:
            assert op.range.startswith(f"'{tab.name}'!")
        for op in tab.format_ops:
            assert op.range.startswith(f"'{tab.name}'!")
        for op in tab.number_format_ops:
            assert op.range.startswith(f"'{tab.name}'!")
        for op in tab.note_ops:
            assert op.range.startswith(f"'{tab.name}'!")
    overview = _tab(spec, TAB_OVERVIEW_UK)
    assert overview.cell_ops[0].range == "'Огляд'!A1"
