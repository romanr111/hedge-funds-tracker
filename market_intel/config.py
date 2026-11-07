"""Environment / .env configuration for market_intel (Agent A owns the body).

.env is loaded from the repo root at import time with a fallback parser -
copy the proven implementation from signals/config.py (_load_dotenv_fallback).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class MarketIntelConfig:
    db_path: Path
    sheets_spreadsheet_id: str
    sheets_folder_id: str
    fred_interval_seconds: float
    yahoo_interval_seconds: float
    http_interval_seconds: float


def load_config(*, db_path: str | None = None) -> MarketIntelConfig:
    raise NotImplementedError("Agent A: implement per approved plan section 'Agent A - data layer'.")
