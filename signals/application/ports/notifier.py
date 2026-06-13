from __future__ import annotations

from typing import Protocol


class NotifierPort(Protocol):
    def send(self, subject: str, body: str) -> None:
        ...
