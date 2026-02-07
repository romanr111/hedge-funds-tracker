from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from types import MappingProxyType
from typing import Final, Iterable, Mapping, Protocol

import requests

from tracker.domain.exceptions import NotificationError


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


@dataclass
class EmailNotifier(Notifier):
    smtp_host: str
    smtp_port: int
    smtp_user: str | None
    smtp_pass: str | None
    email_from: str
    email_to: str

    def send(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.email_from
        message["To"] = self.email_to
        message.set_content(body)

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                server.starttls()
                if self.smtp_user and self.smtp_pass:
                    server.login(self.smtp_user, self.smtp_pass)
                server.send_message(message)
        except smtplib.SMTPException as exc:
            raise NotificationError(f"Failed to send email notification: {exc}") from exc


@dataclass(frozen=True)
class NotifierBuildConfig:
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_pass: str | None
    email_from: str | None
    email_to: str | None


class NotifierBuilder(Protocol):
    def __call__(self, config: NotifierBuildConfig) -> Notifier:
        ...


def _build_telegram_notifier(config: NotifierBuildConfig) -> Notifier:
    if not config.telegram_bot_token or not config.telegram_chat_id:
        raise ValueError("Telegram notifier requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
    return TelegramNotifier(bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id)


def _build_email_notifier(config: NotifierBuildConfig) -> Notifier:
    if not config.smtp_host or not config.email_from or not config.email_to:
        raise ValueError("Email notifier requires SMTP_HOST, EMAIL_FROM, and EMAIL_TO.")
    return EmailNotifier(
        smtp_host=config.smtp_host,
        smtp_port=config.smtp_port,
        smtp_user=config.smtp_user,
        smtp_pass=config.smtp_pass,
        email_from=config.email_from,
        email_to=config.email_to,
    )


DEFAULT_NOTIFIER_BUILDERS: Final[Mapping[str, NotifierBuilder]] = MappingProxyType(
    {
        "telegram": _build_telegram_notifier,
        "email": _build_email_notifier,
    }
)


def build_notifiers(
    notifier_names: Iterable[str],
    *,
    telegram_bot_token: str | None,
    telegram_chat_id: str | None,
    smtp_host: str | None,
    smtp_port: int,
    smtp_user: str | None,
    smtp_pass: str | None,
    email_from: str | None,
    email_to: str | None,
    builders: Mapping[str, NotifierBuilder] | None = None,
) -> list[Notifier]:
    config = NotifierBuildConfig(
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_pass=smtp_pass,
        email_from=email_from,
        email_to=email_to,
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
