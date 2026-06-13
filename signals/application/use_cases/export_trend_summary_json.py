from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from signals.infrastructure.export.json_exporter import (
    TrendSummaryJsonData,
    read_content_fingerprint,
    write_trend_summary_json,
)


@dataclass(frozen=True)
class ExportResult:
    status: str
    path: Path | None
    content_fingerprint: str


def export_trend_summary_json_if_changed(
    path: Path,
    data: TrendSummaryJsonData,
    *,
    dry_run: bool = False,
) -> ExportResult:
    if dry_run:
        return ExportResult(
            status="skipped_dry_run",
            path=None,
            content_fingerprint=data.content_fingerprint,
        )

    if path.exists():
        stored_fingerprint = read_content_fingerprint(path)
        if stored_fingerprint == data.content_fingerprint:
            return ExportResult(
                status="skipped_unchanged",
                path=path,
                content_fingerprint=data.content_fingerprint,
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    write_trend_summary_json(path, data)
    return ExportResult(
        status="written",
        path=path,
        content_fingerprint=data.content_fingerprint,
    )
