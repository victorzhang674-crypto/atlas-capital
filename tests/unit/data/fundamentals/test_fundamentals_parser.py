"""Stage 09 Task 2: concept-map loader, companyfacts → long table, filing index, available_at."""

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
import respx
from fundamentals_helpers import (
    AAPL,
    FIXTURES,
    JPM,
    MSFT,
    companyfacts,
    edgar_client,
    fixture_json,
    serve_fixture,
)

from atlas.core.calendar import TradingCalendar
from atlas.core.errors import ConfigError, DataError
from atlas.core.storage import DEFAULT_REGISTRY, validate_frame
from atlas.data.fundamentals import (
    FACT_COLUMNS,
    FUNDAMENTALS_FACTS,
    RAW_FACT_COLUMNS,
    attach_available_at,
    load_concept_map,
    parse_companyfacts,
    parse_submissions,
)

REVENUE_TAG = "RevenueFromContractWithCustomerExcludingAssessedTax"
AAPL_10K_FY2023 = "0000320193-23-000106"


@pytest.fixture(scope="module")
def tags(repo_root: Path) -> frozenset[tuple[str, str]]:
    return load_concept_map(repo_root / "config" / "xbrl_map.yaml").tags()


@pytest.fixture
def sec() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.route(host="data.sec.gov").mock(side_effect=serve_fixture)
        yield router


# --- concept map loader ----------------------------------------------------------------------


def test_load_concept_map_repo_file(repo_root: Path) -> None:
    cmap = load_concept_map(repo_root / "config" / "xbrl_map.yaml")
    assert list(cmap.names) == [
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "eps_diluted",
        "shares_outstanding",
        "total_assets",
        "total_liabilities",
        "stockholders_equity",
        "cash_and_equivalents",
        "total_debt",
        "operating_cash_flow",
        "capex",
        "rnd",
        "sbc",
    ]
    revenue = cmap["revenue"]
    assert (revenue.kind, revenue.unit) == ("flow", "USD")
    assert revenue.parts[0][1] == (("us-gaap", REVENUE_TAG),)
    debt = cmap["total_debt"]
    assert debt.kind == "stock" and len(debt.parts) == 2
    assert debt.parts[0][2] == (
        ("us-gaap", "LongTermDebtNoncurrent"),
        ("us-gaap", "LongTermDebtCurrent"),
    )
    assert cmap["shares_outstanding"].parts[0][0] == (
        ("dei", "EntityCommonStockSharesOutstanding"),
    )
    assert cmap["eps_diluted"].unit == "USD/shares"
    assert len(cmap.tags()) == 35


@pytest.mark.parametrize(
    ("body", "pattern"),
    [
        ("concepts: {revenue: {kind: level, unit: USD, tags: [Revenues]}}", "kind"),
        (
            "concepts: {revenue: {kind: flow, unit: USD, tags: [Revenues], sum_of: [[A]]}}",
            "exactly one of",
        ),
        ("concepts: {revenue: {kind: flow, unit: USD}}", "exactly one of"),
        ("concepts: {revenue: {kind: flow, unit: USD, tags: [not-a-tag]}}", "not-a-tag"),
        ("concepts: {revenue: {kind: flow, unit: USD, tags: ['ifrs:Revenue']}}", "ifrs"),
        ("concepts: {revenue: {kind: flow, unit: USD, tags: [Revenues], extra: 1}}", "extra"),
        ("concepts: {Revenue: {kind: flow, unit: USD, tags: [Revenues]}}", "Revenue"),
        ("concepts: {revenue: {kind: flow, unit: USD, tags: []}}", "tags"),
    ],
)
def test_load_concept_map_rejects_bad_file(tmp_path: Path, body: str, pattern: str) -> None:
    path = tmp_path / "xbrl_map.yaml"
    path.write_text(body)
    with pytest.raises(ConfigError, match=pattern) as err:
        load_concept_map(path)
    assert str(path) in str(err.value)


def test_load_concept_map_requires_ratio_inputs(repo_root: Path, tmp_path: Path) -> None:
    text = (repo_root / "config" / "xbrl_map.yaml").read_text()
    path = tmp_path / "xbrl_map.yaml"
    path.write_text("\n".join(ln for ln in text.splitlines() if "  capex:" not in ln))
    with pytest.raises(ConfigError, match="capex"):
        load_concept_map(path)


def test_load_concept_map_ratio_input_must_have_right_kind(repo_root: Path, tmp_path: Path) -> None:
    text = (repo_root / "config" / "xbrl_map.yaml").read_text()
    path = tmp_path / "xbrl_map.yaml"
    path.write_text(text.replace("total_assets: {kind: stock", "total_assets: {kind: flow"))
    with pytest.raises(ConfigError, match="total_assets"):
        load_concept_map(path)


def test_load_concept_map_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="concepts"):
        load_concept_map(tmp_path / "absent.yaml")


# --- companyfacts → long table ---------------------------------------------------------------


def test_parse_companyfacts_aapl_fy2023_revenue(tags: frozenset[tuple[str, str]]) -> None:
    facts = parse_companyfacts(companyfacts(AAPL), symbol="aapl", tags=tags)
    assert list(facts.columns) == list(RAW_FACT_COLUMNS)
    row = facts[
        (facts["tag"] == REVENUE_TAG)
        & (facts["period_end"] == date(2023, 9, 30))
        & (facts["accn"] == AAPL_10K_FY2023)
    ].iloc[0]
    assert row["symbol"] == "AAPL"
    assert row["cik"] == AAPL
    assert row["taxonomy"] == "us-gaap"
    assert row["unit"] == "USD"
    assert row["value"] == 383_285_000_000.0
    assert row["period_start"] == date(2022, 9, 25)
    assert row["duration_days"] == 371
    assert (row["fy"], row["fp"], row["form"]) == (2023, "FY", "10-K")
    assert row["filed"] == date(2023, 11, 3)


def test_parse_companyfacts_instants_and_dei(tags: frozenset[tuple[str, str]]) -> None:
    facts = parse_companyfacts(companyfacts(AAPL), symbol="AAPL", tags=tags)
    assets = facts[(facts["tag"] == "Assets") & (facts["accn"] == AAPL_10K_FY2023)]
    assert set(assets["duration_days"]) == {0}
    assert assets["period_start"].isna().all()
    assert 352_583_000_000.0 in set(assets["value"])
    shares = facts[facts["taxonomy"] == "dei"]
    assert set(shares["tag"]) == {"EntityCommonStockSharesOutstanding"}
    assert set(shares["unit"]) == {"shares"}


def test_parse_companyfacts_keeps_only_mapped_tags() -> None:
    facts = parse_companyfacts(
        companyfacts(AAPL), symbol="AAPL", tags=frozenset({("us-gaap", "Assets")})
    )
    assert set(facts["tag"]) == {"Assets"}


def test_parse_companyfacts_sorted_and_unique(tags: frozenset[tuple[str, str]]) -> None:
    for cik in (AAPL, MSFT, JPM):
        facts = parse_companyfacts(companyfacts(cik), symbol="X", tags=tags)
        key = list(FUNDAMENTALS_FACTS.primary_key)
        assert not facts.duplicated(subset=key).any()
        assert facts.equals(facts.sort_values(key).reset_index(drop=True))


def _payload(*entries: dict[str, object]) -> dict[str, object]:
    return {
        "cik": 1,
        "entityName": "X",
        "facts": {"us-gaap": {"Assets": {"units": {"USD": list(entries)}}}},
    }


ENTRY = {"end": "2020-12-31", "val": 10, "accn": "0000000001-21-000001", "fy": 2020,
         "fp": "FY", "form": "10-K", "filed": "2021-02-01"}  # fmt: skip


def test_parse_companyfacts_duplicates() -> None:
    tags = frozenset({("us-gaap", "Assets")})
    same = parse_companyfacts(_payload(ENTRY, dict(ENTRY)), symbol="X", tags=tags)
    assert len(same) == 1
    conflict = parse_companyfacts(_payload(ENTRY, {**ENTRY, "val": 11}), symbol="X", tags=tags)
    assert list(conflict["value"]) == [10.0]


@pytest.mark.parametrize(
    "bad",
    [
        {k: v for k, v in ENTRY.items() if k != "accn"},
        {**ENTRY, "end": "31/12/2020"},
        {**ENTRY, "val": "ten"},
        {**ENTRY, "val": True},
        {**ENTRY, "start": "2021-01-01"},
    ],
)
def test_parse_companyfacts_malformed_raises(bad: dict[str, object]) -> None:
    with pytest.raises(DataError, match="Assets"):
        parse_companyfacts(_payload(bad), symbol="X", tags=frozenset({("us-gaap", "Assets")}))


def test_parse_companyfacts_without_facts_raises() -> None:
    with pytest.raises(DataError, match="facts"):
        parse_companyfacts({"cik": 1}, symbol="X", tags=frozenset())


# --- filing index ----------------------------------------------------------------------------


def test_parse_submissions_acceptance_is_utc() -> None:
    index = parse_submissions(fixture_json("submissions/CIK0000320193.json")["filings"]["recent"])
    assert list(index.columns) == ["accn", "form", "filed", "accepted_at"]
    row = index[index["accn"] == AAPL_10K_FY2023].iloc[0]
    assert row["form"] == "10-K"
    assert row["filed"] == date(2023, 11, 3)
    assert row["accepted_at"] == pd.Timestamp("2023-11-02 22:08:27", tz="UTC")


def test_parse_submissions_blank_acceptance_is_nat() -> None:
    table = {"accessionNumber": ["a"], "form": ["10-K"], "filingDate": ["2020-01-02"],
             "acceptanceDateTime": [""]}  # fmt: skip
    assert parse_submissions(table)["accepted_at"].isna().all()


def test_parse_submissions_rejects_ragged_columns() -> None:
    table = {"accessionNumber": ["a", "b"], "form": ["10-K"], "filingDate": ["2020-01-02"],
             "acceptanceDateTime": [""]}  # fmt: skip
    with pytest.raises(DataError, match="length"):
        parse_submissions(table)


def test_filing_index_fetches_only_needed_pages(sec: respx.MockRouter) -> None:
    index = edgar_client().filing_index(JPM, {"0000019617-12-000262": date(2012, 8, 9)})
    pages = [c.request.url.path for c in sec.calls if "-submissions-" in c.request.url.path]
    assert pages == ["/submissions/CIK0000019617-submissions-064.json"]
    row = index[index["accn"] == "0000019617-12-000262"].iloc[0]
    assert row["form"] == "10-Q/A"
    assert row["accepted_at"] == pd.Timestamp("2012-08-09 11:44:21", tz="UTC")


def test_filing_index_reads_no_page_when_recent_has_the_filing(sec: respx.MockRouter) -> None:
    index = edgar_client().filing_index(AAPL, {AAPL_10K_FY2023: date(2023, 11, 3)})
    assert [c.request.url.path for c in sec.calls] == ["/submissions/CIK0000320193.json"]
    assert AAPL_10K_FY2023 in set(index["accn"])


def test_every_fixture_fact_has_an_acceptance_time(
    sec: respx.MockRouter, tags: frozenset[tuple[str, str]]
) -> None:
    for cik in (AAPL, MSFT, JPM):
        facts = parse_companyfacts(companyfacts(cik), symbol="X", tags=tags)
        needed = dict(zip(facts["accn"], facts["filed"], strict=True))
        index = edgar_client().filing_index(cik, needed)
        assert set(facts["accn"]) <= set(index["accn"])
    recorded = {p.name for p in (FIXTURES / "submissions").glob("CIK0000019617-*")}
    requested = {c.request.url.path.rsplit("/", 1)[-1] for c in sec.calls}
    assert recorded <= requested


# --- available_at ----------------------------------------------------------------------------


def _aapl_facts(tags: frozenset[tuple[str, str]]) -> pd.DataFrame:
    return parse_companyfacts(companyfacts(AAPL), symbol="AAPL", tags=tags)


def test_available_at_uses_acceptance_time(tags: frozenset[tuple[str, str]]) -> None:
    facts = _aapl_facts(tags)
    index = parse_submissions(fixture_json("submissions/CIK0000320193.json")["filings"]["recent"])
    out = attach_available_at(facts, index, TradingCalendar())
    assert list(out.columns) == list(FACT_COLUMNS)
    rows = out[out["accn"] == AAPL_10K_FY2023]
    accepted = pd.Timestamp("2023-11-02 22:08:27", tz="UTC")
    assert (rows["accepted_at"] == accepted).all()
    assert (rows["available_at"] == accepted).all()


def test_available_at_fallback_is_next_session_open(tags: frozenset[tuple[str, str]]) -> None:
    facts = _aapl_facts(tags)
    empty = parse_submissions(
        {"accessionNumber": [], "form": [], "filingDate": [], "acceptanceDateTime": []}
    )
    out = attach_available_at(facts, empty, TradingCalendar())
    rows = out[out["accn"] == AAPL_10K_FY2023]  # filed Friday 2023-11-03
    assert rows["accepted_at"].isna().all()
    expected = datetime(2023, 11, 6, 14, 30, tzinfo=UTC)  # Monday 09:30 EST
    assert (rows["available_at"] == pd.Timestamp(expected)).all()
    assert out["available_at"].notna().all()


def test_facts_frame_validates_against_schema(tags: frozenset[tuple[str, str]]) -> None:
    facts = _aapl_facts(tags)
    index = parse_submissions(fixture_json("submissions/CIK0000320193.json")["filings"]["recent"])
    table = validate_frame(FUNDAMENTALS_FACTS, attach_available_at(facts, index, TradingCalendar()))
    assert table.num_rows == len(facts)
    assert DEFAULT_REGISTRY.get("fundamentals_facts") is FUNDAMENTALS_FACTS


def test_recorded_fixture_is_what_the_readme_says() -> None:
    readme = (FIXTURES / "README.md").read_text()
    for name in (
        "companyfacts",
        "submissions",
        "company_tickers.json",
        "403",
        "record_sec_fixtures.py",
    ):
        assert name in readme
    tickers = json.loads((FIXTURES / "company_tickers.json").read_text())
    assert {e["ticker"] for e in tickers.values()} >= {
        "AAPL",
        "MSFT",
        "JPM",
        "BRK-B",
        "GOOGL",
        "META",
    }
