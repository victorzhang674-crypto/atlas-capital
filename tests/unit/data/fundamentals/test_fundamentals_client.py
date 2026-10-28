"""Stage 09 Task 1: the EDGAR client (rate limit, retries, raw cache) and the ticker → CIK map."""

import io
import json
import logging
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from fundamentals_helpers import (
    AAPL,
    REPO_ROOT,
    TEST_USER_AGENT,
    SleepRecorder,
    edgar_client,
    fixture_json,
    serve_fixture,
)
from pydantic import SecretStr

from atlas.core.config import AtlasConfig, FundamentalsConfig, Secrets
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import configure_logging, reset_logging
from atlas.core.storage import Lake
from atlas.data.fundamentals import (
    CikMap,
    CikOverride,
    build_cik_map,
    build_edgar_client,
    load_cik_overrides,
)

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"


@pytest.fixture
def sec() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.route(host="data.sec.gov").mock(side_effect=serve_fixture)
        router.route(host="www.sec.gov").mock(side_effect=serve_fixture)
        yield router


# --- CIK map ----------------------------------------------------------------------------------


def test_cik_map_from_company_tickers() -> None:
    cik_map = CikMap.from_company_tickers(fixture_json("company_tickers.json"))
    assert cik_map.cik("AAPL") == AAPL
    assert cik_map.cik("BRK.B") == 1067983
    assert cik_map.cik("brk-a") == 1067983
    assert cik_map.cik("GOOG") == cik_map.cik("GOOGL") == 1652044
    assert cik_map.cik("META") == 1326801
    assert cik_map.cik("JPM.PC") == 19617
    assert cik_map.cik("ZZZZ") is None
    assert cik_map.cik("not a ticker!") is None
    assert len(cik_map) == 18


def test_cik_map_duplicate_ticker_keeps_first() -> None:
    payload = {
        "0": {"cik_str": 1, "ticker": "ABC", "title": "First"},
        "1": {"cik_str": 2, "ticker": "abc", "title": "Second"},
    }
    assert CikMap.from_company_tickers(payload).cik("ABC") == 1


def test_cik_map_skips_bad_tickers() -> None:
    payload = {
        "0": {"cik_str": 1, "ticker": "GOOD", "title": "Good"},
        "1": {"cik_str": 2, "ticker": "B@D!", "title": "Bad"},
    }
    cik_map = CikMap.from_company_tickers(payload)
    assert len(cik_map) == 1
    assert cik_map.cik("GOOD") == 1


def test_cik_map_rejects_malformed_payload() -> None:
    with pytest.raises(DataError, match="company_tickers"):
        CikMap.from_company_tickers({"0": {"ticker": "AAPL"}})


# --- client -----------------------------------------------------------------------------------


def test_client_sends_user_agent_and_caches_raw(sec: respx.MockRouter, tmp_lake: Lake) -> None:
    client = edgar_client(tmp_lake)
    first = client.companyfacts(AAPL)
    assert first["entityName"] == "Apple Inc."
    assert sec.calls.call_count == 1
    assert sec.calls.last.request.headers["User-Agent"] == TEST_USER_AGENT
    cached = tmp_lake.raw_dir / "sec" / "companyfacts" / "CIK0000320193.json"
    assert json.loads(cached.read_text()) == first
    assert client.companyfacts(AAPL) == first
    assert sec.calls.call_count == 1  # served from raw/sec/
    client.companyfacts(AAPL, refresh=True)
    assert sec.calls.call_count == 2


def test_client_without_lake_does_not_cache(sec: respx.MockRouter) -> None:
    client = edgar_client()
    client.company_tickers()
    client.company_tickers()
    assert sec.calls.call_count == 2


def test_client_throttles_requests(sec: respx.MockRouter) -> None:
    sleep = SleepRecorder()
    client = edgar_client(sleep=sleep)
    client.company_tickers()
    assert sleep.calls == []
    client.submissions(AAPL)
    client.companyfacts(AAPL)
    assert sleep.calls == [pytest.approx(0.1), pytest.approx(0.1)]


def test_client_retries_server_errors() -> None:
    sleep = SleepRecorder()
    with respx.mock() as router:
        route = router.get(FACTS_URL)
        route.side_effect = [
            httpx.Response(503),
            httpx.Response(429),
            httpx.Response(200, json={"cik": AAPL, "entityName": "Apple Inc.", "facts": {}}),
        ]
        data = edgar_client(sleep=sleep).companyfacts(AAPL)
    assert data["cik"] == AAPL
    assert route.call_count == 3
    assert sleep.calls == [1.0, 0.1, 2.0, 0.1]  # backoff, throttle, backoff, throttle


def test_client_gives_up_after_retries() -> None:
    config = FundamentalsConfig(max_retries=2, backoff_seconds=0.0)
    with respx.mock() as router:
        route = router.get(FACTS_URL).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(DataError, match="3 attempt") as err:
            edgar_client(config=config).companyfacts(AAPL)
    assert route.call_count == 3
    assert isinstance(err.value.__cause__, httpx.ConnectError)


def test_client_404_is_not_retried() -> None:
    with respx.mock() as router:
        route = router.get(FACTS_URL).mock(return_value=httpx.Response(404))
        with pytest.raises(DataError, match="HTTP 404"):
            edgar_client().companyfacts(AAPL)
    assert route.call_count == 1


def test_client_bad_json_raises(tmp_lake: Lake) -> None:
    with respx.mock() as router:
        router.get(FACTS_URL).mock(return_value=httpx.Response(200, content=b"<html>"))
        with pytest.raises(DataError, match="JSON"):
            edgar_client(tmp_lake).companyfacts(AAPL)
    assert not (tmp_lake.raw_dir / "sec" / "companyfacts" / "CIK0000320193.json").exists()


def test_client_requires_user_agent() -> None:
    with pytest.raises(ConfigError, match="User-Agent"):
        from atlas.data.fundamentals import EdgarClient

        EdgarClient(FundamentalsConfig(), user_agent="  ")


def test_build_edgar_client_requires_user_agent(tmp_lake: Lake) -> None:
    with pytest.raises(ConfigError, match="SEC_USER_AGENT"):
        build_edgar_client(AtlasConfig(), tmp_lake)


def test_build_edgar_client_uses_secret(sec: respx.MockRouter, tmp_lake: Lake) -> None:
    cfg = AtlasConfig(secrets=Secrets(sec_user_agent=SecretStr("Owner owner@example.invalid")))
    build_edgar_client(cfg, tmp_lake).company_tickers()
    assert sec.calls.last.request.headers["User-Agent"] == "Owner owner@example.invalid"


def test_user_agent_is_not_logged() -> None:
    stream = io.StringIO()
    configure_logging(level="DEBUG", console=stream)
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    try:
        with respx.mock() as router:
            router.get(FACTS_URL).mock(side_effect=[httpx.Response(503), httpx.Response(404)])
            with pytest.raises(DataError):
                edgar_client().companyfacts(AAPL)
    finally:
        reset_logging()
    text = stream.getvalue()
    assert "fundamentals.request_failed" in text
    assert TEST_USER_AGENT not in text


# --- CIK overrides for former members (Stage 09 addendum) -------------------------------------

OVERRIDES_FILE = REPO_ROOT / "config" / "cik_overrides.yaml"


def test_load_cik_overrides_repo_file() -> None:
    overrides = {o.symbol: o for o in load_cik_overrides(OVERRIDES_FILE)}
    assert len(overrides) == 164
    assert overrides["APC"].cik == 773910  # the ticker APC now belongs to ARKO Petroleum
    assert overrides["STI"].cik == 750556  # the ticker STI now belongs to Solidion
    assert overrides["CELG"].cik == 816284
    assert overrides["ATVI"].cik == 718877
    assert overrides["FRC"].cik is None  # a bank that filed with the FDIC, not the SEC
    assert all(o.evidence and o.name for o in overrides.values())


@pytest.mark.parametrize(
    ("body", "pattern"),
    [
        ("overrides: [{symbol: A, cik: 1, name: x, evidence: y}, "
         "{symbol: a, cik: 2, name: x, evidence: y}]", "twice"),
        ("overrides: [{symbol: A, cik: 0, name: x, evidence: y}]", "cik"),
        ("overrides: [{symbol: A, cik: 1, name: x}]", "evidence"),
        ("overrides: [{symbol: A, cik: 1, name: x, evidence: y, note: z}]", "note"),
        ("overrides: [{symbol: 'B@D!', cik: 1, name: x, evidence: y}]", "symbol"),
    ],
)  # fmt: skip
def test_load_cik_overrides_rejects_bad_file(tmp_path: Path, body: str, pattern: str) -> None:
    path = tmp_path / "cik_overrides.yaml"
    path.write_text(body)
    with pytest.raises(ConfigError, match=pattern) as err:
        load_cik_overrides(path)
    assert str(path) in str(err.value)


def test_load_cik_overrides_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_cik_overrides(tmp_path / "absent.yaml") == ()


def test_cik_map_overrides_add_replace_and_block() -> None:
    base = CikMap({"AAPL": 320193, "APC": 2080921, "FRC": 999})
    overrides = (
        CikOverride(symbol="APC", cik=773910, name="Anadarko", evidence="e"),
        CikOverride(symbol="celg", cik=816284, name="Celgene", evidence="e"),
        CikOverride(symbol="FRC", cik=None, name="First Republic Bank", evidence="e"),
    )
    cik_map = base.with_overrides(overrides)
    assert cik_map.cik("AAPL") == 320193  # untouched
    assert cik_map.cik("APC") == 773910  # replaced
    assert cik_map.cik("CELG") == 816284  # added
    assert cik_map.cik("FRC") is None  # blocked
    assert base.cik("APC") == 2080921  # the original map is unchanged


def test_build_cik_map_applies_repo_overrides(
    sec: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    cik_map = build_cik_map(edgar_client(), AtlasConfig())
    assert cik_map.cik("AAPL") == AAPL
    assert cik_map.cik("CELG") == 816284
    assert cik_map.cik("FRC") is None


def test_cik_overrides_cover_former_members_since_2018() -> None:
    """Every S&P 500 member on or after 2018-01-01 that is not in today's constituents table
    (Stage 08 reconstruction from the recorded fixtures) has a reviewed override entry."""
    from atlas.data.reference import load_ticker_map, parse_changes, parse_constituents
    from atlas.data.universe import build_membership

    fixtures = REPO_ROOT / "tests" / "fixtures" / "universe"
    constituents = parse_constituents((fixtures / "wikipedia_sp500_constituents.html").read_text())
    changes = parse_changes((fixtures / "wikipedia_sp500_changes.html").read_text())
    ticker_map = load_ticker_map(REPO_ROOT / "config" / "ticker_changes.yaml")
    intervals = build_membership(constituents, changes, ticker_map=ticker_map).intervals
    recent = intervals[intervals["end_date"].isna() | (intervals["end_date"] > date(2018, 1, 1))]
    former = set(recent["symbol"]) - set(constituents["symbol"])
    assert {o.symbol for o in load_cik_overrides(OVERRIDES_FILE)} == former
