"""Phase 2 checkpoint (Tier 2 item 7, Tier 3 item 2; Stage 09 code): the `average` concept
kind (a quantity averaged over a reporting period, such as weighted-average shares: never
summed into quarters or a TTM) and the two concepts the map gained, `depreciation_amortization`
and `shares_weighted_average`."""

import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fundamentals_helpers import AAPL, MSFT, REPO_ROOT, fixture_facts, make_facts, repo_concept_map

from atlas.core.config import FundamentalsConfig
from atlas.core.errors import ConfigError
from atlas.core.pit import AsOf
from atlas.data.fundamentals import (
    Concept,
    ConceptMap,
    concept_history,
    concept_values,
    fundamentals_pit,
    fundamentals_pit_detail,
    known_facts,
    load_concept_map,
    metric_names,
    resolve_concept,
)

CFG = FundamentalsConfig()
AVG = Concept(name="avg", kind="average", unit="shares", parts=(((("us-gaap", "W"),),),))
CMAP = ConceptMap({"avg": AVG})
M = 1e6


def at(day: date) -> AsOf:
    return AsOf.for_session(day)


def w(start: date | None, end: date, value: float, filed: date, **extra: Any) -> dict[str, Any]:
    spec = {"tag": "W", "unit": "shares", "end": end, "value": value, "filed": filed, **extra}
    return spec if start is None else spec | {"start": start}


Q3 = (date(2023, 7, 1), date(2023, 9, 30))
NINE_MONTHS = (date(2023, 1, 1), date(2023, 9, 30))
YEAR = (date(2023, 1, 1), date(2023, 12, 31))
FILED_Q3, FILED_FY = date(2023, 11, 1), date(2024, 2, 1)


def facts_2023() -> pd.DataFrame:
    return make_facts(
        w(*NINE_MONTHS, 104.0, FILED_Q3),
        w(*Q3, 100.0, FILED_Q3),
        w(*YEAR, 102.0, FILED_FY),
        w(None, date(2023, 12, 31), 999.0, FILED_FY),  # an instant: not an average
    )


# --- the kind ----------------------------------------------------------------------------------


def test_an_average_resolves_durations_only() -> None:
    periods = resolve_concept(facts_2023(), AVG)
    assert list(periods["duration_days"]) == [92, 273, 365]
    assert 999.0 not in set(periods["value"])


def test_metric_name_of_an_average_is_the_concept_name() -> None:
    assert metric_names(CMAP)[0] == "avg"


def test_latest_average_is_the_shortest_period_at_the_latest_end() -> None:
    known = known_facts(facts_2023(), at(date(2023, 11, 10)))
    found = concept_values(known, CMAP, CFG)["avg"]
    assert found is not None
    # 2023-09-30 has the quarter (100) and the nine months (104): the quarter is the latest
    assert (found.value, found.period_start, found.period_end) == (100.0, *Q3)
    assert (found.source, found.tag) == ("reported", "W")
    assert found.available_at == datetime(2023, 11, 1, 21, tzinfo=UTC)


def test_a_fiscal_year_end_has_only_the_annual_average() -> None:
    known = known_facts(facts_2023(), at(date(2024, 2, 10)))
    found = concept_values(known, CMAP, CFG)["avg"]
    assert found is not None
    assert (found.value, found.period_start, found.period_end) == (102.0, *YEAR)


def test_an_average_is_never_summed_or_differenced() -> None:
    """A flow would give Q4 = FY - 9M and a TTM; an average gives neither."""
    known = known_facts(facts_2023(), at(date(2024, 2, 10)))
    history = concept_history(known, CMAP, ["avg"], CFG)
    assert list(history["period_end"]) == [Q3[1], YEAR[1]]
    assert list(history["value"]) == [100.0, 102.0]
    assert list(history["period_start"]) == [Q3[0], YEAR[0]]
    assert history["ttm"].isna().all()
    assert list(history["source"]) == ["reported", "reported"]
    assert list(history["ttm_source"]) == [None, None]
    assert list(history["concept"]) == ["avg", "avg"]


def test_history_uses_a_restated_average_once_it_is_public() -> None:
    """A later filing restates the quarter (say after a split): the history shows the new
    value with the later filing's `available_at`, and only from then on."""
    restated = make_facts(w(*Q3, 50.0, date(2024, 8, 1)))
    facts = pd.concat([facts_2023(), restated], ignore_index=True)
    before = concept_history(known_facts(facts, at(date(2024, 7, 1))), CMAP, ["avg"], CFG)
    after = concept_history(known_facts(facts, at(date(2024, 8, 5))), CMAP, ["avg"], CFG)
    assert before.loc[before["period_end"] == Q3[1], "value"].item() == 100.0
    row = after[after["period_end"] == Q3[1]].iloc[0]
    assert row["value"] == 50.0
    assert row["available_at"] == pd.Timestamp(datetime(2024, 8, 1, 21, tzinfo=UTC))


def test_no_facts_gives_no_average() -> None:
    empty = make_facts(w(*Q3, 1.0, FILED_Q3)).iloc[0:0]
    assert concept_values(empty, CMAP, CFG)["avg"] is None
    assert concept_history(empty, CMAP, ["avg"], CFG).empty


def test_a_stale_average_is_not_used_in_the_wide_view() -> None:
    detail = fundamentals_pit_detail(facts_2023(), at(date(2025, 6, 2)), CMAP, CFG)
    row = detail[detail["metric"] == "avg"].iloc[0]
    assert math.isnan(row["value"])
    assert row["source"] == "stale"


# --- the concept map ---------------------------------------------------------------------------


def test_repo_map_defines_depreciation_and_weighted_average_shares() -> None:
    cmap = repo_concept_map()
    da = cmap["depreciation_amortization"]
    assert (da.kind, da.unit) == ("flow", "USD")
    assert da.parts == (
        (
            (("us-gaap", "DepreciationDepletionAndAmortization"),),
            (("us-gaap", "DepreciationAndAmortization"),),
            (("us-gaap", "DepreciationAmortizationAndAccretionNet"),),
            (("us-gaap", "Depreciation"), ("us-gaap", "AmortizationOfIntangibleAssets")),
        ),
    )
    shares = cmap["shares_weighted_average"]
    assert (shares.kind, shares.unit) == ("average", "shares")
    assert shares.parts == (
        (
            (("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic"),),
            (("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding"),),
        ),
    )
    names = metric_names(cmap)
    assert "depreciation_amortization_ttm" in names
    assert "shares_weighted_average" in names


def test_load_concept_map_rejects_an_unknown_kind(tmp_path: Path) -> None:
    text = (REPO_ROOT / "config" / "xbrl_map.yaml").read_text()
    assert "kind: average" in text
    bad = tmp_path / "xbrl_map.yaml"
    bad.write_text(text.replace("kind: average", "kind: mean"))
    with pytest.raises(ConfigError, match="shares_weighted_average"):
        load_concept_map(bad)


# --- recorded filings --------------------------------------------------------------------------


def aapl(day: date) -> pd.Series:
    return fundamentals_pit(fixture_facts(AAPL, "AAPL"), at(day), repo_concept_map(), CFG)


def test_aapl_fy2023_depreciation_and_average_shares() -> None:
    """The FY2023 10-K (filed 2023-11-03): depreciation and amortization 11,519 USDm; basic
    weighted-average shares 15,744,231 thousand (the cover page counts 15,552,752 thousand on
    2023-10-20: the average of a year of buybacks is 1.2% above the count)."""
    s = aapl(date(2023, 11, 6))
    assert s["depreciation_amortization_ttm"] == 11_519 * M
    assert s["shares_weighted_average"] == 15_744_231_000.0
    assert s["shares_outstanding"] == 15_552_752_000.0


def test_aapl_quarter_average_beats_the_year_to_date_average() -> None:
    """The 10-Q for the quarter to 2023-07-01 reports the quarter (15,697,614 thousand) and
    the nine months (15,792,497 thousand); the 10-Q to 2023-12-30 the quarter only."""
    assert aapl(date(2023, 8, 10))["shares_weighted_average"] == 15_697_614_000.0
    assert aapl(date(2024, 2, 5))["shares_weighted_average"] == 15_509_763_000.0


def test_aapl_depreciation_ttm_from_year_to_date_cash_flow_lines() -> None:
    """After the Q1 FY2024 10-Q: TTM = FY2023 (11,519) - Q1 FY2023 (2,916) + Q1 FY2024
    (2,848) USDm, the quarters in between derived from year-to-date cash-flow facts."""
    s = aapl(date(2024, 2, 5))
    assert s["depreciation_amortization_ttm"] == pytest.approx((11_519 - 2_916 + 2_848) * M)


def test_msft_depreciation_falls_back_to_depreciation_plus_amortization() -> None:
    """MSFT tags none of the three totals in its recorded filings: the concept is the strict
    sum `Depreciation + AmortizationOfIntangibleAssets`."""
    facts = fixture_facts(MSFT, "MSFT")
    detail = fundamentals_pit_detail(facts, at(date(2023, 8, 1)), repo_concept_map(), CFG)
    row = detail[detail["metric"] == "depreciation_amortization_ttm"].iloc[0]
    assert row["tag"] == "Depreciation + AmortizationOfIntangibleAssets"
    assert row["period_end"] == date(2023, 6, 30)
    assert row["value"] > 0
