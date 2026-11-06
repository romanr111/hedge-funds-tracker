from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from signals.domain.quarters import parse_report_quarter, quarter_sort_key

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
class FilingSourcePeriod:
    """Approved filing sources for a canonical manager during a report-quarter range."""

    source_ciks: tuple[str, ...]
    from_quarter: str | None = None
    through_quarter: str | None = None
    overlap_policy: str | None = None

    def includes(self, report_quarter: str) -> bool:
        target = quarter_sort_key(report_quarter)
        if self.from_quarter is not None and target < quarter_sort_key(self.from_quarter):
            return False
        if self.through_quarter is not None and target > quarter_sort_key(self.through_quarter):
            return False
        return True


@dataclass(frozen=True)
class ManagerConfig:
    name: str
    cik: str
    weight: float
    filing_source_periods: tuple[FilingSourcePeriod, ...] = ()

    def source_ciks_for_quarter(self, report_quarter: str) -> tuple[str, ...]:
        """Return explicitly approved sources, retaining the legacy canonical fallback."""

        if parse_report_quarter(report_quarter) is None:
            raise ValueError(f"Invalid report quarter: {report_quarter}")

        matches = [period for period in self.filing_source_periods if period.includes(report_quarter)]
        if len(matches) > 1:
            raise ValueError(
                f"Manager '{self.name}' has overlapping filing source periods for {report_quarter}."
            )
        if not matches:
            return (self.cik,)
        return matches[0].source_ciks


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
    notify_initial: bool


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _default_managers_file() -> Path:
    return REPO_ROOT / "config" / "managers.json"


def _read_optional_quarter(
    raw_value: object,
    *,
    manager_name: str,
    field_name: str,
) -> str | None:
    if raw_value is None:
        return None
    value = raw_value.strip() if isinstance(raw_value, str) else ""
    if parse_report_quarter(value) is None:
        raise ValueError(
            f"Manager '{manager_name}' has invalid '{field_name}' report quarter."
        )
    return value


def _parse_filing_source_periods(
    raw_value: object,
    *,
    manager_name: str,
) -> tuple[FilingSourcePeriod, ...]:
    if raw_value is None:
        return ()
    if not isinstance(raw_value, list):
        raise ValueError(f"Manager '{manager_name}' field 'filing_source_periods' must be a JSON array.")

    periods: list[FilingSourcePeriod] = []
    for raw_period in raw_value:
        if not isinstance(raw_period, dict):
            raise ValueError(
                f"Manager '{manager_name}' each filing source period must be an object."
            )

        raw_sources = raw_period.get("source_ciks")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise ValueError(
                f"Manager '{manager_name}' each filing source period must include non-empty 'source_ciks'."
            )
        source_ciks = tuple(
            source.strip() if isinstance(source, str) else "" for source in raw_sources
        )
        if not all(source_ciks) or len(set(source_ciks)) != len(source_ciks):
            raise ValueError(
                f"Manager '{manager_name}' filing source CIKs must be non-empty and unique."
            )

        from_quarter = _read_optional_quarter(
            raw_period.get("from_quarter"),
            manager_name=manager_name,
            field_name="from_quarter",
        )
        through_quarter = _read_optional_quarter(
            raw_period.get("through_quarter"),
            manager_name=manager_name,
            field_name="through_quarter",
        )
        if from_quarter is None and through_quarter is None:
            raise ValueError(
                f"Manager '{manager_name}' each filing source period must include "
                "'from_quarter' or 'through_quarter'."
            )
        if (
            from_quarter is not None
            and through_quarter is not None
            and quarter_sort_key(from_quarter) > quarter_sort_key(through_quarter)
        ):
            raise ValueError(
                f"Manager '{manager_name}' filing source period starts after it ends."
            )

        raw_overlap_policy = raw_period.get("overlap_policy")
        if raw_overlap_policy is None:
            overlap_policy = None
        elif isinstance(raw_overlap_policy, str):
            overlap_policy = raw_overlap_policy.strip()
        else:
            raise ValueError(
                f"Manager '{manager_name}' has unsupported filing source overlap policy."
            )
        if overlap_policy not in {None, "sum"}:
            raise ValueError(
                f"Manager '{manager_name}' has unsupported filing source overlap policy."
            )
        if len(source_ciks) > 1 and overlap_policy != "sum":
            raise ValueError(
                f"Manager '{manager_name}' composite filing sources require overlap_policy 'sum'."
            )
        if len(source_ciks) == 1 and overlap_policy is not None:
            raise ValueError(
                f"Manager '{manager_name}' overlap_policy is only valid for composite filing sources."
            )

        periods.append(
            FilingSourcePeriod(
                source_ciks=source_ciks,
                from_quarter=from_quarter,
                through_quarter=through_quarter,
                overlap_policy=overlap_policy,
            )
        )

    for index, period in enumerate(periods):
        period_start = quarter_sort_key(period.from_quarter) if period.from_quarter else None
        period_end = quarter_sort_key(period.through_quarter) if period.through_quarter else None
        for other in periods[index + 1 :]:
            other_start = quarter_sort_key(other.from_quarter) if other.from_quarter else None
            other_end = quarter_sort_key(other.through_quarter) if other.through_quarter else None
            starts_before_other_ends = other_end is None or period_start is None or period_start <= other_end
            other_starts_before_period_ends = period_end is None or other_start is None or other_start <= period_end
            if starts_before_other_ends and other_starts_before_period_ends:
                raise ValueError(f"Manager '{manager_name}' has overlapping filing source periods.")

    return tuple(periods)


def _load_managers_from_iterable(items: Iterable[dict[str, object]]) -> list[ManagerConfig]:
    managers: list[ManagerConfig] = []
    for item in items:
        raw_name = item.get("name")
        raw_cik = item.get("cik")
        raw_weight = item.get("weight")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        cik = raw_cik.strip() if isinstance(raw_cik, str) else ""
        if not name or not cik:
            raise ValueError("Each manager must include non-empty 'name' and 'cik'.")

        if isinstance(raw_weight, (int, float)):
            weight = float(raw_weight)
        elif isinstance(raw_weight, str):
            try:
                weight = float(raw_weight.strip())
            except ValueError as exc:
                raise ValueError(f"Manager '{name}' has invalid 'weight' value.") from exc
        else:
            raise ValueError(f"Manager '{name}' must include numeric 'weight'.")

        if weight <= 0:
            raise ValueError(f"Manager '{name}' must have 'weight' > 0.")
        filing_source_periods = _parse_filing_source_periods(
            item.get("filing_source_periods"),
            manager_name=name,
        )
        managers.append(
            ManagerConfig(
                name=name,
                cik=cik,
                weight=weight,
                filing_source_periods=filing_source_periods,
            )
        )
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

    resolved_db_path = Path(db_path or os.environ.get("DB_PATH", "") or (REPO_ROOT / "data" / "signals.sqlite3"))
    managers_file_env = os.environ.get("MANAGERS_FILE")
    managers_path = Path(managers_file) if managers_file else (Path(managers_file_env) if managers_file_env else None)
    managers_json = os.environ.get("MANAGERS_JSON")

    managers = load_managers(managers_path, managers_json)

    notifiers_list = _split_csv(notifiers or os.environ.get("NOTIFIERS"))

    telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    return AppConfig(
        sec_user_agent=sec_user_agent,
        sec_rate_limit_per_sec=sec_rate_limit_per_sec,
        max_filing_age_days=resolved_max_filing_age_days,
        db_path=resolved_db_path,
        managers=managers,
        notifiers=notifiers_list,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        notify_initial=notify_initial,
    )
