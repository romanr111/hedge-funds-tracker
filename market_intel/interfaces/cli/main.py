"""argparse CLI for market_intel (the only place with argparse and print)."""
from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from typing import Final

from signals.infrastructure.logging import configure_logging, log_context, new_trace_id

from market_intel.application.use_cases.run_market_intel import run_market_intel
from market_intel.composition import build_runtime
from market_intel.config import load_config
from market_intel.domain.exceptions import MarketIntelError, MarketIntelPublishError
from market_intel.domain.models import (
    CollectionResult,
    DashboardSnapshot,
    RunResult,
)
from market_intel.infrastructure.export.gog_sheets_publisher import (
    SETTING_SPREADSHEET_ID,
    SPREADSHEET_LINK_TEMPLATE,
    STATUS_SKIPPED_NO_GOG,
    STATUS_SKIPPED_UNCHANGED,
)

DEGRADED_PUBLISH_STATUSES: Final[frozenset[str]] = frozenset(
    {STATUS_SKIPPED_NO_GOG, STATUS_SKIPPED_UNCHANGED}
)
_HANDLED_SKIP_REASONS: Final[frozenset[str]] = frozenset(
    {"computed", "hand-skip", "hand skip"}
)


def main() -> int:
    configure_logging()
    logger = logging.getLogger(__name__)
    trace_id = new_trace_id()
    with log_context(trace_id=trace_id):
        try:
            return _main(logger)
        except MarketIntelPublishError as exc:
            logger.error("Google Sheets publish failed", extra={"error": str(exc)})
            print(f"Помилка публікації Sheet: {exc}")
            return 3
        except (MarketIntelError, ValueError) as exc:
            logger.error("Market intel run failed", extra={"error": str(exc)})
            print(f"Помилка: {exc}")
            return 1


def _main(logger: logging.Logger) -> int:
    del logger  # kept for parity with the signals CLI shape and future logging
    parser = _build_parser()
    args = parser.parse_args()
    if args.verbose:
        configure_logging(level=logging.DEBUG)

    if args.dry_run and args.no_publish:
        print(
            "Конфлікт прапорців: --dry-run уже вимикає публікацію; "
            "--no-publish разом із ним не використовується."
        )
        return 2

    config = load_config(db_path=args.db)
    runtime = build_runtime(config, dry_run=args.dry_run)
    try:
        if args.spreadsheet_id:
            runtime.store.set_setting(SETTING_SPREADSHEET_ID, args.spreadsheet_id)

        result = run_market_intel(
            config,
            runtime,
            dry_run=args.dry_run,
            no_publish=args.no_publish or args.show,
            reset_sheet=args.reset_sheet,
            skip_sources=tuple(args.skip_source),
        )
        _print_result(result, show_details=args.show)
        if _all_external_sources_failed(result.collected):
            print("Усі зовнішні джерела недоступні; дані не оновлювалися.")
            return 2
        return 0
    finally:
        runtime.store.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="market-intel",
        description="Ринкова розвідка: збір серій, дашборд, публікація Google Sheet.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Тимчасова БД і без публікації; реальні дані не змінюються.",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Не публікувати Google Sheet (збір і розрахунок виконуються).",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Виконати збір і показати деталі модулів без публікації.",
    )
    parser.add_argument(
        "--reset-sheet",
        action="store_true",
        help="Перестворити вкладки перед публікацією.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Шлях до SQLite-бази (перевизначає DB_PATH).",
    )
    parser.add_argument(
        "--spreadsheet-id",
        default=None,
        help="Перевизначити ID таблиці Google Sheets.",
    )
    parser.add_argument(
        "--skip-source",
        action="append",
        default=[],
        metavar="NAME",
        help="Пропустити джерело даних (можна повторювати).",
    )
    parser.add_argument("--verbose", action="store_true", help="DEBUG-логування.")
    return parser


def _print_result(result: RunResult, *, show_details: bool) -> None:
    dashboard = result.dashboard
    print(f"Ринкова розвідка - дані станом на {dashboard.as_of.isoformat()} (Київ)")
    print(f"Системний сигнал: {dashboard.system_signal_uk}")
    for module in dashboard.modules:
        score_text = "n/д" if module.score is None else f"{module.score:.1f}"
        print(f"{module.title_uk}: {module.chip_label_uk} ({score_text}) - {module.verdict_uk}")

    if show_details:
        _print_module_details(dashboard)

    publish = result.publish
    if publish is None:
        print("Sheet: публікація не виконувалась (dry-run / no-publish).")
    else:
        link = (
            SPREADSHEET_LINK_TEMPLATE.format(spreadsheet_id=publish.spreadsheet_id)
            if publish.spreadsheet_id
            else ""
        )
        print(f"Sheet: {publish.status}" + (f" {link}" if link else ""))
        if publish.status in DEGRADED_PUBLISH_STATUSES:
            print(f"Увага: {publish.detail}")

    for error in result.collected.errors:
        print(f"Увага: джерело недоступне: {error}")
    for source, reason in result.collected.skipped:
        print(f"Увага: джерело {source} пропущено ({reason}).")
    for warning in dashboard.warnings_uk:
        print(f"Увага: {warning}")


def _print_module_details(dashboard: DashboardSnapshot) -> None:
    header = ("Показник", "Значення", "Дата", "Перцентиль", "Стан", "Одиниця")
    for module in dashboard.modules:
        indicators = [
            indicator
            for indicator in dashboard.indicators
            if indicator.module_key == module.module_key
        ]
        rows = [
            (
                indicator.title_uk,
                _cell_text(indicator.value),
                _cell_text(indicator.obs_date),
                _cell_text(indicator.percentile),
                indicator.band.label_uk if indicator.band is not None else "",
                indicator.unit_uk,
            )
            for indicator in indicators
        ]
        print()
        print(f"Модуль: {module.title_uk}")
        if rows:
            print(_format_table(header, rows))
        else:
            print("(показників немає)")
    display_only = [
        indicator for indicator in dashboard.indicators if indicator.module_key is None
    ]
    if display_only:
        rows = [
            (
                indicator.title_uk,
                _cell_text(indicator.value),
                _cell_text(indicator.obs_date),
                _cell_text(indicator.percentile),
                indicator.band.label_uk if indicator.band is not None else "",
                indicator.unit_uk,
            )
            for indicator in display_only
        ]
        print()
        print("Модуль: (показники поза модулями)")
        print(_format_table(header, rows))


def _cell_text(value: object) -> str:
    if value is None:
        return "n/д"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return f"{value:.4g}" if isinstance(value, float) else str(value)


def _format_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [
        max(len(str(header[col])), *(len(str(row[col])) if col < len(row) else 0 for row in rows))
        for col in range(len(header))
    ]

    def render(cells: Sequence[str]) -> str:
        padded = []
        for col in range(len(header)):
            text = str(cells[col]) if col < len(cells) else ""
            padded.append(text.ljust(widths[col]))
        return " | ".join(padded).rstrip()

    lines = [render(header), " | ".join("-" * width for width in widths)]
    lines.extend(render(row) for row in rows)
    return "\n".join(lines)


def _all_external_sources_failed(collected: CollectionResult) -> bool:
    """Exit-ladder rule: nothing fetched and skips were not benign."""
    if collected.fetched:
        return False
    unhandled = [
        source
        for source, reason in collected.skipped
        if reason.strip().lower() not in _HANDLED_SKIP_REASONS
    ]
    return bool(unhandled) or bool(collected.errors)
