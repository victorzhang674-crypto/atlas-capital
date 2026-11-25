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
    CikSource,
    FormerCik,
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
    # 164 leavers since 2018 + 78 from 2015-2017 (Phase 1 checkpoint) + 16 issuers with a former
    # CIK and Twenty-First Century Fox (Stage 29 follow-up 4)
    assert len(overrides) == 258
    assert overrides["APC"].cik == 773910  # the ticker APC now belongs to ARKO Petroleum
    assert overrides["ACE"].cik == 20171  # Chubb Corp, removed under the label "ACE" in 2016
    assert overrides["KRFT"].cik == 1545158  # Kraft Foods Group, not Mondelez
    assert overrides["STI"].cik == 750556  # the ticker STI now belongs to Solidion
    assert overrides["CELG"].cik == 816284
    assert overrides["ATVI"].cik == 718877
    assert overrides["FRC"].cik is None  # a bank that filed with the FDIC, not the SEC
    assert all(o.evidence and o.name for o in overrides.values())
    # follow-up 4: issuers that moved to a new CIK keep their history under the former one
    xom = overrides["XOM"]
    assert (xom.cik, [(f.cik, f.until) for f in xom.former]) == (
        2115436,
        [(34088, date(2026, 8, 2))],
    )
    assert [f.cik for f in overrides["AVGO"].former] == [1649338, 1441634]
    assert overrides["VTRS"].since == date(2020, 11, 16)  # Upjohn's filings are not Mylan's
    assert overrides["TFCF"].cik == overrides["TFCFA"].cik == 1308161
    assert sum(bool(o.former) for o in overrides.values()) == 16


@pytest.mark.parametrize(
    ("body", "pattern"),
    [
        ("overrides: [{symbol: A, cik: 1, name: x, evidence: y}, "
         "{symbol: a, cik: 2, name: x, evidence: y}]", "twice"),
        ("overrides: [{symbol: A, cik: 0, name: x, evidence: y}]", "cik"),
        ("overrides: [{symbol: A, cik: 1, name: x}]", "evidence"),
        ("overrides: [{symbol: A, cik: 1, name: x, evidence: y, note: z}]", "note"),
        ("overrides: [{symbol: 'B@D!', cik: 1, name: x, evidence: y}]", "symbol"),
        ("overrides: [{symbol: A, cik: 1, name: x, evidence: y, "
         "former: [{cik: 1, until: 2020-01-01, name: z}]}]", "former CIK 1"),
        ("overrides: [{symbol: A, cik: 1, name: x, evidence: y, former: ["
         "{cik: 2, until: 2020-01-01, name: z}, {cik: 2, until: 2019-01-01, name: z}]}]", "twice"),
        ("overrides: [{symbol: A, cik: null, name: x, evidence: y, "
         "former: [{cik: 2, until: 2020-01-01, name: z}]}]", "no CIK"),
        ("overrides: [{symbol: A, cik: null, name: x, evidence: y, since: 2020-01-01}]", "no CIK"),
        ("overrides: [{symbol: A, cik: 1, name: x, evidence: y, "
         "former: [{cik: 2, name: z}]}]", "until"),
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


def test_cik_overrides_cover_former_members_since_2015() -> None:
    """Every S&P 500 member on or after 2015-01-01 (the backfill's default start; 2018 until the
    Phase 1 checkpoint) that is not in today's constituents table (Stage 08 reconstruction from
    the recorded fixtures) has a reviewed override entry."""
    from atlas.data.reference import load_ticker_map, parse_changes, parse_constituents
    from atlas.data.universe import build_membership

    fixtures = REPO_ROOT / "tests" / "fixtures" / "universe"
    constituents = parse_constituents((fixtures / "wikipedia_sp500_constituents.html").read_text())
    changes = parse_changes((fixtures / "wikipedia_sp500_changes.html").read_text())
    ticker_map = load_ticker_map(REPO_ROOT / "config" / "ticker_changes.yaml")
    intervals = build_membership(constituents, changes, ticker_map=ticker_map).intervals
    recent = intervals[intervals["end_date"].isna() | (intervals["end_date"] > date(2015, 1, 1))]
    former = set(recent["symbol"]) - set(constituents["symbol"])
    listed = {o.symbol for o in load_cik_overrides(OVERRIDES_FILE)}
    assert former <= listed
    # follow-up 4: current members with a former CIK, and Twenty-First Century Fox (the main
    # lake's TFCF / TFCFA, which the recorded change table files under FOXA / FOX)
    assert listed - former == {
        "APA", "AVGO", "BG", "BLK", "CI", "DD", "DIS", "GOOG", "GOOGL", "LIN", "SKYD", "SNDK",
        "TFCF", "TFCFA", "VTRS", "XOM",
    }  # fmt: skip


def test_cik_sources_are_the_current_cik_then_the_former_ones() -> None:
    """Stage 29 follow-up 4: ExxonMobil moved to a new holding company's CIK in 2026; its older
    filings sit under the former CIK, read up to `until`. `since` drops the current CIK's
    filings from before the symbol's issuer took it (Upjohn's before it became Viatris)."""
    base = CikMap({"XOM": 2115436, "VTRS": 1792044, "AAPL": 320193})
    exxon = FormerCik(cik=34088, until=date(2026, 8, 2), name="Exxon Mobil Corp")
    mylan = FormerCik(cik=1623613, until=date(2020, 11, 15), name="Mylan N.V.")
    cik_map = base.with_overrides(
        (
            CikOverride(symbol="XOM", cik=2115436, name="Exxon", evidence="e", former=(exxon,)),
            CikOverride(
                symbol="VTRS", cik=1792044, name="Viatris", evidence="e",
                since=date(2020, 11, 16), former=(mylan,),
            ),
        )
    )  # fmt: skip
    assert cik_map.sources("xom") == (
        CikSource(cik=2115436, since=None, until=None),
        CikSource(cik=34088, since=None, until=date(2026, 8, 2)),
    )
    assert cik_map.sources("VTRS") == (
        CikSource(cik=1792044, since=date(2020, 11, 16), until=None),
        CikSource(cik=1623613, since=None, until=date(2020, 11, 15)),
    )
    assert cik_map.sources("AAPL") == (CikSource(cik=320193, since=None, until=None),)
    assert cik_map.sources("ZZZZ") == () and cik_map.sources("B@D!") == ()
    assert cik_map.cik("XOM") == 2115436
    assert base.sources("XOM") == (CikSource(cik=2115436, since=None, until=None),)
    # an override without former CIKs drops those set before it
    plain = cik_map.with_overrides((CikOverride(symbol="XOM", cik=1, name="x", evidence="e"),))
    assert plain.sources("XOM") == (CikSource(cik=1, since=None, until=None),)
    # `covers`: since and until are both inclusive
    span = CikSource(cik=1, since=date(2020, 1, 2), until=date(2020, 1, 4))
    assert [span.covers(date(2020, 1, d)) for d in (1, 2, 4, 5)] == [False, True, True, False]
    assert CikSource(cik=1, since=None, until=None).covers(date(1990, 1, 1))


def test_a_later_override_replaces_since_and_a_blocked_symbol_does_not_stop_the_rest() -> None:
    first = CikMap({"VTRS": 1}).with_overrides(
        (CikOverride(symbol="VTRS", cik=1, name="v", evidence="e", since=date(2020, 11, 16)),)
    )
    plain = first.with_overrides((CikOverride(symbol="VTRS", cik=1, name="v", evidence="e"),))
    assert plain.sources("VTRS") == (CikSource(cik=1, since=None, until=None),)
    blocked_first = CikMap({"FRC": 9}).with_overrides(
        (
            CikOverride(symbol="FRC", cik=None, name="First Republic Bank", evidence="e"),
            CikOverride(symbol="APC", cik=773910, name="Anadarko", evidence="e"),
        )
    )
    assert (blocked_first.cik("FRC"), blocked_first.cik("APC")) == (None, 773910)


# --- history pages SEC rewrites (Stage 29 follow-up 7) ----------------------------------------

PAGE = "CIK0000000007-submissions-001.json"
PAGE_URL = f"https://data.sec.gov/submissions/{PAGE}"


def _page(rows: list[tuple[str, str, str]]) -> dict[str, list[str]]:
    """A history page from (accession, filing date, acceptance time) rows."""
    return {
        "accessionNumber": [r[0] for r in rows],
        "form": ["10-Q" for _ in rows],
        "filingDate": [r[1] for r in rows],
        "acceptanceDateTime": [r[2] for r in rows],
    }


OLD_ROWS = [("a-1", "2016-05-01", "2016-05-01T20:00:00.000Z"),
            ("a-2", "2016-08-01", "2016-08-01T20:00:00.000Z")]  # fmt: skip
NEW_ROWS = [*OLD_ROWS, ("a-3", "2016-12-20", "2016-12-20T21:22:19.000Z")]
RECENT_ROWS = [("b-1", "2017-03-01", "2017-03-01T20:00:00.000Z")]


def _entry(rows: list[tuple[str, str, str]]) -> dict[str, object]:
    return {"name": PAGE, "filingCount": len(rows), "filingFrom": rows[0][1],
            "filingTo": rows[-1][1]}  # fmt: skip


def test_a_history_page_sec_rewrote_is_fetched_again(tmp_lake: Lake) -> None:
    """SEC rewrites a history page as filings leave `filings.recent` (Agilent's page 001: 1,364
    filings archived, 1,382 now). A cached page that no longer matches its index entry (count,
    first or last date) is fetched again; one that matches is reused."""
    with respx.mock() as router:
        route = router.get(PAGE_URL).mock(return_value=httpx.Response(200, json=_page(OLD_ROWS)))
        client = edgar_client(tmp_lake)
        assert client.submissions_page(PAGE, _entry(OLD_ROWS))["accessionNumber"] == ["a-1", "a-2"]
        assert client.submissions_page(PAGE, _entry(OLD_ROWS))["accessionNumber"] == ["a-1", "a-2"]
        assert route.call_count == 1  # matching: reused
        route.mock(return_value=httpx.Response(200, json=_page(NEW_ROWS)))
        page = client.submissions_page(PAGE, _entry(NEW_ROWS))
        assert page["accessionNumber"] == ["a-1", "a-2", "a-3"] and route.call_count == 2
        cached = json.loads((tmp_lake.raw_dir / "sec" / "submissions" / PAGE).read_text())
        assert cached["accessionNumber"] == ["a-1", "a-2", "a-3"]  # the archive is refreshed


@pytest.mark.parametrize(
    "change",
    [{"filingCount": 3}, {"filingFrom": "2016-04-01"}, {"filingTo": "2016-12-20"}],
)
def test_each_field_of_the_entry_can_show_a_page_is_stale(
    tmp_lake: Lake, change: dict[str, object]
) -> None:
    with respx.mock() as router:
        route = router.get(PAGE_URL).mock(return_value=httpx.Response(200, json=_page(OLD_ROWS)))
        client = edgar_client(tmp_lake)
        client.submissions_page(PAGE, _entry(OLD_ROWS))
        client.submissions_page(PAGE, _entry(OLD_ROWS) | change)
        assert route.call_count == 2


def test_a_page_without_an_index_entry_is_reused_as_before(tmp_lake: Lake) -> None:
    """Without the entry (or with an entry naming none of these fields) a cached page is
    reused, as before."""
    with respx.mock() as router:
        route = router.get(PAGE_URL).mock(return_value=httpx.Response(200, json=_page(OLD_ROWS)))
        client = edgar_client(tmp_lake)
        client.submissions_page(PAGE)
        client.submissions_page(PAGE)
        client.submissions_page(PAGE, {"name": PAGE})
        assert route.call_count == 1


def test_the_filing_index_reads_a_rewritten_page_and_keeps_acceptance_times(
    tmp_lake: Lake,
) -> None:
    """A filing that moved out of `filings.recent` into a rewritten history page keeps its
    acceptance time (Agilent's 10-K of 2016-12-20 lost it on 2026-11-25)."""
    recent = {"cik": "7", "filings": {"recent": _page(RECENT_ROWS), "files": [_entry(NEW_ROWS)]}}
    with respx.mock() as router:
        router.get("https://data.sec.gov/submissions/CIK0000000007.json").respond(json=recent)
        route = router.get(PAGE_URL).mock(return_value=httpx.Response(200, json=_page(OLD_ROWS)))
        client = edgar_client(tmp_lake)
        client.submissions_page(PAGE)  # the archive holds the old page
        route.mock(return_value=httpx.Response(200, json=_page(NEW_ROWS)))
        index = client.filing_index(7, {"a-3": date(2016, 12, 20)}, refresh=True)
    row = index.set_index("accn").loc["a-3"]
    assert str(row["accepted_at"]) == "2016-12-20 21:22:19+00:00"


def test_refresh_false_reuses_an_archived_page_even_when_it_looks_stale(tmp_lake: Lake) -> None:
    """With `refresh=False` every archived answer is reused (offline re-parses), so the filing
    index passes no entry and makes no request."""
    recent = {"cik": "7", "filings": {"recent": _page(RECENT_ROWS), "files": [_entry(NEW_ROWS)]}}
    with respx.mock() as router:
        router.get("https://data.sec.gov/submissions/CIK0000000007.json").respond(json=recent)
        route = router.get(PAGE_URL).mock(return_value=httpx.Response(200, json=_page(OLD_ROWS)))
        client = edgar_client(tmp_lake)
        client.submissions(7, refresh=True)
        client.submissions_page(PAGE)  # the archive holds the old page
        index = client.filing_index(7, {"a-2": date(2016, 8, 1)}, refresh=False)
        assert route.call_count == 1 and "a-2" in set(index["accn"])


def test_a_needed_filing_between_two_pages_date_ranges_is_still_found(tmp_lake: Lake) -> None:
    """companyfacts' `filed` can differ from the submissions' filing date (Citigroup's
    `0000831001-23-000037`: filed 2023-02-27 in companyfacts, in page 016 which ends
    2023-02-26). When the pages chosen by date leave a needed filing missing, the other pages
    are read too."""
    page_a = "CIK0000000007-submissions-001.json"
    page_b = "CIK0000000007-submissions-002.json"
    rows_a = [("c-1", "2023-03-01", "2023-03-01T20:00:00.000Z")]
    rows_b = [("c-0", "2023-02-24", "2023-02-25T02:45:33.000Z")]
    entries = [
        {"name": page_a, "filingCount": 1, "filingFrom": "2023-02-28", "filingTo": "2023-03-01"},
        {"name": page_b, "filingCount": 1, "filingFrom": "2023-02-24", "filingTo": "2023-02-26"},
    ]
    recent = {"cik": "7", "filings": {"recent": _page(RECENT_ROWS), "files": entries}}
    with respx.mock() as router:
        router.get("https://data.sec.gov/submissions/CIK0000000007.json").respond(json=recent)
        router.get(f"https://data.sec.gov/submissions/{page_a}").respond(json=_page(rows_a))
        route_b = router.get(f"https://data.sec.gov/submissions/{page_b}").respond(
            json=_page(rows_b)
        )
        client = edgar_client(tmp_lake)
        index = client.filing_index(7, {"c-0": date(2023, 2, 27)}, refresh=True)
        # a filing found by date needs no other page
        again = client.filing_index(7, {"c-1": date(2023, 3, 1)}, refresh=True)
    assert str(index.set_index("accn").loc["c-0", "accepted_at"]) == "2023-02-25 02:45:33+00:00"
    assert route_b.call_count == 1 and "c-0" not in set(again["accn"])
