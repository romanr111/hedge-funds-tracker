"""Environment / .env configuration for market_intel.

.env is loaded from the repo root at import time with a fallback parser -
implementation copied from signals/config.py (_load_dotenv_fallback proven
in production and tests). Config errors raise ValueError with human-readable
messages (mirrors signals/config.py).
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

load_dotenv: Callable[..., bool] | None
try:
    from dotenv import load_dotenv as _load_dotenv
except ModuleNotFoundError:  # pragma: no cover - exercised only when dependency is missing.
    load_dotenv = None
else:
    load_dotenv = _load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv_fallback(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        try:
            # Matches common .env quote handling without pulling extra deps.
            parsed = shlex.split(value, posix=True)
            normalized_value = parsed[0] if parsed else ""
        except ValueError:
            normalized_value = value.strip("\"'")
        os.environ.setdefault(key, normalized_value)


if load_dotenv is not None:
    load_dotenv(REPO_ROOT / ".env")
else:
    _load_dotenv_fallback(REPO_ROOT / ".env")


@dataclass(frozen=True)
class MarketIntelConfig:
    db_path: Path
    sheets_spreadsheet_id: str
    sheets_folder_id: str
    fred_interval_seconds: float
    yahoo_interval_seconds: float
    http_interval_seconds: float


def _read_interval_seconds(key: str, default: str, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(key, default).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number.") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be >= {minimum} and <= {maximum}.")
    return value


def _default_db_path() -> Path:
    return REPO_ROOT / "data" / "local" / "market_intel.sqlite3"


def load_config(*, db_path: str | None = None) -> MarketIntelConfig:
    """Build MarketIntelConfig from environment (with db_path override param winning)."""

    resolved_db_path = Path(db_path or os.environ.get("MARKET_INTEL_DB_PATH", "") or _default_db_path())

    sheets_spreadsheet_id = os.environ.get("SHEETS_SPREADSHEET_ID", "").strip()
    sheets_folder_id = (
        os.environ.get("SHEETS_FOLDER_ID", "").strip()
        or os.environ.get("EXPORT_JSON_GDRIVE_FOLDER_ID", "").strip()
    )

    return MarketIntelConfig(
        db_path=resolved_db_path,
        sheets_spreadsheet_id=sheets_spreadsheet_id,
        sheets_folder_id=sheets_folder_id,
        fred_interval_seconds=_read_interval_seconds(
            "MARKET_INTEL_FRED_INTERVAL", "1.0", minimum=0.2, maximum=10.0
        ),
        yahoo_interval_seconds=_read_interval_seconds(
            "MARKET_INTEL_YAHOO_INTERVAL", "1.5", minimum=0.2, maximum=60.0
        ),
        http_interval_seconds=_read_interval_seconds(
            "MARKET_INTEL_HTTP_INTERVAL", "1.0", minimum=0.2, maximum=60.0
        ),
    )
