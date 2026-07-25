from __future__ import annotations

import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Iterable, Mapping, Protocol

import requests

from signals.domain.exceptions import NotificationError

MAX_TELEGRAM_SEND_ATTEMPTS: Final = 3


def _status_code_for_error(exc: requests.RequestException) -> int | None:
    response = exc.response
    return response.status_code if response is not None else None


def _is_retryable_request_error(exc: requests.RequestException) -> bool:
    status_code = _status_code_for_error(exc)
    if status_code is not None:
        return status_code == 429 or 500 <= status_code < 600
    return isinstance(exc, (requests.ConnectionError, requests.Timeout))


def _retry_delay_seconds(exc: requests.RequestException, *, failed_attempt: int) -> float:
    response = exc.response
    if response is not None and response.status_code == 429:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            parameters = payload.get("parameters")
            retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
            if isinstance(retry_after, (int, float)) and retry_after > 0:
                return float(retry_after)
    return float(2**failed_attempt)


class Notifier:
    def send(self, subject: str, body: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class TelegramNotifier(Notifier):
    bot_token: str
    chat_id: str

    def send(self, subject: str, body: str) -> None:
        message = f"{subject}\n\n{body}".strip()
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        failure_detail = "request failed"
        for failed_attempt in range(MAX_TELEGRAM_SEND_ATTEMPTS):
            try:
                response = requests.post(url, json={"chat_id": self.chat_id, "text": message}, timeout=30)
                response.raise_for_status()
                return
            except requests.RequestException as exc:
                if _is_retryable_request_error(exc) and failed_attempt + 1 < MAX_TELEGRAM_SEND_ATTEMPTS:
                    time.sleep(_retry_delay_seconds(exc, failed_attempt=failed_attempt))
                    continue
                status_code = _status_code_for_error(exc)
                failure_detail = f"HTTP {status_code}" if status_code is not None else type(exc).__name__
                break
        raise NotificationError(f"Failed to send Telegram notification ({failure_detail}).")


@dataclass(frozen=True)
class NotifierBuildConfig:
    telegram_bot_token: str | None
    telegram_chat_id: str | None


class NotifierBuilder(Protocol):
    def __call__(self, config: NotifierBuildConfig) -> Notifier:
        ...


def _build_telegram_notifier(config: NotifierBuildConfig) -> Notifier:
    if not config.telegram_bot_token or not config.telegram_chat_id:
        raise ValueError("Telegram notifier requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
    return TelegramNotifier(bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id)


DEFAULT_NOTIFIER_BUILDERS: Final[Mapping[str, NotifierBuilder]] = MappingProxyType(
    {
        "telegram": _build_telegram_notifier,
    }
)


def build_notifiers(
    notifier_names: Iterable[str],
    *,
    telegram_bot_token: str | None,
    telegram_chat_id: str | None,
    builders: Mapping[str, NotifierBuilder] | None = None,
) -> list[Notifier]:
    config = NotifierBuildConfig(
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
    )
    notifier_builders = builders if builders is not None else DEFAULT_NOTIFIER_BUILDERS

    notifiers: list[Notifier] = []
    for name in notifier_names:
        key = name.strip().lower()
        builder = notifier_builders.get(key)
        if builder is None:
            raise ValueError(f"Unknown notifier: {name}")
        notifiers.append(builder(config))
    return notifiers
