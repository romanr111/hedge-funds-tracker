from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable

import requests


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
        response = requests.post(url, json={"chat_id": self.chat_id, "text": message}, timeout=30)
        response.raise_for_status()


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

        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
            server.starttls()
            if self.smtp_user and self.smtp_pass:
                server.login(self.smtp_user, self.smtp_pass)
            server.send_message(message)


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
) -> list[Notifier]:
    notifiers: list[Notifier] = []
    for name in notifier_names:
        key = name.strip().lower()
        if key == "telegram":
            if not telegram_bot_token or not telegram_chat_id:
                raise ValueError("Telegram notifier requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
            notifiers.append(TelegramNotifier(bot_token=telegram_bot_token, chat_id=telegram_chat_id))
        elif key == "email":
            if not smtp_host or not email_from or not email_to:
                raise ValueError("Email notifier requires SMTP_HOST, EMAIL_FROM, and EMAIL_TO.")
            notifiers.append(
                EmailNotifier(
                    smtp_host=smtp_host,
                    smtp_port=smtp_port,
                    smtp_user=smtp_user,
                    smtp_pass=smtp_pass,
                    email_from=email_from,
                    email_to=email_to,
                )
            )
        else:
            raise ValueError(f"Unknown notifier: {name}")
    return notifiers
