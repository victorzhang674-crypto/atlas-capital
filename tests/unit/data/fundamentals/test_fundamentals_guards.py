"""Stage 09 guard tests: the fail-loud paths of the client, parser, mapping and quarters."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fundamentals_helpers import AAPL, edgar_client, fixture_json, repo_concept_map

from atlas.core.config import FundamentalsConfig
from atlas.core.errors import ConfigError, DataError
from atlas.data.fundamentals import (
    QUARTER_COLUMNS,
    EdgarClient,
    load_concept_map,
    parse_alternative,
    parse_companyfacts,
    parse_submissions,
    quarterly_values,
    resolve_concept,
)

SUBS_URL = "https://data.sec.gov/submissions/CIK0000320193.json"
TAGS = frozenset({("us-gaap", "Assets")})


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock() as mock:
        yield mock


def test_client_context_manager_closes_its_own_pool() -> None:
    with edgar_client() as client:
        assert isinstance(client, EdgarClient)
    assert client._http.is_closed


def test_client_keeps_an_injected_pool_open() -> None:
    http = httpx.Client()
    EdgarClient(FundamentalsConfig(), user_agent="x y@z.invalid", http=http).close()
    assert not http.is_closed
    http.close()


@pytest.mark.parametrize("name", ["../etc/passwd.json", "a/b.json", "page.txt"])
def test_submissions_page_rejects_bad_names(name: str) -> None:
    with pytest.raises(DataError, match="page name"):
        edgar_client().submissions_page(name)


@pytest.mark.parametrize(
    ("payload", "pattern"),
    [
        ({"cik": AAPL}, "filings.recent"),
        ({"filings": {"recent": [1]}}, "filings.recent"),
        ({"filings": {"recent": {}, "files": [{"name": "x.json"}]}}, "submissions table|history"),
    ],
)
def test_filing_index_rejects_malformed_submissions(
    router: respx.MockRouter, payload: dict[str, Any], pattern: str
) -> None:
    router.get(SUBS_URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(DataError, match=pattern):
        edgar_client().filing_index(AAPL)


def test_filing_index_rejects_bad_history_entry(router: respx.MockRouter) -> None:
    subs = fixture_json("submissions/CIK0000320193.json")
    subs["filings"]["files"] = [{"name": "p.json", "filingFrom": "x", "filingTo": "y"}]
    router.get(SUBS_URL).mock(return_value=httpx.Response(200, json=subs))
    with pytest.raises(DataError, match="history entry"):
        edgar_client().filing_index(AAPL)


def test_client_rejects_a_json_array(router: respx.MockRouter) -> None:
    router.get(SUBS_URL).mock(return_value=httpx.Response(200, json=[1, 2]))
    with pytest.raises(DataError, match="JSON object"):
        edgar_client().submissions(AAPL)


def test_parse_alternative_rejects_a_repeated_tag() -> None:
    with pytest.raises(ValueError, match="twice"):
        parse_alternative("A + A")


def _facts(body: object, cik: object = 1) -> dict[str, object]:
    return {"cik": cik, "facts": {"us-gaap": {"Assets": body}}}


ENTRY = {"end": "2020-12-31", "val": 1, "accn": "a", "form": "10-K", "filed": "2021-02-01"}


@pytest.mark.parametrize(
    ("payload", "pattern"),
    [
        (_facts({"units": {"USD": [ENTRY]}}, cik="x"), "cik"),
        (_facts({"label": "no units"}), "units"),
        (_facts({"units": {"USD": ENTRY}}), "not a list"),
        (_facts({"units": {"USD": ["oops"]}}), "not an object"),
        (_facts({"units": {"USD": [{**ENTRY, "end": 20201231}]}}), "ISO date"),
        (_facts({"units": {"USD": [{**ENTRY, "fy": "2020"}]}}), "fy"),
        (_facts({"units": {"USD": [{**ENTRY, "val": float("inf")}]}}), "finite"),
    ],
)
def test_parse_companyfacts_guards(payload: dict[str, Any], pattern: str) -> None:
    with pytest.raises(DataError, match=pattern):
        parse_companyfacts(payload, symbol="X", tags=TAGS)


def test_parse_companyfacts_accepts_cik_as_string() -> None:
    facts = parse_companyfacts(
        _facts({"units": {"USD": [ENTRY]}}, cik="320193"), symbol="X", tags=TAGS
    )
    assert facts["cik"].tolist() == [320193]


@pytest.mark.parametrize(
    ("table", "pattern"),
    [
        ({"accessionNumber": []}, "lacks"),
        (
            {"accessionNumber": ["a"], "form": ["10-K"], "filingDate": ["2020-01-02"],
             "acceptanceDateTime": ["yesterday"]},
            "acceptanceDateTime",
        ),
        (
            {"accessionNumber": ["a"], "form": ["10-K"], "filingDate": ["02/01/2020"],
             "acceptanceDateTime": [""]},
            "filingDate",
        ),
    ],
)  # fmt: skip
def test_parse_submissions_guards(table: dict[str, Any], pattern: str) -> None:
    with pytest.raises(DataError, match=pattern):
        parse_submissions(table)


def test_quarterly_values_of_a_stock_is_empty() -> None:
    from fundamentals_helpers import make_facts

    facts = make_facts(
        {"tag": "Assets", "end": date(2020, 12, 31), "value": 1, "filed": date(2021, 2, 1)}
    )
    periods = resolve_concept(facts, repo_concept_map()["total_assets"])
    out = quarterly_values(periods, FundamentalsConfig())
    assert out.empty
    assert list(out.columns) == list(QUARTER_COLUMNS)


def test_concept_map_is_a_read_only_mapping() -> None:
    cmap = repo_concept_map()
    assert len(cmap) == 15
    assert list(cmap) == list(cmap.names)


def test_concept_map_rejects_an_empty_sum_part(tmp_path: Path) -> None:
    path = tmp_path / "xbrl_map.yaml"
    path.write_text(
        "concepts: {total_debt: {kind: stock, unit: USD, sum_of: [[LongTermDebt], []]}}"
    )
    with pytest.raises(ConfigError, match="at least one alternative"):
        load_concept_map(path)
