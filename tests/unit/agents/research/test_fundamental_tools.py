"""Stage 29 Task 3: the four tools on the fundamental test world, against hand computation."""

import math
from datetime import UTC, date, datetime, time

import numpy as np
import pytest
from fundamental_world import FLAGGED, QUARTERS, FundamentalWorld
from scipy.stats import percentileofscore

from atlas.agents.evidence import evidence_tag
from atlas.agents.tools import fundamental_tools as ft
from atlas.core.errors import DataError
from atlas.core.types.research import Evidence
from atlas.quant.factors import factor_panel, style_composites

ENDS = [end for _, end in QUARTERS]


def by_id(items: list[Evidence]) -> dict[str, Evidence]:
    return {e.id: e for e in items}


def revenue(world: FundamentalWorld, symbol: str, k: int) -> float:
    c = world.company(symbol)
    return c.revenue0 * (1 + c.growth) ** k


def ttm(values: list[float], k: int) -> float:
    return sum(values[k - 3 : k + 1])


# --- formatting -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "text"),
    [
        (90.754e9, "USD 90.75B"),
        (2.6181e12, "USD 2.618T"),
        (-1e8, "USD -100M"),
        (1234.0, "USD 1.234K"),
        (12.5, "USD 12.5"),
        (math.nan, "n/a"),
    ],
)
def test_usd_format(x: float, text: str) -> None:
    assert ft.fmt_usd(x) == text


def test_percent_and_number_formats() -> None:
    assert ft.fmt_pct(0.4561) == "45.6%"
    assert ft.fmt_pct(-0.009, signed=True) == "-0.9%"
    assert ft.fmt_pct(0.05, signed=True) == "+5.0%"
    assert ft.fmt_pct(math.inf) == "n/m"
    assert ft.fmt_num(26.149) == "26.1"
    assert ft.fmt_num(math.nan) == "n/m"


def test_mid_rank_percentile() -> None:
    import pandas as pd

    values = pd.Series({"A": 1.0, "B": 2.0, "C": 2.0, "D": 4.0, "E": math.nan})
    assert ft.percentile(values, "A", 1) == (12.5, 4)
    assert ft.percentile(values, "B", 1) == (50.0, 4)
    assert ft.percentile(values, "D", 1) == (87.5, 4)
    assert ft.percentile(values, "E", 1) == (None, 4)  # no value
    assert ft.percentile(values, "A", 5) == (None, 4)  # too few values
    for symbol in "ABCD":
        expected = percentileofscore(values.dropna(), values[symbol], kind="mean")
        assert ft.percentile(values, symbol, 1)[0] == pytest.approx(expected)


# --- get_fundamentals_summary -----------------------------------------------------------------


def test_summary_shows_the_last_eight_quarters_and_the_ttm(fworld: FundamentalWorld) -> None:
    items = by_id(ft.fundamentals_summary(fworld.view(), "AAA"))
    item = items["fsum:AAA:2023-12-31:revenue"]
    revs = [revenue(fworld, "AAA", k) for k in range(16)]
    for k in range(8, 16):
        assert f"{ENDS[k]} {ft.fmt_usd(revs[k])}" in item.text
    assert str(ENDS[7]) not in item.text  # only the last eight
    assert f"TTM to 2023-12-31: {ft.fmt_usd(ttm(revs, 15))}" in item.text
    assert item.as_of == datetime.combine(date(2024, 2, 5), time(21), tzinfo=UTC)  # Q4 filing
    assert item.source == "sec_edgar"


def test_summary_items_and_ids(fworld: FundamentalWorld) -> None:
    items = ft.fundamentals_summary(fworld.view(), "AAA")
    ids = [e.id for e in items]
    assert ids == [
        "fsum:AAA:2023-12-31:revenue",
        "fsum:AAA:2023-12-31:gross_profit",
        "fsum:AAA:2023-12-31:operating_income",
        "fsum:AAA:2023-12-31:net_income",
        "fsum:AAA:2023-12-31:eps_diluted",
        "fsum:AAA:2023-12-31:operating_cash_flow",
        "fsum:AAA:2023-12-31:capex",
        "fsum:AAA:2023-12-31:total_assets",
        "fsum:AAA:2023-12-31:accounts_receivable",
        "fsum:AAA:2023-12-31:cash_and_equivalents",
        "fsum:AAA:2023-12-31:total_debt",
        "fsum:AAA:2023-12-31:stockholders_equity",
        "fsum:AAA:2023-12-31:margins",
        "fsum:AAA:2023-12-31:growth",
    ]
    for item in items:
        evidence_tag(item.id)  # every id can be cited
    assert ft.fundamentals_summary(fworld.view(), "AAA") == items  # stable


def test_summary_margins_and_growth_match_hand_computation(fworld: FundamentalWorld) -> None:
    c = fworld.company("AAA")
    items = by_id(ft.fundamentals_summary(fworld.view(), "AAA"))
    revs = [revenue(fworld, "AAA", k) for k in range(16)]
    i = list(fworld.companies).index(c)
    gross = [revs[k] * c.gross_margin * (1 + 0.02 * np.sin(k + i)) for k in range(16)]
    margins = items["fsum:AAA:2023-12-31:margins"].text
    assert f"gross {ft.fmt_pct(ttm(gross, 15) / ttm(revs, 15))}" in margins
    assert f"operating {ft.fmt_pct(c.op_margin)}" in margins
    assert f"net {ft.fmt_pct(c.net_margin)}" in margins
    assert f"free cash flow {ft.fmt_pct(c.ocf_margin - c.capex_ratio)}" in margins
    assert "TTM to 2022-12-31:" in margins  # the year-earlier margins
    growth = items["fsum:AAA:2023-12-31:growth"].text
    rev_growth = ttm(revs, 15) / ttm(revs, 11) - 1
    assert f"revenue {ft.fmt_pct(rev_growth, signed=True)}" in growth
    cagr = (ttm(revs, 15) / ttm(revs, 3)) ** (1 / 3) - 1
    assert f"(vs TTM to 2020-12-31): {ft.fmt_pct(cagr, signed=True)} a year" in growth


def test_summary_of_a_blocked_or_unknown_symbol_is_a_data_error(
    world: object, fworld: FundamentalWorld
) -> None:
    with pytest.raises(DataError, match="blocked"):
        ft.fundamentals_summary(world.view(), "BAD")  # type: ignore[attr-defined]
    with pytest.raises(DataError, match="no fundamentals known for ZZZ"):
        ft.fundamentals_summary(fworld.view(), "ZZZ")


# --- get_factor_ranks -------------------------------------------------------------------------


def test_ranks_are_mid_rank_percentiles_within_the_sector(fworld: FundamentalWorld) -> None:
    view = fworld.view()
    items = by_id(ft.factor_ranks(view, "BBB"))
    assert list(items) == [
        f"frank:BBB:2024-03-01:{g}" for g in ("value", "quality", "growth", "leverage")
    ]
    peers = ["AAA", "BBB", "CCC", "DDD"]  # the Information Technology members
    panel = factor_panel(view, peers)
    composites = style_composites(panel.exposures)
    for group in ("value", "quality", "growth", "leverage"):
        expected = percentileofscore(
            composites[group].dropna(), composites.at["BBB", group], kind="mean"
        )
        text = items[f"frank:BBB:2024-03-01:{group}"].text
        assert f"style composite at percentile {expected:.0f} of 4 peers" in text
        assert "Information Technology peers (sp500 members on 2024-03-01)" in text
    ey = panel.raw["earnings_yield"]
    pct = percentileofscore(ey.dropna(), ey["BBB"], kind="mean")
    assert (
        f"earnings_yield {ft.fmt_pct(ey['BBB'])} (percentile {pct:.0f} of 4 peers;"
        in items["frank:BBB:2024-03-01:value"].text
    )


def test_too_few_peers_are_not_ranked(fworld: FundamentalWorld) -> None:
    from atlas.core.config import AtlasConfig

    strict = AtlasConfig.model_validate(
        {"quant": {"factors": {"min_names": 3}}, "agents": {"fundamental": {"min_sector_names": 5}}}
    )
    text = ft.factor_ranks(fworld.view(config=strict), "BBB")[0].text
    assert "style composite at not ranked (4 peers with a value)" in text


def test_a_symbol_without_a_sector_cannot_be_ranked(fworld: FundamentalWorld) -> None:
    with pytest.raises(DataError, match="no GICS sector"):
        ft.factor_ranks(fworld.view(), "LLL")


# --- get_valuation ----------------------------------------------------------------------------


def _close(view: object, symbol: str, day: date) -> float:
    bars = view._shared.lake.read_table("prices_daily", filters=[("symbol", "=", symbol)])  # type: ignore[attr-defined]
    known = bars[bars["date"] <= day]
    return float(known["close"].iloc[-1])


def _cover_count(fworld: FundamentalWorld, symbol: str, end: date) -> float:
    facts = fworld.clean.read_table("fundamentals_facts", filters=[("symbol", "=", symbol)])
    counts = facts[facts["tag"] == "EntityCommonStockSharesOutstanding"]
    nearest = min(counts["period_end"], key=lambda d: abs((d - end).days))
    return float(counts.loc[counts["period_end"] == nearest, "value"].iloc[0])


def test_own_history_pe_uses_raw_prices_and_counts_so_a_split_cancels(
    fworld: FundamentalWorld,
) -> None:
    """CCC splits 2:1 on 2024-02-12: its quarter-end market caps are raw close x raw count."""
    view = fworld.view()
    c = fworld.company("CCC")
    pes = []
    for k, end in enumerate(ENDS):
        if k < 3 or end < date(2021, 9, 1):
            continue
        before = [
            d
            for d in fworld.clean.read_table("prices_daily", filters=[("symbol", "=", "CCC")])[
                "date"
            ]
            if d <= end
        ]
        if not before or (end - max(before)).days > 10:
            continue
        cap = _close(view, "CCC", end) * _cover_count(fworld, "CCC", end)
        net_income = sum(revenue(fworld, "CCC", j) * c.net_margin for j in range(k - 3, k + 1))
        pes.append(cap / net_income)
    text = by_id(ft.valuation(view, "CCC"))["val:CCC:2024-03-01:pe"].text
    assert f"own history at {len(pes)} fiscal quarter ends" in text
    assert f"median {ft.fmt_num(float(np.median(pes)))}" in text
    assert f"low {ft.fmt_num(min(pes))}, high {ft.fmt_num(max(pes))}" in text


def test_current_multiples_and_sector_medians(fworld: FundamentalWorld) -> None:
    view = fworld.view()
    peers = ["AAA", "BBB", "CCC", "DDD"]
    panel = factor_panel(view, peers)
    fund = view.fundamentals_panel(peers)
    pe = {s: panel.market.at[s, "market_cap"] / fund.at[s, "net_income_ttm"] for s in peers}
    items = by_id(ft.valuation(view, "BBB"))
    text = items["val:BBB:2024-03-01:pe"].text
    assert text.startswith(f"BBB P/E (market cap / TTM net income): now {ft.fmt_num(pe['BBB'])};")
    assert (
        f"Information Technology median {ft.fmt_num(float(np.median(list(pe.values()))))} "
        "(4 peers with a value)" in text
    )
    market = items["val:BBB:2024-03-01:market"].text
    assert ft.fmt_usd(panel.market.at["BBB", "market_cap"]) in market
    assert [i.split(":")[-1] for i in items] == [
        "market",
        "pe",
        "ps",
        "pb",
        "ev_ebitda",
        "fcf_yield",
    ]


def test_a_negative_denominator_is_not_meaningful(fworld: FundamentalWorld) -> None:
    assert math.isnan(
        ft.multiples(1e9, 1e9, net_income=-1.0, revenue=1.0, equity=0.0, ebitda=-5.0, fcf=-1e8)[
            "pe"
        ]
    )
    out = ft.multiples(1e9, 1.2e9, net_income=5e7, revenue=2e8, equity=0.0, ebitda=-5.0, fcf=-1e8)
    assert out["pe"] == 20.0 and out["ps"] == 5.0
    assert math.isnan(out["pb"]) and math.isnan(out["ev_ebitda"])
    assert out["fcf_yield"] == -0.1  # a negative yield is meaningful


# --- get_red_flags ----------------------------------------------------------------------------


def test_the_flagged_filer_trips_four_checks(fworld: FundamentalWorld) -> None:
    items = ft.red_flags(fworld.view(), FLAGGED)
    status = {e.id.split(":")[-1]: e.text.split(": ")[1].split(".")[0] for e in items}
    assert status == {
        "accruals": "FLAG",
        "receivables": "FLAG",
        "fcf_debt": "FLAG",
        "dilution": "FLAG",
        "going_concern": "NOT EVALUATED",
    }
    assert [e.id for e in items] == [f"rflag:NNN:2024-03-01:{c}" for c in ft.RED_FLAG_CHECKS]


def test_a_healthy_filer_is_clear(fworld: FundamentalWorld) -> None:
    items = by_id(ft.red_flags(fworld.view(), "DDD"))  # DDD: no share drift, positive cash flow
    assert "dilution (share count growth): CLEAR" in items["rflag:DDD:2024-03-01:dilution"].text
    assert ": CLEAR." in items["rflag:DDD:2024-03-01:fcf_debt"].text
    assert ": CLEAR." in items["rflag:DDD:2024-03-01:receivables"].text  # receivables track revenue


def test_red_flag_numbers_match_hand_computation(fworld: FundamentalWorld) -> None:
    c = fworld.company(FLAGGED)
    revs = [c.revenue0 * (1 + c.growth) ** k for k in range(16)]
    assets = c.assets * (1 + c.growth / 4) ** 15
    ratio = (ttm(revs, 15) * (c.net_margin - c.ocf_margin)) / assets
    text = by_id(ft.red_flags(fworld.view(), FLAGGED))["rflag:NNN:2024-03-01:accruals"].text
    assert f"= {ft.fmt_pct(ratio)}" in text
    assert "(TTM / balances at 2023-12-31 vs 2022-12-31)" in text
    dilution = by_id(ft.red_flags(fworld.view(), FLAGGED))["rflag:NNN:2024-03-01:dilution"].text
    assert f"{ft.fmt_pct((1 + c.share_drift) ** 4 - 1, signed=True)} year on year" in dilution


# --- the registry and prefetch -----------------------------------------------------------------


def test_the_registry_serves_the_four_tools(fworld: FundamentalWorld) -> None:
    from atlas.agents.context import AgentContext
    from atlas.agents.research.fundamental import fundamental_registry
    from atlas.llm.schemas import ToolCall

    registry = fundamental_registry()
    assert {t.name for t in ft.FUNDAMENTAL_TOOLS} <= set(registry.names)
    ctx = AgentContext(fworld.view())
    out = registry.call(ctx, ToolCall(id="c1", name="get_valuation", arguments={"symbol": "ccc"}))
    assert out.error is None and out.evidence[0].id == "val:CCC:2024-03-01:market"
    assert "[E:val:CCC:2024-03-01:pe]" in out.text
    bad = registry.call(ctx, ToolCall(id="c2", name="get_red_flags", arguments={"symbol": ""}))
    assert bad.error is not None and bad.text.startswith("ERROR: invalid arguments")
    nosector = registry.call(
        ctx, ToolCall(id="c3", name="get_factor_ranks", arguments={"symbol": "LLL"})
    )
    assert nosector.error is not None and "no GICS sector" in nosector.error


def test_prefetch_turns_a_secondary_failure_into_a_gap(fworld: FundamentalWorld) -> None:
    items = ft.prefetch(fworld.view(), "LLL")
    gaps = [e for e in items if e.id.startswith("gap:")]
    assert [g.id for g in gaps] == [
        "gap:LLL:2024-03-01:get_factor_ranks",
        "gap:LLL:2024-03-01:get_valuation",
    ]
    assert "no GICS sector" in gaps[0].text
    assert {g.source for g in gaps} == {"atlas"}
    assert any(e.id.startswith("rflag:LLL") for e in items)


def test_prefetch_equals_the_four_tools(fworld: FundamentalWorld) -> None:
    view = fworld.view()
    joined = [
        *ft.fundamentals_summary(view, "AAA"),
        *ft.factor_ranks(view, "AAA"),
        *ft.valuation(view, "AAA"),
        *ft.red_flags(view, "AAA"),
    ]
    assert ft.prefetch(view, "AAA") == joined
