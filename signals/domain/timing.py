from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


KYIV_TZ = ZoneInfo("Europe/Kyiv")


def now_kyiv() -> datetime:
    return datetime.now(KYIV_TZ)


def format_local_datetime(value: datetime) -> str:
    # Intentionally hide timezone suffix in user-facing messages.
    return value.strftime("%Y-%m-%d %H:%M:%S")
