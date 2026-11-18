"""Stage 29 follow-up (audit A9, mutation testing): contracts of the fundamental tools and the
analyst that the first test set left unpinned: evidence sources and point-in-time stamps,
staleness and threshold boundaries, a single tool failing inside `prefetch`, the exact
NOT EVALUATED reasons, the repair-problem texts, the memory query and a caller's registry."""

import math
import re
from datetime import date, timedelta
from typing import Any

import pandas as pd
import pytest
from fundamental_world import AS_OF, CONFIG, FundamentalWorld

from atlas.agents.context import AgentContext
from atlas.agents.research import FundamentalAnalyst, fundamental_registry
from atlas.agents.research.fundamental import MEMORY_QUERY, SECTIONS, SIGNALS, sentences
from atlas.agents.tools import fundamental_tools as ft
from atlas.agents.trace import TraceStore
from atlas.core.config import AtlasConfig, RedFlagsConfig
from atlas.core.errors import ConfigError, DataError
from atlas.core.types.research import AnalystReport, Direction, Signal
from atlas.llm import FakeLLM, LLMGateway

DAY = AS_OF.isoformat()
RF = RedFlagsConfig()
NAN = math.nan


def config(**fundamental: Any) -> AtlasConfig:
    """`CONFIG` with `agents.fundamental` overrides; `max_staleness_days` goes to
    `data.fundamentals`."""
    staleness = fundamental.pop("max_staleness_days", None)
    raw: dict[str, Any] = {
        "quant": {"factors": {"min_names": 3}},
        "agents": {"fundamental": {"min_sector_names": 3, **fundamental}},
    }
    if staleness is not None:
        raw["data"] = {"fundamentals": {"max_staleness_days": staleness}}
    return AtlasConfig.model_validate(raw)


def latest_end(fworld: FundamentalWorld, symbol: str = "AAA") -> date:
    history = ft._history(fworld.view(), symbol)
    return history.of("revenue", "ttm")["period_end"].iloc[-1]  # type: ignore[no-any-return]


# --- evidence sources and point-in-time stamps --------------------------------------------------


def test_every_item_names_its_source(fworld: FundamentalWorld) -> None:
    sources = {"fsum": "sec_edgar", "frank": "factors", "val": "valuation", "rflag": "red_flags"}
    items = ft.prefetch(fworld.view(), "AAA")
    assert {e.id.split(":")[0] for e in items} == set(sources)
    assert all(e.source == sources[e.id.split(":")[0]] for e in items)
    gaps = [e for e in ft.prefetch(fworld.view(), "LLL") if e.id.startswith("gap:")]
    assert gaps and {g.source for g in gaps} == {"atlas"}


@pytest.mark.parametrize(
    "concept",
    [
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "eps_diluted",
        "operating_cash_flow",
        "capex",
    ],
)
@pytest.mark.parametrize("which", ["latest", "year_ago", "three_ago"])
def test_margins_and_growth_are_stamped_when_their_last_input_became_known(
    fworld: FundamentalWorld, concept: str, which: str
) -> None:
    """A TTM input known later than the others (a restatement, say) moves the stamp of the
    margins and growth items to when it became known; an input they do not read does not."""
    view = fworld.view()
    history = ft._history(view, "AAA")
    base = {e.id.rsplit(":", 1)[1]: e.as_of for e in ft._derived_items("AAA", history, view)}
    assert base["margins"] == base["growth"]
    ends = list(history.of("revenue", "ttm")["period_end"])
    end = ends[-1]
    target = {
        "latest": end,
        "year_ago": ft._nearest_end(ends, end - timedelta(days=365), ft._tolerance(view)),
        "three_ago": ft._nearest_end(ends, end - timedelta(days=3 * 365), ft._tolerance(view)),
    }[which]
    later = base["margins"] + timedelta(days=3)
    assert later < view.as_of.cutoff

    def restated(name: str) -> ft._History:
        rows = history.rows.copy()
        hit = (rows["concept"] == name) & (rows["period_end"] == target)
        assert hit.sum() == 1
        rows.loc[hit, "available_at"] = pd.Timestamp(later)
        return ft._History(rows=rows, day=history.day)

    moved = ft._derived_items("AAA", restated(concept), view)
    assert [e.as_of for e in moved] == [later, later]
    untouched = ft._derived_items("AAA", restated("total_assets"), view)
    assert [e.as_of for e in untouched] == [base["margins"]] * 2


def test_free_cash_flow_growth_is_signed_and_tracks_revenue(fworld: FundamentalWorld) -> None:
    """BBB's margins are constant, so its free cash flow grows exactly as its revenue."""
    items = ft.fundamentals_summary(fworld.view(), "BBB")
    growth = next(e for e in items if e.id.endswith("growth"))
    revenue = growth.text.split("revenue ", 1)[1].split(",", 1)[0]
    assert revenue.startswith("+") and revenue.endswith("%")
    assert f"free cash flow {revenue};" in growth.text and "n/m" not in growth.text


def test_without_a_year_earlier_the_items_say_so(fworld: FundamentalWorld) -> None:
    view = fworld.view()
    history = ft._history(view, "AAA")
    end = history.of("revenue", "ttm")["period_end"].iloc[-1]
    rows = history.rows
    recent = rows[(rows["period_end"] > end - timedelta(days=300)) | rows["ttm"].isna()]
    margins, growth = ft._derived_items("AAA", ft._History(rows=recent, day=history.day), view)
    assert margins.text.endswith("; a year earlier: not available.")
    assert growth.text == f"AAA TTM growth to {end}: no TTM a year earlier."


# --- staleness and thresholds -------------------------------------------------------------------


def test_staleness_is_more_than_max_staleness_days(fworld: FundamentalWorld) -> None:
    end = latest_end(fworld)
    days = (AS_OF - end).days
    fresh = fworld.view(config=config(max_staleness_days=days))
    stale = fworld.view(config=config(max_staleness_days=days - 1))

    def summary_text(view: Any) -> str:
        return " ".join(e.text for e in ft.fundamentals_summary(view, "AAA"))

    assert "stale" not in summary_text(fresh)
    assert f"The latest period ended {end}, {days} days before {AS_OF}: stale." in (
        summary_text(stale)
    )
    assert [e.text.split(": ", 1)[1].split(".")[0] for e in ft.red_flags(fresh, "AAA")][:4] == [
        "CLEAR"
    ] * 4
    why = f"the latest period ended {end}, more than {days - 1} days before {AS_OF}"
    texts = [e.text for e in ft.red_flags(stale, "AAA")]
    for check, text in zip(ft.RED_FLAG_CHECKS[:4], texts[:4], strict=True):
        assert text == (
            f"AAA red-flag check {check} ({ft._CHECK_TEXT[check]}): NOT EVALUATED. "
            f"stale or missing filings ({why})."
        )
    assert texts[4].startswith("AAA red-flag check going_concern")


def test_dilution_on_its_own_share_count(fworld: FundamentalWorld) -> None:
    view = fworld.view()
    history = ft._history(view, "AAA")
    tol = ft._tolerance(view)
    rows = history.rows
    shares = rows[rows["concept"] == "shares_outstanding"]
    latest = shares["period_end"].max()
    days = (AS_OF - latest).days

    def dilution(frame: pd.DataFrame, staleness: int) -> tuple[ft.RedFlag, str]:
        return ft._dilution(
            view, "AAA", ft._History(rows=frame, day=history.day), RF, tol, staleness
        )

    flag, periods = dilution(rows, days)
    assert flag.status == "CLEAR" and periods.startswith(f"counts dated {latest} vs ")
    assert dilution(rows, days - 1) == (
        ft.RedFlag("dilution", "NOT EVALUATED", f"the latest share count is dated {latest}: stale"),
        "",
    )
    no_counts = rows[rows["concept"] != "shares_outstanding"]
    assert dilution(no_counts, days) == (
        ft.RedFlag("dilution", "NOT EVALUATED", "share count a year earlier missing"),
        "",
    )
    one_count = rows[(rows["concept"] != "shares_outstanding") | (rows["period_end"] == latest)]
    assert dilution(one_count, days) == (
        ft.RedFlag("dilution", "NOT EVALUATED", "share count a year earlier missing"),
        f"count dated {latest}",
    )


def test_a_split_after_both_counts_leaves_the_dilution_as_reported(
    fworld: FundamentalWorld,
) -> None:
    """CCC splits after its last count, so both counts are pre-split: the split-adjusted
    growth equals the reported one."""
    view = fworld.view()
    history = ft._history(view, "CCC")
    counts = history.of("shares_outstanding")
    latest = counts["period_end"].iloc[-1]
    ends = list(counts["period_end"])
    before = ft._nearest_end(ends, latest - timedelta(days=365), ft._tolerance(view))
    value = dict(zip(counts["period_end"], counts["value"], strict=True))
    growth = value[latest] / value[before] - 1
    flag = dict(ft.red_flag_results(view, "CCC", history))
    (dilution,) = [f for f in flag if f.check == "dilution"]
    assert f"(split-adjusted) {ft.fmt_pct(growth, signed=True)} year on year" in dilution.detail


def test_own_history_and_peer_minimums_are_inclusive(fworld: FundamentalWorld) -> None:
    pe = next(e for e in ft.valuation(fworld.view(), "AAA") if e.id.endswith(":pe"))
    quarters = int(pe.text.split("own history at ", 1)[1].split(" ", 1)[0])
    peers = int(pe.text.split(" peers with a value)", 1)[0].rsplit("(", 1)[1])

    def pe_text(**fundamental: Any) -> str:
        view = fworld.view(config=config(**fundamental))
        return next(e.text for e in ft.valuation(view, "AAA") if e.id.endswith(":pe"))

    assert f"own history at {quarters} " in pe_text(min_valuation_quarters=quarters)
    short = pe_text(min_valuation_quarters=quarters + 1)
    assert short.startswith("AAA P/E (")
    assert f"; own history: {quarters} quarter(s) with a value, too few to compare;" in short
    assert f"({peers} peers with a value)" in pe_text(min_sector_names=peers)
    assert f"median: too few peers with a value ({peers})" in pe_text(min_sector_names=peers + 1)


# --- prefetch with one tool failing ---------------------------------------------------------


@pytest.mark.parametrize(
    ("attribute", "tool"),
    [("factor_ranks_from", "get_factor_ranks"), ("valuation_from", "get_valuation")],
)
def test_one_failing_tool_becomes_its_own_gap(
    fworld: FundamentalWorld, monkeypatch: pytest.MonkeyPatch, attribute: str, tool: str
) -> None:
    def boom(view: object, sector: object) -> list[object]:
        raise DataError("no prices")

    full = ft.prefetch(fworld.view(), "AAA")
    monkeypatch.setattr(ft, attribute, boom)
    items = ft.prefetch(fworld.view(), "AAA")
    gap = ft.gap_item(fworld.view(), "AAA", tool, DataError("no prices"))
    assert gap.text == f"{tool} could not be computed for AAA: no prices"
    prefix = {"get_factor_ranks": "frank:", "get_valuation": "val:"}[tool]
    kept = [e for e in full if not e.id.startswith(prefix)]
    position = next(i for i, e in enumerate(full) if e.id.startswith(prefix))
    assert items == [*kept[:position], gap, *kept[position:]]


def test_failing_red_flags_become_the_last_gap(
    fworld: FundamentalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    full = ft.prefetch(fworld.view(), "AAA")

    def boom(view: object, symbol: str) -> list[object]:
        raise DataError("no filings")

    monkeypatch.setattr(ft, "red_flags", boom)
    items = ft.prefetch(fworld.view(), "AAA")
    gap = ft.gap_item(fworld.view(), "AAA", "get_red_flags", DataError("no filings"))
    assert items == [*(e for e in full if not e.id.startswith("rflag:")), gap]
    assert gap.id == f"gap:AAA:{DAY}:get_red_flags"


def test_a_sector_failure_keeps_the_summary_and_names_the_reason(
    fworld: FundamentalWorld,
) -> None:
    items = ft.prefetch(fworld.view(), "LLL")
    assert items[: len(ft.fundamentals_summary(fworld.view(), "LLL"))] == (
        ft.fundamentals_summary(fworld.view(), "LLL")
    )
    gaps = [e for e in items if e.id.startswith("gap:")]
    assert all("no GICS sector" in g.text for g in gaps) and len(gaps) == 2


# --- the rules' exact details ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "detail"),
    [
        (ft.accruals_rule(NAN, 0.01, RF), "net income, operating cash flow or assets missing"),
        (ft.receivables_rule(NAN, 0.1, RF), "receivables or revenue a year earlier missing"),
        (ft.fcf_debt_rule(NAN, 0.1, RF), "TTM free cash flow missing"),
        (ft.fcf_debt_rule(1e8, 0.1, RF), "TTM free cash flow USD 100M is not negative"),
        (
            ft.fcf_debt_rule(-1e8, NAN, RF),
            "TTM free cash flow USD -100M is negative; total debt a year earlier missing",
        ),
        (
            ft.fcf_debt_rule(-1e8, math.inf, RF),
            "TTM free cash flow USD -100M with total debt new debt (none a year earlier) year on "
            "year (flag when debt grows more than +10.0%)",
        ),
        (ft.dilution_rule(NAN, RF), "share count a year earlier missing"),
        (
            ft.accruals_rule(0.02, NAN, RF),
            "accruals (TTM net income - TTM operating cash flow) / total assets = 2.0% (flag "
            "above 10.0%, or a rise of more than 5.0 points to a positive level)",
        ),
        (
            ft.going_concern_rule("Substantial doubt about the going concern.", RF),
            'MD&A mentions "going concern", "substantial doubt"',
        ),
        (
            ft.going_concern_rule("All is well.", RF),
            'MD&A mentions none of "going concern", "substantial doubt"',
        ),
    ],
)
def test_rule_details(flag: ft.RedFlag, detail: str) -> None:
    assert flag.detail == detail


def test_accruals_jump_needs_a_positive_level_and_more_than_the_jump() -> None:
    rf = RedFlagsConfig(accruals_max=0.9, accruals_jump=0.25)
    assert ft.accruals_rule(0.0, -0.5, rf).status == "CLEAR"  # not positive
    assert ft.accruals_rule(0.5, 0.25, rf).status == "CLEAR"  # rise equal to the jump
    assert ft.accruals_rule(0.5, 0.24, rf).status == "FLAG"


def test_debt_growth_edges() -> None:
    assert ft._debt_growth(0.5, 0.0) == math.inf  # debt from none
    assert ft._debt_growth(0.0, 0.0) == 0.0
    assert ft._debt_growth(2.0, 0.5) == 3.0
    assert math.isnan(ft._debt_growth(NAN, 1.0))


# --- helpers' edges ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "text"),
    [
        (1e12, "USD 1T"),
        (1e9, "USD 1B"),
        (1e6, "USD 1M"),
        (1e3, "USD 1K"),
        (0.0, "USD 0"),
        (0.5, "USD 0.5"),
        (-5.0, "USD -5"),
    ],
)
def test_fmt_usd_edges(x: float, text: str) -> None:
    assert ft.fmt_usd(x) == text


def test_not_finite_formats() -> None:
    assert ft.fmt_shares(NAN) == "n/a" and ft.fmt_points(NAN) == "n/m"
    assert ft._factor_value(NAN, "fraction_per_year") == "n/m"


def test_ratio_and_percentile_edges() -> None:
    assert ft._ratio(1.0, 0.5) == 2.0 and math.isnan(ft._ratio(1.0, 0.0))
    values = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": math.inf})
    assert ft.percentile(values, "B", 3) == (50.0, 3)  # inf is not a value; n == min ranks
    assert ft.percentile(values, "B", 4) == (None, 3)


def test_nearest_end_edges() -> None:
    d = date(2024, 3, 31)
    assert ft._nearest_end([d - timedelta(days=1), d + timedelta(days=1)], d, 1) == (
        d - timedelta(days=1)
    )  # a tie goes to the earlier
    assert ft._nearest_end([d + timedelta(days=2)], d, 2) == d + timedelta(days=2)
    assert ft._nearest_end([d + timedelta(days=5)], d, 1) is None


def test_split_factor_lookup() -> None:
    index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    splits = pd.DataFrame({"X": [2.0, 1.0, 1.0]}, index=index)
    assert ft._factor_at(splits, "X", date(2024, 1, 2)) == 2.0  # on the day itself
    assert ft._factor_at(splits, "X", date(2024, 1, 3)) == 1.0
    assert ft._factor_at(splits, "X", date(2023, 12, 29)) == 2.0  # before the window: first
    assert ft._factor_at(splits, "Y", date(2024, 1, 3)) == 1.0  # unknown symbol
    assert ft._factor_at(splits.iloc[:0], "X", date(2024, 1, 3)) == 1.0  # no sessions


def test_metric_of_a_missing_row_or_column_is_nan() -> None:
    frame = pd.DataFrame({"a": [1.0]}, index=["X"])
    assert ft._metric(frame, "X", "a") == 1.0
    assert math.isnan(ft._metric(frame, "X", "b")) and math.isnan(ft._metric(frame, "Y", "a"))


def test_history_rows_and_lookups(fworld: FundamentalWorld) -> None:
    history = ft._history(fworld.view(), "AAA")
    end = history.of("revenue", "ttm")["period_end"].iloc[-1]
    assert history.of("revenue", "ttm")["ttm"].notna().all()
    first = history.of("revenue")["period_end"].iloc[0]
    assert math.isnan(history.at("revenue", first, "ttm"))  # no TTM for the first quarter
    assert math.isnan(history.at("revenue", date(1999, 12, 31)))
    assert history.stamp("revenue", date(1999, 12, 31), "ttm") is None
    on_day = ft._History(rows=history.rows, day=end)
    assert on_day.of("revenue", "ttm")["period_end"].iloc[-1] == end  # the day itself counts


def test_a_flow_with_no_discrete_quarters_still_shows_its_ttm(fworld: FundamentalWorld) -> None:
    history = ft._history(fworld.view(), "AAA")
    rows = history.rows.copy()
    rows.loc[rows["concept"] == "revenue", "value"] = NAN
    item = ft._flow_item(
        "AAA", ft._History(rows=rows, day=history.day), "revenue", "revenue", "USD", 4
    )
    assert item is not None
    end = history.of("revenue", "ttm")["period_end"].iloc[-1]
    assert item.id == f"fsum:AAA:{end.isoformat()}:revenue"
    assert item.text.startswith("AAA revenue by fiscal quarter (USD; quarter end, value): none. ")
    assert f"TTM to {end}: USD " in item.text
    no_ttm = history.rows.copy()
    no_ttm.loc[no_ttm["concept"] == "revenue", "ttm"] = NAN
    quarters = ft._flow_item(
        "AAA", ft._History(rows=no_ttm, day=history.day), "revenue", "revenue", "USD", 4
    )
    assert quarters is not None and "TTM" not in quarters.text


# --- the analyst --------------------------------------------------------------------------------


def report(
    *, signals: tuple[str, ...] = SIGNALS, summary: str | None = None, risks: tuple[str, ...] = ()
) -> AnalystReport:
    tag = "[E:fsum:AAA:2023-12-31:revenue]"
    return AnalystReport(
        ticker="AAA",
        as_of=AS_OF,
        analyst="fundamental_analyst",
        summary=summary or "\n".join(f"{s}: fine {tag}." for s in SECTIONS),
        signals=tuple(
            Signal(name=n, direction=Direction.LONG, strength=0.5, evidence_ids=(tag[3:-1],))
            for n in signals
        ),
        risks=risks,
        confidence=0.6,
    )


def check(fworld: FundamentalWorld, output: AnalystReport) -> list[str]:
    agent = FundamentalAnalyst(LLMGateway(FakeLLM({}), CONFIG), config=CONFIG, traces=TraceStore())
    return list(agent.check_output(AgentContext(fworld.view(), inputs={"symbol": "AAA"}), output))


def test_the_problem_texts(fworld: FundamentalWorld) -> None:
    assert check(fworld, report(signals=("quality", "valuation", "growth"))) == [
        "signals must be exactly quality, valuation, growth, balance_sheet (each once); "
        "got quality, valuation, growth"
    ]
    summary = "\n".join(f"{s}: fine." for s in SECTIONS[:4])
    assert check(fworld, report(summary=summary)) == [
        "the summary needs these sections, in order, each starting a line with its label and "
        'a colon: "Red flags:", "What would change my mind:"'
    ]
    uncited = report(
        summary="\n".join(
            f"{s}: up 5%." if i == 0 else f"{s}: fine." for i, s in enumerate(SECTIONS)
        ),
        risks=("Debt doubled to 4.2B.",),
    )
    assert check(fworld, uncited) == [
        "every sentence that states a number needs an [E:<id>] tag of its evidence; these have "
        'none: "Business quality: up 5%."; "risks[0]: Debt doubled to 4.2B."'
    ]


def test_no_signals_at_all_is_named(fworld: FundamentalWorld) -> None:
    try:
        bare = report(signals=())
    except ValueError:
        pytest.skip("AnalystReport needs at least one signal")
    assert check(fworld, bare)[0].endswith("(each once); got none")


def test_the_memory_is_asked_about_the_symbol(
    fworld: FundamentalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def recall(self: AgentContext, *args: object, **kwargs: object) -> str:
        calls.append((args, kwargs))
        return "(no memories)"

    monkeypatch.setattr(AgentContext, "recall", recall)
    agent = FundamentalAnalyst(LLMGateway(FakeLLM({}), CONFIG), config=CONFIG, traces=TraceStore())
    inputs = agent.prompt_inputs(AgentContext(fworld.view(), inputs={"symbol": " bbb "}))
    assert inputs == {"symbol": "BBB", "memories": "(no memories)"}
    assert calls == [
        (("fundamental_analyst", MEMORY_QUERY.format(symbol="BBB")), {"symbol": "BBB"})
    ]


def test_a_given_tool_registry_is_kept() -> None:
    mine = fundamental_registry()
    agent = FundamentalAnalyst(LLMGateway(FakeLLM({}), CONFIG), config=CONFIG, tool_registry=mine)
    assert agent._registry is mine
    default = FundamentalAnalyst(LLMGateway(FakeLLM({}), CONFIG), config=CONFIG)
    assert default._registry is not mine
    names = default._registry.names
    assert {t.name for t in ft.FUNDAMENTAL_TOOLS} <= set(names() if callable(names) else names)


def test_the_missing_symbol_message_is_exact(fworld: FundamentalWorld) -> None:
    with pytest.raises(
        ConfigError, match=r"^fundamental_analyst needs inputs\['symbol'\] \(a ticker\)$"
    ):
        FundamentalAnalyst.symbol(AgentContext(fworld.view()))


def test_the_tools_pass_the_view_and_the_symbol(fworld: FundamentalWorld) -> None:
    """The registered tools read the run's view for the symbol they are asked about (built at
    import time, so a mutation tool cannot switch them; this pins them instead)."""
    ctx = AgentContext(fworld.view())
    for tool, body in zip(
        ft.FUNDAMENTAL_TOOLS,
        (ft.fundamentals_summary, ft.factor_ranks, ft.valuation, ft.red_flags),
        strict=True,
    ):
        assert tool.args is ft.FundamentalArgs and tool.description
        assert tool.fn(ctx, ft.FundamentalArgs(symbol="BBB")) == body(fworld.view(), "BBB")
    assert [t.name for t in ft.FUNDAMENTAL_TOOLS] == [
        "get_fundamentals_summary",
        "get_factor_ranks",
        "get_valuation",
        "get_red_flags",
    ]


# --- second pass: survivors of the re-run -------------------------------------------------------


def test_without_ttm_revenue_the_four_checks_say_so(fworld: FundamentalWorld) -> None:
    view = fworld.view()
    full = ft._history(view, "AAA")
    history = ft._History(rows=full.rows[full.rows["concept"] != "revenue"], day=full.day)
    why = "stale or missing filings (no TTM revenue)"
    assert ft.red_flag_results(view, "AAA", history) == [
        *((ft.RedFlag(c, "NOT EVALUATED", why), "") for c in ft.RED_FLAG_CHECKS[:4]),
        (ft.going_concern_rule(None, RF), ""),
    ]


def test_every_going_concern_outcome_names_its_check() -> None:
    for text in (None, "All is well.", "Substantial doubt remains."):
        assert ft.going_concern_rule(text, RF).check == "going_concern"


def test_an_empty_piece_is_skipped_not_the_end() -> None:
    assert sentences("\n\nOne. Two.\n\n\nThree [E1].") == ["One.", "Two.", "Three [E1]."]


def test_the_sector_panel_of_a_blocked_symbol_is_refused(world: object) -> None:
    with pytest.raises(
        DataError, match=r"^symbol BAD is blocked by a critical data-quality issue$"
    ):
        ft.sector_panel(world.view(), "BAD")  # type: ignore[attr-defined]


def test_the_latest_period_is_the_last_with_a_ttm_revenue(fworld: FundamentalWorld) -> None:
    """A latest quarter whose revenue has a value but no TTM (a missing 10-Q, say) does not
    count: the items and checks read as if that revenue row were not there."""
    view = fworld.view()
    full = ft._history(view, "AAA")
    rows = full.rows
    last = rows.loc[rows["concept"] == "revenue", "period_end"].max()
    hit = (rows["concept"] == "revenue") & (rows["period_end"] == last)
    no_ttm = rows.copy()
    no_ttm.loc[hit, "ttm"] = NAN
    a = ft._History(rows=no_ttm, day=full.day)
    b = ft._History(rows=rows[~hit], day=full.day)
    assert ft.red_flag_results(view, "AAA", a) == ft.red_flag_results(view, "AAA", b)
    assert ft.red_flag_results(view, "AAA", a) != ft.red_flag_results(view, "AAA", full)
    assert ft._derived_items("AAA", a, view) == ft._derived_items("AAA", b, view)
    assert ft._history_multiples(view, "AAA", a) == ft._history_multiples(view, "AAA", b)


@pytest.mark.parametrize("scale", [0.5, 0.0, -1.0])
def test_the_revenue_cagr_of_a_shrinking_zero_or_negative_revenue(
    fworld: FundamentalWorld, scale: float
) -> None:
    """Shrinking revenue has a negative CAGR; a zero or negative latest TTM revenue has none."""
    view = fworld.view()
    full = ft._history(view, "AAA")
    end = latest_end(fworld)
    ends = list(full.of("revenue", "ttm")["period_end"])
    three_ago = ft._nearest_end(ends, end - timedelta(days=3 * ft._YEAR), ft._tolerance(view))
    rows = full.rows.copy()
    hit = (rows["concept"] == "revenue") & (rows["period_end"] == end)
    rows.loc[hit, "ttm"] = rows.loc[hit, "ttm"] * scale * 0.5
    history = ft._History(rows=rows, day=full.day)
    ratio = history.at("revenue", end, "ttm") / history.at("revenue", three_ago, "ttm")
    cagr = ratio ** (1 / 3) - 1 if ratio > 0 else NAN
    assert scale <= 0 or cagr < 0
    growth = next(
        e.text for e in ft._derived_items("AAA", history, view) if e.id.endswith("growth")
    )
    expected = f"revenue CAGR over 3 years (vs TTM to {three_ago}): {ft.fmt_pct(cagr, signed=True)}"
    assert expected + " a year" in growth


def test_own_history_counts_only_finite_multiples(
    fworld: FundamentalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quarter whose multiple is not finite (a loss quarter's P/E, say) is not part of the
    own history it is compared with."""
    view = fworld.view()
    sector = ft.sector_panel(view, "AAA")
    past = ft._history_multiples(view, "AAA", ft._history(view, "AAA"))
    gone = [ft._QuarterMultiples(q.end, {k: NAN for k in q.values}) for q in past[:2]]
    monkeypatch.setattr(ft, "_history_multiples", lambda *_: gone + past[2:])
    texts = {e.id.rsplit(":", 1)[1]: e.text for e in ft.valuation_from(view, sector)}
    for key, _, _ in ft.MULTIPLES:
        n = sum(math.isfinite(q.values[key]) for q in past[2:])
        assert f"own history at {n} fiscal quarter ends" in texts[key]


def test_a_piece_holding_only_a_tag_joins_the_sentence_before() -> None:
    assert sentences("Revenue rose 5%. [E:fsum:AAA:x]. Margins fell.") == [
        "Revenue rose 5%. [E:fsum:AAA:x].",
        "Margins fell.",
    ]


def test_a_current_multiple_that_is_not_finite_gets_no_percentile(
    fworld: FundamentalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A loss-maker's P/E today is n/m: its own history is still shown, without a percentile."""
    view = fworld.view()
    sector = ft.sector_panel(view, "AAA")
    current = ft._current_multiples(sector)
    current.loc["AAA", "pe"] = NAN
    monkeypatch.setattr(ft, "_current_multiples", lambda _: current)
    text = next(e.text for e in ft.valuation_from(view, sector) if e.id.endswith(":pe"))
    own = r"own history at \d+ fiscal quarter ends \S+: median [\d.]+, low [\d.]+, high [\d.]+; "
    assert re.search(r"now n/m; " + own, text)
