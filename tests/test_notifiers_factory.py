from __future__ import annotations

from typing import Mapping

import pytest

from tracker.infrastructure.notify.notifiers import (
    Notifier,
    NotifierBuildConfig,
    NotifierBuilder,
    TelegramNotifier,
    build_notifiers,
)


class _CustomNotifier(Notifier):
    def send(self, subject: str, body: str) -> None:
        del subject, body


def test_build_notifiers_unknown_name_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown notifier: sms"):
        build_notifiers(
            ["sms"],
            telegram_bot_token=None,
            telegram_chat_id=None,
            smtp_host=None,
            smtp_port=587,
            smtp_user=None,
            smtp_pass=None,
            email_from=None,
            email_to=None,
        )


def test_build_notifiers_telegram_requires_credentials() -> None:
    with pytest.raises(ValueError, match="Telegram notifier requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"):
        build_notifiers(
            ["telegram"],
            telegram_bot_token=None,
            telegram_chat_id=None,
            smtp_host=None,
            smtp_port=587,
            smtp_user=None,
            smtp_pass=None,
            email_from=None,
            email_to=None,
        )


def test_build_notifiers_builds_telegram_notifier() -> None:
    notifiers = build_notifiers(
        ["telegram"],
        telegram_bot_token="bot-token",
        telegram_chat_id="chat-id",
        smtp_host=None,
        smtp_port=587,
        smtp_user=None,
        smtp_pass=None,
        email_from=None,
        email_to=None,
    )
    assert len(notifiers) == 1
    assert isinstance(notifiers[0], TelegramNotifier)


def test_build_notifiers_uses_custom_registry_builder() -> None:
    def build_custom(config: NotifierBuildConfig) -> Notifier:
        assert config.smtp_port == 587
        return _CustomNotifier()

    custom_builders: Mapping[str, NotifierBuilder] = {"custom": build_custom}
    notifiers = build_notifiers(
        ["custom"],
        telegram_bot_token=None,
        telegram_chat_id=None,
        smtp_host=None,
        smtp_port=587,
        smtp_user=None,
        smtp_pass=None,
        email_from=None,
        email_to=None,
        builders=custom_builders,
    )
    assert len(notifiers) == 1
    assert isinstance(notifiers[0], _CustomNotifier)


def test_build_notifiers_empty_registry_is_not_replaced_with_default() -> None:
    with pytest.raises(ValueError, match="Unknown notifier: telegram"):
        build_notifiers(
            ["telegram"],
            telegram_bot_token="bot-token",
            telegram_chat_id="chat-id",
            smtp_host=None,
            smtp_port=587,
            smtp_user=None,
            smtp_pass=None,
            email_from=None,
            email_to=None,
            builders={},
        )
