"""Phase 2 checkpoint, Tier 3 item 2 (Stage 16 code): a company that files no usable
cover-page share count gets its market cap from its weighted-average basic shares
(`average_shares`, the `fallback` of `compute_market`, `quant.factors.share_count_fallback`).

The average of a period is an approximation of the count on a date (buybacks and issuance
move it by about a percent a year), so it only fills a missing count and the market frame
says which names used it (`shares_source`)."""

import math
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from factor_world import (
    AS_OF,
    STOCKS,
    build_factor_world,
    history_frame,
    panel_frame,
    prices_frame,
    splits_frame,
)

from atlas.core.config import AtlasConfig, FactorsConfig
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.factors import (
    HISTORY_CONCEPTS,
    MARKET_COLUMNS,
    SHARES_COLUMNS,
    adjusted_shares,
    average_shares,
    compute_market,
    factor_config_hash,
    factor_panel,
    implied_shares,
)

DAY = date(2024, 3, 1)
SESSIONS = [
    date(2024, 1, 19),
    date(2024, 1, 22),
    date(2024, 2, 14),
    date(2024, 2, 15),
    date(2024, 2, 28),
    date(2024, 2, 29),
]
Q4 = date(2023, 12, 31)
HALF_THEN_ONE = [0.5, 0.5, 0.5, 1, 1, 1]  # a 2:1 split ex 2024-02-15


def _averages(*rows: tuple[str, date, float, date]) -> pd.DataFrame:
    """`fundamentals_history` rows of `shares_weighted_average`: `(symbol, period_end,
    value, filed)`, known from 21:00 UTC on the filing day."""
    frames = [
        history_frame(
            [(symbol, "shares_weighted_average", end, value, math.nan)],
            available_at=datetime(filed.year, filed.month, filed.day, 21, tzinfo=UTC),
        )
        for symbol, end, value, filed in rows
    ]
    return pd.concat(frames, ignore_index=True)


def _counts(*rows: tuple[str, date, float]) -> pd.DataFrame:
    return history_frame([(s, "shares_outstanding", d, v, math.nan) for s, d, v in rows])


# --- average_shares ----------------------------------------------------------------------------


def test_history_concepts_include_the_average() -> None:
    assert "shares_weighted_average" in HISTORY_CONCEPTS
    assert "shares_source" in MARKET_COLUMNS


def test_average_shares_are_restated_from_the_filing_day() -> None:
    """Both quarters end 2023-12-31, before the 2:1 split of 2024-02-15. AAA filed on
    2024-01-22, before the split: its 20 are pre-split shares, 40 of today's. BBB filed on
    2024-02-20, after it: a filing made after a split states every share figure in new
    shares, so its 40 are already today's."""
    splits = splits_frame(SESSIONS, {"AAA": HALF_THEN_ONE, "BBB": HALF_THEN_ONE})
    hist = _averages(
        ("AAA", Q4, 20.0, date(2024, 1, 22)),
        ("BBB", Q4, 40.0, date(2024, 2, 20)),
    )
    out = average_shares(hist, splits)
    assert list(out.columns) == list(SHARES_COLUMNS)
    got = {(r.symbol, r.period_end): r.shares for r in out.itertuples()}
    assert got == {("AAA", Q4): 40.0, ("BBB", Q4): 40.0}


def test_average_shares_ignore_other_concepts_and_excluded_symbols() -> None:
    splits = splits_frame(SESSIONS, symbols=["AAA", "BRK.B"])
    hist = pd.concat(
        [
            _averages(
                ("AAA", Q4, 20.0, date(2024, 1, 22)),
                ("BRK.B", Q4, 1.4, date(2024, 1, 22)),
            ),
            _counts(("AAA", date(2024, 1, 20), 21.0)),
        ],
        ignore_index=True,
    )
    out = average_shares(hist, splits, exclude=("BRK.B",))
    assert [(r.symbol, r.shares) for r in out.itertuples()] == [("AAA", 20.0)]
    assert list(average_shares(hist.iloc[0:0], splits).columns) == list(SHARES_COLUMNS)


# --- the confirmation by net income / EPS ---------------------------------------------------------


def test_implied_shares_is_net_income_over_eps() -> None:
    panel = panel_frame(
        {
            "AAA": {"net_income_ttm": 500.0, "eps_diluted_ttm": 2.5},
            "BBB": {"net_income_ttm": -90.0, "eps_diluted_ttm": -0.3},  # a loss: still a count
            "CCC": {
                "net_income_ttm": 500.0,
                "eps_diluted_ttm": 0.001,
            },  # EPS too small
            "DDD": {"net_income_ttm": 500.0, "eps_diluted_ttm": -2.5},  # signs disagree
            "EEE": {"net_income_ttm": 500.0},  # no EPS
            "FFF": {"eps_diluted_ttm": 2.5},  # no net income
        }
    )
    got = implied_shares(panel, min_abs_eps=0.01)
    assert got == {"AAA": 200.0, "BBB": pytest.approx(300.0)}


def test_average_shares_keep_only_confirmed_averages() -> None:
    """Filers sometimes state the average in thousands or millions (KO's 10-Q for Q1 2019
    says 4,271 for 4,271 million shares). An average is kept only if it is within
    `max_ratio` of the count implied by net income / EPS, as filed."""
    splits = splits_frame(SESSIONS, symbols=["KO", "IBKR", "XYZ", "NOEPS"])
    q3, filed_q3, filed_q4 = date(2023, 9, 30), date(2023, 10, 25), date(2024, 1, 22)
    hist = _averages(
        ("KO", q3, 4_300_000_000.0, filed_q3),
        ("KO", Q4, 4_271.0, filed_q4),  # in millions: a unit error
        ("IBKR", Q4, 100_000_000.0, filed_q4),  # one class of four: 0.25 of the implied count
        ("XYZ", Q4, 5_000_000_000_000.0, filed_q4),  # a thousand times too many
        ("NOEPS", Q4, 1_000_000.0, filed_q4),  # nothing to confirm it with
    )
    implied = {"KO": 4_310_000_000.0, "IBKR": 400_000_000.0, "XYZ": 5_000_000_000.0}
    out = average_shares(hist, splits, confirm=implied, max_ratio=30.0)
    got = [(r.symbol, r.period_end, r.shares) for r in out.itertuples()]
    assert got == [("IBKR", Q4, 100_000_000.0), ("KO", q3, 4_300_000_000.0)]
    # the band's edges belong to it; without `confirm` nothing is checked
    edge = average_shares(hist, splits, confirm={"IBKR": 3_000_000_000.0}, max_ratio=30.0)
    assert [r.symbol for r in edge.itertuples()] == ["IBKR"]
    assert len(average_shares(hist, splits)) == 5


def test_a_unit_error_falls_back_to_the_last_confirmed_average() -> None:
    """KO above: its latest average is a unit error, so the cap uses the one before it."""
    panel = panel_frame({"KO": {"total_debt": 0.0, "cash_and_equivalents": 0.0}})
    prices = prices_frame([("KO", date(2024, 2, 29), 50.0)])
    splits = splits_frame(SESSIONS, symbols=["KO"])
    hist = _averages(
        ("KO", date(2023, 9, 30), 4_300_000_000.0, date(2023, 10, 25)),
        ("KO", Q4, 4_271.0, date(2024, 1, 22)),
    )
    fallback = average_shares(hist, splits, confirm={"KO": 4_310_000_000.0}, max_ratio=30.0)
    m = compute_market(
        prices,
        adjusted_shares(_counts(), splits),
        splits,
        panel,
        day=DAY,
        max_staleness_days=400,
        fallback=fallback,
    )
    assert (m.loc["KO", "shares"], m.loc["KO", "shares_date"]) == (4.3e9, date(2023, 9, 30))
    assert m.loc["KO", "market_cap"] == pytest.approx(215e9)


# --- compute_market ----------------------------------------------------------------------------


def _market(fallback: pd.DataFrame | None, **kw: object) -> pd.DataFrame:
    panel = panel_frame(
        {
            "AAA": {"total_debt": 300.0, "cash_and_equivalents": 100.0},  # count and average
            "BBB": {"total_debt": 0.0, "cash_and_equivalents": 10.0},  # stale count, average
            "CCC": {"total_debt": 0.0, "cash_and_equivalents": 0.0},  # average only
            "DDD": {"total_debt": 0.0, "cash_and_equivalents": 0.0},  # nothing
            "EEE": {"total_debt": 0.0, "cash_and_equivalents": 0.0},  # stale average only
        }
    )
    prices = prices_frame(
        [(s, date(2024, 2, 29), 10.0) for s in ["AAA", "BBB", "CCC", "DDD", "EEE"]]
    )
    splits = splits_frame(SESSIONS, symbols=["AAA", "BBB", "CCC", "DDD", "EEE"])
    shares = adjusted_shares(
        _counts(("AAA", date(2024, 1, 20), 20.0), ("BBB", date(2022, 12, 1), 7.0)), splits
    )
    args: dict = {"day": DAY, "max_staleness_days": 400} | kw
    return compute_market(prices, shares, splits, panel, fallback=fallback, **args)


def _fallback() -> pd.DataFrame:
    splits = splits_frame(SESSIONS, symbols=["AAA", "BBB", "CCC", "DDD", "EEE"])
    return average_shares(
        _averages(
            ("AAA", Q4, 99.0, date(2024, 1, 22)),
            ("BBB", date(2023, 9, 30), 8.0, date(2023, 11, 1)),
            ("BBB", Q4, 9.0, date(2024, 1, 22)),
            ("CCC", Q4, 3.0, date(2024, 1, 22)),
            ("EEE", date(2022, 12, 31), 5.0, date(2023, 2, 1)),
        ),
        splits,
    )


def test_fallback_fills_only_a_missing_count() -> None:
    m = _market(_fallback())
    assert list(m.columns) == list(MARKET_COLUMNS)
    aaa = m.loc["AAA"]  # a fresh cover-page count: the average (99) is not used
    assert (aaa["shares"], aaa["shares_date"], aaa["shares_source"]) == (
        20.0,
        date(2024, 1, 20),
        "reported",
    )
    assert aaa["market_cap"] == 200.0
    bbb = m.loc["BBB"]  # its count is 456 days old: the latest average
    assert (bbb["shares"], bbb["shares_date"], bbb["shares_source"]) == (9.0, Q4, "average")
    assert (bbb["market_cap"], bbb["enterprise_value"]) == (90.0, 80.0)
    ccc = m.loc["CCC"]
    assert (ccc["shares"], ccc["shares_source"], ccc["market_cap"]) == (3.0, "average", 30.0)


def test_no_count_and_no_fresh_average_gives_no_cap() -> None:
    m = _market(_fallback())
    for symbol in ("DDD", "EEE"):  # EEE's only average ended 426 days ago
        row = m.loc[symbol]
        assert math.isnan(row["shares"]) and math.isnan(row["market_cap"])
        assert row["shares_date"] is None and row["shares_source"] is None


def test_without_a_fallback_nothing_changes() -> None:
    with_it, without = _market(_fallback()), _market(None)
    assert without.loc["AAA"].equals(with_it.loc["AAA"])
    for symbol in ("BBB", "CCC"):
        assert math.isnan(without.loc[symbol, "market_cap"])
        assert without.loc[symbol, "shares_source"] is None


def test_fallback_cap_is_on_the_price_session_basis() -> None:
    """The latest bar (2024-02-14) predates the 2:1 split; the average was filed before it
    too: 20 pre-split shares x 100 = 2,000, not doubled."""
    panel = panel_frame({"AAA": {"total_debt": 0.0, "cash_and_equivalents": 0.0}})
    prices = prices_frame([("AAA", date(2024, 2, 14), 100.0)])
    splits = splits_frame(SESSIONS, {"AAA": HALF_THEN_ONE})
    fallback = average_shares(_averages(("AAA", Q4, 20.0, date(2024, 1, 22))), splits)
    none = adjusted_shares(_counts(), splits)
    m = compute_market(
        prices, none, splits, panel, day=DAY, max_staleness_days=400, fallback=fallback
    )
    assert m.loc["AAA", "shares"] == 40.0  # today's shares
    assert m.loc["AAA", "market_cap"] == pytest.approx(2000.0)


# --- settings ----------------------------------------------------------------------------------


def test_fallback_settings_defaults_and_validation() -> None:
    cfg = FactorsConfig()
    assert cfg.share_count_fallback is True
    assert cfg.share_count_fallback_excluded == ("BRK.B",)
    assert cfg.share_count_fallback_max_ratio == 3.0
    with pytest.raises(ValueError, match="share_count_fallback_max_ratio"):
        FactorsConfig(share_count_fallback_max_ratio=1.0)
    assert FactorsConfig(
        share_count_fallback_excluded=[" brk-b ", "abc"]
    ).share_count_fallback_excluded == (
        "BRK.B",
        "ABC",
    )
    with pytest.raises(ValueError, match="invalid symbol"):
        FactorsConfig(share_count_fallback_excluded=["not a symbol!"])
    with pytest.raises(ValueError, match="twice"):
        FactorsConfig(share_count_fallback_excluded=["BRK.B", "brk-b"])


def test_the_settings_and_the_definitions_are_part_of_the_cache_tag() -> None:
    base = AtlasConfig()
    off = FactorsConfig(share_count_fallback=False)
    other = FactorsConfig(share_count_fallback_excluded=())
    tags = {
        factor_config_hash(base),
        factor_config_hash(base, off),
        factor_config_hash(base, other),
    }
    assert len(tags) == 3


def test_definitions_version_changes_the_cache_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """A changed formula (EBIT -> EBITDA) must not read panels stored under the old one."""
    import atlas.quant.factors.fundamental as module

    before = factor_config_hash(AtlasConfig())
    monkeypatch.setattr(module, "FACTOR_DEFINITIONS_VERSION", module.FACTOR_DEFINITIONS_VERSION + 1)
    assert factor_config_hash(AtlasConfig()) != before
    assert module.FACTOR_DEFINITIONS_VERSION >= 2


# --- through DataView --------------------------------------------------------------------------

CFG = FactorsConfig(min_names=5)
NO_COUNT = ("CCC", "EEE")  # CCC splits 2:1 on 2024-02-12, after its Q4 filing of 2024-02-05


def _lake(root: Path) -> Lake:
    return Lake(root / "lake", registry=DEFAULT_REGISTRY.copy())


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory) -> pd.DataFrame:
    """The market frame of the world where every company files a cover-page count."""
    lake = build_factor_world(_lake(tmp_path_factory.mktemp("with_counts"))).lake
    return factor_panel(DataView(lake, AS_OF), config=CFG).market


@pytest.fixture(scope="module")
def lake_without(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    """The same world, but CCC and EEE file no cover-page count."""
    root = tmp_path_factory.mktemp("without_counts")
    return build_factor_world(_lake(root), no_cover_count=NO_COUNT).lake


def test_panel_uses_the_average_where_there_is_no_count(
    reference: pd.DataFrame, lake_without: Lake
) -> None:
    """In the world the quarter's average equals the count, so the caps must agree, the
    split of CCC included (its average was filed a week before the split)."""
    p = factor_panel(DataView(lake_without, AS_OF), config=CFG)
    assert (reference["shares_source"] == "reported").all()
    for symbol in STOCKS:
        want = "average" if symbol in NO_COUNT else "reported"
        assert p.market.loc[symbol, "shares_source"] == want, symbol
        assert p.market.loc[symbol, "market_cap"] == pytest.approx(
            reference.loc[symbol, "market_cap"], rel=1e-12
        )
    assert p.raw.loc[list(NO_COUNT), "size"].notna().all()
    # issuance stays on cover-page counts: an average a year apart is not a count
    assert p.raw.loc[list(NO_COUNT), "net_issuance"].isna().all()


def test_panel_drops_an_average_that_net_income_over_eps_contradicts(
    reference: pd.DataFrame, tmp_path: Path
) -> None:
    """EEE restates its Q4 average a million times too small (a later filing, known at the
    as-of): the panel uses its Q3 average, not the restated figure."""
    world = build_factor_world(_lake(tmp_path), no_cover_count=NO_COUNT)
    facts = world.lake.read_table("fundamentals_facts")
    q4 = facts[
        (facts["symbol"] == "EEE")
        & (facts["tag"] == "WeightedAverageNumberOfSharesOutstandingBasic")
        & (facts["period_end"] == date(2023, 12, 31))
    ]
    assert len(q4) == 1
    stamp = pd.Timestamp(datetime(2024, 2, 20, 21, tzinfo=UTC))
    wrong = q4.assign(
        value=q4["value"] / 1e6,
        accn="0000000016-24-999999",
        filed=date(2024, 2, 20),
        accepted_at=stamp,
        available_at=stamp,
    )
    world.lake.write_table("fundamentals_facts", wrong, mode="upsert")
    eee = next(c for c in world.companies if c.symbol == "EEE")
    p = factor_panel(DataView(world.lake, AS_OF), config=CFG)
    row = p.market.loc["EEE"]
    assert (row["shares_source"], row["shares_date"]) == ("average", date(2023, 9, 30))
    assert row["shares"] == pytest.approx(eee.shares * (1 + eee.share_drift) ** 14, rel=1e-12)
    assert row["market_cap"] == pytest.approx(
        reference.loc["EEE", "market_cap"] / (1 + eee.share_drift), rel=1e-9
    )
    unchecked = FactorsConfig(min_names=5, share_count_fallback_max_ratio=1e9)
    loose = factor_panel(DataView(world.lake, AS_OF), config=unchecked).market.loc["EEE"]
    assert loose["shares"] == pytest.approx(eee.shares * (1 + eee.share_drift) ** 15 / 1e6)


def test_panel_without_the_fallback_has_no_cap_for_them(lake_without: Lake) -> None:
    off = FactorsConfig(min_names=5, share_count_fallback=False)
    p = factor_panel(DataView(lake_without, AS_OF), config=off)
    assert p.market.loc[list(NO_COUNT), "market_cap"].isna().all()
    assert p.market["market_cap"].notna().sum() == len(STOCKS) - len(NO_COUNT)


def test_panel_excludes_the_listed_symbols_from_the_fallback(lake_without: Lake) -> None:
    cfg = FactorsConfig(min_names=5, share_count_fallback_excluded=("EEE",))
    p = factor_panel(DataView(lake_without, AS_OF), config=cfg)
    assert math.isnan(p.market.loc["EEE", "market_cap"])
    assert p.market.loc["EEE", "shares_source"] is None
    assert p.market.loc["CCC", "shares_source"] == "average"


# --- Tier 4 / backlog: a wrong cover-page count, and averages without net income / EPS -------


def test_a_count_far_from_a_confirmed_average_is_replaced_by_it() -> None:
    """HBAN's cover count was stated in thousands (x934 against net income / EPS) while its
    weighted average was right: with `max_ratio`, a fresh count more than that factor from
    a fresh confirmed average (both in today's shares) is the wrong one."""
    panel = panel_frame(
        {
            "AAA": {"total_debt": 0.0, "cash_and_equivalents": 0.0},  # count 1000x too small
            "BBB": {"total_debt": 0.0, "cash_and_equivalents": 0.0},  # count 2x the average
        }
    )
    prices = prices_frame([(s, date(2024, 2, 29), 10.0) for s in ("AAA", "BBB")])
    splits = splits_frame(SESSIONS, symbols=["AAA", "BBB"])
    counts = adjusted_shares(
        _counts(("AAA", date(2024, 1, 20), 0.8), ("BBB", date(2024, 1, 20), 2000.0)), splits
    )
    averages = average_shares(
        _averages(("AAA", Q4, 800.0, date(2024, 1, 22)), ("BBB", Q4, 1000.0, date(2024, 1, 22))),
        splits,
    )
    m = compute_market(
        prices, counts, splits, panel, day=DAY, max_staleness_days=400, fallback=averages,
        max_ratio=3.0,
    )  # fmt: skip
    assert (m.loc["AAA", "shares"], m.loc["AAA", "shares_source"]) == (800.0, "average")
    assert m.loc["AAA", "market_cap"] == 8000.0
    assert (m.loc["BBB", "shares"], m.loc["BBB", "shares_source"]) == (2000.0, "reported")
    plain = compute_market(  # without max_ratio a fresh count is never replaced
        prices, counts, splits, panel, day=DAY, max_staleness_days=400, fallback=averages
    )
    assert plain.loc["AAA", "shares"] == 0.8 and plain.loc["AAA", "shares_source"] == "reported"


def test_replacing_a_count_is_logged() -> None:
    from structlog.testing import capture_logs

    panel = panel_frame({"AAA": {"total_debt": 0.0, "cash_and_equivalents": 0.0}})
    prices = prices_frame([("AAA", date(2024, 2, 29), 10.0)])
    splits = splits_frame(SESSIONS, symbols=["AAA"])
    counts = adjusted_shares(_counts(("AAA", date(2024, 1, 20), 0.8)), splits)
    averages = average_shares(_averages(("AAA", Q4, 800.0, date(2024, 1, 22))), splits)
    with capture_logs() as logs:
        compute_market(
            prices, counts, splits, panel, day=DAY, max_staleness_days=400, fallback=averages,
            max_ratio=3.0,
        )  # fmt: skip
    events = [e for e in logs if e["event"] == "factors.share_count_replaced"]
    assert len(events) == 1 and events[0]["symbols"] == ["AAA"]


def test_an_average_without_net_income_eps_is_confirmed_by_an_old_count() -> None:
    """Molson Coors and Airbnb have no TTM net income / EPS at some dates; their latest cover
    count, however old (in today's shares), can confirm the average instead."""
    splits = splits_frame(SESSIONS, symbols=["TAP", "ABNB", "BAD"])
    hist = _averages(
        ("TAP", Q4, 210.0, date(2024, 1, 22)),
        ("ABNB", Q4, 640.0, date(2024, 1, 22)),
        ("BAD", Q4, 0.21, date(2024, 1, 22)),  # in millions: a unit error
    )
    old_counts = {"TAP": 200.0, "BAD": 210.0}  # no count at all for ABNB
    out = average_shares(hist, splits, confirm={}, counts=old_counts, max_ratio=3.0)
    assert [(r.symbol, r.shares) for r in out.itertuples()] == [("TAP", 210.0)]
    # an implied count, when there is one, decides (the old count is not consulted)
    out = average_shares(hist, splits, confirm={"BAD": 0.2}, counts=old_counts, max_ratio=3.0)
    assert [r.symbol for r in out.itertuples()] == ["BAD", "TAP"]


def test_panel_replaces_a_wrong_count_and_confirms_by_old_counts(tmp_path: Path) -> None:
    """Through `factor_panel`: FFF's latest cover count is filed a thousand times too small
    (its weighted average and net income / EPS agree): the cap uses the average."""
    world = build_factor_world(_lake(tmp_path))
    facts = world.lake.read_table("fundamentals_facts")
    latest = (
        facts[(facts["symbol"] == "FFF") & (facts["tag"] == "EntityCommonStockSharesOutstanding")]
        .sort_values("period_end")
        .tail(1)
    )
    world.lake.write_table(
        "fundamentals_facts", latest.assign(value=latest["value"] / 1000), mode="upsert"
    )
    fff = next(c for c in world.companies if c.symbol == "FFF")
    p = factor_panel(DataView(world.lake, AS_OF), config=CFG)
    row = p.market.loc["FFF"]
    assert row["shares_source"] == "average"
    assert row["shares"] == pytest.approx(fff.shares * (1 + fff.share_drift) ** 15, rel=1e-12)
    others = p.market.drop(index="FFF")
    assert (others["shares_source"] == "reported").all()
