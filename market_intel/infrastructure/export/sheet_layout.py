"""Pure Google Sheets layout spec builder for the market intelligence dashboard.

Pure module (no I/O): converts a DashboardSnapshot plus gauge history into
frozen spec objects that the gog publisher translates into `gog` invocations.
Numeric data (scores, values, percentiles) is carried as real numbers so the
publisher can write numeric cells; only Ukrainian labels are string content.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from market_intel.domain.models import (
    MODULE_KEYS,
    MODULE_TITLES_UK,
    DashboardSnapshot,
    GaugeHistoryPoint,
    ModuleGauge,
    SeriesSnapshot,
)

SPREADSHEET_TITLE_UK: Final[str] = "Market Intel - Ринкова розвідка"
CHART_TITLE_UK: Final[str] = "Динаміка оцінок модулів"
DISCLAIMER_UK: Final[str] = "Система не дає фінансових порад."

TAB_OVERVIEW_UK: Final[str] = "Огляд"
TAB_FORWARD_UK: Final[str] = "Форвардна дохідність"
TAB_HISTORY_UK: Final[str] = "Історія"

TAB_NAMES_UK: Final[tuple[str, ...]] = (
    "Огляд",
    "Валютація",
    "Настрої",
    "Техніка",
    "Макро і ліквідність",
    "Кредит",
    "Форвардна дохідність",
    "Історія",
)

OVERVIEW_HEADER_ROW_UK: Final[tuple[str, ...]] = (
    "Модуль",
    "Стан",
    "Оцінка",
    "Вердикт",
    "Контекст",
    "Факти",
)
OVERVIEW_TITLE_TEMPLATE_UK: Final[
    str
] = "Ринкова розвідка - дані станом на {as_of} (Київ)"
SYSTEM_SIGNAL_LABEL_UK: Final[str] = "Системний сигнал:"
WARNINGS_LABEL_UK: Final[str] = "Попередження і застереження:"

MODULE_INDICATOR_HEADER_ROW_UK: Final[tuple[str, ...]] = (
    "Показник",
    "Значення",
    "Одиниця",
    "Дата",
    "Перцентиль",
    "Стан",
    "Свіжість",
    "Сенс",
    "Джерело",
)
MODULE_NOT_COUNTED_LABEL_UK: Final[str] = "Не враховано:"
CHIP_COLOR_NOTE_UK: Final[
    str
] = "Колір стану: від світло-зеленого (добре) до червоного (погано)."
SCORE_NOTE_UK: Final[str] = "Композит членів модуля за шкалою 0-100."

FORWARD_TITLE_UK: Final[str] = "Форвардна дохідність: умови - результати"
FORWARD_LEVELS_LINE_UK: Final[
    str
] = "Рівні доказів: ESTABLISHED / TENTATIVE / TBC / NOT ESTABLISHED. Система не дає фінансових порад."
FORWARD_HEADER_ROW_UK: Final[tuple[str, ...]] = (
    "Умова",
    "Горизонт",
    "Результат",
    "Невизначеність",
    "Макс. просадка",
    "Спостережень",
    "Рівень доказів",
    "Джерело / цитата",
    "Активна зараз",
)
FORWARD_EVIDENCE_NOTE_UK: Final[
    str
] = "ESTABLISHED - мета-аналіз; TENTATIVE - поодинокі дослідження; TBC - правило сформульоване, оцінка очікує даних; NOT ESTABLISHED - цитованної бази немає."
ACTIVE_CELL_LABEL_UK: Final[str] = "так"
INACTIVE_CELL_LABEL_UK: Final[str] = "ні"

HISTORY_DATE_HEADER_UK: Final[str] = "Дата"
HISTORY_NOTE_UK: Final[str] = "Один рядок на день розрахунку; стовпці - оцінки модулів."

HEADER_FILL_RGB: Final[Mapping[str, float]] = {
    "red": 0.118,
    "green": 0.227,
    "blue": 0.373,
}
HEADER_TEXT_RGB: Final[Mapping[str, float]] = {"red": 1.0, "green": 1.0, "blue": 1.0}
ACTIVE_FILL_RGB: Final[Mapping[str, float]] = {
    "red": 0.486,
    "green": 0.702,
    "blue": 0.259,
}
_HEADER_FORMAT_FIELDS: Final[
    str
] = "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.foregroundColor"
_TITLE_FORMAT_FIELDS: Final[
    str
] = "userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.fontSize"
_BOLD_FORMAT_FIELDS: Final[str] = "userEnteredFormat.textFormat.bold"
_BACKGROUND_FORMAT_FIELDS: Final[str] = "userEnteredFormat.backgroundColor"
# Long plain-Ukrainian explanation cells must wrap (otherwise the text is
# clipped at the column edge when the neighbour cell is not empty).
_WRAP_FORMAT_JSON: Final[Mapping[str, object]] = {"wrapStrategy": "WRAP"}
_WRAP_FORMAT_FIELDS: Final[str] = "userEnteredFormat.wrapStrategy"

_CHART_MIN_ROWS: Final[int] = 2
_MIN_CLEAR_ROWS: Final[int] = 300


@dataclass(frozen=True)
class CellOp:
    """One write operation: values grid for an A1 range (numbers stay numeric)."""

    range: str
    values: list[list[object]]


@dataclass(frozen=True)
class FormatOp:
    """One cell-format operation (Sheets API CellFormat JSON + field mask)."""

    range: str
    format_json: Mapping[str, object]
    fields: str


@dataclass(frozen=True)
class NumberFormatOp:
    """One number-format operation for a range."""

    range: str
    type_: str
    pattern: str


@dataclass(frozen=True)
class NoteOp:
    """One cell-note operation."""

    range: str
    note: str


@dataclass(frozen=True)
class ChartSpec:
    """Line chart of the history tab (best-effort, added once by publisher)."""

    title: str
    tab: str


@dataclass(frozen=True)
class TabSpec:
    """Full layout of one spreadsheet tab."""

    name: str
    cell_ops: tuple[CellOp, ...]
    format_ops: tuple[FormatOp, ...]
    number_format_ops: tuple[NumberFormatOp, ...]
    note_ops: tuple[NoteOp, ...]
    freeze_rows: int = 1
    column_widths: tuple[int, ...] = ()


@dataclass(frozen=True)
class SheetDocumentSpec:
    """Full document: fixed tab order, optional chart, spreadsheet title."""

    tabs: tuple[TabSpec, ...]
    chart: ChartSpec | None
    title: str


def _column_letter(index: int) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return letters[index]


def _cell_ops_for_rows(tab_name: str, rows: Sequence[tuple[int, list[object]]]) -> tuple[CellOp, ...]:
    return tuple(
        CellOp(range=f"'{tab_name}'!A{row}", values=[list(cells)]) for row, cells in rows
    )


def _header_format_json() -> dict[str, object]:
    return {
        "backgroundColor": dict(HEADER_FILL_RGB),
        "textFormat": {"bold": True, "foregroundColor": dict(HEADER_TEXT_RGB)},
    }


def _title_format_json(size: int = 14) -> dict[str, object]:
    return {"textFormat": {"bold": True, "fontSize": size}}


def _chip_fill_format_json(chip_color: str) -> dict[str, object] | None:
    rgb = _hex_to_rgb(chip_color)
    if rgb is None:
        return None
    return {
        "backgroundColor": rgb,
        "textFormat": {"bold": True},
    }


def _hex_to_rgb(color_hex: str) -> dict[str, float] | None:
    text = color_hex.strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        red = int(text[0:2], 16)
        green = int(text[2:4], 16)
        blue = int(text[4:6], 16)
    except ValueError:
        return None
    return {"red": red / 255, "green": green / 255, "blue": blue / 255}


def _gauge_for(snapshot: DashboardSnapshot, module_key: str) -> ModuleGauge | None:
    for module in snapshot.modules:
        if module.module_key == module_key:
            return module
    return None


def _indicators_for(snapshot: DashboardSnapshot, module_key: str) -> tuple[SeriesSnapshot, ...]:
    return tuple(
        indicator
        for indicator in snapshot.indicators
        if indicator.module_key == module_key
    )


def _build_overview_tab(snapshot: DashboardSnapshot) -> TabSpec:
    rows: list[tuple[int, list[object]]] = []
    format_ops: list[FormatOp] = []
    note_ops: list[NoteOp] = []

    rows.append((1, [OVERVIEW_TITLE_TEMPLATE_UK.format(as_of=snapshot.as_of.isoformat())]))
    format_ops.append(
        FormatOp(range="'Огляд'!A1", format_json=_title_format_json(), fields=_TITLE_FORMAT_FIELDS)
    )
    note_ops.append(
        NoteOp(
            range="'Огляд'!A1",
            note="Дані оновлюються автоматично. Система не дає фінансових порад.",
        )
    )
    rows.append((2, [SYSTEM_SIGNAL_LABEL_UK, snapshot.system_signal_uk]))
    format_ops.append(
        FormatOp(
            range="'Огляд'!A2",
            format_json={"textFormat": {"bold": True}},
            fields=_BOLD_FORMAT_FIELDS,
        )
    )

    table_header_row = 4
    rows.append((table_header_row, list(OVERVIEW_HEADER_ROW_UK)))
    last_header_column = _column_letter(len(OVERVIEW_HEADER_ROW_UK) - 1)
    format_ops.append(
        FormatOp(
            range=f"'Огляд'!A{table_header_row}:{last_header_column}{table_header_row}",
            format_json=_header_format_json(),
            fields=_HEADER_FORMAT_FIELDS,
        )
    )

    module_first_row = table_header_row + 1
    for offset, module_key in enumerate(MODULE_KEYS):
        module = _gauge_for(snapshot, module_key)
        if module is None:
            continue
        row = module_first_row + offset
        rows.append(
            (
                row,
                [
                    module.title_uk,
                    module.chip_label_uk,
                    module.score if module.score is not None else "",
                    module.verdict_uk,
                    module.context_forward_uk,
                    "; ".join(module.facts_uk),
                ],
            )
        )
        chip_format = _chip_fill_format_json(module.chip_color)
        if chip_format is not None:
            format_ops.append(
                FormatOp(
                    range=f"'Огляд'!B{row}",
                    format_json=chip_format,
                    fields=_BACKGROUND_FORMAT_FIELDS,
                )
            )
    number_format_ops: list[NumberFormatOp] = []
    if snapshot.modules:
        # Wrap the three wide explanation columns (Вердикт/Контекст/Факти) so
        # the text is fully readable inside the sheet, not clipped.
        format_ops.append(
            FormatOp(
                range=f"'{TAB_OVERVIEW_UK}'!D{module_first_row}:F{module_first_row + len(MODULE_KEYS) - 1}",
                format_json=_WRAP_FORMAT_JSON,
                fields=_WRAP_FORMAT_FIELDS,
            )
        )
        number_format_ops.append(
            NumberFormatOp(
                range=f"'{TAB_OVERVIEW_UK}'!C{module_first_row}:C{module_first_row + len(MODULE_KEYS) - 1}",
                type_="NUMBER",
                pattern="0.0",
            )
        )

    warning_label_row = module_first_row + len(MODULE_KEYS) + 1
    next_row = warning_label_row
    rows.append((next_row, [WARNINGS_LABEL_UK]))
    format_ops.append(
        FormatOp(
            range=f"'Огляд'!A{next_row}",
            format_json={"textFormat": {"bold": True}},
            fields=_BOLD_FORMAT_FIELDS,
        )
    )
    for warning in snapshot.warnings_uk:
        next_row += 1
        rows.append((next_row, [warning]))
    next_row += 1
    rows.append((next_row, [DISCLAIMER_UK]))
    format_ops.append(
        FormatOp(
            range=f"'Огляд'!A{next_row}",
            format_json={"textFormat": {"bold": True}},
            fields=_BOLD_FORMAT_FIELDS,
        )
    )

    return TabSpec(
        name=TAB_OVERVIEW_UK,
        cell_ops=_cell_ops_for_rows(TAB_OVERVIEW_UK, rows),
        format_ops=tuple(format_ops),
        number_format_ops=tuple(number_format_ops),
        note_ops=tuple(note_ops),
        freeze_rows=1,
        column_widths=(260, 150, 90, 380, 420, 460),
    )


def _build_module_tab(snapshot: DashboardSnapshot, module_key: str) -> TabSpec:
    tab_name = MODULE_TITLES_UK[module_key]
    gauge = _gauge_for(snapshot, module_key)
    rows: list[tuple[int, list[object]]] = []
    format_ops: list[FormatOp] = []
    note_ops: list[NoteOp] = []

    header_cells: list[object] = [tab_name]
    if gauge is not None:
        header_cells = [
            gauge.title_uk,
            gauge.chip_label_uk,
            gauge.score if gauge.score is not None else "",
            gauge.verdict_uk,
        ]
    rows.append((1, header_cells))
    format_ops.append(
        FormatOp(
            range=f"'{tab_name}'!A1",
            format_json=_title_format_json(size=13),
            fields=_TITLE_FORMAT_FIELDS,
        )
    )
    note_ops.append(
        NoteOp(range=f"'{tab_name}'!B1", note=CHIP_COLOR_NOTE_UK)
    )
    if gauge is not None:
        note_ops.append(NoteOp(range=f"'{tab_name}'!C1", note=SCORE_NOTE_UK))
        chip_format = _chip_fill_format_json(gauge.chip_color)
        if chip_format is not None:
            format_ops.append(
                FormatOp(
                    range=f"'{tab_name}'!B1",
                    format_json=chip_format,
                    fields=_BACKGROUND_FORMAT_FIELDS,
                )
            )
        row = 2
        if gauge.context_forward_uk:
            rows.append((row, [gauge.context_forward_uk]))
            row += 1
        for fact in gauge.facts_uk:
            rows.append((row, [fact]))
            row += 1

    indicator_header_row = 4 + len(gauge.facts_uk) if gauge is not None else 3
    rows.append((indicator_header_row, list(MODULE_INDICATOR_HEADER_ROW_UK)))
    last_header_column = _column_letter(len(MODULE_INDICATOR_HEADER_ROW_UK) - 1)
    format_ops.append(
        FormatOp(
            range=f"'{tab_name}'!A{indicator_header_row}:{last_header_column}{indicator_header_row}",
            format_json=_header_format_json(),
            fields=_HEADER_FORMAT_FIELDS,
        )
    )

    indicator_first_row = indicator_header_row + 1
    indicator_last_row = indicator_header_row
    for indicator in _indicators_for(snapshot, module_key):
        indicator_last_row += 1
        band_label = indicator.band.label_uk if indicator.band is not None else ""
        rows.append(
            (
                indicator_last_row,
                [
                    indicator.title_uk,
                    indicator.value if indicator.value is not None else "",
                    indicator.unit_uk,
                    indicator.obs_date.isoformat() if indicator.obs_date is not None else "",
                    indicator.percentile if indicator.percentile is not None else "",
                    band_label,
                    indicator.stale_reason_uk if indicator.stale_reason_uk is not None else "",
                    indicator.meaning_uk,
                    indicator.source_uk,
                ],
            )
        )

    format_ops.append(
        FormatOp(
            range=f"'{tab_name}'!A1",
            format_json={"textFormat": {"bold": True}},
            fields=_BOLD_FORMAT_FIELDS,
        )
    )
    number_format_ops: list[NumberFormatOp] = []
    if indicator_last_row >= indicator_first_row:
        number_format_ops.append(
            NumberFormatOp(
                range=f"'{tab_name}'!B{indicator_first_row}:B{indicator_last_row}",
                type_="NUMBER",
                pattern="0.00",
            )
        )
        # Percentiles are stored on a 0-100 scale; the numeric cells carry an
        # explicit "%" suffix so Sheets does not multiply the value by 100.
        number_format_ops.append(
            NumberFormatOp(
                range=f"'{tab_name}'!E{indicator_first_row}:E{indicator_last_row}",
                type_="NUMBER",
                pattern='0.0"%"',
            )
        )
        # Wrap the long explanation columns (Свіжість/Сенс) for readability.
        format_ops.append(
            FormatOp(
                range=f"'{tab_name}'!G{indicator_first_row}:H{indicator_last_row}",
                format_json=_WRAP_FORMAT_JSON,
                fields=_WRAP_FORMAT_FIELDS,
            )
        )

    last_row = indicator_last_row
    if gauge is not None and gauge.dropped_uk:
        last_row += 1
        rows.append((last_row, [MODULE_NOT_COUNTED_LABEL_UK]))
        format_ops.append(
            FormatOp(
                range=f"'{tab_name}'!A{last_row}",
                format_json={"textFormat": {"bold": True}},
                fields=_BOLD_FORMAT_FIELDS,
            )
        )
        for dropped in gauge.dropped_uk:
            last_row += 1
            rows.append((last_row, [dropped]))

    return TabSpec(
        name=tab_name,
        cell_ops=_cell_ops_for_rows(tab_name, rows),
        format_ops=tuple(format_ops),
        number_format_ops=tuple(number_format_ops),
        note_ops=tuple(note_ops),
        freeze_rows=1,
        column_widths=(230, 120, 100, 100, 110, 110, 130, 330, 150),
    )


def _build_forward_tab(snapshot: DashboardSnapshot) -> TabSpec:
    rows: list[tuple[int, list[object]]] = []
    format_ops: list[FormatOp] = []

    rows.append((1, [FORWARD_TITLE_UK]))
    format_ops.append(
        FormatOp(
            range=f"'{TAB_FORWARD_UK}'!A1",
            format_json=_title_format_json(),
            fields=_TITLE_FORMAT_FIELDS,
        )
    )
    rows.append((2, [FORWARD_LEVELS_LINE_UK]))
    header_row = 4
    rows.append((header_row, list(FORWARD_HEADER_ROW_UK)))
    last_header_column = _column_letter(len(FORWARD_HEADER_ROW_UK) - 1)
    format_ops.append(
        FormatOp(
            range=f"'{TAB_FORWARD_UK}'!A{header_row}:{last_header_column}{header_row}",
            format_json=_header_format_json(),
            fields=_HEADER_FORMAT_FIELDS,
        )
    )

    row = header_row
    for forward_row in snapshot.forward_rows:
        row += 1
        rows.append(
            (
                row,
                [
                    forward_row.condition_uk,
                    forward_row.horizon_uk,
                    forward_row.result_uk,
                    forward_row.uncertainty_uk,
                    forward_row.max_dd_uk,
                    forward_row.n_uk,
                    forward_row.evidence_level,
                    forward_row.citation_uk,
                    ACTIVE_CELL_LABEL_UK if forward_row.active_now else INACTIVE_CELL_LABEL_UK,
                ],
            )
        )
        if forward_row.active_now:
            format_ops.append(
                FormatOp(
                    range=f"'{TAB_FORWARD_UK}'!A{row}",
                    format_json={"backgroundColor": dict(ACTIVE_FILL_RGB)},
                    fields=_BACKGROUND_FORMAT_FIELDS,
                )
            )
    number_format_ops: tuple[NumberFormatOp, ...] = ()
    note_ops: tuple[NoteOp, ...] = ()
    if snapshot.forward_rows:
        number_format_ops = ()
        note_ops = (
            NoteOp(
                range=f"'{TAB_FORWARD_UK}'!G{header_row}",
                note=FORWARD_EVIDENCE_NOTE_UK,
            ),
        )
        first_data_row = header_row + 1
        last_data_row = row
        for column_letter in ("A", "D", "H"):
            format_ops.append(
                FormatOp(
                    range=f"'{TAB_FORWARD_UK}'!{column_letter}{first_data_row}:{column_letter}{last_data_row}",
                    format_json=_WRAP_FORMAT_JSON,
                    fields=_WRAP_FORMAT_FIELDS,
                )
            )

    return TabSpec(
        name=TAB_FORWARD_UK,
        cell_ops=_cell_ops_for_rows(TAB_FORWARD_UK, rows),
        format_ops=tuple(format_ops),
        number_format_ops=number_format_ops,
        note_ops=note_ops,
        freeze_rows=1,
        column_widths=(460, 130, 150, 200, 140, 130, 150, 280, 110),
    )


def _build_history_tab(
    history_rows: Sequence[GaugeHistoryPoint],
) -> tuple[TabSpec, ChartSpec | None]:
    dates = sorted({point.run_date for point in history_rows})
    header = [
        HISTORY_DATE_HEADER_UK,
        *(MODULE_TITLES_UK[module_key] for module_key in MODULE_KEYS),
    ]
    rows: list[tuple[int, list[object]]] = [
        (1, header),
    ]
    for offset, run_date in enumerate(dates, start=2):
        cells: list[object] = [run_date.isoformat()]
        for module_key in MODULE_KEYS:
            point = next(
                (
                    candidate
                    for candidate in history_rows
                    if candidate.run_date == run_date and candidate.module_key == module_key
                ),
                None,
            )
            cells.append(
                point.score if point is not None and point.score is not None else ""
            )
        rows.append((offset, cells))

    format_ops = [
        FormatOp(
            range=f"'{TAB_HISTORY_UK}'!A1:F1",
            format_json=_header_format_json(),
            fields=_HEADER_FORMAT_FIELDS,
        )
    ]
    number_format_ops: tuple[NumberFormatOp, ...] = ()
    if dates:
        number_format_ops = (
            NumberFormatOp(
                range=f"'{TAB_HISTORY_UK}'!B2:F{1 + len(dates)}",
                type_="NUMBER",
                pattern="0.0",
            ),
        )
    tab = TabSpec(
        name=TAB_HISTORY_UK,
        cell_ops=_cell_ops_for_rows(TAB_HISTORY_UK, rows),
        format_ops=tuple(format_ops),
        number_format_ops=number_format_ops,
        note_ops=(
            NoteOp(range=f"'{TAB_HISTORY_UK}'!A1", note=HISTORY_NOTE_UK),
        ),
        freeze_rows=1,
        column_widths=(110, 120, 120, 120, 120, 120),
    )
    chart = (
        ChartSpec(title=CHART_TITLE_UK, tab=TAB_HISTORY_UK)
        if len(dates) >= _CHART_MIN_ROWS
        else None
    )
    return tab, chart


def build_sheet_document(
    snapshot: DashboardSnapshot,
    history_rows: Sequence[GaugeHistoryPoint],
) -> SheetDocumentSpec:
    """Build the full sheet document spec: 8 tabs in the approved fixed order."""
    overview_tab = _build_overview_tab(snapshot)
    module_tabs = tuple(
        _build_module_tab(snapshot, module_key) for module_key in MODULE_KEYS
    )
    forward_tab = _build_forward_tab(snapshot)
    history_tab, chart = _build_history_tab(history_rows)
    return SheetDocumentSpec(
        tabs=(overview_tab, *module_tabs, forward_tab, history_tab),
        chart=chart,
        title=SPREADSHEET_TITLE_UK,
    )


def count_cells(tabs: Sequence[TabSpec]) -> int:
    """Count data cells across all tab cell ops (for publish run detail)."""
    total = 0
    for tab in tabs:
        for op in tab.cell_ops:
            for row in op.values:
                total += len(row)
    return total
