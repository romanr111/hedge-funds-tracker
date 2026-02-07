from __future__ import annotations

from dataclasses import dataclass

from tracker.config import AppConfig
from tracker.infrastructure.notify.notifiers import Notifier, build_notifiers
from tracker.infrastructure.sec.sec_http_gateway import SecClient
from tracker.infrastructure.storage.sqlite_state_repository import StateStore


@dataclass(frozen=True)
class Runtime:
    client: SecClient
    store: StateStore
    notifiers: list[Notifier]


def build_notifier_list(config: AppConfig, *, dry_run: bool, test_notification: bool) -> list[Notifier]:
    if dry_run and not test_notification:
        return []
    if not config.notifiers:
        return []
    return build_notifiers(
        config.notifiers,
        telegram_bot_token=config.telegram_bot_token,
        telegram_chat_id=config.telegram_chat_id,
    )


def build_runtime(config: AppConfig, *, dry_run: bool, test_notification: bool) -> Runtime:
    notifiers = build_notifier_list(config, dry_run=dry_run, test_notification=test_notification)
    min_interval = 1.0 / config.sec_rate_limit_per_sec
    client = SecClient(user_agent=config.sec_user_agent, min_interval_seconds=min_interval)
    store = StateStore(config.db_path)
    return Runtime(client=client, store=store, notifiers=notifiers)
