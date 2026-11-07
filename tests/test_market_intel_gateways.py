from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import requests

import market_intel.infrastructure.factset_gateway as factset_module
import market_intel.infrastructure.http_base as http_base
import market_intel.infrastructure.multpl_gateway as multpl_module
from market_intel.application.use_cases.collect_vintages import collect_vintages
from market_intel.domain.exceptions import CollectorError
from market_intel.domain.models import CollectionResult, SeriesObservation, SeriesSpec
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


def test_request_retries_rate_limit_then_succeeds() -> None:
    session = _FakeSession([
        _FakeResponse(status_code=429, headers={"Retry-After": "0"}),
        _FakeResponse(status_code=503, headers={"Retry-After": "0"}),
        _FakeResponse('{"ok": true}'),
    ])
    gateway = FredCsvGateway(session=session, min_interval_seconds=0.0)
    payload = gateway._get_json("https://example.test/x")
    assert payload == {"ok": True}
    assert len(session.calls) == 3


def test_request_honors_retry_after_without_sleeping_hardcoded_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[float] = []

    def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(http_base.time, "sleep", fake_sleep)
    session = _FakeSession([
        _FakeResponse(status_code=429, headers={"Retry-After": "7"}),
        _FakeResponse("ok"),
    ])
    gateway = FredCsvGateway(session=session, min_interval_seconds=0.0)
    assert gateway._get_text("https://example.test/x") == "ok"
    assert recorded == [7]


def test_request_raises_rate_limit_exceeded_after_attempts() -> None:
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


def test_request_wraps_repeated_request_exception() -> None:
    class _BrokenSession:
        headers: dict[str, str] = {}

        def request(self, *args: Any, **kwargs: Any) -> _FakeResponse:
            raise requests.ConnectionError("conn refused")

    gateway = FredCsvGateway(session=_BrokenSession(), min_interval_seconds=0.0)  # type: ignore[arg-type]
    with pytest.raises(CollectorError, match="Failed to fetch"):
        gateway._get_text("https://example.test/x")


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


def test_yahoo_gateway_keeps_last_bar_after_market_close() -> None:
    session = _FakeSession([_FakeResponse(_fixture("yahoo_chart.json"))])
    gateway = YahooChartGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("spx"), now_utc=_utc(2025, 1, 7, 22, 30))

    assert [(o.obs_date, o.value) for o in observations] == [
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


def test_yahoo_gateway_encodes_symbol_in_path() -> None:
    session = _FakeSession([_FakeResponse(_fixture("yahoo_chart.json"))])
    gateway = YahooChartGateway(session=session, min_interval_seconds=0.0)
    gateway.fetch(_spec("vix3m"), now_utc=_utc(2025, 1, 7, 22, 30))
    assert session.calls[0]["url"].endswith("/chart/%5EVIX3M")
    assert session.calls[0]["params"] == {"range": "5y", "interval": "1d"}


# --- multpl ----------------------------------------------------------------


def test_multpl_gateway_parses_current_and_month_end_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pin the Kyiv fetch date so fixture expectations never drift with the clock.
    kyiv = multpl_module.now_kyiv().tzinfo
    pinned = lambda: datetime(2026, 11, 7, 7, tzinfo=kyiv)
    monkeypatch.setattr(multpl_module, "now_kyiv", pinned)
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


def test_multpl_gateway_history_collapses_same_month_to_freshest_print() -> None:
    from market_intel.infrastructure.multpl_gateway import parse_monthly_history

    points = parse_monthly_history(_fixture("multpl_history.html"), fetched_on=date(2026, 12, 10))
    assert points[-1] == (date(2026, 11, 30), 38.91)  # Nov 6 daily print beats Nov 1 monthly
    assert [p for p in points][-2] == (date(2026, 10, 31), 40.60)


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
    assert session.headers["User-Agent"].startswith("Mozilla/5.0")


def test_cnn_gateway_prefers_fetched_on_and_defaults_to_us_today() -> None:
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


def test_factset_gateway_follows_pdf_link_from_landing_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    landing = _fixture("factset_landing.html")
    pdf_fixture = _fixture("factset_forward_pe.txt")
    monkeypatch.setattr(factset_module, "run_pdftotext", lambda *a, **k: pdf_fixture)

    session = _FakeSession([
        _FakeResponse(landing, url="https://www.factset.com/earningsinsight"),
        _FakeResponse(content=b"%PDF-1.7 fake"),
    ])
    gateway = FactsetGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("forward_pe"), now_utc=_utc(2026, 11, 7, 7))

    assert len(session.calls) == 2
    assert session.calls[1]["url"].endswith("EarningsInsight_110626A.pdf")
    assert observations[0].value == 17.7


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


def test_factset_gateway_temp_files_are_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[str] = []

    real_tmp = factset_module.tempfile.NamedTemporaryFile

    def recording_tmp(*args: Any, **kwargs: Any) -> Any:
        handle = real_tmp(*args, **kwargs)
        written.append(handle.name)
        return handle

    pdf_fixture = _fixture("factset_forward_pe.txt")
    monkeypatch.setattr(factset_module.tempfile, "NamedTemporaryFile", recording_tmp)
    monkeypatch.setattr(factset_module, "run_pdftotext", lambda *_a, **_k: pdf_fixture)
    session = _FakeSession([_FakeResponse(content=b"%PDF-1.7 fake")])
    gateway = FactsetGateway(session=session, min_interval_seconds=0.0)
    gateway.fetch(_spec("forward_pe"), now_utc=_utc(2026, 11, 7, 7))

    assert written, "gateway must write exactly one temp pdf"
    assert all(not Path(name).exists() for name in written)


# --- Damodaran -------------------------------------------------------------


def test_damodaran_gateway_takes_latest_implied_erp_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import market_intel.infrastructure.damodaran_gateway as damodaran_module

    kyiv = damodaran_module.now_kyiv().tzinfo
    monkeypatch.setattr(damodaran_module, "now_kyiv", lambda: datetime(2026, 11, 7, 7, tzinfo=kyiv))
    session = _FakeSession([_FakeResponse(_fixture("damodaran_histimpl.html"))])
    gateway = DamodaranGateway(session=session, min_interval_seconds=0.0)

    observations = gateway.fetch(_spec("erp"), now_utc=_utc(2026, 11, 7, 7))

    assert len(observations) == 1
    assert observations[0].value == pytest.approx(4.23)
    assert observations[0].source == "damodaran"


def test_damodaran_gateway_skips_blank_erp_rows() -> None:
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
    by_key = dict(result.skipped)
    assert by_key["fg"] == "джерело вимкнено вручну"  # source cnn_fg disabled
    assert by_key["ecy"] == "обчислюється пізніше"
    assert by_key["pct_200dma"] == "обчислюється пізніше"
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
    assert dict(result.skipped)["hy_oas"] == "HTTP 502 for upstream"
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
    assert dict(result.skipped)["cape"]
