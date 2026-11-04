"""Stage 16 Task 2 (Stage 09 code): the TTM at every period end and the per-concept history
of the known facts (`ttm_values`, `concept_history`), used by the fundamental factors."""

import math
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import pytest
from fundamentals_helpers import AAPL, JPM, MSFT, fixture_facts, make_facts, repo_concept_map

from atlas.core.config import FundamentalsConfig
from atlas.core.pit import AsOf
from atlas.data.fundamentals import (
    HISTORY_COLUMNS,
    TTM_COLUMNS,
    Concept,
    concept_history,
    known_facts,
    quarterly_values,
    resolve_concept,
    ttm,
    ttm_values,
)

CFG = FundamentalsConfig()
FLOW = Concept(name="c", kind="flow", unit="USD", parts=(((("us-gaap", "A"),),),))
JAN1 = date(2023, 1, 1)
Q_ENDS = (date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30), date(2023, 12, 31))
Q_STARTS = (JAN1, date(2023, 4, 1), date(2023, 7, 1), date(2023, 10, 1))
Q1_24 = (date(2024, 1, 1), date(2024, 3, 31))


def spec(start: date, end: date, value: float, filed: date, **extra: Any) -> dict[str, Any]:
    return {"tag": "A", "start": start, "end": end, "value": value, "filed": filed, **extra}


def periods(*specs: dict[str, Any]) -> pd.DataFrame:
    return resolve_concept(make_facts(*specs), FLOW)


def discrete_2023() -> list[dict[str, Any]]:
    return [
        spec(s, e, v, date(2024, 2, 1))
        for s, e, v in zip(Q_STARTS, Q_ENDS, (10, 15, 20, 25), strict=True)
    ]


# --- ttm_values ------------------------------------------------------------------------------


def test_ttm_values_one_row_per_end() -> None:
    t = ttm_values(periods(*discrete_2023(), spec(*Q1_24, 30, date(2024, 5, 1))), CFG)
    assert list(t.columns) == list(TTM_COLUMNS)
    assert list(t["period_end"]) == [Q_ENDS[3], Q1_24[1]]
    assert list(t["value"]) == [70.0, 90.0]
    assert list(t["source"]) == ["quarters", "quarters"]
    assert list(t["period_start"]) == [JAN1, Q_STARTS[1]]


def test_ttm_values_prefers_annual() -> None:
    fy = spec(JAN1, Q_ENDS[3], 71, date(2024, 2, 1), accn="fy")
    t = ttm_values(periods(*discrete_2023(), fy), CFG)
    row = t.set_index("period_end").loc[Q_ENDS[3]]
    assert (row["value"], row["source"], row["accn"]) == (71.0, "annual", "fy")


def test_ttm_values_gap_has_no_row() -> None:
    q2_24 = spec(date(2024, 4, 1), date(2024, 6, 30), 30, date(2024, 8, 1))
    t = ttm_values(periods(*discrete_2023(), q2_24), CFG)  # Q1 2024 missing
    assert list(t["period_end"]) == [Q_ENDS[3]]


def test_ttm_values_empty() -> None:
    t = ttm_values(periods(*discrete_2023()[:3]), CFG)
    assert t.empty
    assert list(t.columns) == list(TTM_COLUMNS)
    assert ttm_values(periods(), CFG).empty


@pytest.mark.parametrize(("cik", "symbol"), [(AAPL, "AAPL"), (MSFT, "MSFT"), (JPM, "JPM")])
@pytest.mark.parametrize("day", [date(2019, 6, 3), date(2022, 11, 1), date(2024, 3, 1)])
def test_ttm_is_the_last_ttm_value(cik: int, symbol: str, day: date) -> None:
    known = known_facts(fixture_facts(cik, symbol), AsOf.for_session(day))
    for concept in repo_concept_map().values():
        if concept.kind != "flow":
            continue
        p = resolve_concept(known, concept)
        single, series = ttm(p, CFG), ttm_values(p, CFG)
        if single is None:
            assert series.empty
            continue
        last = series.iloc[-1]
        assert (single.value, single.period_end, single.source) == (
            last["value"],
            last["period_end"],
            last["source"],
        )
        assert (single.period_start, single.accn, single.tag) == (
            last["period_start"],
            last["accn"],
            last["tag"],
        )


# --- concept_history -------------------------------------------------------------------------


def _facts() -> pd.DataFrame:
    rev = [spec(*Q1_24, 30, date(2024, 5, 1))]
    rev += [{**s, "tag": "Revenues"} for s in discrete_2023()]
    rev = [{**s, "tag": "Revenues"} for s in rev]
    shares = [
        {
            "taxonomy": "dei",
            "tag": "EntityCommonStockSharesOutstanding",
            "unit": "shares",
            "end": d,
            "filed": f,
            "value": v,
        }
        for d, f, v in (
            (date(2023, 4, 20), date(2023, 5, 1), 1000),
            (date(2024, 4, 22), date(2024, 5, 1), 1100),
        )
    ]
    return make_facts(*rev, *shares)


def test_concept_history_flows_and_stocks() -> None:
    known = known_facts(_facts(), AsOf.for_session(date(2024, 6, 3)))
    h = concept_history(known, repo_concept_map(), ("revenue", "shares_outstanding"), CFG)
    assert list(h.columns) == list(HISTORY_COLUMNS)
    rev = h[h["concept"] == "revenue"].set_index("period_end")
    assert list(rev.index) == [*Q_ENDS, Q1_24[1]]
    assert list(rev["value"]) == [10.0, 15.0, 20.0, 25.0, 30.0]
    assert list(rev["ttm"].iloc[:3].isna()) == [True, True, True]
    assert list(rev["ttm"].iloc[3:]) == [70.0, 90.0]
    assert list(rev["source"]) == ["reported"] * 5
    assert list(rev["ttm_source"].iloc[3:]) == ["quarters", "quarters"]
    assert rev.loc[Q1_24[1], "period_start"] == Q1_24[0]
    sh = h[h["concept"] == "shares_outstanding"]
    assert list(sh["period_end"]) == [date(2023, 4, 20), date(2024, 4, 22)]
    assert list(sh["value"]) == [1000.0, 1100.0]
    assert sh["ttm"].isna().all()
    assert sh["period_start"].isna().all()
    assert h["available_at"].dtype == "datetime64[us, UTC]"


def test_concept_history_ttm_only_end() -> None:
    # An annual figure with only Q1-Q2 known: the FY end has a TTM but no discrete quarter.
    facts = make_facts(
        *[{**s, "tag": "Revenues"} for s in discrete_2023()[:2]],
        {**spec(JAN1, Q_ENDS[3], 80, date(2024, 2, 1)), "tag": "Revenues"},
    )
    known = known_facts(facts, AsOf.for_session(date(2024, 3, 1)))
    h = concept_history(known, repo_concept_map(), ("revenue",), CFG).set_index("period_end")
    assert math.isnan(h.loc[Q_ENDS[3], "value"])
    assert h.loc[Q_ENDS[3], "ttm"] == 80.0
    assert h.loc[Q_ENDS[3], "ttm_source"] == "annual"


def test_concept_history_available_at_is_latest_input() -> None:
    late = datetime(2024, 5, 2, 20, tzinfo=UTC)
    facts = make_facts(
        *[{**s, "tag": "Revenues"} for s in discrete_2023()],
        {**spec(*Q1_24, 30, date(2024, 5, 2)), "tag": "Revenues", "available_at": late},
    )
    known = known_facts(facts, AsOf.for_session(date(2024, 6, 3)))
    h = concept_history(known, repo_concept_map(), ("revenue",), CFG).set_index("period_end")
    assert h.loc[Q1_24[1], "available_at"] == pd.Timestamp(late)


def test_concept_history_uses_restatement_known_at_as_of() -> None:
    facts = make_facts(
        *[{**s, "tag": "Revenues"} for s in discrete_2023()],
        {**spec(Q_STARTS[0], Q_ENDS[0], 12, date(2024, 4, 1)), "tag": "Revenues", "form": "10-K/A"},
    )
    cmap = repo_concept_map()
    before = concept_history(known_facts(facts, AsOf.for_session(date(2024, 3, 1))), cmap,
                             ("revenue",), CFG)  # fmt: skip
    after = concept_history(known_facts(facts, AsOf.for_session(date(2024, 4, 3))), cmap,
                            ("revenue",), CFG)  # fmt: skip
    assert before.set_index("period_end").loc[Q_ENDS[3], "ttm"] == 70.0
    assert after.set_index("period_end").loc[Q_ENDS[3], "ttm"] == 72.0


def test_concept_history_empty_and_unknown() -> None:
    known = known_facts(make_facts(), AsOf.for_session(date(2024, 3, 1)))
    h = concept_history(known, repo_concept_map(), ("revenue", "total_assets"), CFG)
    assert h.empty
    assert list(h.columns) == list(HISTORY_COLUMNS)
    with pytest.raises(ValueError, match="unknown concept"):
        concept_history(known, repo_concept_map(), ("nope",), CFG)


def test_concept_history_annual_only_flow() -> None:
    # Found on the real lake (2026-09-30): a filer with only fiscal-year figures has no
    # discrete quarter; the empty quarter frame must still merge with the TTM rows.
    facts = make_facts({**spec(JAN1, Q_ENDS[3], 80, date(2024, 2, 1)), "tag": "Revenues"})
    known = known_facts(facts, AsOf.for_session(date(2024, 3, 1)))
    h = concept_history(known, repo_concept_map(), ("revenue",), CFG)
    assert list(h["period_end"]) == [Q_ENDS[3]]
    assert h["ttm"].iloc[0] == 80.0 and math.isnan(h["value"].iloc[0])
    q = quarterly_values(resolve_concept(known, repo_concept_map()["revenue"]), CFG)
    assert q.empty and q["period_end"].dtype == object
