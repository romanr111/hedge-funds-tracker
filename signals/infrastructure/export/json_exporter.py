from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TrendSummaryJsonData:
    payload: dict[str, Any]
    content_fingerprint: str


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_trend_summary_json_data(payload: dict[str, Any]) -> TrendSummaryJsonData:
    # Strip volatile fields so fingerprint is stable across re-runs with identical data
    stable_payload = json.loads(json.dumps(payload, default=str))
    stable_payload.get("metadata", {}).pop("generated_at", None)
    fingerprint = _fingerprint(stable_payload)
    payload["metadata"]["content_fingerprint"] = fingerprint
    return TrendSummaryJsonData(payload=payload, content_fingerprint=fingerprint)


def write_trend_summary_json(path: Path, data: TrendSummaryJsonData) -> None:
    path.write_text(json.dumps(data.payload, indent=2, default=str), encoding="utf-8")


def read_content_fingerprint(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    fingerprint = metadata.get("content_fingerprint")
    return str(fingerprint) if fingerprint is not None else None
