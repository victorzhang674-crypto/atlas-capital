"""Stage 09 Task 7: ingestion into the lake and the lake-backed `Fundamentals` reader."""

from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
import respx
import yaml
from fundamentals_helpers import (
    AAPL,
    FIXTURES,
    JPM,
    MSFT,
    REPO_ROOT,
    companyfacts,
    edgar_client,
    fixture_facts,
    fixture_json,
    repo_concept_map,
    serve_fixture,
)
from structlog.testing import capture_logs

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.pit import AsOf
from atlas.core.storage import Lake
from atlas.data.fundamentals import (
    FUNDAMENTALS_FACTS,
    CikMap,
    CikOverride,
    FormerCik,
    Fundamentals,
    FundamentalsIngestReport,
    fundamentals_pit,
    ingest_fundamentals,
    load_concept_map,
    parse_companyfacts,
)

AAPL_10K_FY2023 = "0000320193-23-000106"


@pytest.fixture
def sec() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json").mock(
            return_value=httpx.Response(200, json={"cik": 1, "entityName": "E", "facts": {}})
        )
        router.route(host="data.sec.gov").mock(side_effect=serve_fixture)
        yield router


@pytest.fixture
def cik_map() -> CikMap:
    payload = fixture_json("company_tickers.json")
    payload["extra"] = {"cik_str": 1, "ticker": "EMPTY", "title": "No mapped facts"}
    return CikMap.from_company_tickers(payload)


def ingest(lake: Lake, cik_map: CikMap, symbols: list[str]) -> FundamentalsIngestReport:
    return ingest_fundamentals(
        lake, edgar_client(lake), symbols, cik_map=cik_map, concept_map=repo_concept_map()
    )


def expected_rows(*ciks: int) -> int:
    tags = repo_concept_map().tags()
    return sum(len(parse_companyfacts(companyfacts(c), symbol="X", tags=tags)) for c in ciks)


def test_ingest_fixtures_writes_facts(
    sec: respx.MockRouter, tmp_lake: Lake, cik_map: CikMap
) -> None:
    report = ingest(tmp_lake, cik_map, ["AAPL", "msft", "JPM"])
    assert report.symbols_ok == ("AAPL", "MSFT", "JPM")
    assert report.symbols_failed == ()
    assert report.rows == expected_rows(AAPL, MSFT, JPM)
    assert report.fallback_rows == 0
    stored = tmp_lake.read_table(FUNDAMENTALS_FACTS.name)
    assert len(stored) == report.rows
    assert set(stored["symbol"]) == {"AAPL", "MSFT", "JPM"}
    assert stored["available_at"].notna().all()
    tenk = stored[stored["accn"] == AAPL_10K_FY2023]
    assert (tenk["available_at"] == pd.Timestamp("2023-11-02 22:08:27", tz="UTC")).all()
    raw = tmp_lake.raw_dir / "sec"
    assert (raw / "companyfacts" / "CIK0000320193.json").exists()
    assert (raw / "submissions" / "CIK0000019617-submissions-064.json").exists()


def test_ingest_is_idempotent(sec: respx.MockRouter, tmp_lake: Lake, cik_map: CikMap) -> None:
    first = ingest(tmp_lake, cik_map, ["AAPL"])
    before = tmp_lake.read_table(FUNDAMENTALS_FACTS.name)
    second = ingest(tmp_lake, cik_map, ["AAPL"])
    assert first.rows == second.rows
    pd.testing.assert_frame_equal(tmp_lake.read_table(FUNDAMENTALS_FACTS.name), before)


def test_failed_symbol_never_aborts_batch(
    sec: respx.MockRouter, tmp_lake: Lake, cik_map: CikMap
) -> None:
    report = ingest(tmp_lake, cik_map, ["ZZZZ", "GOOGL", "EMPTY", "AAPL"])
    assert report.symbols_ok == ("AAPL",)
    assert report.symbols_failed == ("ZZZZ", "GOOGL", "EMPTY")
    assert "no CIK" in report.failure_reasons["ZZZZ"]
    assert "HTTP 404" in report.failure_reasons["GOOGL"]  # no companyfacts recorded for it
    assert "no mapped facts" in report.failure_reasons["EMPTY"]
    assert set(tmp_lake.read_table(FUNDAMENTALS_FACTS.name)["symbol"]) == {"AAPL"}


def test_fundamentals_reads_lake(sec: respx.MockRouter, tmp_lake: Lake, cik_map: CikMap) -> None:
    ingest(tmp_lake, cik_map, ["AAPL", "JPM"])
    reader = Fundamentals(tmp_lake, concept_map=repo_concept_map())
    as_of = AsOf.for_session(date(2023, 11, 3))
    s = reader.pit("aapl", date(2023, 11, 3))
    assert s.name == "AAPL"
    expected = fundamentals_pit(
        fixture_facts(AAPL, "AAPL"), as_of, repo_concept_map(), AtlasConfig().data.fundamentals
    )
    pd.testing.assert_series_equal(s, expected, check_names=False)
    assert s["revenue_ttm"] == 383_285e6
    assert reader.pit("AAPL", as_of).equals(s)
    detail = reader.pit_detail("AAPL", as_of).set_index("metric")
    assert detail.loc["revenue_ttm", "accn"] == AAPL_10K_FY2023
    assert len(reader.facts("JPM")) == len(fixture_facts(JPM, "JPM"))


def test_fundamentals_missing_table_raises(tmp_lake: Lake) -> None:
    with pytest.raises(DataError, match="ingest_fundamentals"):
        Fundamentals(tmp_lake, concept_map=repo_concept_map()).pit("AAPL", date(2023, 11, 3))


def test_unknown_symbol_is_all_nan(sec: respx.MockRouter, tmp_lake: Lake, cik_map: CikMap) -> None:
    ingest(tmp_lake, cik_map, ["AAPL"])
    s = Fundamentals(tmp_lake, concept_map=repo_concept_map()).pit("ZZZZ", date(2023, 11, 3))
    assert s.isna().all()
    assert s.name == "ZZZZ"


def test_fundamentals_rejects_datetime(tmp_lake: Lake) -> None:
    with pytest.raises(TypeError, match="date"):
        Fundamentals(tmp_lake, concept_map=repo_concept_map()).pit(
            "AAPL",
            datetime(2023, 11, 3),  # type: ignore[arg-type]
        )


def test_fundamentals_default_concept_map_is_the_repo_file(
    monkeypatch: pytest.MonkeyPatch, tmp_lake: Lake
) -> None:
    from fundamentals_helpers import REPO_ROOT

    monkeypatch.chdir(REPO_ROOT)
    assert Fundamentals(tmp_lake).concept_map.names == repo_concept_map().names


def test_ingest_rejects_a_bare_string(
    sec: respx.MockRouter, tmp_lake: Lake, cik_map: CikMap
) -> None:
    with pytest.raises(ValueError, match="not one string"):
        ingest(tmp_lake, cik_map, "AAPL")  # type: ignore[arg-type]
    assert sec.calls.call_count == 0


def test_ingest_rejects_an_invalid_symbol_before_any_request(
    sec: respx.MockRouter, tmp_lake: Lake, cik_map: CikMap
) -> None:
    with pytest.raises(ValueError, match="B@D"):
        ingest(tmp_lake, cik_map, ["AAPL", "B@D!"])
    assert sec.calls.call_count == 0
    assert not tmp_lake.has_table(FUNDAMENTALS_FACTS.name)


def test_ingest_dedupes_after_normalising(
    sec: respx.MockRouter, tmp_lake: Lake, cik_map: CikMap
) -> None:
    report = ingest(tmp_lake, cik_map, ["aapl", "AAPL", "brk-b"])
    assert report.symbols_ok == ("AAPL",)
    assert report.symbols_failed == ("BRK.B",)  # CIK known, no companyfacts recorded
    facts_calls = [c for c in sec.calls if "/companyfacts/CIK0000320193" in c.request.url.path]
    assert len(facts_calls) == 1


def test_a_former_cik_adds_its_facts_filed_until_its_last_day(
    sec: respx.MockRouter, tmp_lake: Lake
) -> None:
    """Stage 29 follow-up 4: a symbol whose issuer moved to a new CIK (ExxonMobil 2026, Disney
    2019, ...) reads the former CIK's facts filed up to `until` and the current CIK's facts filed
    from `since`, each row with the CIK that filed it. Here MSFT's CIK plays the former one."""
    until, since = date(2020, 6, 30), date(2019, 1, 1)
    override = CikOverride(
        symbol="AAPL", cik=AAPL, name="Apple", evidence="e", since=since,
        former=(FormerCik(cik=MSFT, until=until, name="Microsoft"),),
    )  # fmt: skip
    cik_map = CikMap({"AAPL": AAPL}).with_overrides((override,))
    report = ingest(tmp_lake, cik_map, ["AAPL"])
    tags = repo_concept_map().tags()
    own = parse_companyfacts(companyfacts(AAPL), symbol="AAPL", tags=tags)
    former = parse_companyfacts(companyfacts(MSFT), symbol="AAPL", tags=tags)
    own, former = own[own["filed"] >= since], former[former["filed"] <= until]
    assert len(own) and len(former)
    stored = tmp_lake.read_table(FUNDAMENTALS_FACTS.name)
    assert report.rows == len(stored) == len(own) + len(former)
    by_cik = stored.groupby("cik")["filed"].agg(["min", "max", "size"])
    assert by_cik.loc[MSFT, "max"] <= until and by_cik.loc[MSFT, "size"] == len(former)
    assert by_cik.loc[AAPL, "min"] >= since and by_cik.loc[AAPL, "size"] == len(own)
    assert stored["available_at"].notna().all()


def test_an_empty_current_cik_does_not_stop_the_former_one(
    sec: respx.MockRouter, tmp_lake: Lake
) -> None:
    """A current CIK with no fact in its window (`since` after every filing) still lets the
    former CIK's facts in."""
    override = CikOverride(
        symbol="AAPL", cik=AAPL, name="Apple", evidence="e", since=date(2099, 1, 1),
        former=(FormerCik(cik=MSFT, until=date(2099, 1, 1), name="Microsoft"),),
    )  # fmt: skip
    report = ingest(tmp_lake, CikMap({"AAPL": AAPL}).with_overrides((override,)), ["AAPL"])
    assert report.symbols_ok == ("AAPL",)
    assert set(tmp_lake.read_table(FUNDAMENTALS_FACTS.name)["cik"]) == {MSFT}


def test_a_co_registrants_fact_is_stored_once_under_the_current_cik(tmp_lake: Lake) -> None:
    """A filing of co-registrants (L-3 Holdings and L-3 Communications) carries the same facts
    under both CIKs: each fact is stored once, with the current CIK."""
    twin = {**companyfacts(AAPL), "cik": 2}
    override = CikOverride(
        symbol="AAPL", cik=AAPL, name="Apple", evidence="e",
        former=(FormerCik(cik=2, until=date(2099, 1, 1), name="Apple co-registrant"),),
    )  # fmt: skip
    with respx.mock(assert_all_called=False) as router:
        router.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000000002.json").respond(
            json=twin
        )
        router.get("https://data.sec.gov/submissions/CIK0000000002.json").respond(
            content=(FIXTURES / "submissions" / "CIK0000320193.json").read_bytes()
        )
        router.route(host="data.sec.gov").mock(side_effect=serve_fixture)
        report = ingest(tmp_lake, CikMap({"AAPL": AAPL}).with_overrides((override,)), ["AAPL"])
    stored = tmp_lake.read_table(FUNDAMENTALS_FACTS.name)
    assert report.rows == len(stored) == expected_rows(AAPL)
    assert set(stored["cik"]) == {AAPL}


def test_refresh_false_reads_the_archived_answers_of_every_cik(
    sec: respx.MockRouter, tmp_lake: Lake
) -> None:
    """With `refresh=False` the companyfacts and submissions archived by an earlier run are
    read for the current and the former CIK alike: no request is made."""
    override = CikOverride(
        symbol="AAPL", cik=AAPL, name="Apple", evidence="e",
        former=(FormerCik(cik=MSFT, until=date(2020, 6, 30), name="Microsoft"),),
    )  # fmt: skip
    cik_map = CikMap({"AAPL": AAPL}).with_overrides((override,))
    first = ingest(tmp_lake, cik_map, ["AAPL"])
    calls = sum(route.call_count for route in sec.routes)
    again = ingest_fundamentals(
        tmp_lake, edgar_client(tmp_lake), ["AAPL"], cik_map=cik_map,
        concept_map=repo_concept_map(), refresh=False,
    )  # fmt: skip
    assert (again.rows, again.symbols_ok) == (first.rows, ("AAPL",))
    assert calls > 0 and sum(route.call_count for route in sec.routes) == calls
    # and `refresh=True` asks EDGAR again for both CIKs: companyfacts and submissions of each,
    # plus the history pages that no longer match their index entry (Stage 29 follow-up 7:
    # SEC rewrites them; the recorded pages are trimmed, so both are fetched again)
    ingest(tmp_lake, cik_map, ["AAPL"])
    assert sum(route.call_count for route in sec.routes) == calls + 4 + 2


# --- Stage 29 follow-up 5: the old lines mutation testing left untested ------------------------

NO_CIK = "no CIK (not in company_tickers.json or config/cik_overrides.yaml)"


def _no_acceptance(cik: int) -> dict[str, Any]:
    """The recorded submissions of `cik` with every acceptance time of `filings.recent` blank."""
    subs: dict[str, Any] = fixture_json(f"submissions/CIK{cik:010d}.json")
    recent = subs["filings"]["recent"]
    recent["acceptanceDateTime"] = ["" for _ in recent["acceptanceDateTime"]]
    return subs


@pytest.fixture
def unaccepted() -> Iterator[respx.MockRouter]:
    """EDGAR with AAPL's and MSFT's recent filings carrying no acceptance time."""
    with respx.mock(assert_all_called=False) as router:
        for cik in (AAPL, MSFT):
            router.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").mock(
                return_value=httpx.Response(200, json=_no_acceptance(cik))
            )
        router.route(host="data.sec.gov").mock(side_effect=serve_fixture)
        yield router


def test_failure_reasons_and_the_request_message_are_exact(
    sec: respx.MockRouter, tmp_lake: Lake, cik_map: CikMap
) -> None:
    report = ingest(tmp_lake, cik_map, ["ZZZZ", "EMPTY"])
    assert dict(report.failure_reasons) == {
        "ZZZZ": NO_CIK,
        "EMPTY": "no mapped facts in companyfacts",
    }
    with pytest.raises(
        ValueError, match=r"^symbols must be a sequence of tickers, not one string$"
    ):
        ingest(tmp_lake, cik_map, "AAPL")  # type: ignore[arg-type]


def test_the_log_events(sec: respx.MockRouter, tmp_lake: Lake, cik_map: CikMap) -> None:
    with capture_logs() as logs:
        report = ingest(tmp_lake, cik_map, ["GOOGL", "AAPL"])
    failed = [e for e in logs if e["event"] == "fundamentals.symbol_failed"]
    assert failed == [
        {
            "event": "fundamentals.symbol_failed",
            "symbol": "GOOGL",
            "error": report.failure_reasons["GOOGL"],
            "log_level": "warning",
        }
    ]
    assert "HTTP 404" in failed[0]["error"]
    done = [e for e in logs if e["event"] == "fundamentals.ingest"]
    assert done == [
        {
            "event": "fundamentals.ingest",
            "rows": report.rows,
            "symbols_ok": 1,
            "symbols_failed": 1,
            "fallback_rows": 0,
            "log_level": "info",
        }
    ]
    assert report.rows == expected_rows(AAPL) > 0


def test_fallback_rows_add_up_over_symbols_and_use_the_default_calendar(
    unaccepted: respx.MockRouter, tmp_path: Path, cik_map: CikMap
) -> None:
    """Facts of filings without an acceptance time are available at the open of the session
    after the filing date (the default calendar's, `TradingCalendar(config=config.calendar)`),
    and the report and the log count them over every symbol."""
    one = {s: ingest(Lake(tmp_path / s), cik_map, [s]).fallback_rows for s in ("AAPL", "MSFT")}
    assert one["AAPL"] > 0 and one["MSFT"] > 0
    lake = Lake(tmp_path / "both")
    with capture_logs() as logs:
        both = ingest(lake, cik_map, ["AAPL", "MSFT"])
    assert both.fallback_rows == one["AAPL"] + one["MSFT"]
    done = [e for e in logs if e["event"] == "fundamentals.ingest"]
    assert done[0]["fallback_rows"] == both.fallback_rows
    stored = lake.read_table(FUNDAMENTALS_FACTS.name)
    late = stored[stored["accepted_at"].isna()].iloc[0]
    cal = TradingCalendar()
    expected = pd.Timestamp(cal.session_open(cal.next_session(late["filed"])))
    assert late["available_at"] == expected


def test_a_given_calendar_or_the_config_s_calendar_dates_the_fallback_rows(
    unaccepted: respx.MockRouter, tmp_path: Path, cik_map: CikMap
) -> None:
    """The calendar given, else the config's (`calendar` bounds ending in 2016 put the 2023
    filings outside it), dates facts without an acceptance time."""
    narrow = TradingCalendar(date(2000, 1, 1), date(2016, 1, 1))
    with pytest.raises(ValueError, match="outside the calendar"):
        ingest_fundamentals(
            Lake(tmp_path / "given"), edgar_client(), ["AAPL"], cik_map=cik_map,
            concept_map=repo_concept_map(), calendar=narrow,
        )  # fmt: skip
    bounded = AtlasConfig.model_validate({"calendar": {"end": "2016-01-01"}})
    with pytest.raises(ValueError, match="outside the calendar"):
        ingest_fundamentals(
            Lake(tmp_path / "config"), edgar_client(), ["AAPL"], cik_map=cik_map,
            concept_map=repo_concept_map(), config=bounded,
        )  # fmt: skip


def test_the_concept_map_given_or_else_the_config_s_map_file(
    sec: respx.MockRouter, tmp_path: Path, cik_map: CikMap
) -> None:
    """Only the tags of the concept map are stored: the map given, else the one in the
    config's `xbrl_map_file`."""
    data = yaml.safe_load((REPO_ROOT / "config" / "xbrl_map.yaml").read_text())
    for concept in data["concepts"].values():  # each tagged concept keeps its first tag only
        if "tags" in concept:
            concept["tags"] = concept["tags"][:1]
    small = tmp_path / "small_map.yaml"
    small.write_text(yaml.safe_dump(data))
    kept = load_concept_map(small).tags()
    full = ingest(Lake(tmp_path / "full"), cik_map, ["AAPL"]).rows
    given = ingest_fundamentals(
        Lake(tmp_path / "given"), edgar_client(), ["AAPL"], cik_map=cik_map,
        concept_map=load_concept_map(small),
    )  # fmt: skip
    cfg = AtlasConfig.model_validate({"data": {"fundamentals": {"xbrl_map_file": str(small)}}})
    configured = ingest_fundamentals(
        Lake(tmp_path / "configured"), edgar_client(), ["AAPL"], cik_map=cik_map, config=cfg
    )
    for report, root in ((given, "given"), (configured, "configured")):
        stored = Lake(tmp_path / root).read_table(FUNDAMENTALS_FACTS.name)
        assert 0 < report.rows < full
        assert set(zip(stored["taxonomy"], stored["tag"], strict=True)) <= set(kept)
