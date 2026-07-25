from __future__ import annotations

import time
from typing import Mapping

import pytest
import requests

from signals.domain.exceptions import NotificationError
from signals.infrastructure.notify.notifiers import (
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
        )


def test_build_notifiers_telegram_requires_credentials() -> None:
    with pytest.raises(ValueError, match="Telegram notifier requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"):
        build_notifiers(
            ["telegram"],
            telegram_bot_token=None,
            telegram_chat_id=None,
        )


def test_build_notifiers_builds_telegram_notifier() -> None:
    notifiers = build_notifiers(
        ["telegram"],
        telegram_bot_token="bot-token",
        telegram_chat_id="chat-id",
    )
    assert len(notifiers) == 1
    assert isinstance(notifiers[0], TelegramNotifier)


def test_build_notifiers_uses_custom_registry_builder() -> None:
    def build_custom(config: NotifierBuildConfig) -> Notifier:
        assert config.telegram_bot_token is None
        assert config.telegram_chat_id is None
        return _CustomNotifier()

    custom_builders: Mapping[str, NotifierBuilder] = {"custom": build_custom}
    notifiers = build_notifiers(
        ["custom"],
        telegram_bot_token=None,
        telegram_chat_id=None,
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
            builders={},
        )


def test_telegram_notifier_does_not_expose_token_when_request_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_request(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise requests.HTTPError(
            "401 Client Error: Unauthorized for url: https://api.telegram.org/botexample-secret-token/sendMessage"
        )

    monkeypatch.setattr("signals.infrastructure.notify.notifiers.requests.post", fail_request)

    with pytest.raises(NotificationError) as raised:
        TelegramNotifier(bot_token="example-secret-token", chat_id="chat-id").send("Subject", "Body")

    assert "example-secret-token" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_telegram_notifier_retries_transient_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    failed_response = requests.Response()
    failed_response.status_code = 500
    successful_response = requests.Response()
    successful_response.status_code = 200
    responses = [failed_response, successful_response]
    sleeps: list[float] = []

    def post(*args: object, **kwargs: object) -> requests.Response:
        del args, kwargs
        return responses.pop(0)

    monkeypatch.setattr("signals.infrastructure.notify.notifiers.requests.post", post)
    monkeypatch.setattr(time, "sleep", sleeps.append)

    TelegramNotifier(bot_token="token", chat_id="chat-id").send("Subject", "Body")

    assert responses == []
    assert sleeps == [1.0]


def test_telegram_notifier_honors_telegram_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    rate_limited_response = requests.Response()
    rate_limited_response.status_code = 429
    rate_limited_response._content = b'{"ok":false,"parameters":{"retry_after":4}}'
    successful_response = requests.Response()
    successful_response.status_code = 200
    responses = [rate_limited_response, successful_response]
    sleeps: list[float] = []

    def post(*args: object, **kwargs: object) -> requests.Response:
        del args, kwargs
        return responses.pop(0)

    monkeypatch.setattr("signals.infrastructure.notify.notifiers.requests.post", post)
    monkeypatch.setattr(time, "sleep", sleeps.append)

    TelegramNotifier(bot_token="token", chat_id="chat-id").send("Subject", "Body")

    assert responses == []
    assert sleeps == [4.0]


def test_telegram_notifier_stops_after_three_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [requests.Response(), requests.Response(), requests.Response()]
    for response in responses:
        response.status_code = 500
    sleeps: list[float] = []

    def post(*args: object, **kwargs: object) -> requests.Response:
        del args, kwargs
        return responses.pop(0)

    monkeypatch.setattr("signals.infrastructure.notify.notifiers.requests.post", post)
    monkeypatch.setattr(time, "sleep", sleeps.append)

    with pytest.raises(NotificationError, match="HTTP 500"):
        TelegramNotifier(bot_token="token", chat_id="chat-id").send("Subject", "Body")

    assert responses == []
    assert sleeps == [1.0, 2.0]


def test_telegram_notifier_retries_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    def post(*args: object, **kwargs: object) -> requests.Response:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        raise requests.Timeout("request timed out")

    monkeypatch.setattr("signals.infrastructure.notify.notifiers.requests.post", post)
    monkeypatch.setattr(time, "sleep", sleeps.append)

    with pytest.raises(NotificationError, match="Timeout"):
        TelegramNotifier(bot_token="token", chat_id="chat-id").send("Subject", "Body")

    assert attempts == 3
    assert sleeps == [1.0, 2.0]
