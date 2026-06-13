from __future__ import annotations

import pytest
import requests

from signals.domain.exceptions import (
    InformationTableLookupError,
    InformationTableNotFoundError,
    SubmissionsFetchError,
)
from signals.infrastructure.sec.sec_http_gateway import (
    FilingIndexEntry,
    SecClient,
    accession_no_dashes,
    cik_dir,
    normalize_cik,
)


class _DummyResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        text: str = "",
        json_payload: object = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self._json_payload = json_payload

    def json(self) -> object:
        if isinstance(self._json_payload, Exception):
            raise self._json_payload
        return self._json_payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"http status {self.status_code}")


def _patch_request_sequence(
    monkeypatch: pytest.MonkeyPatch,
    client: SecClient,
    events: list[_DummyResponse | Exception],
) -> None:
    def fake_request(*args: object, **kwargs: object) -> _DummyResponse:
        del args, kwargs
        event = events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event

    monkeypatch.setattr(client, "_throttle", lambda: None)
    monkeypatch.setattr(client._session, "request", fake_request)


def test_normalize_helpers() -> None:
    assert normalize_cik("123") == "0000000123"
    assert normalize_cik("CIK 1067983") == "0001067983"
    assert cik_dir("0001067983") == "1067983"
    assert accession_no_dashes("0001193125-25-282901") == "000119312525282901"


def test_throttle_sleeps_when_elapsed_below_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    client = SecClient(user_agent="test-agent", min_interval_seconds=0.5)
    client._last_request_time = 10.0

    monkeypatch.setattr("signals.infrastructure.sec.sec_http_gateway.time.time", lambda: 10.2)
    monkeypatch.setattr("signals.infrastructure.sec.sec_http_gateway.time.sleep", lambda value: sleeps.append(value))

    client._throttle()
    assert sleeps == [pytest.approx(0.3)]


def test_request_retries_on_retryable_http_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    client = SecClient(user_agent="test-agent")
    events: list[_DummyResponse | Exception] = [
        _DummyResponse(status_code=429, headers={"Retry-After": "2"}),
        _DummyResponse(status_code=503),
        _DummyResponse(status_code=200, json_payload={"ok": True}),
    ]
    _patch_request_sequence(monkeypatch, client, events)
    monkeypatch.setattr("signals.infrastructure.sec.sec_http_gateway.time.sleep", lambda value: sleeps.append(value))
    monkeypatch.setattr("signals.infrastructure.sec.sec_http_gateway.time.time", lambda: 100.0)

    response = client._request("GET", "https://example.com")
    assert response.status_code == 200
    assert sleeps == [2, 2]


def test_request_retries_after_request_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    client = SecClient(user_agent="test-agent")
    events: list[_DummyResponse | Exception] = [
        requests.ConnectionError("network"),
        _DummyResponse(status_code=200, json_payload={"ok": True}),
    ]
    _patch_request_sequence(monkeypatch, client, events)
    monkeypatch.setattr("signals.infrastructure.sec.sec_http_gateway.time.sleep", lambda value: sleeps.append(value))
    monkeypatch.setattr("signals.infrastructure.sec.sec_http_gateway.time.time", lambda: 100.0)

    response = client._request("GET", "https://example.com")
    assert response.status_code == 200
    assert sleeps == [1]


def test_request_raises_last_error_when_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SecClient(user_agent="test-agent")
    events: list[_DummyResponse | Exception] = [
        requests.Timeout("timeout-1"),
        requests.Timeout("timeout-2"),
    ]
    _patch_request_sequence(monkeypatch, client, events)
    monkeypatch.setattr("signals.infrastructure.sec.sec_http_gateway.time.sleep", lambda *_: None)

    with pytest.raises(requests.Timeout, match="timeout-2"):
        client._request("GET", "https://example.com", max_retries=2)


def test_request_raises_for_last_response_when_all_attempts_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SecClient(user_agent="test-agent")
    events: list[_DummyResponse | Exception] = [_DummyResponse(status_code=500), _DummyResponse(status_code=500)]
    _patch_request_sequence(monkeypatch, client, events)
    monkeypatch.setattr("signals.infrastructure.sec.sec_http_gateway.time.sleep", lambda *_: None)

    with pytest.raises(requests.HTTPError, match="http status 500"):
        client._request("GET", "https://example.com", max_retries=2)


def test_get_json_validates_payload_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SecClient(user_agent="test-agent")
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: _DummyResponse(json_payload={"ok": True}))
    assert client._get_json("https://example.com") == {"ok": True}

    monkeypatch.setattr(
        client,
        "_request",
        lambda *_args, **_kwargs: _DummyResponse(json_payload=ValueError("invalid")),
    )
    with pytest.raises(requests.RequestException, match="Failed to decode JSON"):
        client._get_json("https://example.com")

    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: _DummyResponse(json_payload=[1, 2, 3]))
    with pytest.raises(requests.RequestException, match="Expected JSON object"):
        client._get_json("https://example.com")


def test_get_text_and_get_submissions_wrap_request_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SecClient(user_agent="test-agent")

    def fail_request(*_args: object, **_kwargs: object) -> _DummyResponse:
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(client, "_request", fail_request)

    with pytest.raises(InformationTableLookupError, match="Failed to fetch text"):
        client.get_text("https://example.com/test.xml")

    with pytest.raises(SubmissionsFetchError, match="Failed to fetch submissions for CIK"):
        client.get_submissions("0001067983")


def test_get_filing_index_validates_shape_and_parses_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SecClient(user_agent="test-agent")

    monkeypatch.setattr(client, "_get_json", lambda *_args, **_kwargs: {"directory": []})
    with pytest.raises(InformationTableLookupError, match="Invalid filing index format"):
        client.get_filing_index("0001067983", "0001193125-25-282901")

    monkeypatch.setattr(client, "_get_json", lambda *_args, **_kwargs: {"directory": {"item": {}}})
    with pytest.raises(InformationTableLookupError, match="Invalid filing index entries"):
        client.get_filing_index("0001067983", "0001193125-25-282901")

    def fake_get_json(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "directory": {
                "item": [
                    {"name": "a.xml", "type": "EX-101"},
                    {"name": "", "type": "EX-101"},
                    {"name": "b.xml", "type": 10},
                    {"invalid": True},
                ]
            }
        }

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    rows = client.get_filing_index("0001067983", "0001193125-25-282901")
    assert rows == [
        FilingIndexEntry(name="a.xml", type="EX-101"),
        FilingIndexEntry(name="b.xml", type=None),
    ]


def test_get_filing_index_wraps_get_json_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SecClient(user_agent="test-agent")

    def fail_json(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise requests.RequestException("index down")

    monkeypatch.setattr(client, "_get_json", fail_json)
    with pytest.raises(InformationTableLookupError, match="Failed to fetch filing index"):
        client.get_filing_index("0001067983", "0001193125-25-282901")


def test_find_information_table_url_raises_when_no_xml_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SecClient(user_agent="test-agent")

    monkeypatch.setattr(client, "get_filing_index", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(client, "get_text", lambda *_args, **_kwargs: "<html><a href='plain.txt'>plain</a></html>")

    with pytest.raises(InformationTableNotFoundError, match="Could not locate information table XML"):
        client.find_information_table_url("0001067983", "0001193125-25-282901")


def test_find_information_table_url_uses_best_valid_xml_from_index(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SecClient(user_agent="test-agent")

    monkeypatch.setattr(
        client,
        "get_filing_index",
        lambda *_args, **_kwargs: [
            FilingIndexEntry(name="primary_doc.xml", type=None),
            FilingIndexEntry(name="actual_infotable.xml", type=None),
        ],
    )

    def fake_get_text(url: str) -> str:
        if url.endswith("primary_doc.xml"):
            return "<html>not xml info table</html>"
        if url.endswith("actual_infotable.xml"):
            return "<informationTable><infoTable/></informationTable>"
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(client, "get_text", fake_get_text)
    found = client.find_information_table_url("0001067983", "0001193125-25-282901")
    assert found.endswith("actual_infotable.xml")
