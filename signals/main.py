from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from signals.application.ports.notifier import NotifierPort
from signals.application.ports.sec_gateway import SecGateway
from signals.application.ports.state_repository import StateRepository
from signals.application.use_cases.track_manager import process_manager as _process_manager
from signals.domain.filings import extract_filings, filing_to_dict, filings_from_dicts, filter_by_filing_age
from signals.domain.models import Manager
from signals.domain.timing import format_local_datetime, now_kyiv
from signals.interfaces.cli.main import main as cli_main
from signals.parse_13f import parse_infotable


class _ManagerLike(Protocol):
    name: str
    cik: str


def _extract_filings(submissions: dict[str, Any]) -> list[dict[str, str | None]]:
    return [filing_to_dict(filing) for filing in extract_filings(submissions)]


def _now_kyiv() -> datetime:
    return now_kyiv()


def _format_local_datetime(value: datetime) -> str:
    return format_local_datetime(value)


def _filter_by_filing_age(filings: list[dict[str, Any]], max_filing_age_days: int) -> list[dict[str, str | None]]:
    typed_filings = filings_from_dicts(filings)
    filtered = filter_by_filing_age(typed_filings, max_filing_age_days, today=_now_kyiv().date())
    return [filing_to_dict(filing) for filing in filtered]


def _send_notifications(notifiers: Sequence[NotifierPort], subject: str, body: str) -> None:
    for notifier in notifiers:
        notifier.send(subject, body)


def process_manager(
    manager: _ManagerLike,
    store: StateRepository,
    client: SecGateway,
    notifiers: Sequence[NotifierPort],
    *,
    notify_initial: bool,
    dry_run: bool,
    max_filing_age_days: int,
) -> None:
    _process_manager(
        Manager(name=manager.name, cik=manager.cik),
        store,
        client,
        notifiers,
        notify_initial=notify_initial,
        dry_run=dry_run,
        max_filing_age_days=max_filing_age_days,
        parse_infotable_fn=parse_infotable,
        now_fn=_now_kyiv,
    )


def main() -> int:
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
