from __future__ import annotations

from typing import Any, Protocol


class SecGateway(Protocol):
    def get_submissions(self, cik: str) -> dict[str, Any]:
        ...

    def find_information_table_url(self, cik: str, accession: str) -> str:
        ...

    def get_text(self, url: str) -> str:
        ...
