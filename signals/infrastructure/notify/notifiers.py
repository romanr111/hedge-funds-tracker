from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Iterable, Mapping, Protocol

import requests

from signals.domain.exceptions import NotificationError


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
        try:
            response = requests.post(url, json={"chat_id": self.chat_id, "text": message}, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise NotificationError(f"Failed to send Telegram notification: {exc}") from exc


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
