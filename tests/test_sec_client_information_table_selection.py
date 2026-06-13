from __future__ import annotations

import pytest

from signals.domain.exceptions import InformationTableLookupError
from signals.sec_client import FilingIndexEntry, SecClient


def test_find_information_table_skips_primary_doc_html(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SecClient(user_agent="test-agent")

    monkeypatch.setattr(
        client,
        "get_filing_index",
        lambda cik, accession: [
            FilingIndexEntry(name="primary_doc.xml", type=None),
            FilingIndexEntry(name="46994.xml", type=None),
        ],
    )

    def fake_get_text(url: str) -> str:
        if url.endswith("/primary_doc.xml"):
            return "<html><head></head><body>not information table</body></html>"
        if url.endswith("/46994.xml"):
            return "<informationTable><infoTable/></informationTable>"
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(client, "get_text", fake_get_text)

    url = client.find_information_table_url("0001067983", "0001193125-25-282901")

    assert url.endswith("/46994.xml")


def test_find_information_table_from_index_html_validates_xml(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SecClient(user_agent="test-agent")

    def fake_get_filing_index(cik: str, accession: str) -> list[FilingIndexEntry]:
        del cik, accession
        raise InformationTableLookupError("index.json unavailable")

    def fake_get_text(url: str) -> str:
        if url.endswith("-index.html"):
            return (
                '<a href="xslForm13F_X02/primary_doc.xml">primary</a>'
                '<a href="form13f_20250930.xml">info</a>'
            )
        if url.endswith("/xslForm13F_X02/primary_doc.xml"):
            return "<html><body>rendered filing page</body></html>"
        if url.endswith("/form13f_20250930.xml"):
            return "<informationTable><infoTable/></informationTable>"
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(client, "get_filing_index", fake_get_filing_index)
    monkeypatch.setattr(client, "get_text", fake_get_text)

    url = client.find_information_table_url("0001536411", "0001536411-25-000017")

    assert url.endswith("/form13f_20250930.xml")
