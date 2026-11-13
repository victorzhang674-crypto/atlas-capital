"""Phase 2 checkpoint, Tier 3 item 3: the per-company pipeline (`resolve_concept`,
`quarterly_values`, `ttm_values`, `concept_values`, `concept_history`) gives exactly what the
pandas implementation it replaced gave (`pandas_reference.py`), on random companies with
restatements, year-to-date chains, odd period lengths, competing tags and gaps, and on the
recorded AAPL / MSFT / JPM facts."""

import math
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pandas_reference as ref
import pytest
from fundamentals_helpers import AAPL, fixture_facts, make_facts, repo_concept_map

from atlas.core.config import FundamentalsConfig
from atlas.core.pit import AsOf
from atlas.data.fundamentals import (
    concept_history,
    concept_values,
    known_facts,
    latest_balance,
    quarterly_values,
    resolve_concept,
    ttm,
    ttm_values,
)

CFG = FundamentalsConfig()
CMAP = repo_concept_map()

FLOW_TAGS: dict[str, list[tuple[str, str]]] = {
    "revenue": [
        ("us-gaap", "Revenues"),
        ("us-gaap", "RevenuesNetOfInterestExpense"),
        ("us-gaap", "InterestIncomeExpenseNet"),
        ("us-gaap", "NoninterestIncome"),
        ("us-gaap", "SalesRevenueNet"),
    ],
    "gross_profit": [("us-gaap", "GrossProfit")],
    "operating_income": [("us-gaap", "OperatingIncomeLoss")],
    "net_income": [("us-gaap", "NetIncomeLoss"), ("us-gaap", "ProfitLoss")],
    "eps_diluted": [("us-gaap", "EarningsPerShareDiluted")],
    "operating_cash_flow": [("us-gaap", "NetCashProvidedByUsedInOperatingActivities")],
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
        ("us-gaap", "PaymentsToAcquireProductiveAssets"),
    ],
}
STOCK_TAGS: list[tuple[str, str, str]] = [
    ("dei", "EntityCommonStockSharesOutstanding", "shares"),
    ("us-gaap", "CommonStockSharesOutstanding", "shares"),
    ("us-gaap", "Assets", "USD"),
    ("us-gaap", "StockholdersEquity", "USD"),
    ("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD"),
    ("us-gaap", "CashAndDueFromBanks", "USD"),
    ("us-gaap", "LongTermDebt", "USD"),
    ("us-gaap", "LongTermDebtNoncurrent", "USD"),
    ("us-gaap", "LongTermDebtCurrent", "USD"),
    ("us-gaap", "ShortTermBorrowings", "USD"),
    ("us-gaap", "CommercialPaper", "USD"),
    ("us-gaap", "OtherShortTermBorrowings", "USD"),
]


def random_company(seed: int) -> pd.DataFrame:
    """A random company's `fundamentals_facts`: 4-7 fiscal years of quarters whose lengths
    vary (13- and 14-week quarters, the odd short or long one), each period reported as a
    discrete quarter, a year-to-date total, a fiscal year, or not at all; comparatives and
    restatements in later filings; several tags per concept; ties in `available_at`."""
    rng = np.random.default_rng(seed)
    specs: list[dict[str, Any]] = []
    start = date(2015, 1, 1) + timedelta(days=int(rng.integers(0, 300)))
    n_years = int(rng.integers(4, 8))
    filing = 0

    def flow_unit(concept: str) -> str:
        return "USD/shares" if concept == "eps_diluted" else "USD"

    def add(spec: dict[str, Any]) -> None:
        if rng.random() < 0.04:  # a fact in a unit no concept uses
            spec = {**spec, "unit": "EUR"}
        specs.append(spec)

    used = {c: [t for t in tags if rng.random() < 0.7] or tags[:1] for c, tags in FLOW_TAGS.items()}
    used_stock = [t for t in STOCK_TAGS if rng.random() < 0.65]
    history: list[tuple[date, date, dict[str, float]]] = []  # past quarters, for comparatives
    for _year in range(n_years):
        year_start = start
        quarter_ends: list[date] = []
        values: list[dict[str, float]] = []
        for q in range(4):
            length = int(rng.choice([91, 91, 91, 98, 84, 90, 92, 80, 100, 105]))
            end = start + timedelta(days=length - 1)
            vals = {c: float(np.round(rng.normal(100, 30), 2)) for c in FLOW_TAGS}
            filed = end + timedelta(days=int(rng.integers(20, 50)))
            # two filings in a row sometimes become public at the same moment (a tie)
            stamp = datetime.combine(filed, time(21 if rng.random() < 0.8 else 13), tzinfo=UTC)
            filing += 1
            accn = f"0000000001-{filing:02d}-{int(rng.integers(1, 5)):06d}"
            base = {"filed": filed, "available_at": stamp, "accn": accn}
            for concept, tags in used.items():
                unit = flow_unit(concept)
                for taxonomy, tag in tags:
                    v = vals[concept] * float(rng.choice([1.0, 1.0, 0.5, 1.01]))
                    common = {**base, "tag": tag, "taxonomy": taxonomy, "unit": unit}
                    kind = rng.random()
                    if q < 3:
                        if kind < 0.55:  # the discrete quarter
                            add({**common, "value": v, "start": start, "end": end})
                        if kind < 0.08:  # and a second one from the same start, a week shorter
                            short = end - timedelta(days=7)
                            add({**common, "value": v * 0.9, "start": start, "end": short})
                        if 0.35 < kind < 0.9 and q > 0:  # the year-to-date total
                            ytd = sum(x[concept] for x in values) + v
                            add({**common, "value": ytd, "start": year_start, "end": end})
                        if kind > 0.97 and q > 0:  # a stray period that tiles nothing
                            add(
                                {
                                    **common,
                                    "value": v,
                                    "start": end - timedelta(days=29),
                                    "end": end,
                                }
                            )
                    else:
                        fy = sum(x[concept] for x in values) + v
                        if kind < 0.85:
                            add(
                                {
                                    **common,
                                    "value": fy,
                                    "start": year_start,
                                    "end": end,
                                    "form": "10-K",
                                }
                            )
                        if kind < 0.12:  # a second fiscal year from the same start, a week shorter
                            short = end - timedelta(days=7)
                            add({**common, "value": fy * 0.98, "start": year_start, "end": short})
                        if kind > 0.6:
                            add({**common, "value": v, "start": start, "end": end, "form": "10-K"})
                    if history and rng.random() < 0.3:  # a comparative, sometimes restated
                        h_start, h_end, h_vals = history[int(rng.integers(0, len(history)))]
                        restated = h_vals[concept] * float(rng.choice([1.0, 1.0, 1.1]))
                        add({**common, "value": restated, "start": h_start, "end": h_end})
            for taxonomy, tag, unit in used_stock:
                if rng.random() < 0.85:
                    v = float(np.round(rng.uniform(10, 1000), 2))
                    common = {**base, "tag": tag, "taxonomy": taxonomy, "unit": unit}
                    add({**common, "value": v, "end": end})
                    if history and rng.random() < 0.2:  # last period's balance again
                        add({**common, "value": v * 0.9, "end": history[-1][1]})
            history.append((start, end, vals))
            values.append(vals)
            quarter_ends.append(end)
            start = end + timedelta(days=1 if rng.random() < 0.95 else 3)  # a rare gap
    facts = make_facts(*specs)
    return facts.drop_duplicates(
        subset=["taxonomy", "tag", "unit", "period_end", "duration_days", "accn"], keep="last"
    ).reset_index(drop=True)


def _cutoffs(facts: pd.DataFrame) -> list[AsOf]:
    stamps = sorted(set(facts["available_at"]))
    picks = [stamps[len(stamps) // 3], stamps[(2 * len(stamps)) // 3], stamps[-1]]
    out = []
    for stamp in picks:
        cutoff = pd.Timestamp(stamp) + pd.Timedelta(hours=1)
        day = cutoff.tz_convert("America/New_York").date()
        out.append(AsOf(day=day, cutoff=cutoff.to_pydatetime()))
    return out


def _same_frame(got: pd.DataFrame, want: pd.DataFrame, what: str) -> None:
    assert list(got.columns) == list(want.columns), what
    assert list(got.dtypes) == list(want.dtypes), f"{what}: {got.dtypes} vs {want.dtypes}"
    pd.testing.assert_frame_equal(got, want, check_exact=True, obj=what)


def _same_metric(got: object, want: object, what: str) -> None:
    if want is None or got is None:
        assert got is want, what
        return
    assert got == want, f"{what}: {got} != {want}"
    assert math.copysign(1.0, got.value) == math.copysign(1.0, want.value)  # type: ignore[attr-defined]
    assert type(got.period_end) is type(want.period_end)  # type: ignore[attr-defined]
    assert got.available_at.utcoffset() == want.available_at.utcoffset()  # type: ignore[attr-defined]


def check_company(facts: pd.DataFrame, label: str) -> int:
    """Compare every function on `facts` at three cut-offs and on the raw facts; returns the
    number of non-empty resolved concepts (so a test can tell it compared something)."""
    seen = 0
    frames = [(f"{label} raw", facts)]
    frames += [(f"{label} @ {p.cutoff}", known_facts(facts, p)) for p in _cutoffs(facts)]
    for where, known in frames:
        for concept in CMAP.values():
            what = f"{where} {concept.name}"
            want = ref.resolve_concept(known, concept)
            got = resolve_concept(known, concept)
            _same_frame(got, want, f"resolve {what}")
            seen += int(not want.empty)
            if concept.kind == "flow":
                want_q = ref.quarterly_values(want, CFG)
                _same_frame(quarterly_values(got, CFG), want_q, f"quarters {what}")
                _same_frame(ttm_values(got, CFG), ref.ttm_values(want, CFG), f"ttm {what}")
                _same_frame(
                    ttm_values(got, CFG, quarters=want_q),
                    ref.ttm_values(want, CFG, quarters=want_q),
                    f"ttm with quarters {what}",
                )
                _same_metric(ttm(got, CFG), ref.ttm(want, CFG), f"ttm value {what}")
            else:
                _same_metric(latest_balance(got), ref.latest_balance(want), f"balance {what}")
        want_values = ref.concept_values(known, CMAP, CFG)
        got_values = concept_values(known, CMAP, CFG)
        assert list(got_values) == list(want_values)
        for name, want_value in want_values.items():
            _same_metric(got_values[name], want_value, f"{where} value {name}")
        for names in (list(CMAP), ["revenue", "gross_profit", "eps_diluted", "shares_outstanding"]):
            _same_frame(
                concept_history(known, CMAP, names, CFG),
                ref.concept_history(known, CMAP, names, CFG),
                f"{where} history {len(names)}",
            )
    return seen


UNIT_SEEDS = (0, 1, 2, 3)
"""Seeds compared in the unit suite; `tests/integration/test_fundamentals_reference_sweep.py`
compares many more and the other recorded companies."""


@pytest.mark.parametrize("seed", UNIT_SEEDS)
def test_random_company_matches_the_pandas_reference(seed: int) -> None:
    assert check_company(random_company(seed), f"seed {seed}") > 20


def test_recorded_company_matches_the_pandas_reference() -> None:
    assert check_company(fixture_facts(AAPL, "AAPL"), "AAPL") > 30


def _flow(start: date, end: date, value: float, accn: str, stamp: datetime) -> dict[str, Any]:
    return {"tag": "Revenues", "value": value, "start": start, "end": end, "accn": accn,
            "filed": stamp.date(), "available_at": stamp}  # fmt: skip


def crafted_ties_and_gaps() -> pd.DataFrame:
    """Cases the random companies rarely produce: inputs of a derived value that became public
    at the same moment in different filings (which filing is cited?), and three quarters that
    do not tile the fiscal year."""
    t0 = datetime(2020, 8, 1, 21, tzinfo=UTC)
    t1, t2, t3 = (t0 + timedelta(days=365 * k) for k in (1, 2, 3))
    return make_facts(
        # 2020: Q2 = H1 - Q1, both public at t0 in different filings
        _flow(date(2020, 1, 1), date(2020, 3, 31), 10.0, "A-2", t0),
        _flow(date(2020, 1, 1), date(2020, 6, 30), 25.0, "A-1", t0),
        # 2021: three 100-day quarters leave 65 days: no Q4 from the fiscal year
        _flow(date(2021, 1, 1), date(2021, 4, 10), 11.0, "B-1", t1),
        _flow(date(2021, 4, 11), date(2021, 7, 19), 12.0, "B-2", t1),
        _flow(date(2021, 7, 20), date(2021, 10, 27), 13.0, "B-3", t1),
        _flow(date(2021, 1, 1), date(2021, 12, 31), 50.0, "B-4", t1 + timedelta(days=150)),
        # 2022: four reported quarters, all public at t2 in different filings, no fiscal year
        _flow(date(2022, 1, 1), date(2022, 3, 31), 20.0, "C-4", t2),
        _flow(date(2022, 4, 1), date(2022, 6, 30), 21.0, "C-3", t2),
        _flow(date(2022, 7, 1), date(2022, 9, 30), 22.0, "C-2", t2),
        _flow(date(2022, 10, 1), date(2022, 12, 31), 23.0, "C-1", t2),
        # 2023: Q4 = FY - Q1..Q3, all public at t3 in different filings
        _flow(date(2023, 1, 1), date(2023, 3, 31), 30.0, "D-1", t3),
        _flow(date(2023, 4, 1), date(2023, 6, 30), 31.0, "D-2", t3),
        _flow(date(2023, 7, 1), date(2023, 9, 30), 32.0, "D-3", t3),
        _flow(date(2023, 1, 1), date(2023, 12, 31), 130.0, "D-0", t3),
    )


def test_crafted_ties_and_gaps_match_the_pandas_reference() -> None:
    facts = crafted_ties_and_gaps()
    assert check_company(facts, "crafted") > 0
    quarters = quarterly_values(resolve_concept(facts, CMAP["revenue"]), CFG)
    by_end = quarters.set_index("period_end")
    q2 = by_end.loc[date(2020, 6, 30)]
    assert q2["value"] == 15.0 and q2["source"] == "derived" and q2["accn"] == "A-1"
    assert date(2021, 12, 31) not in by_end.index  # 65 days left: not a quarter
    q4 = by_end.loc[date(2023, 12, 31)]
    assert q4["value"] == 37.0 and q4["source"] == "derived" and q4["accn"] == "D-0"
    totals = ttm_values(resolve_concept(facts, CMAP["revenue"]), CFG).set_index("period_end")
    year = totals.loc[date(2022, 12, 31)]
    assert year["value"] == 86.0 and year["source"] == "quarters" and year["accn"] == "C-4"
    assert totals.loc[date(2023, 12, 31), "source"] == "annual"


def test_empty_and_degenerate_inputs_match_the_pandas_reference() -> None:
    facts = random_company(3)
    empty = facts.iloc[0:0]
    one = facts.iloc[[0]]
    instants_only = facts[facts["duration_days"] == 0]
    flows_only = facts[facts["duration_days"] > 0]
    for label, frame in (
        ("empty", empty),
        ("one row", one),
        ("instants", instants_only),
        ("flows", flows_only),
    ):
        for concept in CMAP.values():
            want = ref.resolve_concept(frame, concept)
            got = resolve_concept(frame, concept)
            _same_frame(got, want, f"{label} {concept.name}")
            if concept.kind == "flow":
                _same_frame(quarterly_values(got, CFG), ref.quarterly_values(want, CFG), label)
                _same_frame(ttm_values(got, CFG), ref.ttm_values(want, CFG), label)
        assert concept_values(frame, CMAP, CFG) == ref.concept_values(frame, CMAP, CFG)
        _same_frame(
            concept_history(frame, CMAP, list(CMAP), CFG),
            ref.concept_history(frame, CMAP, list(CMAP), CFG),
            f"{label} history",
        )


def paths_exercised(seeds: Sequence[int]) -> tuple[set[str], set[str], set[str]]:
    """(quarter sources, TTM sources, tags) the random companies of `seeds` produce."""
    sources: set[str] = set()
    ttm_sources: set[str] = set()
    tags: set[str] = set()
    for seed in seeds:
        facts = random_company(seed)
        for concept in CMAP.values():
            periods = resolve_concept(facts, concept)
            tags |= set(periods["tag"])
            if concept.kind == "flow":
                sources |= set(quarterly_values(periods, CFG)["source"])
                ttm_sources |= set(ttm_values(periods, CFG)["source"])
    return sources, ttm_sources, tags


def test_the_random_companies_exercise_every_path() -> None:
    """The generator is only a useful oracle if it produces derived quarters, both TTM
    sources, strict sums and part sums."""
    sources, ttm_sources, tags = paths_exercised(UNIT_SEEDS)
    assert sources == {"reported", "derived"}
    assert ttm_sources == {"annual", "quarters"}
    assert any(" + " in t for t in tags) and any("; " in t for t in tags)
