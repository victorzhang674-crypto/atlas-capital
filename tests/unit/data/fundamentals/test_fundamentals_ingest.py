"""Stage 09 Task 7: ingestion into the lake and the lake-backed `Fundamentals` reader."""

from collections.abc import Iterator
from datetime import date, datetime

import httpx
import pandas as pd
import pytest
import respx
from fundamentals_helpers import (
    AAPL,
    JPM,
    MSFT,
    companyfacts,
    edgar_client,
    fixture_facts,
    fixture_json,
    repo_concept_map,
    serve_fixture,
)

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.pit import AsOf
from atlas.core.storage import Lake
from atlas.data.fundamentals import (
    FUNDAMENTALS_FACTS,
    CikMap,
    Fundamentals,
    FundamentalsIngestReport,
    fundamentals_pit,
    ingest_fundamentals,
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
