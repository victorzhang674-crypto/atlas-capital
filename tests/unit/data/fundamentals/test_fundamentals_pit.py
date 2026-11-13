"""Stage 09 Tasks 5-6: point-in-time facts (restatements), the wide view, ratios, look-ahead."""

import math
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from fundamentals_helpers import AAPL, JPM, MSFT, fixture_facts, make_facts, repo_concept_map

from atlas.core.config import FundamentalsConfig
from atlas.core.errors import LookAheadError
from atlas.core.pit import AsOf
from atlas.data.fundamentals import (
    DETAIL_COLUMNS,
    RATIO_NAMES,
    compute_ratios,
    fundamentals_pit,
    fundamentals_pit_detail,
    known_facts,
    metric_names,
    quarterly_values,
    resolve_concept,
)

CFG = FundamentalsConfig()
Q1 = {"tag": "A", "start": date(2020, 1, 1), "end": date(2020, 3, 31)}


def at(day: date) -> AsOf:
    return AsOf.for_session(day)


def quarter_value(facts: pd.DataFrame, as_of: AsOf, concept: str, end: date) -> float:
    known = known_facts(facts, as_of)
    q = quarterly_values(resolve_concept(known, repo_concept_map()[concept]), CFG)
    return float(q.set_index("period_end").loc[end, "value"])


# --- Task 5: known facts and restatements ----------------------------------------------------


def test_known_facts_latest_filing_per_period() -> None:
    facts = make_facts(
        {**Q1, "value": 100, "filed": date(2020, 5, 1), "accn": "orig"},
        {**Q1, "value": 90, "filed": date(2021, 2, 1), "accn": "restated"},
        {**Q1, "tag": "B", "value": 5, "filed": date(2030, 1, 2), "accn": "future"},
    )
    early = known_facts(facts, at(date(2020, 6, 1)))
    assert list(early["accn"]) == ["orig"]
    late = known_facts(facts, at(date(2021, 6, 1)))
    assert list(late["accn"]) == ["restated"]
    assert "future" not in set(known_facts(facts, at(date(2029, 12, 31)))["accn"])


def test_fact_ending_after_its_filing_is_ignored() -> None:
    # Phase 1 checkpoint, Tier 3: AAL's 10-Q of 2026-07-23 dates its cover-page share count
    # 2027-07-17 (a filer typo). As the latest balance it froze the metric for a year; a period
    # cannot end after the filing that reports it, so such a fact is left out (never guessed).
    facts = make_facts(
        {"tag": "S", "value": 100, "end": date(2026, 6, 30), "filed": date(2026, 7, 23)},
        {"tag": "S", "value": 101, "end": date(2027, 7, 17), "filed": date(2026, 7, 23)},
        {"tag": "S", "value": 102, "end": date(2026, 7, 23), "filed": date(2026, 7, 23)},
    )
    known = known_facts(facts, at(date(2026, 8, 3)))
    assert sorted(known["value"]) == [100.0, 102.0]  # a period ending on the filing day is fine


def test_known_facts_cutoff_is_strict() -> None:
    cutoff = at(date(2020, 5, 4)).cutoff
    facts = make_facts({**Q1, "value": 1, "filed": date(2020, 5, 4), "available_at": cutoff})
    assert known_facts(facts, at(date(2020, 5, 4))).empty
    assert len(known_facts(facts, at(date(2020, 5, 5)))) == 1


def test_restated_value_not_seen_before_its_filing() -> None:
    facts = make_facts(
        {**Q1, "value": 100, "filed": date(2020, 5, 1)},
        {**Q1, "value": 90, "filed": date(2021, 2, 1)},
    )
    flow = repo_concept_map()["revenue"].model_copy(update={"parts": (((("us-gaap", "A"),),),)})
    for day, expected in ((date(2020, 6, 1), 100.0), (date(2021, 6, 1), 90.0)):
        q = quarterly_values(resolve_concept(known_facts(facts, at(day)), flow), CFG)
        assert q["value"].tolist() == [expected]


def test_jpm_q1_2012_restatement() -> None:
    """JPM's 10-Q/A (London Whale) cut Q1 2012 revenue; accepted 2012-08-09 07:44 ET."""
    jpm = fixture_facts(JPM, "JPM")
    end = date(2012, 3, 31)
    assert quarter_value(jpm, at(date(2012, 8, 8)), "revenue", end) == 26_712e6
    assert quarter_value(jpm, at(date(2012, 8, 9)), "revenue", end) == 26_052e6


def test_msft_asc606_recast() -> None:
    msft = fixture_facts(MSFT, "MSFT")
    end = date(2016, 9, 30)
    assert quarter_value(msft, at(date(2017, 6, 1)), "revenue", end) == 20_453e6
    assert quarter_value(msft, at(date(2018, 6, 1)), "revenue", end) == 21_928e6


def test_known_facts_keeps_available_at_dtype() -> None:
    known = known_facts(fixture_facts(JPM, "JPM"), at(date(2015, 1, 2)))
    assert isinstance(known["available_at"].dtype, pd.DatetimeTZDtype)
    assert (known["available_at"] < pd.Timestamp(datetime(2015, 1, 2, 13, tzinfo=UTC))).all()


# --- Task 6: the wide view, ratios and look-ahead --------------------------------------------

AAPL_10K_FY2023 = "0000320193-23-000106"
M = 1e6


def aapl_pit(day: date) -> pd.Series:
    return fundamentals_pit(fixture_facts(AAPL, "AAPL"), at(day), repo_concept_map(), CFG)


def test_acceptance_fundamentals_pit_aapl_fy2023() -> None:
    """Brief acceptance: AAPL as of 2023-11-03 matches the FY2023 10-K (accepted 2023-11-02
    22:08:27 UTC, filed 2023-11-03), the fixture-derived figures."""
    s = aapl_pit(date(2023, 11, 3))
    assert list(s.index) == list(metric_names(repo_concept_map()))
    expected = {
        "revenue_ttm": 383_285 * M,
        "gross_profit_ttm": 169_148 * M,
        "operating_income_ttm": 114_301 * M,
        "net_income_ttm": 96_995 * M,
        "eps_diluted_ttm": 6.13,
        "shares_outstanding": 15_552_752_000.0,
        "total_assets": 352_583 * M,
        "total_liabilities": 290_437 * M,
        "stockholders_equity": 62_146 * M,
        "cash_and_equivalents": 29_965 * M,
        "total_debt": (105_103 + 5_985) * M,
        "operating_cash_flow_ttm": 110_543 * M,
        "capex_ttm": 10_959 * M,
        "rnd_ttm": 29_915 * M,
        "sbc_ttm": 10_833 * M,
        "depreciation_amortization_ttm": 11_519 * M,
        "shares_weighted_average": 15_744_231_000.0,  # basic, fiscal year (not a count)
        "gross_margin": 169_148 / 383_285,
        "operating_margin": 114_301 / 383_285,
        "roe": 96_995 / 62_146,
        "roa": 96_995 / 352_583,
        "debt_to_equity": (105_103 + 5_985) / 62_146,
        "fcf_ttm": (110_543 - 10_959) * M,
    }
    assert s.to_dict() == pytest.approx(expected, rel=1e-12)


def test_acceptance_detail_cites_the_10k() -> None:
    detail = fundamentals_pit_detail(
        fixture_facts(AAPL, "AAPL"), at(date(2023, 11, 3)), repo_concept_map(), CFG
    ).set_index("metric")
    assert list(detail.columns) == list(DETAIL_COLUMNS)[1:]
    revenue = detail.loc["revenue_ttm"]
    assert revenue["source"] == "annual"
    assert revenue["accn"] == AAPL_10K_FY2023
    assert revenue["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert (revenue["period_start"], revenue["period_end"]) == (
        date(2022, 9, 25),
        date(2023, 9, 30),
    )
    assert revenue["available_at"] == pd.Timestamp("2023-11-02 22:08:27", tz="UTC")
    assert detail.loc["shares_outstanding", "tag"] == "dei:EntityCommonStockSharesOutstanding"
    assert detail.loc["gross_margin", "source"] == "ratio"
    assert detail.loc["gross_margin", "period_end"] == date(2023, 9, 30)


def test_fundamentals_pit_before_10k_uses_previous_ttm() -> None:
    """The evening-before acceptance: at 08:00 ET on 2023-11-02 the 10-K is not public yet."""
    s = aapl_pit(date(2023, 11, 2))
    assert s["revenue_ttm"] == (90_146 + 117_154 + 94_836 + 81_797) * M
    detail = fundamentals_pit_detail(
        fixture_facts(AAPL, "AAPL"), at(date(2023, 11, 2)), repo_concept_map(), CFG
    ).set_index("metric")
    assert detail.loc["revenue_ttm", "period_end"] == date(2023, 7, 1)
    assert detail.loc["revenue_ttm", "source"] == "quarters"


def test_ratios_known_values() -> None:
    values = {
        "revenue_ttm": 200.0,
        "gross_profit_ttm": 80.0,
        "operating_income_ttm": 30.0,
        "net_income_ttm": 20.0,
        "stockholders_equity": 100.0,
        "total_assets": 400.0,
        "total_debt": 50.0,
        "operating_cash_flow_ttm": 45.0,
        "capex_ttm": 15.0,
    }
    assert compute_ratios(values) == pytest.approx(
        {
            "gross_margin": 0.4,
            "operating_margin": 0.15,
            "roe": 0.2,
            "roa": 0.05,
            "debt_to_equity": 0.5,
            "fcf_ttm": 30.0,
        }
    )
    assert tuple(compute_ratios(values)) == RATIO_NAMES


@pytest.mark.parametrize(
    ("changes", "nan"),
    [
        ({"stockholders_equity": -5.0}, {"roe", "debt_to_equity"}),
        ({"stockholders_equity": 0.0}, {"roe", "debt_to_equity"}),
        ({"revenue_ttm": 0.0}, {"gross_margin", "operating_margin"}),
        ({"total_assets": math.nan}, {"roa"}),
        ({"capex_ttm": math.nan}, {"fcf_ttm"}),
        ({"gross_profit_ttm": math.nan}, {"gross_margin"}),
    ],
)
def test_ratio_with_missing_or_nonpositive_denominator_is_nan(
    changes: dict[str, float], nan: set[str]
) -> None:
    values = dict.fromkeys(
        (
            "revenue_ttm",
            "gross_profit_ttm",
            "operating_income_ttm",
            "net_income_ttm",
            "stockholders_equity",
            "total_assets",
            "total_debt",
            "operating_cash_flow_ttm",
            "capex_ttm",
        ),
        10.0,
    )
    ratios = compute_ratios({**values, **changes})
    assert {k for k, v in ratios.items() if math.isnan(v)} == nan


def test_jpm_missing_concepts_are_nan() -> None:
    s = fundamentals_pit(fixture_facts(JPM, "JPM"), at(date(2024, 3, 1)), repo_concept_map(), CFG)
    for name in ("gross_profit_ttm", "operating_income_ttm", "capex_ttm", "rnd_ttm",
                 "gross_margin", "operating_margin", "fcf_ttm"):  # fmt: skip
        assert math.isnan(s[name]), name
    for name in ("revenue_ttm", "net_income_ttm", "total_assets", "total_debt", "roe", "roa"):
        assert math.isfinite(s[name]) and s[name] > 0, name


def test_stale_metric_is_nan() -> None:
    facts = make_facts(
        {"tag": "Assets", "end": date(2020, 12, 31), "value": 5, "filed": date(2021, 2, 1)}
    )
    fresh = fundamentals_pit(facts, at(date(2021, 6, 1)), repo_concept_map(), CFG)
    assert fresh["total_assets"] == 5.0
    limit = date(2020, 12, 31) + timedelta(days=CFG.max_staleness_days)
    assert fundamentals_pit(facts, at(limit), repo_concept_map(), CFG)["total_assets"] == 5.0
    later = at(limit + timedelta(days=3))
    assert math.isnan(fundamentals_pit(facts, later, repo_concept_map(), CFG)["total_assets"])
    detail = fundamentals_pit_detail(facts, later, repo_concept_map(), CFG).set_index("metric")
    assert detail.loc["total_assets", "source"] == "stale"


def test_no_facts_gives_all_nan() -> None:
    s = fundamentals_pit(make_facts(), at(date(2021, 6, 1)), repo_concept_map(), CFG)
    assert s.isna().all()
    assert len(s) == len(metric_names(repo_concept_map()))


def _future_facts(cutoff: datetime) -> pd.DataFrame:
    after = cutoff + timedelta(minutes=1)
    return make_facts(
        {"tag": "RevenueFromContractWithCustomerExcludingAssessedTax", "value": 1.0,
         "start": date(2022, 9, 25), "end": date(2023, 9, 30), "filed": date(2023, 11, 3),
         "available_at": after},
        {"tag": "Assets", "value": 1.0, "end": date(2023, 12, 30), "filed": date(2024, 2, 2),
         "available_at": after + timedelta(days=90)},
    )  # fmt: skip


def test_future_facts_do_not_change_result() -> None:
    as_of = at(date(2023, 11, 3))
    facts = fixture_facts(AAPL, "AAPL")
    baseline = fundamentals_pit(facts, as_of, repo_concept_map(), CFG)
    polluted = pd.concat([facts, _future_facts(as_of.cutoff)], ignore_index=True)
    assert fundamentals_pit(polluted, as_of, repo_concept_map(), CFG).equals(baseline)


def test_guard_raises_if_filter_bypassed(monkeypatch: pytest.MonkeyPatch) -> None:
    import atlas.data.fundamentals.pit as pit_module

    as_of = at(date(2023, 11, 3))
    facts = pd.concat([fixture_facts(AAPL, "AAPL"), _future_facts(as_of.cutoff)], ignore_index=True)
    monkeypatch.setattr(pit_module, "pit_filter", lambda df, _as_of: df)
    with pytest.raises(LookAheadError):
        fundamentals_pit(facts, as_of, repo_concept_map(), CFG)


def test_data_limitations_doc_covers_fundamentals() -> None:
    from fundamentals_helpers import REPO_ROOT

    text = (REPO_ROOT / "docs" / "data-limitations.md").read_text(encoding="utf-8")
    section = text.split("## Fundamentals", 1)[1].lower()
    for word in ("companyfacts", "acceptance", "restat", "cik", "nan", "q4", "dimension"):
        assert word in section, word
