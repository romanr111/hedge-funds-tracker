from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import requests

import market_intel.infrastructure.factset_gateway as factset_module
import market_intel.infrastructure.http_base as http_base
from market_intel.application.use_cases.collect_vintages import collect_vintages
from market_intel.domain.exceptions import CollectorError
from market_intel.domain.models import (
    SKIP_COMPUTED,
    SKIP_INTERNAL,
    SKIP_MANUAL,
    SKIP_SOURCE_ERROR,
    CollectionResult,
    SeriesObservation,
    SeriesSpec,
)
from market_intel.infrastructure.cnn_gateway import CnnFgGateway
from market_intel.infrastructure.damodaran_gateway import DamodaranGateway
from market_intel.infrastructure.factset_gateway import FactsetGateway
from market_intel.infrastructure.fred_gateway import FredCsvGateway
from market_intel.infrastructure.finra_gateway import FinraGateway
from market_intel.infrastructure.http_base import RateLimitExceeded
from market_intel.infrastructure.multpl_gateway import MultplGateway
from market_intel.infrastructure.yahoo_gateway import YahooChartGateway
from market_intel.registries import SERIES_SPECS

FIXTURES = Path(__file__).parent / "fixtures" / "market_intel"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class _FakeResponse:
    def __init__(
        self,
        text: str = "",
        status_code: int = 200,
        *,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        url: str = "https://example.test/page",
    ) -> None:
        self.text = text
        self.status_code = status_code
        self.content = content if content is not None else text.encode()
        self.headers = headers or {}
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self) -> Any:
        return json.loads(self.text)


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}

    def request(self, method: str, url: str, timeout: Any = None, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self._responses:
            raise RuntimeError("No fake responses configured")
        return self._responses.pop(0)


def _spec(key: str) -> SeriesSpec:
    return SERIES_SPECS[key]


# --- http_base ------------------------------------------------------------


def test_request_retry_and_failure_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[float] = []
    monkeypatch.setattr(http_base.time, "sleep", recorded.append)

    # 429 then 503 then success.
    session = _FakeSession([
        _FakeResponse(status_code=429, headers={"Retry-After": "0"}),
        _FakeResponse(status_code=503, headers={"Retry-After": "0"}),
        _FakeResponse('{"ok": true}'),
    ])
    gateway = FredCsvGateway(session=session, min_interval_seconds=0.0)
    assert gateway._get_json("https://example.test/x") == {"ok": True}
    assert len(session.calls) == 3

    # Retry-After is honored; no hardcoded backoff is added on rate limits.
    recorded.clear()
    session = _FakeSession([
        _FakeResponse(status_code=429, headers={"Retry-After": "7"}),
        _FakeResponse("ok"),
    ])
    gateway = FredCsvGateway(session=session, min_interval_seconds=0.0)
    assert gateway._get_text("https://example.test/x") == "ok"
    assert recorded == [7]

    # Exhausting retries on 429 raises the structured error.
    recorded.clear()
    session = _FakeSession([
        _FakeResponse(status_code=429, headers={"Retry-After": "0"}),
        _FakeResponse(status_code=429, headers={"Retry-After": "0"}),
        _FakeResponse(status_code=429, headers={"Retry-After": "0"}),
    ])
    gateway = FredCsvGateway(session=session, min_interval_seconds=0.0)
    with pytest.raises(RateLimitExceeded, match="HTTP 429") as exc_info:
        gateway._get_text("https://example.test/x")
    assert exc_info.value.attempts == 3
    assert isinstance(exc_info.value, CollectorError)
    assert len(session.calls) == 3
    assert recorded == [0, 0]

    # Transport errors back off 1s then 2s and surface as CollectorError.
    recorded.clear()

    class _BrokenSession:
        headers: dict[str, str] = {}

        def request(self, *args: Any, **kwargs: Any) -> _FakeResponse:
            raise requests.ConnectionError("conn refused")

    gateway = FredCsvGateway(session=_BrokenSession(), min_interval_seconds=0.0)  # type: ignore[arg-type]
    with pytest.raises(CollectorError, match="Failed to fetch"):
        gateway._get_text("https://example.test/x")
    assert recorded == [1, 2]


# --- FRED ------------------------------------------------------------------


def test_fred_gateway_parses_fixture_and_sets_user_agent() -> None:
    session = _FakeSession([_FakeResponse(_fixture("fred_hy_oas.csv"))])
    gateway = FredCsvGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("hy_oas"), now_utc=_utc(2026, 11, 7, 7))

    assert session.headers["User-Agent"] == "market-intel/0.1 (local CLI)"
    assert len(session.calls) == 1
    assert session.calls[0]["url"].endswith("fredgraph.csv")
    assert session.calls[0]["params"] == {"id": "BAMLH0A0HYM2"}
    assert observations[-1].series_id == "hy_oas"
    assert observations[-1].obs_date == date(2026, 11, 5)
    assert observations[-1].value == 3.48
    assert observations[-1].source == "fred"
    assert observations[-1].fetched_at == _utc(2026, 11, 7, 7)
    assert all(o.obs_date >= date(2026, 10, 29) for o in observations)


def test_fred_gateway_retries_plain_url_when_cosd_request_fails() -> None:
    session = _FakeSession([
        _FakeResponse("Not found", status_code=404),
        _FakeResponse(_fixture("fred_hy_oas.csv")),
    ])
    gateway = FredCsvGateway(session=session, min_interval_seconds=0.0)
    spec = SeriesSpec(
        key="hy_oas",
        title_uk="test",
        source="fred",
        freq="daily",
        staleness_days=3,
        params={"series_id": "BAMLH0A0HYM2", "cosd": "2026-10-29"},
    )

    observations = gateway.fetch(spec, now_utc=_utc(2026, 11, 7, 7))

    assert session.calls[0]["params"] == {"id": "BAMLH0A0HYM2", "cosd": "2026-10-29"}
    assert session.calls[1]["params"] == {"id": "BAMLH0A0HYM2"}
    assert observations


def test_fred_gateway_rejects_wrong_header_and_wrong_source() -> None:
    session = _FakeSession([_FakeResponse("observation_date,SOMETHING\n2026-11-05,3.4")])
    gateway = FredCsvGateway(session=session, min_interval_seconds=0.0)
    with pytest.raises(CollectorError, match="unexpected header"):
        gateway.fetch(_spec("hy_oas"), now_utc=_utc(2026, 11, 7, 7))

    session = _FakeSession([_FakeResponse("observation_date,BAMLH0A0HYM2\n2026-11-05,3.4")])
    gateway = FredCsvGateway(session=session, min_interval_seconds=0.0)
    with pytest.raises(CollectorError, match="source='fred'"):
        gateway.fetch(SERIES_SPECS["spx"], now_utc=_utc(2026, 11, 7, 7))


def test_fred_gateway_rejects_headerless_and_header_only_payloads() -> None:
    # A 200 that is not a FRED CSV (an error page, an interstitial) must not
    # register as a successful fetch with zero observations.
    session = _FakeSession([
        _FakeResponse("Just an error page, no CSV"),
        _FakeResponse("observation_date,BAMLH0A0HYM2\n2026-11-05,."),
    ])
    gateway = FredCsvGateway(session=session, min_interval_seconds=0.0)

    with pytest.raises(CollectorError, match="missing observation_date header"):
        gateway.fetch(_spec("hy_oas"), now_utc=_utc(2026, 11, 7, 7))
    with pytest.raises(CollectorError, match="no parsable observations"):
        gateway.fetch(_spec("hy_oas"), now_utc=_utc(2026, 11, 7, 7))


# --- Yahoo -----------------------------------------------------------------


def test_yahoo_gateway_parses_fixture_dropping_incomplete_last_bar() -> None:
    # Fixture bars: Jan 2, 3, 6, 7 2025 (Jan 7 close, one None session, then last bar).
    # now_utc = Jan 7 19:00Z -> 14:00 NY, so the Jan 7 bar is an incomplete session.
    session = _FakeSession([_FakeResponse(_fixture("yahoo_chart.json"))])
    gateway = YahooChartGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("spx"), now_utc=_utc(2025, 1, 7, 19))

    assert [(o.obs_date, o.value) for o in observations] == [
        (date(2025, 1, 2), 100.5),
        (date(2025, 1, 6), 101.25),
    ]
    # Observations must land under the registry key, not the Yahoo symbol.
    assert {o.series_id for o in observations} == {"spx"}

    # Registry key != Yahoo symbol (^VIX3M): the request path is URL-encoded
    # and the stored series id stays spec.key.
    session = _FakeSession([_FakeResponse(_fixture("yahoo_chart.json"))])
    gateway = YahooChartGateway(session=session, min_interval_seconds=0.0)
    vix3m = gateway.fetch(_spec("vix3m"), now_utc=_utc(2025, 1, 7, 22, 30))
    assert {o.series_id for o in vix3m} == {"vix3m"}
    assert session.calls[0]["url"].endswith("/chart/%5EVIX3M")
    assert session.calls[0]["params"] == {"range": "5y", "interval": "1d"}

    # After the market close the same last bar is a complete session and kept.
    session = _FakeSession([_FakeResponse(_fixture("yahoo_chart.json"))])
    gateway = YahooChartGateway(session=session, min_interval_seconds=0.0)
    closed = gateway.fetch(_spec("spx"), now_utc=_utc(2025, 1, 7, 22, 30))
    assert [(o.obs_date, o.value) for o in closed] == [
        (date(2025, 1, 2), 100.5),
        (date(2025, 1, 6), 101.25),
        (date(2025, 1, 7), 99.0),
    ]


def test_yahoo_gateway_opens_breaker_after_three_consecutive_429s() -> None:
    responses = [
        _FakeResponse("Too Many Requests", status_code=429, headers={"Retry-After": "0"})
        for _ in range(3)
    ]
    session = _FakeSession(responses)
    gateway = YahooChartGateway(session=session, min_interval_seconds=0.0)

    for _ in range(3):
        with pytest.raises(CollectorError):
            gateway.fetch(_spec("spx"), now_utc=_utc(2025, 1, 7, 19))
    assert len(session.calls) == 3
    assert gateway.breaker_open is True

    # Breaker open: no network calls, empty list served.
    result = gateway.fetch(_spec("spx"), now_utc=_utc(2025, 1, 7, 19))
    assert result == []
    assert len(session.calls) == 3


def test_yahoo_gateway_rejects_bad_payload_shape_and_missing_symbol() -> None:
    session = _FakeSession([_FakeResponse("{\"chart\":{\"result\":[]},\"error\":null}")])
    gateway = YahooChartGateway(session=session, min_interval_seconds=0.0)
    with pytest.raises(CollectorError, match="unexpected payload shape"):
        gateway.fetch(_spec("spx"), now_utc=_utc(2025, 1, 7, 19))

    session = _FakeSession([])
    gateway = YahooChartGateway(session=session, min_interval_seconds=0.0)
    spec = SeriesSpec(key="x", title_uk="x", source="yahoo", freq="daily", staleness_days=3, params={})
    with pytest.raises(CollectorError, match="'symbol'"):
        gateway.fetch(spec, now_utc=_utc(2025, 1, 7, 19))


# --- multpl ----------------------------------------------------------------


def test_multpl_gateway_parses_current_and_month_end_history() -> None:
    # The fetch date rides the injected now_utc (November: Kyiv = UTC+2 -> 09:00).
    session = _FakeSession([
        _FakeResponse(_fixture("multpl_current.html")),
        _FakeResponse(_fixture("multpl_history.html")),
    ])
    gateway = MultplGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("cape"), now_utc=_utc(2026, 11, 7, 7))

    # History: Aug/Sep/Oct month-ends (Nov rows are future month-ends), then the current print.
    history = observations[:-1]
    assert [(o.obs_date, o.value) for o in history] == [
        (date(2026, 8, 31), 41.00),
        (date(2026, 9, 30), 40.42),
        (date(2026, 10, 31), 40.60),
    ]
    current = observations[-1]
    assert current.value == 38.91
    assert current.obs_date == date(2026, 11, 7)
    assert {o.series_id for o in observations} == {"cape"}
    assert len(session.calls) == 2

    # The same-month history row collapses to the freshest print.
    from market_intel.infrastructure.multpl_gateway import parse_monthly_history

    points = parse_monthly_history(_fixture("multpl_history.html"), fetched_on=date(2026, 12, 10))
    assert points[-1] == (date(2026, 11, 30), 38.91)  # Nov 6 daily print beats Nov 1 monthly
    assert points[-2] == (date(2026, 10, 31), 40.60)


def test_multpl_gateway_raises_without_current_value() -> None:
    session = _FakeSession([
        _FakeResponse("<html><body>no ratio here</body></html>"),
        _FakeResponse(_fixture("multpl_history.html")),
    ])
    gateway = MultplGateway(session=session, min_interval_seconds=0.0)
    with pytest.raises(CollectorError, match="Current Shiller PE Ratio"):
        gateway.fetch(_spec("cape"), now_utc=_utc(2026, 11, 7, 7))


# --- FINRA -----------------------------------------------------------------


def test_finra_gateway_parses_margin_table_fixture() -> None:
    session = _FakeSession([_FakeResponse(_fixture("finra_margin.html"))])
    gateway = FinraGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("margin_debt"), now_utc=_utc(2026, 11, 7, 7))

    assert [(o.obs_date, o.value) for o in observations] == [
        (date(2026, 8, 31), 1460000.0),
        (date(2026, 9, 30), 1480000.0),
    ]
    assert observations[0].source == "finra"
    assert {o.series_id for o in observations} == {"margin_debt"}


def test_finra_gateway_fails_safe_on_unknown_layout() -> None:
    session = _FakeSession([_FakeResponse("<html><body><p>Pricing page</p></body></html>")])
    gateway = FinraGateway(session=session, min_interval_seconds=0.0)
    with pytest.raises(CollectorError, match="Debit Balances"):
        gateway.fetch(_spec("margin_debt"), now_utc=_utc(2026, 11, 7, 7))


# --- CNN -------------------------------------------------------------------


def test_cnn_gateway_parses_latest_score_with_timestamp_date() -> None:
    session = _FakeSession([_FakeResponse(_fixture("cnn_fg.json"))])
    gateway = CnnFgGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("fg"), now_utc=_utc(2026, 11, 7, 7))

    assert len(observations) == 1
    assert observations[0].value == pytest.approx(22.0792756539235)
    assert observations[0].obs_date == date(2026, 11, 6)  # timestamp -> US date
    assert observations[0].series_id == "fg"
    assert session.headers["User-Agent"].startswith("Mozilla/5.0")

    # An explicit fetched_on field wins; otherwise the US trading date of now_utc.
    payload = '{"fear_and_greed": {"score": 31.5, "fetched_on": "2026-11-05T10:00:00-05:00"}}'
    session = _FakeSession([_FakeResponse(payload)])
    gateway = CnnFgGateway(session=session, min_interval_seconds=0.0)
    observations = gateway.fetch(_spec("fg"), now_utc=_utc(2026, 11, 7, 7))
    assert observations[0].obs_date == date(2026, 11, 5)

    session = _FakeSession([_FakeResponse('{"fear_and_greed": {"score": 31.5}}')])
    gateway = CnnFgGateway(session=session, min_interval_seconds=0.0)
    observations = gateway.fetch(_spec("fg"), now_utc=_utc(2025, 1, 7, 22, 30))
    assert observations[0].obs_date == date(2025, 1, 7)  # 17:30 NY

    session = _FakeSession([_FakeResponse('{"nope": true}')])
    gateway = CnnFgGateway(session=session, min_interval_seconds=0.0)
    with pytest.raises(CollectorError, match="fear_and_greed"):
        gateway.fetch(_spec("fg"), now_utc=_utc(2025, 1, 7, 22, 30))


# --- FactSet ---------------------------------------------------------------


def test_factset_gateway_uses_direct_pdf_response_with_pdftotext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[str] = []
    real_tmp = factset_module.tempfile.NamedTemporaryFile

    def recording_tmp(*args: Any, **kwargs: Any) -> Any:
        handle = real_tmp(*args, **kwargs)
        written.append(handle.name)
        return handle

    monkeypatch.setattr(factset_module.tempfile, "NamedTemporaryFile", recording_tmp)
    pdf_fixture = _fixture("factset_forward_pe.txt")

    def fake_run(pdf_path: str, **kwargs: Any) -> str:
        assert pdf_path.endswith(".pdf")
        return pdf_fixture

    monkeypatch.setattr(factset_module, "run_pdftotext", fake_run)
    session = _FakeSession([
        _FakeResponse(
            content=b"%PDF-1.7 fake",
            headers={"Content-Type": "application/pdf"},
            url="https://advantage.factset.com/EarningsInsight_110626A.pdf",
        )
    ])
    gateway = FactsetGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("forward_pe"), now_utc=_utc(2026, 11, 7, 7))

    assert len(session.calls) == 1
    assert len(observations) == 1
    assert observations[0].value == 17.7
    assert observations[0].source == "factset"
    assert observations[0].series_id == "forward_pe"
    # MMDDYY stamp in the resolved filename (EarningsInsight_110626A.pdf) wins
    # over the injected clock: a re-run of the same report dedupes cleanly.
    assert observations[0].obs_date == date(2026, 11, 6)

    # The temp PDF is deleted after parsing.
    assert written, "gateway must write a temp pdf"
    assert all(not Path(name).exists() for name in written)

    # No filename stamp and no cover date in the fixture text: the injected
    # clock (Kyiv 09:00 Nov 7) is the fallback, and the "November 1, 2023"
    # historical reference in the fixture must be ignored.
    session = _FakeSession([_FakeResponse(content=b"%PDF-1.7 fake")])
    gateway = FactsetGateway(session=session, min_interval_seconds=0.0)
    clocked = gateway.fetch(_spec("forward_pe"), now_utc=_utc(2026, 11, 7, 7))
    assert clocked[0].obs_date == date(2026, 11, 7)
    assert all(not Path(name).exists() for name in written)

    # A landing page without a direct PDF follows the discovered PDF link.
    landing = _fixture("factset_landing.html")
    monkeypatch.setattr(factset_module, "run_pdftotext", lambda *a, **k: pdf_fixture)
    session = _FakeSession([
        _FakeResponse(landing, url="https://www.factset.com/earningsinsight"),
        _FakeResponse(content=b"%PDF-1.7 fake"),
    ])
    gateway = FactsetGateway(session=session, min_interval_seconds=0.0)
    linked = gateway.fetch(_spec("forward_pe"), now_utc=_utc(2026, 11, 7, 7))
    assert len(session.calls) == 2
    assert session.calls[1]["url"].endswith("EarningsInsight_110626A.pdf")
    assert linked[0].value == 17.7
    assert linked[0].obs_date == date(2026, 11, 6)  # filename MMDDYY stamp


def test_factset_gateway_raises_when_pdftotext_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factset_module.shutil, "which", lambda name: None)
    session = _FakeSession([
        _FakeResponse(content=b"%PDF-1.7 fake", headers={"Content-Type": "application/pdf"})
    ])
    gateway = FactsetGateway(session=session, min_interval_seconds=0.0)
    with pytest.raises(CollectorError, match="pdftotext"):
        gateway.fetch(_spec("forward_pe"), now_utc=_utc(2026, 11, 7, 7))


def test_factset_gateway_fails_without_pdf_link() -> None:
    session = _FakeSession([
        _FakeResponse("<html><body>No downloads here</body></html>", url="https://x.test/page")
    ])
    gateway = FactsetGateway(session=session, min_interval_seconds=0.0)
    with pytest.raises(CollectorError, match="PDF"):
        gateway.fetch(_spec("forward_pe"), now_utc=_utc(2026, 11, 7, 7))


def test_factset_gateway_takes_cover_date_from_pdf_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_fixture = _fixture("factset_cover_date.txt")
    monkeypatch.setattr(factset_module, "run_pdftotext", lambda *a, **k: pdf_fixture)
    session = _FakeSession([
        _FakeResponse(
            content=b"%PDF-1.7 fake",
            headers={"Content-Type": "application/pdf"},
            # No MMDDYY stamp in the filename: the cover date must be found
            # in the text near the report masthead, not the trap date and
            # not the injected Nov 5 clock.
            url="https://advantage.factset.com/EarningsInsight.pdf",
        )
    ])
    gateway = FactsetGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("forward_pe"), now_utc=_utc(2026, 11, 5, 9))

    assert observations[0].value == 18.2
    assert observations[0].obs_date == date(2026, 11, 6)


def test_factset_date_parsers_reject_invalid_and_historical_dates() -> None:
    assert factset_module.date_from_pdf_filename(
        "https://advantage.factset.com/hubfs/EarningsInsight_110626A.pdf"
    ) == date(2026, 11, 6)
    # Not an MMDDYY stamp: month 24 is invalid, bare .pdf has no digits.
    assert factset_module.date_from_pdf_filename("https://x.test/report_241201.pdf") is None
    assert factset_module.date_from_pdf_filename("https://x.test/EarningsInsight.pdf") is None
    assert factset_module.date_from_pdf_filename("https://x.test/page") is None
    # The stamp is anchored to the "_MMDDYY[A].pdf" tail: the last six digits of
    # a longer run (here "062026") are not a date.
    assert factset_module.date_from_pdf_filename("https://x.test/EarningsInsight_11062026.pdf") is None
    assert factset_module.date_from_pdf_filename(
        "https://x.test/EarningsInsight_110626A.pdf?v=2"
    ) == date(2026, 11, 6)
    # The existing fixture cites "November 1, 2023" historically only: it is
    # outside both cover zones, so no text date is claimed.
    assert factset_module.cover_date_from_pdf_text(_fixture("factset_forward_pe.txt")) is None
    assert factset_module.cover_date_from_pdf_text(
        _fixture("factset_cover_date.txt")
    ) == date(2026, 11, 6)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # Valid calendar stamp but in the far future: not a current report.
        ("EarningsInsight_123199A.pdf", date(2026, 11, 7)),
        # More than a year old: not a current report.
        ("EarningsInsight_010125A.pdf", date(2026, 11, 7)),
        # Tomorrow in the report's US cover date is allowed (Kyiv runs ahead).
        ("EarningsInsight_110826A.pdf", date(2026, 11, 8)),
    ],
)
def test_factset_gateway_ignores_implausible_filename_dates(
    monkeypatch: pytest.MonkeyPatch, filename: str, expected: date
) -> None:
    # Fixture text has neither a cover date nor a usable stamp, so the
    # injected clock (Kyiv Nov 7) is the fallback for rejected stamps.
    monkeypatch.setattr(
        factset_module, "run_pdftotext", lambda *a, **k: _fixture("factset_forward_pe.txt")
    )
    session = _FakeSession([
        _FakeResponse(
            content=b"%PDF-1.7 fake",
            headers={"Content-Type": "application/pdf"},
            url=f"https://advantage.factset.com/{filename}",
        )
    ])
    gateway = FactsetGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("forward_pe"), now_utc=_utc(2026, 11, 7, 7))

    assert observations[0].obs_date == expected


def test_factset_gateway_falls_back_to_cover_date_when_filename_date_is_implausible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factset_module, "run_pdftotext", lambda *a, **k: _fixture("factset_cover_date.txt")
    )
    session = _FakeSession([
        _FakeResponse(
            content=b"%PDF-1.7 fake",
            headers={"Content-Type": "application/pdf"},
            url="https://advantage.factset.com/EarningsInsight_123199A.pdf",
        )
    ])
    gateway = FactsetGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("forward_pe"), now_utc=_utc(2026, 11, 7, 7))

    assert observations[0].obs_date == date(2026, 11, 6)  # cover text, not 2099-12-31


# --- Damodaran -------------------------------------------------------------


def test_damodaran_gateway_takes_latest_implied_erp_row() -> None:
    # The obs date rides the injected now_utc (November: Kyiv = UTC+2 -> 09:00);
    # annual rows carry a bare year only, so run-date stamping stays by design.
    session = _FakeSession([_FakeResponse(_fixture("damodaran_histimpl.html"))])
    gateway = DamodaranGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("erp"), now_utc=_utc(2026, 11, 7, 7))

    assert len(observations) == 1
    assert observations[0].value == pytest.approx(4.23)
    assert observations[0].source == "damodaran"
    assert observations[0].series_id == "erp"
    assert observations[0].obs_date == date(2026, 11, 7)

    # Blank ERP cells are skipped; the latest non-blank row wins.
    payload = "\n".join(
        [
            "<tr><td><strong>Year</strong></td><td>Implied ERP (FCFE)</td></tr>",
            "<tr><td>1960</td><td>&nbsp;</td></tr>",
            "<tr><td>1961</td><td>2.92%</td></tr>",
            "<tr><td></td><td></td></tr>",
        ]
    )
    session = _FakeSession([_FakeResponse(payload)])
    gateway = DamodaranGateway(session=session, min_interval_seconds=0.0)
    observations = gateway.fetch(_spec("erp"), now_utc=_utc(2026, 11, 7, 7))
    assert observations[0].value == pytest.approx(2.92)


def test_damodaran_gateway_fails_when_no_erp_found() -> None:
    session = _FakeSession([_FakeResponse("<html><body><p>nothing</p></body></html>")])
    gateway = DamodaranGateway(session=session, min_interval_seconds=0.0)
    with pytest.raises(CollectorError, match="implied ERP"):
        gateway.fetch(_spec("erp"), now_utc=_utc(2026, 11, 7, 7))


# --- collect_vintages --------------------------------------------------------


class _FakeStore:
    def __init__(self) -> None:
        self.appended: list[SeriesObservation] = []

    def append_observations(self, observations: Any) -> int:
        self.appended.extend(observations)
        return len(observations)


class _BrokenStore(_FakeStore):
    def append_observations(self, observations: Any) -> int:
        if any(getattr(obs, "series_id", "") == "cape" for obs in observations):
            raise RuntimeError("db locked")
        return super().append_observations(observations)


class _GoodFetcher:
    def fetch(self, spec: SeriesSpec, *, now_utc: datetime) -> list[SeriesObservation]:
        return [
            SeriesObservation(
                series_id=spec.key,
                obs_date=date(2026, 11, 5),
                value=1.0,
                source=spec.source,
                fetched_at=now_utc,
            )
        ]


class _EmptyFetcher:
    def fetch(self, spec: SeriesSpec, *, now_utc: datetime) -> list[SeriesObservation]:
        return []


class _FailingFetcher:
    def fetch(self, spec: SeriesSpec, *, now_utc: datetime) -> list[SeriesObservation]:
        raise CollectorError("HTTP 502 for upstream")


class _ExplodingFetcher:
    def fetch(self, spec: SeriesSpec, *, now_utc: datetime) -> list[SeriesObservation]:
        raise RuntimeError("boom")


def _test_specs() -> dict[str, SeriesSpec]:
    return {
        "cape": SERIES_SPECS["cape"],
        "ecy": SERIES_SPECS["ecy"],
        "fg": SERIES_SPECS["fg"],
        "hy_oas": SERIES_SPECS["hy_oas"],
        "nfci": SERIES_SPECS["nfci"],
        "pct_200dma": SERIES_SPECS["pct_200dma"],
    }


def test_collect_vintages_routes_sources_and_skips() -> None:
    store = _FakeStore()
    fetchers = {"fred": _GoodFetcher(), "multpl": _GoodFetcher(), "cnn_fg": _GoodFetcher()}
    result = collect_vintages(
        store, fetchers, _test_specs(), skip_sources=("cnn_fg",), now_utc=_utc(2026, 11, 7, 7)
    )

    assert isinstance(result, CollectionResult)
    assert result.fetched == ("cape", "hy_oas", "nfci")
    by_key = {skip.key: skip for skip in result.skipped}
    assert by_key["fg"].code == SKIP_MANUAL  # source cnn_fg disabled
    assert by_key["fg"].detail == "джерело вимкнено вручну"
    assert by_key["ecy"].code == SKIP_COMPUTED
    assert by_key["pct_200dma"].code == SKIP_COMPUTED
    assert by_key["pct_200dma"].detail == "обчислюється пізніше"
    assert "nfci" not in by_key  # fred adapter exists for nfci
    assert result.errors == ()
    assert sorted(o.series_id for o in store.appended) == ["cape", "hy_oas", "nfci"]


def test_collect_vintages_counts_empty_fetches_as_fetched() -> None:
    store = _FakeStore()
    result = collect_vintages(
        store,
        {"multpl": _EmptyFetcher(), "fred": _GoodFetcher()},
        {"cape": SERIES_SPECS["cape"], "hy_oas": SERIES_SPECS["hy_oas"]},
        skip_sources=(),
        now_utc=_utc(2026, 11, 7, 7),
    )
    assert result.fetched == ("cape", "hy_oas")
    assert len(store.appended) == 1  # cape returned no rows


def test_collect_vintages_isolates_collector_errors() -> None:
    store = _FakeStore()
    result = collect_vintages(
        store,
        {"fred": _FailingFetcher(), "multpl": _GoodFetcher()},
        {"cape": SERIES_SPECS["cape"], "hy_oas": SERIES_SPECS["hy_oas"]},
        skip_sources=(),
        now_utc=_utc(2026, 11, 7, 7),
    )
    assert result.fetched == ("cape",)
    skip = next(s for s in result.skipped if s.key == "hy_oas")
    assert skip.code == SKIP_SOURCE_ERROR
    assert skip.detail == "HTTP 502 for upstream"  # CollectorError text rides in detail
    assert result.errors == ()
    assert [o.series_id for o in store.appended] == ["cape"]


def test_collect_vintages_records_unexpected_crashes_in_errors() -> None:
    store = _FakeStore()
    result = collect_vintages(
        store,
        {"multpl": _ExplodingFetcher()},
        {"cape": SERIES_SPECS["cape"]},
        skip_sources=(),
        now_utc=_utc(2026, 11, 7, 7),
    )
    assert result.fetched == ()
    assert result.errors == ("cape: RuntimeError('boom')",)
    [skip] = result.skipped
    assert skip.key == "cape"
    assert skip.code == SKIP_INTERNAL
    assert skip.detail == "помилка виконання зібрання (див. errors)"


def test_collect_vintages_isolates_storage_errors() -> None:
    store = _BrokenStore()
    result = collect_vintages(
        store,
        {"multpl": _GoodFetcher(), "fred": _GoodFetcher()},
        {"cape": SERIES_SPECS["cape"], "hy_oas": SERIES_SPECS["hy_oas"]},
        skip_sources=(),
        now_utc=_utc(2026, 11, 7, 7),
    )
    # The storage failure on cape must not stop hy_oas from being collected.
    assert result.fetched == ("hy_oas",)
    assert result.errors == ("cape: RuntimeError('db locked')",)
    [skip] = result.skipped
    assert skip.key == "cape"
    assert skip.code == SKIP_INTERNAL
    assert [o.series_id for o in store.appended] == ["hy_oas"]
