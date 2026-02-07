from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

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
class ManagerConfig:
    name: str
    cik: str


@dataclass(frozen=True)
class AppConfig:
    sec_user_agent: str
    sec_rate_limit_per_sec: float
    max_filing_age_days: int
    db_path: Path
    managers: list[ManagerConfig]
    notifiers: list[str]
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_pass: str | None
    email_from: str | None
    email_to: str | None
    notify_initial: bool


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _default_managers_file() -> Path:
    return REPO_ROOT / "config" / "managers.json"


def _load_managers_from_iterable(items: Iterable[dict[str, object]]) -> list[ManagerConfig]:
    managers: list[ManagerConfig] = []
    for item in items:
        raw_name = item.get("name")
        raw_cik = item.get("cik")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        cik = raw_cik.strip() if isinstance(raw_cik, str) else ""
        if not name or not cik:
            raise ValueError("Each manager must include non-empty 'name' and 'cik'.")
        managers.append(ManagerConfig(name=name, cik=cik))
    return managers


def load_managers(managers_file: Path | None, managers_json: str | None) -> list[ManagerConfig]:
    if managers_json:
        data = json.loads(managers_json)
        if not isinstance(data, list):
            raise ValueError("MANAGERS_JSON must be a JSON array.")
        return _load_managers_from_iterable(data)

    file_path = managers_file or _default_managers_file()
    if not file_path.exists():
        raise FileNotFoundError(f"Managers file not found: {file_path}")

    data = json.loads(file_path.read_text())
    if not isinstance(data, list):
        raise ValueError("Managers file must contain a JSON array.")
    return _load_managers_from_iterable(data)


def load_config(
    *,
    db_path: str | None = None,
    managers_file: str | None = None,
    notifiers: str | None = None,
    max_filing_age_days: int | None = None,
    notify_initial: bool = False,
) -> AppConfig:
    sec_user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not sec_user_agent:
        raise ValueError("SEC_USER_AGENT is required (descriptive user agent with contact email).")

    rate_limit_raw = os.environ.get("SEC_RATE_LIMIT_PER_SEC", "5").strip()
    try:
        sec_rate_limit_per_sec = float(rate_limit_raw)
    except ValueError as exc:
        raise ValueError("SEC_RATE_LIMIT_PER_SEC must be a number.") from exc
    if sec_rate_limit_per_sec <= 0 or sec_rate_limit_per_sec > 10:
        raise ValueError("SEC_RATE_LIMIT_PER_SEC must be > 0 and <= 10.")

    max_age_raw = (
        str(max_filing_age_days)
        if max_filing_age_days is not None
        else os.environ.get("MAX_FILING_AGE_DAYS", "180").strip()
    )
    try:
        resolved_max_filing_age_days = int(max_age_raw)
    except ValueError as exc:
        raise ValueError("MAX_FILING_AGE_DAYS must be an integer.") from exc
    if resolved_max_filing_age_days < 0:
        raise ValueError("MAX_FILING_AGE_DAYS must be >= 0.")

    resolved_db_path = Path(db_path or os.environ.get("DB_PATH", "") or (REPO_ROOT / "data" / "tracker.sqlite3"))
    managers_file_env = os.environ.get("MANAGERS_FILE")
    managers_path = Path(managers_file) if managers_file else (Path(managers_file_env) if managers_file_env else None)
    managers_json = os.environ.get("MANAGERS_JSON")

    managers = load_managers(managers_path, managers_json)

    notifiers_list = _split_csv(notifiers or os.environ.get("NOTIFIERS"))

    telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    email_from = os.environ.get("EMAIL_FROM")
    email_to = os.environ.get("EMAIL_TO")

    return AppConfig(
        sec_user_agent=sec_user_agent,
        sec_rate_limit_per_sec=sec_rate_limit_per_sec,
        max_filing_age_days=resolved_max_filing_age_days,
        db_path=resolved_db_path,
        managers=managers,
        notifiers=notifiers_list,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_pass=smtp_pass,
        email_from=email_from,
        email_to=email_to,
        notify_initial=notify_initial,
    )
