"""Stage 09 Task 3: resolving a concept per period, with tag fallbacks and sums of parts."""

from datetime import UTC, date, datetime

import pandas as pd
from fundamentals_helpers import AAPL, JPM, fixture_facts, make_facts, repo_concept_map

from atlas.data.fundamentals import PERIOD_COLUMNS, Concept, latest_filings, resolve_concept

Q1 = {"start": date(2023, 1, 1), "end": date(2023, 3, 31)}
Q2 = {"start": date(2023, 4, 1), "end": date(2023, 6, 30)}
END_Q1, END_Q2 = date(2023, 3, 31), date(2023, 6, 30)
FILED = date(2023, 8, 1)


def concept(kind: str, *parts: tuple[tuple[str, ...], ...]) -> Concept:
    resolved = tuple(
        tuple(tuple(("us-gaap", tag) for tag in alt) for alt in part) for part in parts
    )
    return Concept(name="c", kind=kind, unit="USD", parts=resolved)  # type: ignore[arg-type]


def test_resolve_fallback_per_period() -> None:
    facts = make_facts(
        {"tag": "A", "value": 1, **Q1, "filed": FILED},
        {"tag": "B", "value": 10, **Q1, "filed": FILED},
        {"tag": "B", "value": 20, **Q2, "filed": FILED},
    )
    out = resolve_concept(facts, concept("flow", (("A",), ("B",))))
    assert list(out.columns) == list(PERIOD_COLUMNS)
    assert list(out["value"]) == [1.0, 20.0]  # A wins Q1; B fills Q2
    assert list(out["tag"]) == ["A", "B"]
    assert list(out["period_start"]) == [date(2023, 1, 1), date(2023, 4, 1)]
    assert list(out["duration_days"]) == [90, 91]


def test_resolve_strict_sum_alternative() -> None:
    facts = make_facts(
        {"tag": "N", "value": 95, "end": END_Q1, "filed": FILED},
        {"tag": "C", "value": 10, "end": END_Q1, "filed": FILED},
        {"tag": "N", "value": 90, "end": END_Q2, "filed": FILED},  # no C at END_Q2
        {"tag": "T", "value": 7, "end": END_Q2, "filed": FILED},
    )
    out = resolve_concept(facts, concept("stock", (("N", "C"), ("T",))))
    assert list(out["value"]) == [105.0, 7.0]
    assert list(out["tag"]) == ["N + C", "T"]
    assert out["period_start"].isna().all()


def test_resolve_sum_of_parts_missing_part_is_zero() -> None:
    later = datetime(2023, 8, 2, 12, tzinfo=UTC)
    facts = make_facts(
        {"tag": "LTD", "value": 100, "end": END_Q1, "filed": FILED, "accn": "a-1"},
        {
            "tag": "CP",
            "value": 5,
            "end": END_Q1,
            "filed": FILED,
            "available_at": later,
            "accn": "a-2",
        },
        {"tag": "LTD", "value": 80, "end": END_Q2, "filed": FILED},
    )
    out = resolve_concept(facts, concept("stock", (("LTD",),), (("CP",),)))
    assert list(out["value"]) == [105.0, 80.0]
    assert out["tag"].iloc[0] == "LTD; CP"
    assert out["available_at"].iloc[0] == pd.Timestamp(later)  # the latest input
    assert out["accn"].iloc[0] == "a-2"


def test_resolve_sum_of_parts_all_missing_is_empty() -> None:
    facts = make_facts({"tag": "OTHER", "value": 1, "end": END_Q1, "filed": FILED})
    out = resolve_concept(facts, concept("stock", (("LTD",),), (("CP",),)))
    assert out.empty
    assert list(out.columns) == list(PERIOD_COLUMNS)


def test_resolve_ignores_other_units_and_kinds() -> None:
    facts = make_facts(
        {"tag": "A", "value": 1, **Q1, "filed": FILED, "unit": "EUR"},
        {"tag": "A", "value": 2, "end": END_Q1, "filed": FILED},  # an instant
        {"tag": "A", "value": 3, **Q2, "filed": FILED},
    )
    flow = resolve_concept(facts, concept("flow", (("A",),)))
    assert list(flow["value"]) == [3.0]
    stock = resolve_concept(facts, concept("stock", (("A",),)))
    assert list(stock["value"]) == [2.0]


def test_resolve_uses_latest_filing_per_period() -> None:
    facts = make_facts(
        {"tag": "A", "value": 100, **Q1, "filed": date(2023, 5, 1)},
        {"tag": "A", "value": 90, **Q1, "filed": date(2024, 5, 1)},
    )
    assert list(resolve_concept(facts, concept("flow", (("A",),)))["value"]) == [90.0]
    assert len(latest_filings(facts)) == 1


def _value(out: pd.DataFrame, end: date, duration: int | None = None) -> pd.Series:
    rows = out[out["period_end"] == end]
    if duration is not None:
        rows = rows[rows["duration_days"] == duration]
    assert len(rows) == 1, rows
    return rows.iloc[0]


def test_aapl_total_debt_fy2023() -> None:
    out = resolve_concept(fixture_facts(AAPL, "AAPL"), repo_concept_map()["total_debt"])
    row = _value(out, date(2023, 9, 30))
    assert row["value"] == 105_103_000_000 + 5_985_000_000
    assert row["tag"] == "LongTermDebt; CommercialPaper"


def test_aapl_revenue_switches_tag_over_time() -> None:
    out = resolve_concept(fixture_facts(AAPL, "AAPL"), repo_concept_map()["revenue"])
    fy2014 = _value(out, date(2014, 9, 27), 364)
    assert (fy2014["tag"], fy2014["value"]) == ("SalesRevenueNet", 182_795_000_000)
    fy2017 = _value(out, date(2017, 9, 30), 371)  # reported under three tags: priority wins
    assert (fy2017["tag"], fy2017["value"]) == ("Revenues", 229_234_000_000)
    fy2023 = _value(out, date(2023, 9, 30), 371)
    assert fy2023["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert fy2023["value"] == 383_285_000_000


def test_jpm_has_no_gross_profit() -> None:
    jpm = fixture_facts(JPM, "JPM")
    assert resolve_concept(jpm, repo_concept_map()["gross_profit"]).empty
    assert resolve_concept(jpm, repo_concept_map()["capex"]).empty
    debt = resolve_concept(jpm, repo_concept_map()["total_debt"])
    assert not debt.empty


def test_resolve_priority_beats_a_later_filing_of_a_lower_tag() -> None:
    """Documented limit: per period the first alternative wins, even when a lower-priority tag
    was filed later (a restatement filed under another tag is not picked up)."""
    facts = make_facts(
        {"tag": "A", "value": 100, **Q1, "filed": date(2023, 5, 1)},
        {"tag": "B", "value": 90, **Q1, "filed": date(2024, 5, 1)},
    )
    out = resolve_concept(facts, concept("flow", (("A",), ("B",))))
    assert (out["tag"].tolist(), out["value"].tolist()) == (["A"], [100.0])


def test_bank_revenue_is_net_interest_plus_noninterest_income() -> None:
    """A bank that tags no total revenue: revenue = net interest income + noninterest income,
    not `RevenueFromContractWithCustomer…`, which for a bank covers fee income only (SunTrust
    FY2018: 5,987 M + 3,226 M = 9,213 M; the fee tag would give a fraction of it)."""
    fy = {"start": date(2018, 1, 1), "end": date(2018, 12, 31), "filed": date(2019, 2, 22)}
    facts = make_facts(
        {"tag": "InterestIncomeExpenseNet", "value": 5_987, **fy},
        {"tag": "NoninterestIncome", "value": 3_226, **fy},
        {"tag": "RevenueFromContractWithCustomerExcludingAssessedTax", "value": 1_500, **fy},
    )
    out = resolve_concept(facts, repo_concept_map()["revenue"])
    assert out["value"].tolist() == [9_213.0]
    assert out["tag"].tolist() == ["InterestIncomeExpenseNet + NoninterestIncome"]
