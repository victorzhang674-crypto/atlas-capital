"""Stage 09 Task 4: discrete quarters (reported, YTD differences, Q4 = FY - Q1..Q3), TTM for
flows and the latest balance for stocks."""

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import pytest
from fundamentals_helpers import AAPL, fixture_facts, make_facts, repo_concept_map

from atlas.core.config import FundamentalsConfig
from atlas.data.fundamentals import (
    QUARTER_COLUMNS,
    Concept,
    MetricValue,
    latest_balance,
    quarterly_values,
    resolve_concept,
    ttm,
)

CFG = FundamentalsConfig()
FLOW = Concept(name="c", kind="flow", unit="USD", parts=(((("us-gaap", "A"),),),))
STOCK = Concept(name="s", kind="stock", unit="USD", parts=(((("us-gaap", "A"),),),))
JAN1 = date(2023, 1, 1)
Q_ENDS = (date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30), date(2023, 12, 31))
Q_STARTS = (JAN1, date(2023, 4, 1), date(2023, 7, 1), date(2023, 10, 1))


def spec(start: date, end: date, value: float, filed: date, **extra: Any) -> dict[str, Any]:
    return {"tag": "A", "start": start, "end": end, "value": value, "filed": filed, **extra}


def periods(*specs: dict[str, Any]) -> pd.DataFrame:
    return resolve_concept(make_facts(*specs), FLOW)


def quarters_of(*specs: dict[str, Any]) -> dict[date, tuple[float, str]]:
    q = quarterly_values(periods(*specs), CFG)
    assert list(q.columns) == list(QUARTER_COLUMNS)
    return {r.period_end: (r.value, r.source) for r in q.itertuples()}


def discrete_2023(values: tuple[float, ...] = (10, 15, 20, 25)) -> list[dict[str, Any]]:
    return [
        spec(s, e, v, date(2024, 2, 1)) for s, e, v in zip(Q_STARTS, Q_ENDS, values, strict=True)
    ]


def test_quarters_reported() -> None:
    q = quarters_of(*discrete_2023())
    assert q == {e: (v, "reported") for e, v in zip(Q_ENDS, (10, 15, 20, 25), strict=True)}


def test_quarters_from_ytd_chain() -> None:
    q = quarters_of(
        spec(JAN1, Q_ENDS[0], 10, date(2023, 5, 1)),
        spec(JAN1, Q_ENDS[1], 25, date(2023, 8, 1)),
        spec(JAN1, Q_ENDS[2], 45, date(2023, 11, 1)),
        spec(JAN1, Q_ENDS[3], 70, date(2024, 2, 1)),
    )
    assert q == {
        Q_ENDS[0]: (10, "reported"),
        Q_ENDS[1]: (15, "derived"),
        Q_ENDS[2]: (20, "derived"),
        Q_ENDS[3]: (25, "derived"),
    }


def test_derived_quarter_carries_latest_input() -> None:
    late = datetime(2024, 2, 1, 22, tzinfo=UTC)
    q = quarterly_values(
        periods(
            spec(JAN1, Q_ENDS[2], 45, date(2023, 11, 1), accn="ytd-9m"),
            spec(JAN1, Q_ENDS[3], 70, date(2024, 2, 1), available_at=late, accn="fy"),
        ),
        CFG,
    )
    q4 = q[q["period_end"] == Q_ENDS[3]].iloc[0]
    assert q4["period_start"] == Q_STARTS[3]
    assert q4["available_at"] == pd.Timestamp(late)
    assert q4["accn"] == "fy"


def test_q4_from_fy_minus_three_quarters() -> None:
    q = quarters_of(*discrete_2023()[:3], spec(JAN1, Q_ENDS[3], 72, date(2024, 2, 1)))
    assert q[Q_ENDS[3]] == (72 - 10 - 15 - 20, "derived")


def test_q4_not_derived_when_quarters_do_not_tile() -> None:
    q = quarters_of(*discrete_2023()[:3:2], spec(JAN1, Q_ENDS[3], 72, date(2024, 2, 1)))
    assert Q_ENDS[3] not in q


def test_reported_quarter_beats_derived() -> None:
    q = quarters_of(
        spec(JAN1, Q_ENDS[0], 10, date(2023, 5, 1)),
        spec(JAN1, Q_ENDS[1], 25, date(2023, 8, 1)),
        spec(Q_STARTS[1], Q_ENDS[1], 16, date(2023, 8, 1)),
    )
    assert q[Q_ENDS[1]] == (16, "reported")


def test_ttm_uses_annual_at_fiscal_year_end() -> None:
    result = ttm(
        periods(*discrete_2023(), spec(JAN1, Q_ENDS[3], 71, date(2024, 2, 1), accn="fy")),
        CFG,
    )
    assert isinstance(result, MetricValue)
    assert (result.value, result.period_start, result.period_end) == (71, JAN1, Q_ENDS[3])
    assert (result.source, result.accn) == ("annual", "fy")


def test_ttm_sums_four_contiguous_quarters() -> None:
    q1_2024 = spec(date(2024, 1, 1), date(2024, 3, 31), 30, date(2024, 5, 1), accn="q1-24")
    result = ttm(periods(*discrete_2023(), q1_2024), CFG)
    assert result is not None
    assert (result.value, result.period_start) == (15 + 20 + 25 + 30, Q_STARTS[1])
    assert (result.period_end, result.source, result.accn) == (
        date(2024, 3, 31),
        "quarters",
        "q1-24",
    )


def test_ttm_skips_end_with_gap() -> None:
    q2_2024 = spec(date(2024, 4, 1), date(2024, 6, 30), 30, date(2024, 8, 1))
    result = ttm(periods(*discrete_2023(), q2_2024), CFG)  # Q1 2024 missing
    assert result is not None
    assert (result.value, result.period_end) == (70, Q_ENDS[3])


def test_ttm_none_without_a_full_year() -> None:
    assert ttm(periods(*discrete_2023()[:3]), CFG) is None
    assert ttm(periods(), CFG) is None


def test_latest_balance() -> None:
    facts = make_facts(
        {"tag": "A", "end": date(2023, 6, 30), "value": 5, "filed": date(2023, 8, 1)},
        {"tag": "A", "end": date(2023, 9, 30), "value": 7, "filed": date(2023, 11, 1)},
    )
    result = latest_balance(resolve_concept(facts, STOCK))
    assert result is not None
    assert (result.value, result.period_start, result.period_end) == (7, None, date(2023, 9, 30))
    assert result.source == "reported"
    assert latest_balance(resolve_concept(make_facts(), STOCK)) is None


def _aapl(concept: str) -> pd.DataFrame:
    return resolve_concept(fixture_facts(AAPL, "AAPL"), repo_concept_map()[concept])


def test_aapl_q4_fy2023_revenue_derived() -> None:
    q = quarterly_values(_aapl("revenue"), CFG).set_index("period_end")
    assert q.loc[date(2023, 9, 30), "value"] == 383_285e6 - 117_154e6 - 94_836e6 - 81_797e6
    assert q.loc[date(2023, 9, 30), "source"] == "derived"
    assert q.loc[date(2023, 7, 1), ["value", "source"]].tolist() == [81_797e6, "reported"]


def test_aapl_operating_cash_flow_quarters_from_ytd() -> None:
    q = quarterly_values(_aapl("operating_cash_flow"), CFG).set_index("period_end")
    expected = {
        date(2022, 12, 31): 34_005e6,
        date(2023, 4, 1): 62_565e6 - 34_005e6,
        date(2023, 7, 1): 88_945e6 - 62_565e6,
        date(2023, 9, 30): 110_543e6 - 88_945e6,
    }
    for end, value in expected.items():
        assert q.loc[end, "value"] == pytest.approx(value)


def test_aapl_ttm_full_history_ends_after_fy2023() -> None:
    annual = ttm(_aapl("revenue"), CFG)
    assert annual is not None  # the full history: the latest fiscal quarter is the end
    assert annual.period_end > date(2023, 9, 30)
