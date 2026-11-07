"""Google Sheets publisher backed by the `gog` CLI.

Implements the `SheetsPublisher` port: create-or-update the dashboard
spreadsheet idempotently, driven by the pure `sheet_layout` spec.

Every flag below was verified via `gog ... --help` (gog 0.20.0, 2026-11-07):

- `gog sheets create (new) <title> [--parent FOLDER_ID] [-j] [--results-only]`
  - `--parent` exists on create: no create-then-move fallback is needed.
  - `gog drive move <fileId> --parent FOLDER_ID` is the documented fallback.
- `gog sheets metadata (info) <spreadsheetId> [-j]`
  - response carries `spreadsheetId` and `sheets[].properties.{title,sheetId}`.
- `gog sheets add-tab (add-sheet) <spreadsheetId> <tabName>`
- `gog sheets delete-tab (delete-sheet) <spreadsheetId> <tabName>`
  - destructive: must run with `--force` (client `-y`) to skip confirmation.
- `gog sheets clear <spreadsheetId> <range>`
- `gog sheets batch-update (batch) --data-json=STRING|@file <spreadsheetId>
  [--input RAW|USER_ENTERED]`
  - `--input RAW` avoids USER_ENTERED locale parsing (e.g. `+0,4%` becoming
    a formula); `@file` keeps the payload out of argv quoting.
- `gog sheets format <spreadsheetId> <range> --format-json=CELL_FORMAT
  --format-fields=FIELD_MASK`
- `gog sheets number-format <spreadsheetId> <range> --type NUMBER
  --pattern PATTERN`
- `gog sheets freeze <spreadsheetId> --rows N [--sheet TAB]`
- `gog sheets resize-columns <spreadsheetId> <columns> --width N | --auto`
  - `<columns>` is an A1 column span such as `Tab!A:C`.
- `gog sheets update-note (set-note) <spreadsheetId> <range> --note TEXT`
  - the note is a flag, not a positional argument.
- `gog sheets chart list <spreadsheetId> [-j]` and
  `gog sheets chart create (add,new) --spec-json=STRING|@file <spreadsheetId>
  [--sheet TAB] [--anchor CELL] [--width N] [--height N]`
  - accepted spec: Sheets API ChartSpec (basicChart). Chart handling is
    best-effort: failures warn and are skipped, never raised.

Publishing never performs a live write from inside the publisher in tests;
callers inject a fake client. Failures on non-critical ops only warn.
"""
from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from market_intel.application.ports.vintage_repository import VintageRepository
from market_intel.domain.exceptions import MarketIntelPublishError
from market_intel.domain.models import (
    DashboardSnapshot,
    GaugeHistoryPoint,
    PublishResult,
)
from market_intel.infrastructure.export.gog_client import GogClient, GogOutcome
from market_intel.infrastructure.export.sheet_layout import (
    SPREADSHEET_TITLE_UK,
    SheetDocumentSpec,
    TabSpec,
    build_sheet_document,
    count_cells,
)

logger = logging.getLogger(__name__)

SETTING_SPREADSHEET_ID: Final[str] = "sheets_spreadsheet_id"

STATUS_CREATED: Final[str] = "created"
STATUS_UPDATED: Final[str] = "updated"
STATUS_SKIPPED_UNCHANGED: Final[str] = "skipped_unchanged"
STATUS_SKIPPED_NO_GOG: Final[str] = "skipped_no_gog"

_ACCEPTED_PUBLISH_STATUSES: Final[frozenset[str]] = frozenset(
    {STATUS_CREATED, STATUS_UPDATED}
)

SPREADSHEET_LINK_TEMPLATE: Final[str] = (
    "https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
)

_SKIPPED_UNCHANGED_DETAIL_UK: Final[str] = "контент не змінився"
_SKIPPED_NO_GOG_DETAIL_UK: Final[str] = "gog не знайдено в PATH"

# First sheet titles the Sheets API may create before we add our own tabs.
_API_DEFAULT_TAB_NAMES: Final[frozenset[str]] = frozenset(
    {"Sheet1", "Аркуш1", "Лист1"}
)
_MIN_CLEAR_ROWS: Final[int] = 300
_CLEAR_RANGE_TEMPLATE: Final[str] = f"'{{tab}}'!A1:AN{_MIN_CLEAR_ROWS}"
_CHART_ANCHOR_CELL: Final[str] = "I2"
_CHART_WIDTH_PX: Final[int] = 760
_CHART_HEIGHT_PX: Final[int] = 420
_CHART_MIN_DATA_ROWS: Final[int] = 2

_BATCH_FLAG: Final[str] = "--data-json"


class GogSheetsPublisher:
    """Concrete implementation of the `SheetsPublisher` port (gog CLI adapter)."""

    def __init__(
        self,
        store: VintageRepository,
        *,
        client: GogClient | None = None,
        spreadsheet_title: str = SPREADSHEET_TITLE_UK,
        folder_id: str | None = None,
        min_interval_seconds: float = 0.3,
    ) -> None:
        self._store = store
        self._client = client if client is not None else GogClient(
            min_interval_seconds=min_interval_seconds
        )
        self._spreadsheet_title = spreadsheet_title
        self._folder_id = folder_id
        self._min_interval_seconds = min_interval_seconds

    # Publishing entry point -------------------------------------------------

    def publish(
        self,
        snapshot: DashboardSnapshot,
        history_rows: Sequence[GaugeHistoryPoint],
        *,
        reset_sheet: bool = False,
    ) -> PublishResult:
        """Create or update the dashboard spreadsheet (idempotent per content)."""
        started_at = datetime.now(timezone.utc)
        spec = build_sheet_document(snapshot, history_rows)
        fingerprint = _content_fingerprint(spec)

        last_run = self._store.last_publish_run()
        if (
            last_run is not None
            and not reset_sheet
            and last_run.fingerprint == fingerprint
            and last_run.status in _ACCEPTED_PUBLISH_STATUSES
        ):
            logger.info(
                "Skipping publish: dashboard content is unchanged",
                extra={"fingerprint": fingerprint, "status": last_run.status},
            )
            self._record_run(
                started_at=started_at,
                spreadsheet_id=last_run.spreadsheet_id,
                fingerprint=fingerprint,
                status=STATUS_SKIPPED_UNCHANGED,
                detail=_SKIPPED_UNCHANGED_DETAIL_UK,
            )
            return PublishResult(
                status=STATUS_SKIPPED_UNCHANGED,
                spreadsheet_id=last_run.spreadsheet_id,
                detail=_SKIPPED_UNCHANGED_DETAIL_UK,
            )

        if not self._client.gog_available():
            logger.warning(
                "gog not found in PATH; skipping Google Sheets publish",
                extra={"content_cells": count_cells(spec.tabs)},
            )
            self._record_run(
                started_at=started_at,
                spreadsheet_id=None,
                fingerprint=fingerprint,
                status=STATUS_SKIPPED_NO_GOG,
                detail=_SKIPPED_NO_GOG_DETAIL_UK,
            )
            return PublishResult(
                status=STATUS_SKIPPED_NO_GOG,
                spreadsheet_id=None,
                detail=_SKIPPED_NO_GOG_DETAIL_UK,
            )

        # `--reset-sheet` is an explicit rebuild request, so it bypasses the
        # unchanged-content skip above by design.
        spreadsheet_id, metadata_doc, created_now = self._resolve_spreadsheet()
        self._store.set_setting(SETTING_SPREADSHEET_ID, spreadsheet_id)
        tab_titles = self._existing_tab_titles(
            spreadsheet_id, metadata_doc, created_now
        )

        with tempfile.TemporaryDirectory(prefix="market-intel-publish-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            tab_names = [tab.name for tab in spec.tabs]
            if created_now:
                self._remove_default_tabs(
                    spreadsheet_id, tab_titles, keep=tab_names
                )
            elif reset_sheet:
                self._delete_spec_tabs(spreadsheet_id, spec.tabs, tab_titles)
                tab_titles = [t for t in tab_titles if t not in tab_names]
            self._add_missing_tabs(spreadsheet_id, spec.tabs, tab_titles)

            self._write_tab_data(spreadsheet_id, spec.tabs, tmp_path)
            self._apply_tab_formats(spreadsheet_id, spec.tabs)
            chart_added = self._publish_chart(
                spreadsheet_id, spec, metadata_doc, tmp_path
            )

        finished_at = datetime.now(timezone.utc)
        cells = count_cells(spec.tabs)
        status = STATUS_CREATED if created_now else STATUS_UPDATED
        detail = f"{len(spec.tabs)} tabs; {cells} cells"
        detail += "; chart ok" if chart_added else "; chart skipped"
        self._record_run(
            started_at=started_at,
            spreadsheet_id=spreadsheet_id,
            fingerprint=fingerprint,
            status=status,
            detail=detail,
        )
        return PublishResult(status=status, spreadsheet_id=spreadsheet_id, detail=detail)

    # Spreadsheet resolution -------------------------------------------------

    def _resolve_spreadsheet(
        self,
    ) -> tuple[str, Mapping[str, Any] | None, bool]:
        """Return (spreadsheet_id, metadata_doc, created_now)."""
        stored_id = self._store.get_setting(SETTING_SPREADSHEET_ID)
        if stored_id:
            outcome = self._client._run(["sheets", "metadata", stored_id, "-j"])
            doc = _parse_json_dict(outcome)
            if doc is not None and doc.get("spreadsheetId"):
                return str(doc["spreadsheetId"]), doc, False
            logger.warning(
                "Stored spreadsheet is not reachable; creating a new one",
                extra={"spreadsheet_id": stored_id, "returncode": outcome.returncode},
            )

        create_args = ["sheets", "create", self._spreadsheet_title]
        if self._folder_id:
            # Verified: `gog sheets create <title> --parent FOLDER_ID`.
            create_args.extend(["--parent", self._folder_id])
        create_args.extend(["-j", "--results-only"])
        outcome = self._client._run(create_args)
        if outcome.returncode != 0:
            raise MarketIntelPublishError(
                "Не вдалося створити Spreadsheet: "
                f"rc={outcome.returncode}; {(outcome.stderr or outcome.stdout).strip()}"
            )
        doc = _parse_json_dict(outcome)
        if doc is None:
            raise MarketIntelPublishError(
                "Не вдалося розібрати JSON-відповідь `gog sheets create`"
            )
        spreadsheet_id, parse_path = _extract_spreadsheet_id(doc)
        logger.info(
            "Created spreadsheet",
            extra={"parse_path": parse_path, "returncode": outcome.returncode},
        )
        if spreadsheet_id is None:
            raise MarketIntelPublishError(
                "У відповіді `gog sheets create` відсутній spreadsheetId"
            )
        return spreadsheet_id, doc, True

    def _existing_tab_titles(
        self,
        spreadsheet_id: str,
        metadata_doc: Mapping[str, Any] | None,
        created_now: bool,
    ) -> list[str]:
        titles = _tab_titles(metadata_doc)
        if titles is not None:
            return titles
        if created_now:
            # The create response sometimes omits `sheets`; fetch metadata once.
            outcome = self._client._run(["sheets", "metadata", spreadsheet_id, "-j"])
            doc = _parse_json_dict(outcome)
            titles = _tab_titles(doc)
            if titles is not None:
                return titles
        titles = _tab_titles(metadata_doc)
        if titles is None:
            logger.warning(
                "Could not read spreadsheet metadata tab list; assuming no tabs",
                extra={"spreadsheet_id": spreadsheet_id},
            )
            titles = []
        return titles

    # Tab lifecycle ----------------------------------------------------------

    def _remove_default_tabs(
        self,
        spreadsheet_id: str,
        tab_titles: Sequence[str],
        *,
        keep: Sequence[str],
    ) -> None:
        for title in tab_titles:
            if title not in keep and title in _API_DEFAULT_TAB_NAMES:
                self._delete_tab(spreadsheet_id, title)

    def _delete_spec_tabs(
        self,
        spreadsheet_id: str,
        tabs: Sequence[TabSpec],
        tab_titles: Sequence[str],
    ) -> None:
        for tab in tabs:
            if tab.name in tab_titles:
                self._delete_tab(spreadsheet_id, tab.name)

    def _delete_tab(self, spreadsheet_id: str, tab_name: str) -> None:
        # Verified: `gog sheets delete-tab <ssid> <tabName>` requires `--force`
        # (client `-y`) to skip its confirmation prompt.
        outcome = self._client._run(
            ["sheets", "delete-tab", spreadsheet_id, tab_name], force=True
        )
        if outcome.returncode != 0:
            logger.warning(
                "Failed to delete tab (continuing)",
                extra={
                    "tab": tab_name,
                    "returncode": outcome.returncode,
                    "stderr": (outcome.stderr or outcome.stdout).strip()[:200],
                },
            )

    def _add_missing_tabs(
        self,
        spreadsheet_id: str,
        tabs: Sequence[TabSpec],
        tab_titles: Sequence[str],
    ) -> None:
        for tab in tabs:
            if tab.name in tab_titles:
                continue
            outcome = self._client._run(["sheets", "add-tab", spreadsheet_id, tab.name])
            if outcome.returncode != 0:
                logger.warning(
                    "Failed to add tab (continuing)",
                    extra={
                        "tab": tab.name,
                        "returncode": outcome.returncode,
                        "stderr": (outcome.stderr or outcome.stdout).strip()[:200],
                    },
                )

    # Data and formatting ----------------------------------------------------

    def _write_tab_data(
        self,
        spreadsheet_id: str,
        tabs: Sequence[TabSpec],
        tmp_path: Path,
    ) -> None:
        for index, tab in enumerate(tabs):
            clear_outcome = self._client._run(
                [
                    "sheets",
                    "clear",
                    spreadsheet_id,
                    _CLEAR_RANGE_TEMPLATE.format(tab=tab.name),
                ]
            )
            if clear_outcome.returncode != 0:
                logger.warning(
                    "Failed to clear tab before rewrite (continuing)",
                    extra={"tab": tab.name, "returncode": clear_outcome.returncode},
                )
            payload = [
                {"range": op.range, "values": op.values} for op in tab.cell_ops
            ]
            data_file = tmp_path / f"tab-{index:02d}-values.json"
            data_file.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            # Verified: `batch-update --data-json=@file <ssid> --input RAW`.
            outcome = self._client._run(
                [
                    "sheets",
                    "batch-update",
                    f"{_BATCH_FLAG}=@{data_file}",
                    "--input",
                    "RAW",
                    spreadsheet_id,
                ]
            )
            if outcome.returncode != 0:
                raise MarketIntelPublishError(
                    f"Не вдалося записати вкладку «{tab.name}»: "
                    f"rc={outcome.returncode}; {(outcome.stderr or outcome.stdout).strip()}"
                )

    def _apply_tab_formats(self, spreadsheet_id: str, tabs: Sequence[TabSpec]) -> None:
        for tab in tabs:
            for format_op in tab.format_ops:
                outcome = self._client._run(
                    [
                        "sheets",
                        "format",
                        spreadsheet_id,
                        format_op.range,
                        "--format-json",
                        json.dumps(dict(format_op.format_json), ensure_ascii=False),
                        "--format-fields",
                        format_op.fields,
                    ]
                )
                self._warn_non_critical(outcome, op="format", tab=tab.name)
            for number_op in tab.number_format_ops:
                outcome = self._client._run(
                    [
                        "sheets",
                        "number-format",
                        spreadsheet_id,
                        number_op.range,
                        "--type",
                        number_op.type_,
                        "--pattern",
                        number_op.pattern,
                    ]
                )
                self._warn_non_critical(outcome, op="number-format", tab=tab.name)
            freeze_outcome = self._client._run(
                [
                    "sheets",
                    "freeze",
                    spreadsheet_id,
                    "--rows",
                    str(tab.freeze_rows),
                    "--sheet",
                    tab.name,
                ]
            )
            self._warn_non_critical(freeze_outcome, op="freeze", tab=tab.name)
            for letters, width in _resize_groups(tab.column_widths):
                outcome = self._client._run(
                    [
                        "sheets",
                        "resize-columns",
                        spreadsheet_id,
                        f"'{tab.name}'!{letters}",
                        "--width",
                        str(width),
                    ]
                )
                self._warn_non_critical(outcome, op="resize-columns", tab=tab.name)
            for note_op in tab.note_ops:
                outcome = self._client._run(
                    [
                        "sheets",
                        "update-note",
                        spreadsheet_id,
                        note_op.range,
                        "--note",
                        note_op.note,
                    ]
                )
                self._warn_non_critical(outcome, op="update-note", tab=tab.name)

    def _warn_non_critical(self, outcome: GogOutcome, *, op: str, tab: str) -> None:
        if outcome.returncode != 0:
            logger.warning(
                "Non-critical gog operation failed (continuing)",
                extra={
                    "op": op,
                    "tab": tab,
                    "returncode": outcome.returncode,
                    "stderr": (outcome.stderr or outcome.stdout).strip()[:200],
                },
            )

    # Chart ------------------------------------------------------------------

    def _publish_chart(
        self,
        spreadsheet_id: str,
        spec: SheetDocumentSpec,
        metadata_doc: Mapping[str, Any] | None,
        tmp_path: Path,
    ) -> bool:
        """Best-effort: add the history line chart once; never raise."""
        chart = spec.chart
        if chart is None:
            return False
        history_tab = next(
            (tab for tab in spec.tabs if tab.name == chart.tab), None
        )
        if history_tab is None or len(history_tab.cell_ops) == 0:
            return False
        data_row_count = len(history_tab.cell_ops) - 1  # first op is the header
        if data_row_count < _CHART_MIN_DATA_ROWS:
            logger.info(
                "Not enough history rows for a chart; skipping",
                extra={"rows": data_row_count},
            )
            return False

        list_outcome = self._client._run(["sheets", "chart", "list", spreadsheet_id, "-j"])
        if list_outcome.returncode != 0:
            logger.warning(
                "Cannot list existing charts; skipping chart creation",
                extra={"returncode": list_outcome.returncode},
            )
            return False
        list_doc = _parse_json_dict(list_outcome)
        if list_doc is None:
            logger.warning("Unparseable chart list output; skipping chart creation")
            return False
        if chart.title in _chart_titles(list_doc):
            logger.info("Chart already present", extra={"title": chart.title})
            return True

        sheet_id = _sheet_id_for(metadata_doc, chart.tab)
        if sheet_id is None:
            # Newly add-tab'ed tabs are missing from the resolved metadata doc;
            # re-fetch metadata once to resolve their sheetId.
            refresh_outcome = self._client._run(
                ["sheets", "metadata", spreadsheet_id, "-j"]
            )
            refreshed_doc = _parse_json_dict(refresh_outcome)
            sheet_id = _sheet_id_for(refreshed_doc, chart.tab)
            if sheet_id is None:
                logger.warning(
                    "History tab sheetId unknown; skipping chart creation",
                    extra={"tab": chart.tab},
                )
                return False

        series_count = len(history_tab.cell_ops[0].values[0]) - 1
        end_row = 1 + data_row_count
        chart_spec: dict[str, Any] = {
            "title": chart.title,
            "basicChart": {
                "chartType": "LINE",
                "legendPosition": "BOTTOM_LEGEND",
                "headerCount": 1,
                "domains": [
                    {
                        "source": {
                            "sheetId": sheet_id,
                            "startColumnIndex": 0,
                            "endColumnIndex": 1,
                            "startRowIndex": 1,
                            "endRowIndex": end_row,
                        }
                    }
                ],
                "series": [
                    {
                        "source": {
                            "sheetId": sheet_id,
                            "startColumnIndex": column,
                            "endColumnIndex": column + 1,
                            "startRowIndex": 1,
                            "endRowIndex": end_row,
                        }
                    }
                    for column in range(1, series_count + 1)
                ],
            },
        }
        spec_file = tmp_path / "chart-spec.json"
        spec_file.write_text(
            json.dumps(chart_spec, ensure_ascii=False), encoding="utf-8"
        )
        # Verified:
        # `gog sheets chart create --spec-json=STRING|@file <ssid> [--sheet TAB]
        #  [--anchor CELL] [--width N] [--height N]`.
        outcome = self._client._run(
            [
                "sheets",
                "chart",
                "create",
                "--spec-json",
                f"@{spec_file}",
                "--sheet",
                chart.tab,
                "--anchor",
                _CHART_ANCHOR_CELL,
                "--width",
                str(_CHART_WIDTH_PX),
                "--height",
                str(_CHART_HEIGHT_PX),
                spreadsheet_id,
            ]
        )
        if outcome.returncode != 0:
            logger.warning(
                "Chart creation failed (continuing)",
                extra={
                    "title": chart.title,
                    "returncode": outcome.returncode,
                    "stderr": (outcome.stderr or outcome.stdout).strip()[:200],
                },
            )
            return False
        logger.info("Chart created", extra={"title": chart.title})
        return True

    # Bookkeeping ------------------------------------------------------------

    def _record_run(
        self,
        *,
        started_at: datetime,
        spreadsheet_id: str | None,
        fingerprint: str,
        status: str,
        detail: str,
    ) -> None:
        self._store.record_publish_run(
            spreadsheet_id=spreadsheet_id,
            fingerprint=fingerprint,
            status=status,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            detail=detail,
        )


# Module-level helpers --------------------------------------------------------


def _content_fingerprint(spec: SheetDocumentSpec) -> str:
    """SHA-256 over all tab value arrays (mirrors the signals exporter style)."""
    payload: list[dict[str, Any]] = [
        {
            "tab": tab.name,
            "values": [row for op in tab.cell_ops for row in op.values],
        }
        for tab in spec.tabs
    ]
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_json_dict(outcome: GogOutcome) -> dict[str, Any] | None:
    if outcome.returncode != 0:
        return None
    try:
        parsed = json.loads(outcome.stdout) if outcome.stdout.strip() else None
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_spreadsheet_id(doc: Mapping[str, Any]) -> tuple[str | None, str]:
    """Extract spreadsheetId, reporting which parse path produced it."""
    primary = doc.get("spreadsheetId")
    if isinstance(primary, str) and primary:
        return primary, "primary:spreadsheetId"
    for container in ("result", "data", "spreadsheet", "response"):
        nested = doc.get(container)
        if isinstance(nested, Mapping):
            nested_id = nested.get("spreadsheetId")
            if isinstance(nested_id, str) and nested_id:
                return nested_id, f"nested:{container}.spreadsheetId"
    return None, "missing"


def _tab_titles(metadata_doc: Mapping[str, Any] | None) -> list[str] | None:
    """Extract tab titles from a metadata doc; None when shape is unknown."""
    if metadata_doc is None:
        return None
    sheets = metadata_doc.get("sheets")
    if not isinstance(sheets, list):
        return None
    titles: list[str] = []
    for sheet in sheets:
        if not isinstance(sheet, Mapping):
            continue
        properties = sheet.get("properties")
        if isinstance(properties, Mapping):
            title = properties.get("title")
            if isinstance(title, str):
                titles.append(title)
    return titles


def _sheet_id_for(metadata_doc: Mapping[str, Any] | None, tab_title: str) -> int | None:
    if metadata_doc is None:
        return None
    sheets = metadata_doc.get("sheets")
    if not isinstance(sheets, list):
        return None
    for sheet in sheets:
        if not isinstance(sheet, Mapping):
            continue
        properties = sheet.get("properties")
        if isinstance(properties, Mapping) and properties.get("title") == tab_title:
            sheet_id = properties.get("sheetId")
            if isinstance(sheet_id, int):
                return sheet_id
    return None


def _chart_titles(chart_list_doc: Mapping[str, Any]) -> list[str]:
    """Best-effort extraction of chart titles from `gog sheets chart list` JSON."""
    charts = chart_list_doc.get("charts")
    if not isinstance(charts, list):
        return []
    titles: list[str] = []
    for chart in charts:
        if not isinstance(chart, Mapping):
            continue
        title = chart.get("title")
        if not isinstance(title, str):
            spec = chart.get("spec")
            title = spec.get("title") if isinstance(spec, Mapping) else None
        if isinstance(title, str):
            titles.append(title)
    return titles


def _resize_groups(column_widths: Sequence[int]) -> list[tuple[str, int]]:
    """Group neighbouring columns with equal width into single resize calls."""
    groups: list[tuple[str, int]] = []
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    start = 0
    while start < len(column_widths):
        end = start
        while (
            end + 1 < len(column_widths)
            and column_widths[end + 1] == column_widths[start]
        ):
            end += 1
        first = letters[start]
        last = letters[end]
        groups.append((f"{first}:{last}", column_widths[start]))
        start = end + 1
    return groups
