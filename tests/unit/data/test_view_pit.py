"""Stage 13: `DataView`, the point-in-time facade over the data lake."""

import dataclasses
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from view_helpers import (
    REPO_ROOT,
    WORLD_AS_OF,
    WORLD_LATER,
    build_world,
    make_actions,
    make_bars,
    repo_concept_map,
    sessions_ending,
    utc,
    walk,
    world_catalog,
    world_lexicon,
)

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError, LookAheadError
from atlas.core.pit import AsOf
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.core.types.research import Evidence
from atlas.data.events import Events
from atlas.data.fundamentals import Fundamentals
from atlas.data.macro import Macro
from atlas.data.news import News
from atlas.data.sentiment import Sentiment, Social
from atlas.data.universe import Universe
from atlas.data.view import DataView

AS_OF = AsOf.for_session(date(2024, 3, 1))

# --- Task 8: construction, immutability, advance -------------------------------------------------


def test_view_as_of_from_date_and_asof(tmp_lake: Lake) -> None:
    view = DataView(tmp_lake, date(2024, 3, 1))
    assert view.as_of == AS_OF and view.day == date(2024, 3, 1)
    late = AsOf(day=date(2024, 3, 1), cutoff=utc(2024, 3, 1, 22))
    assert DataView(tmp_lake, late).as_of == late
    cfg = AtlasConfig.model_validate({"calendar": {"decision_time": "09:15"}})
    assert DataView(tmp_lake, date(2024, 3, 1), cfg).as_of.cutoff == utc(2024, 3, 1, 14, 15)


def test_view_rejects_datetime(tmp_lake: Lake) -> None:
    with pytest.raises(TypeError, match="datetime"):
        DataView(tmp_lake, datetime(2024, 3, 1, 8))  # type: ignore[arg-type]


def test_view_is_immutable(tmp_lake: Lake) -> None:
    view = DataView(tmp_lake, AS_OF)
    with pytest.raises(AttributeError, match="immutable"):
        view.as_of = AsOf.for_session(date(2024, 3, 4))  # type: ignore[misc]
    with pytest.raises(AttributeError, match="immutable"):
        view._as_of = AsOf.for_session(date(2024, 3, 4))
    with pytest.raises(AttributeError, match="immutable"):
        del view._as_of
    assert view.as_of == AS_OF


def test_advance_returns_new_view(tmp_lake: Lake) -> None:
    view = DataView(tmp_lake, AS_OF)
    later = view.advance(date(2024, 3, 4))
    assert later is not view
    assert later.as_of == AsOf.for_session(date(2024, 3, 4))
    assert view.as_of == AS_OF  # the original is unchanged
    assert later.advance(AsOf.for_session(date(2024, 3, 4))).as_of == later.as_of


def test_advance_backwards_raises(tmp_lake: Lake) -> None:
    view = DataView(tmp_lake, AS_OF)
    with pytest.raises(ValueError, match="before"):
        view.advance(date(2024, 2, 29))


# --- Task 9: prices, returns, adv ----------------------------------------------------------------

END = date(2024, 2, 29)


def _bars_lake(lake: Lake, *frames: pd.DataFrame, actions: pd.DataFrame | None = None) -> None:
    lake.write_table("prices_daily", pd.concat(frames, ignore_index=True), mode="upsert")
    if actions is not None:
        lake.write_table("corporate_actions", actions, mode="upsert")


def test_prices_window_and_columns(tmp_lake: Lake) -> None:
    _bars_lake(tmp_lake, make_bars("AAA", walk(60), end=date(2024, 3, 1)))
    frame = DataView(tmp_lake, AS_OF).prices(["aaa"], 5)
    assert list(frame.columns) == [
        "symbol", "date", "open", "high", "low", "close", "volume", "adj_close", "vendor",
        "available_at",
    ]  # fmt: skip
    assert list(frame["date"]) == sessions_ending(END, 5)  # the 03-01 bar is unknown at 08:00
    assert set(frame["symbol"]) == {"AAA"}
    assert (frame["available_at"] < pd.Timestamp(AS_OF.cutoff)).all()
    assert DataView(tmp_lake, AS_OF).prices(["ZZZ"], 5).empty


def test_prices_adjusted_with_known_split_only(tmp_lake: Lake) -> None:
    days = sessions_ending(date(2024, 3, 6), 70)
    closes = walk(70, vol=0.001)
    split_at = days.index(date(2024, 2, 26))
    closes = closes[:split_at] + [c / 2 for c in closes[split_at:]]
    actions = make_actions(
        ("AAA", date(2024, 2, 26), "split", 2.0, None),  # known
        ("AAA", date(2024, 3, 4), "split", 3.0, None),  # ex-date after the as-of: unknown
    )
    _bars_lake(tmp_lake, make_bars("AAA", closes, end=days[-1]), actions=actions)
    frame = DataView(tmp_lake, AS_OF).prices(["AAA"], 10)
    before = frame[frame["date"] < date(2024, 2, 26)]
    after = frame[frame["date"] >= date(2024, 2, 26)]
    assert (before["adj_close"] == before["close"] / 2).all()  # the 3:1 split is not applied
    assert (after["adj_close"] == after["close"]).all()


def test_returns_known_answer(tmp_lake: Lake) -> None:
    days = sessions_ending(END, 3)
    _bars_lake(
        tmp_lake,
        make_bars("AAA", [100.0, 110.0, 99.0], end=END),
        make_bars("BBB", [50.0, 50.0, 55.0], end=END),
    )
    ret = DataView(tmp_lake, AS_OF).returns(["AAA", "BBB", "CCC"], 2)
    assert list(ret.columns) == ["AAA", "BBB", "CCC"]
    assert list(ret.index) == list(pd.DatetimeIndex(days[1:], name="date"))
    assert ret["AAA"].tolist() == pytest.approx([0.1, -0.1])
    assert ret["BBB"].tolist() == pytest.approx([0.0, 0.1])
    assert ret["CCC"].isna().all()  # no bars


def test_returns_missing_bar_is_nan(tmp_lake: Lake) -> None:
    days = sessions_ending(END, 4)
    bars = make_bars("AAA", [100.0, 101.0, 102.0, 103.0], end=END)
    _bars_lake(tmp_lake, bars[bars["date"] != days[1]])
    ret = DataView(tmp_lake, AS_OF).returns(["AAA"], 3)
    assert ret["AAA"].isna().tolist() == [True, True, False]


def test_returns_include_dividends(tmp_lake: Lake) -> None:
    days = sessions_ending(END, 3)
    actions = make_actions(("AAA", days[2], "dividend", None, 1.0))
    _bars_lake(tmp_lake, make_bars("AAA", [100.0, 100.0, 99.0], end=END), actions=actions)
    ret = DataView(tmp_lake, AS_OF).returns(["AAA"], 2)
    assert ret["AAA"].tolist() == pytest.approx([0.0, 0.0])  # 99 + 1 dividend = flat


def test_adv_known_answer(tmp_lake: Lake) -> None:
    _bars_lake(tmp_lake, make_bars("AAA", [10.0, 20.0, 30.0], end=END, volume=[100, 200, 300]))
    adv = DataView(tmp_lake, AS_OF).adv(["AAA", "ZZZ"], 2)
    assert list(adv.index) == ["AAA", "ZZZ"] and list(adv.columns) == [
        "adv_usd", "adv_shares", "sessions"
    ]  # fmt: skip
    assert adv.loc["AAA", "adv_usd"] == pytest.approx((20 * 200 + 30 * 300) / 2)
    assert adv.loc["AAA", "adv_shares"] == pytest.approx(250.0)
    assert adv.loc["AAA", "sessions"] == 2
    assert pd.isna(adv.loc["ZZZ", "adv_usd"]) and adv.loc["ZZZ", "sessions"] == 0


def test_adv_shares_split_adjusted(tmp_lake: Lake) -> None:
    days = sessions_ending(END, 2)
    actions = make_actions(("AAA", days[1], "split", 2.0, None))
    bars = make_bars("AAA", [100.0, 50.0], end=END, volume=[1_000, 2_000])
    _bars_lake(tmp_lake, bars, actions=actions)
    adv = DataView(tmp_lake, AS_OF).adv(["AAA"], 2)
    assert adv.loc["AAA", "adv_shares"] == pytest.approx(2_000.0)  # 1,000 old = 2,000 new
    assert adv.loc["AAA", "adv_usd"] == pytest.approx(100_000.0)


def test_adv_default_window(tmp_lake: Lake) -> None:
    _bars_lake(tmp_lake, make_bars("AAA", walk(40), end=END))
    assert DataView(tmp_lake, AS_OF).adv(["AAA"]).loc["AAA", "sessions"] == 20


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_lookback_validation(tmp_lake: Lake, bad: object) -> None:
    view = DataView(tmp_lake, AS_OF)
    with pytest.raises(ValueError, match="lookback"):
        view.prices(["AAA"], bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="lookback"):
        view.returns(["AAA"], bad)  # type: ignore[arg-type]


def test_sessions_window(tmp_lake: Lake) -> None:  # Stage 15 D2
    _bars_lake(tmp_lake, make_bars("AAA", walk(60), end=date(2024, 3, 1)))
    view = DataView(tmp_lake, AS_OF)
    assert view.sessions(5) == tuple(sessions_ending(END, 5))  # the 03-01 bar is unknown at 08:00
    assert list(view.prices(["AAA"], 5)["date"]) == list(view.sessions(5))
    evening = AsOf.for_session(date(2024, 3, 1), time(19, 0))  # after the bar is published
    assert DataView(tmp_lake, evening).sessions(2) == (END, date(2024, 3, 1))
    assert view.sessions(1) == (END,)


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_sessions_lookback_validation(tmp_lake: Lake, bad: object) -> None:
    with pytest.raises(ValueError, match="lookback"):
        DataView(tmp_lake, AS_OF).sessions(bad)  # type: ignore[arg-type]


def test_symbols_must_be_a_sequence(tmp_lake: Lake) -> None:
    with pytest.raises(ValueError, match="sequence"):
        DataView(tmp_lake, AS_OF).prices("AAA", 5)


# --- Task 10: delegated methods ----------------------------------------------------------------


@dataclass(frozen=True)
class World:
    """The clean and dirty test lakes and the reference inputs every view needs."""

    clean: Lake
    dirty: Lake
    lexicon: Any
    concept_map: Any
    catalog: Any
    shared: dict[tuple[int, date], DataView] = dataclasses.field(default_factory=dict)

    def view(
        self, lake: Lake, day: date | AsOf = WORLD_AS_OF, *, fresh: bool = False, **kwargs: Any
    ) -> DataView:
        """A view of `lake` at `day`. Views without extra arguments are shared across tests
        (they are immutable); `fresh=True` (or any keyword) builds a new one."""
        key = (id(lake), day if isinstance(day, date) else day.day)
        if not fresh and not kwargs and isinstance(day, date) and key in self.shared:
            return self.shared[key]
        view = DataView(
            lake, day, concept_map=self.concept_map, catalog=self.catalog, lexicon=self.lexicon,
            **kwargs,
        )  # fmt: skip
        if not fresh and not kwargs and isinstance(day, date):
            self.shared[key] = view
        return view


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    root: Path = tmp_path_factory.mktemp("world")
    lakes = []
    for name, future in (("clean", False), ("dirty", True)):
        lake = Lake(root / name, registry=DEFAULT_REGISTRY.copy())
        build_world(lake, future=future)
        lakes.append(lake)
    return World(lakes[0], lakes[1], world_lexicon(root), repo_concept_map(), world_catalog())


def test_quality_issues_and_blocked(world: World) -> None:
    view = world.view(world.clean)
    assert view.blocked == frozenset({"BAD"})
    report = view.quality_issues()
    assert ("BAD", "nonpositive_price") in set(zip(report["symbol"], report["check"], strict=True))
    assert (report["available_at"] < pd.Timestamp(AS_OF.cutoff)).all()


def test_blocked_runs_only_the_checks_that_can_block(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 2 checkpoint: the block list must not pay for the warning-only checks (the
    fundamentals jump check is 97% of a report on the real lake)."""

    def boom(*args: object, **kwargs: object) -> list[object]:
        raise AssertionError("a check that cannot block was run")

    for name in ("check_fundamentals_jumps", "check_macro_gaps", "check_missing_sessions"):
        monkeypatch.setattr(f"atlas.data.quality.{name}", boom)
    view = world.view(world.clean, fresh=True)
    assert view.blocked == frozenset({"BAD"})
    assert list(view.prices(["AAA", "BAD"], 5)["symbol"].unique()) == ["AAA"]
    with pytest.raises(DataError, match=r"BAD is blocked .* nonpositive_price on"):
        view.fundamentals("BAD")
    with pytest.raises(AssertionError, match="cannot block"):
        view.quality_issues()  # the full report still runs every check, on request


def test_blocked_equals_the_full_reports_critical_symbols(world: World) -> None:
    for lake in (world.clean, world.dirty):
        for severity in ({}, {"return_outlier": "critical", "macro_gap": "critical"}):
            cfg = AtlasConfig.model_validate({"data": {"quality": {"severity": severity}}})
            view = world.view(lake, fresh=True, config=cfg)
            full = view.quality_issues()
            expected = frozenset(full.loc[full["severity"] == "critical", "symbol"])
            assert view.blocked == expected
            assert expected  # the world always blocks something


def test_fundamentals_matches_reader(world: World) -> None:
    view = world.view(world.clean)
    expected = Fundamentals(world.clean, concept_map=world.concept_map).pit("AAA", AS_OF)
    pd.testing.assert_series_equal(view.fundamentals("aaa"), expected)
    assert view.fundamentals("AAA")["revenue_ttm"] == pytest.approx(430e6)


def test_fundamentals_panel_matches_reader(world: World) -> None:
    view = world.view(world.clean)
    panel = view.fundamentals_panel(["AAA", "BAD", "BBB", "ZZZ"])
    assert list(panel.index) == ["AAA", "BBB", "ZZZ"]
    reader = Fundamentals(world.clean, concept_map=world.concept_map)
    for symbol in ("AAA", "BBB"):
        pd.testing.assert_series_equal(
            panel.loc[symbol], reader.pit(symbol, AS_OF), check_names=False
        )
    assert panel.loc["ZZZ"].isna().all()


def test_macro_matches_reader(world: World) -> None:
    view = world.view(world.clean)
    ids = ["DGS10", "CPIAUCSL"]
    expected = Macro(world.clean, catalog=world.catalog).pit(ids, AS_OF, timedelta(days=90))
    pd.testing.assert_frame_equal(view.macro(ids, timedelta(days=90)), expected)
    pd.testing.assert_frame_equal(view.macro(["dgs10", "CPIAUCSL"], 90), expected)
    with pytest.raises(ValueError, match="lookback"):
        view.macro(ids, 0)


def test_news_matches_reader(world: World) -> None:
    view = world.view(world.clean)
    expected = News(world.clean).pit("AAA", AS_OF)
    assert view.news("AAA") == expected and [e.id for e in expected] == [
        "news:alpaca-2",
        "news:alpaca-1",
    ]
    assert view.news("AAA", lookback=1) == News(world.clean).pit("AAA", AS_OF, 1)


def test_market_news(world: World) -> None:
    view = world.view(world.clean)
    assert view.market_news() == News(world.clean).market(AS_OF)
    assert [e.id for e in view.market_news()] == ["news:alpaca-3"]


def test_sentiment_matches_reader(world: World) -> None:
    view = world.view(world.clean)
    reader = Sentiment(world.clean, lexicon=world.lexicon)
    assert view.sentiment("AAA") == reader.at("AAA", AS_OF)
    assert view.sentiment("AAA").score > 0
    pd.testing.assert_frame_equal(view.sentiment_daily("AAA", 5), reader.daily("AAA", AS_OF, 5))


def test_events_matches_reader(world: World) -> None:
    view = world.view(world.clean)
    upcoming = view.events(horizon_days=30)
    expected = Events(world.clean).upcoming(AS_OF, 30)
    expected = expected[expected["symbol"] != "BAD"].reset_index(drop=True)
    pd.testing.assert_frame_equal(upcoming, expected)
    assert {"cpi", "earnings"} <= set(upcoming["event_type"])
    only = view.events(["AAA", "BAD"], horizon_days=30)
    assert set(only["symbol"].dropna()) == {"AAA"} and "cpi" in set(only["event_type"])


def test_past_events_and_reactions(world: World) -> None:
    view = world.view(world.clean)
    events = Events(world.clean)
    pd.testing.assert_frame_equal(view.past_events("AAA"), events.past("AAA", AS_OF))
    pd.testing.assert_frame_equal(view.earnings_reactions("AAA"), events.reactions("AAA", AS_OF))
    assert list(view.earnings_reactions("AAA", n=1)["event_id"]) == ["sec-aaa-1"]


def test_social_matches_reader(world: World) -> None:
    view = world.view(world.clean)
    assert view.social("AAA") == Social(world.clean).evidence("AAA", AS_OF)
    assert [e.id for e in view.social("AAA", lookback=3, limit=5)] == ["social:st-1"]


def test_universe_and_tradable(world: World) -> None:
    view = world.view(world.clean)
    universe = Universe(world.clean)
    assert universe.members(AS_OF) == ["AAA", "BAD", "BBB", "CCC"]
    assert view.universe() == ["AAA", "BBB", "CCC"]
    assert universe.tradable(AS_OF) == ["AAA", "BAD", "BBB", "CCC"]
    assert view.tradable() == ["AAA", "BBB", "CCC"]
    assert "SPY" in view.universe("macro_etfs")


def test_instrument(world: World) -> None:
    view = world.view(world.clean)
    assert view.instrument("aaa").name == "AAA Corp"
    assert view.instrument("SPY").is_etf
    with pytest.raises(DataError, match="ZZZ"):
        view.instrument("ZZZ")


# --- Task 11: the leak suite, parametrised over every DataView method --------------------------

UNIVERSE = ["AAA", "BBB", "CCC", "SPY"]

METHODS: dict[str, Callable[[DataView], Any]] = {
    "prices": lambda v: v.prices(UNIVERSE, 30),
    "returns": lambda v: v.returns(UNIVERSE, 30),
    "adv": lambda v: v.adv(UNIVERSE),
    "fundamentals": lambda v: v.fundamentals("AAA"),
    "fundamentals_panel": lambda v: v.fundamentals_panel(["AAA", "BBB"]),
    "macro": lambda v: v.macro(["DGS10", "CPIAUCSL"], 120),
    "news": lambda v: v.news("AAA"),
    "market_news": lambda v: v.market_news(),
    "sentiment": lambda v: v.sentiment("AAA"),
    "sentiment_daily": lambda v: v.sentiment_daily("AAA", 10),
    "events": lambda v: v.events(horizon_days=30),
    "past_events": lambda v: v.past_events("AAA"),
    "earnings_reactions": lambda v: v.earnings_reactions("AAA"),
    "social": lambda v: v.social("AAA"),
    "universe": lambda v: v.universe(),
    "tradable": lambda v: v.tradable(),
    "instrument": lambda v: v.instrument("AAA"),
    "quality_issues": lambda v: v.quality_issues(),
    "blocked": lambda v: v.blocked,
    "sessions": lambda v: v.sessions(30),
    "fundamentals_history": lambda v: v.fundamentals_history(
        ["AAA", "BBB"], ["revenue", "total_assets"]
    ),
    "split_factors": lambda v: v.split_factors(UNIVERSE, 60),
    "macro_vintages": lambda v: v.macro_vintages(["DGS10", "CPIAUCSL"], 120),  # Stage 21
    "members_on": lambda v: v.members_on(v.day),  # Stage 21
    "ciks": lambda v: v.ciks(["AAA", "BBB"]),  # Phase 2 checkpoint
}
"""Every public data method of `DataView`, called as strategy or agent code would."""


def test_methods_cover_the_public_api() -> None:
    public = {
        name
        for name in dir(DataView)
        if not name.startswith("_")
        and name not in {"as_of", "day", "config", "advance", "cache_info"}
    }
    assert public == set(METHODS)


def _norm(value: Any) -> Any:
    """A comparable, NaN-safe form of a method's result."""
    if isinstance(value, pd.DataFrame | pd.Series):
        return value.to_json(orient="split", date_format="iso", default_handler=str)
    if isinstance(value, list):
        return [v.model_dump(mode="json") if isinstance(v, Evidence) else v for v in value]
    if isinstance(value, frozenset):
        return sorted(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return [
            None if isinstance(x, float) and math.isnan(x) else x
            for x in dataclasses.astuple(value)
        ]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _assert_known(value: Any, as_of: AsOf) -> None:
    if isinstance(value, pd.DataFrame) and "available_at" in value.columns:
        known = value["available_at"].dropna()
        assert (known < pd.Timestamp(as_of.cutoff)).all()
    if isinstance(value, list):
        assert all(as_of.knows(e.as_of) for e in value if isinstance(e, Evidence))


@pytest.mark.parametrize("method", sorted(METHODS))
def test_future_rows_never_leak(world: World, method: str) -> None:
    clean = METHODS[method](world.view(world.clean))
    dirty = METHODS[method](world.view(world.dirty))
    assert _norm(dirty) == _norm(clean)
    _assert_known(dirty, AS_OF)


STATIC = {
    "instrument": "instruments is static reference data (no knowledge time to inject)",
    "sessions": "the session grid moves with the as-of and the calendar, not with lake rows",
    "ciks": "an issuer's CIK never changes: a later fact carries the same one (the leak and "
    "future-row suites still cover the method)",
}


@pytest.mark.parametrize("method", sorted(set(METHODS) - set(STATIC)))
def test_injected_rows_are_visible_later(world: World, method: str) -> None:
    """The rows the leak suite injects do change each answer once known: the suite above is
    not vacuous."""
    clean = METHODS[method](world.view(world.clean, WORLD_LATER))
    dirty = METHODS[method](world.view(world.dirty, WORLD_LATER))
    assert _norm(dirty) != _norm(clean)
    _assert_known(dirty, AsOf.for_session(WORLD_LATER))


LATE = pd.Timestamp(AS_OF.cutoff) + pd.Timedelta(hours=1)


def _late_frame(frame: pd.DataFrame) -> pd.DataFrame:
    late = frame.head(1).copy()
    late["available_at"] = LATE
    return pd.concat([frame, late], ignore_index=True)


def _late_evidence(items: list[Evidence]) -> list[Evidence]:
    return [*items, Evidence(id="news:late", source="test", as_of=LATE.to_pydatetime(), text="x")]


def _wrap(
    monkeypatch: pytest.MonkeyPatch, owner: Any, name: str, change: Callable[[Any], Any]
) -> None:
    original = getattr(owner, name)
    monkeypatch.setattr(owner, name, lambda *a, **k: change(original(*a, **k)))


def _identity(frame: pd.DataFrame, *_: Any, **__: Any) -> pd.DataFrame:
    return frame


def _leak_bars(mp: pytest.MonkeyPatch) -> None:
    import atlas.data.view as view_module

    mp.setattr(view_module, "latest_known_session", lambda *_: date(2024, 3, 8))
    mp.setattr(view_module, "pit_filter", _identity)


def _leak_news_window(mp: pytest.MonkeyPatch) -> None:
    import atlas.data.news.pit as news_pit

    mp.setattr(news_pit, "pit_filter", _identity)
    original = News.articles
    mp.setattr(
        News, "articles", lambda self, start, end: original(self, start, end + timedelta(days=30))
    )


def _leak_quality(mp: pytest.MonkeyPatch) -> None:
    import atlas.data.quality as quality_module

    mp.setattr(quality_module, "pit_filter", _identity)


def _leak_universe(mp: pytest.MonkeyPatch) -> None:
    import atlas.data.universe as universe_module

    mp.setattr(universe_module, "pit_filter", _identity)


def _leak_fundamentals(mp: pytest.MonkeyPatch) -> None:
    # Both fundamentals methods build their detail with `assemble_detail` (Phase 1 checkpoint:
    # cross-day cache of the concept values); before, with `Fundamentals.pit_detail` and
    # `fundamentals_pit_detail`.
    import atlas.data.view as view_module

    _wrap(mp, view_module, "assemble_detail", _late_frame)


def _leak_ciks(mp: pytest.MonkeyPatch) -> None:  # Phase 2 checkpoint
    import atlas.data.view as view_module

    mp.setattr(view_module, "pit_filter", lambda frame, *_a, **_k: _late_frame(frame))


def _leak_history(mp: pytest.MonkeyPatch) -> None:  # Stage 16
    import atlas.data.view as view_module

    _wrap(mp, view_module, "concept_history", _late_frame)


LEAKS: dict[str, Callable[[pytest.MonkeyPatch], None]] = {
    "prices": _leak_bars,
    "returns": _leak_bars,
    "adv": _leak_bars,
    "fundamentals": _leak_fundamentals,
    "fundamentals_panel": _leak_fundamentals,
    "macro": lambda mp: _wrap(mp, Macro, "pit_detail", _late_frame),
    "news": lambda mp: _wrap(mp, News, "pit", _late_evidence),
    "market_news": lambda mp: _wrap(mp, News, "market", _late_evidence),
    "sentiment": _leak_news_window,
    "sentiment_daily": _leak_news_window,
    "events": lambda mp: _wrap(mp, Events, "upcoming", _late_frame),
    "past_events": lambda mp: _wrap(mp, Events, "past", _late_frame),
    "earnings_reactions": lambda mp: _wrap(mp, Events, "reactions", _late_frame),
    "social": lambda mp: _wrap(mp, Social, "evidence", _late_evidence),
    "tradable": _leak_universe,
    "quality_issues": _leak_quality,
    "blocked": _leak_quality,
    "sessions": _leak_bars,
    "fundamentals_history": _leak_history,
    "split_factors": _leak_bars,
    "macro_vintages": lambda mp: _wrap(mp, Macro, "vintages", _late_frame),  # Stage 21
    "ciks": _leak_ciks,
}
"""How to make each method's delegate leak a row from after the cutoff."""

NO_TIMESTAMPS = {
    "universe": "membership is dated by start/end dates, covered by the leak suite",
    "instrument": "static reference data, no knowledge time",
    "members_on": "membership is dated by start/end dates, as universe (Stage 21)",
}


def test_every_method_has_a_guard_test() -> None:
    assert set(LEAKS) | set(NO_TIMESTAMPS) == set(METHODS)


@pytest.mark.parametrize("method", sorted(LEAKS))
def test_guard_raises_if_filter_bypassed(
    world: World, method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = world.view(world.dirty, fresh=True)
    if method not in {"quality_issues", "blocked"}:
        _ = view.blocked  # the quality report before the patch: only the method's own path leaks
    LEAKS[method](monkeypatch)
    with pytest.raises(LookAheadError):
        METHODS[method](view)


SINGLE = {
    "fundamentals": lambda v: v.fundamentals("BAD"),
    "news": lambda v: v.news("BAD"),
    "sentiment": lambda v: v.sentiment("BAD"),
    "sentiment_daily": lambda v: v.sentiment_daily("BAD", 5),
    "past_events": lambda v: v.past_events("BAD"),
    "earnings_reactions": lambda v: v.earnings_reactions("BAD"),
    "social": lambda v: v.social("BAD"),
    "instrument": lambda v: v.instrument("bad"),
}
MULTI: dict[str, Callable[[DataView], Any]] = {
    "prices": lambda v: set(v.prices(["AAA", "BAD"], 30)["symbol"]),
    "returns": lambda v: set(v.returns(["AAA", "BAD"], 30).columns),
    "adv": lambda v: set(v.adv(["AAA", "BAD"]).index),
    "fundamentals_panel": lambda v: set(v.fundamentals_panel(["AAA", "BAD"]).index),
    "fundamentals_history": lambda v: set(
        v.fundamentals_history(["AAA", "BAD"], ["revenue"])["symbol"]
    ),
    "split_factors": lambda v: set(v.split_factors(["AAA", "BAD"], 30).columns),
    "events": lambda v: (
        set(v.events(horizon_days=30)["symbol"].dropna())
        | set(v.events(["AAA", "BAD"], horizon_days=30)["symbol"].dropna())
    ),
    "universe": lambda v: set(v.universe()),
    "tradable": lambda v: set(v.tradable()),
    "members_on": lambda v: set(v.members_on(v.day)),  # Stage 21
    "ciks": lambda v: set(v.ciks(["AAA", "BAD"]).index),  # Phase 2 checkpoint
}
NOT_PER_SYMBOL = {
    "macro": "series, not symbols: see test_macro_drops_blocked_series",
    "macro_vintages": "series, not symbols: see test_macro_vintages_drops_blocked_series",
    "market_news": "market-wide, about no single symbol",
    "quality_issues": "the report itself names the blocked symbol",
    "blocked": "the blocked set itself",
    "sessions": "the session grid, about no symbol",
}


def test_every_method_has_a_block_test() -> None:
    assert set(SINGLE) | set(MULTI) | set(NOT_PER_SYMBOL) == set(METHODS)


@pytest.mark.parametrize("method", sorted(SINGLE))
def test_blocked_symbol_raises(world: World, method: str) -> None:
    with pytest.raises(DataError, match=r"BAD is blocked on 2024-03-01.*nonpositive_price"):
        SINGLE[method](world.view(world.clean))


@pytest.mark.parametrize("method", sorted(MULTI))
def test_blocked_symbol_absent(world: World, method: str) -> None:
    found = MULTI[method](world.view(world.clean))
    assert "AAA" in found and "BAD" not in found


def test_blocked_symbol_present_without_the_block(world: World) -> None:
    """The world really has BAD data: with the block off (severity warning) it shows up."""
    cfg = AtlasConfig.model_validate(
        {"data": {"quality": {"severity": {"nonpositive_price": "warning"}}}}
    )
    view = world.view(world.clean, config=cfg)
    assert view.blocked == frozenset()
    for method, call in MULTI.items():
        assert "BAD" in call(view), method
    for call in SINGLE.values():
        call(view)  # no DataError


def test_macro_drops_blocked_series(world: World) -> None:
    cfg = AtlasConfig.model_validate(
        {"data": {"quality": {"severity": {"macro_gap": "critical"}, "macro_max_age_days": {
            "daily": 10, "weekly": 21, "monthly": 30, "quarterly": 230, "annual": 550}}}}
    )  # fmt: skip
    view = world.view(world.clean, config=cfg)
    assert "CPIAUCSL" in view.blocked  # January CPI is 60 days old at 2024-03-01
    assert list(view.macro(["DGS10", "CPIAUCSL"], 120).columns) == ["DGS10"]


# --- Task 12: caching and immutability ---------------------------------------------------------


def _counting_reads(monkeypatch: pytest.MonkeyPatch, lake: Lake) -> list[str]:
    calls: list[str] = []
    original = lake.read_table

    def counted(name: str, *args: Any, **kwargs: Any) -> pd.DataFrame:
        calls.append(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(lake, "read_table", counted)
    return calls


def test_cache_hit_reads_lake_once(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    view = world.view(world.clean, fresh=True)
    _ = view.blocked  # the day's quality report, read once
    calls = _counting_reads(monkeypatch, world.clean)
    first = view.prices(["AAA", "BBB"], 20)
    reads = len(calls)
    assert reads > 0 and view.cache_info().misses >= 2
    before = view.cache_info()
    second = view.prices(["aaa", "BBB", "AAA"], 20)  # same request, other spelling
    after = view.cache_info()
    assert len(calls) == reads  # no lake read
    assert after.misses == before.misses and after.hits > before.hits
    pd.testing.assert_frame_equal(first, second)


def test_cache_keyed_by_args_and_as_of(world: World) -> None:
    view = world.view(world.clean, date(2024, 2, 29), fresh=True)
    view.prices(["AAA"], 5)
    misses = view.cache_info().misses
    assert len(view.prices(["AAA"], 6)) == 6
    assert view.cache_info().misses > misses  # other arguments: computed
    later = view.advance(date(2024, 3, 1))
    misses = later.cache_info().misses
    assert later.prices(["AAA"], 5)["date"].max() == date(2024, 2, 29)
    assert later.cache_info().misses > misses  # other as-of: computed
    assert view.prices(["AAA"], 5)["date"].max() == date(2024, 2, 28)


def test_cache_returns_copies(world: World) -> None:
    view = world.view(world.clean, fresh=True)
    frame = view.prices(["AAA"], 5)
    frame.loc[:, "close"] = -1.0
    assert (view.prices(["AAA"], 5)["close"] > 0).all()
    series = view.fundamentals("AAA")
    series[:] = 0.0
    assert view.fundamentals("AAA")["revenue_ttm"] == pytest.approx(430e6)
    items = view.news("AAA")
    items.clear()
    assert len(view.news("AAA")) == 2
    names = view.universe()
    names.append("ZZZ")
    assert "ZZZ" not in view.universe()


def test_cache_lru_eviction(world: World) -> None:
    cfg = AtlasConfig.model_validate({"data": {"view": {"cache_size": 3}}})
    view = world.view(world.clean, config=cfg)
    _ = view.blocked  # fills two entries: quality_issues and blocked
    view.prices(["AAA"], 5)  # third entry; evicts nothing yet
    info = view.cache_info()
    assert (info.size, info.maxsize) == (3, 3)
    view.prices(["BBB"], 5)  # evicts quality_issues, the least recently used
    misses = view.cache_info().misses
    view.prices(["AAA"], 5)  # still cached
    assert view.cache_info().misses == misses
    view.quality_issues()  # evicted: computed again
    assert view.cache_info().misses == misses + 1
    assert view.cache_info().size == 3


def test_cache_misses_after_write(tmp_lake: Lake) -> None:
    _bars_lake(tmp_lake, make_bars("AAA", [10.0, 11.0, 12.0], end=END))
    view = DataView(tmp_lake, AS_OF)
    assert view.prices(["AAA"], 1)["close"].tolist() == [12.0]
    tmp_lake.write_table("prices_daily", make_bars("AAA", [13.0], end=END), mode="upsert")
    assert view.prices(["AAA"], 1)["close"].tolist() == [13.0]  # the lake changed: recomputed


def test_advanced_view_shares_cache(world: World) -> None:
    view = world.view(world.clean, date(2024, 2, 29), fresh=True)
    first = view.advance(date(2024, 3, 1))
    first.prices(["AAA"], 5)
    misses = first.cache_info().misses
    second = view.advance(date(2024, 3, 1))
    second.prices(["AAA"], 5)
    assert second.cache_info().misses == misses  # the same cache, the same key: no recompute


def test_view_is_read_only(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[str] = []
    monkeypatch.setattr(world.clean, "write_table", lambda name, *a, **k: writes.append(name))
    generation = world.clean.generation
    view = world.view(world.clean, fresh=True)
    for call in METHODS.values():
        call(view)
    assert writes == [] and world.clean.generation == generation


def test_limitations_doc_has_stage_13_section() -> None:
    text = (REPO_ROOT / "docs" / "data-limitations.md").read_text(encoding="utf-8")
    section = text.split("## Data quality and DataView (Stage 13)")[1]
    for phrase in ("window_sessions", "adjusted close", "return_outlier", "vendor_mix", "cache"):
        assert phrase in section


# --- Stage 21: every known vintage, membership on an earlier day ------------------------------


def test_macro_vintages_drops_blocked_series(world: World) -> None:  # Stage 21
    cfg = AtlasConfig.model_validate(
        {"data": {"quality": {"severity": {"macro_gap": "critical"}, "macro_max_age_days": {
            "daily": 10, "weekly": 21, "monthly": 30, "quarterly": 230, "annual": 550}}}}
    )  # fmt: skip
    view = world.view(world.clean, config=cfg)
    assert set(view.macro_vintages(["DGS10", "CPIAUCSL"], 120)["series_id"]) == {"DGS10"}


def test_macro_vintages_reproduce_macro(world: World) -> None:  # Stage 21
    """The latest known vintage per observation of `macro_vintages` is `macro`'s frame."""
    from atlas.data.macro.pit import known_observations

    view = world.view(world.clean)
    rows = view.macro_vintages(["DGS10", "CPIAUCSL"], 120)
    columns = ["series_id", "obs_date", "value", "realtime_start", "available_at"]
    assert list(rows.columns) == columns
    latest = known_observations(rows, view.as_of).dropna(subset=["value"])
    wide = latest.pivot(index="obs_date", columns="series_id", values="value")
    wide = wide.reindex(columns=["DGS10", "CPIAUCSL"]).astype("float64")
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index), name="obs_date")
    wide.columns.name = None
    pd.testing.assert_frame_equal(wide.sort_index(), view.macro(["DGS10", "CPIAUCSL"], 120))
    with pytest.raises(ValueError, match="lookback"):
        view.macro_vintages(["DGS10"], 0)
    with pytest.raises(ValueError, match="sequence"):
        view.macro_vintages("DGS10", 30)


def test_members_on_an_earlier_day(world: World) -> None:  # Stage 21
    view = world.view(world.clean, WORLD_LATER)
    assert view.members_on(view.day) == view.universe()
    earlier = world.view(world.clean)
    assert view.members_on(earlier.day) == sorted(set(earlier.universe()) - view.blocked)
    with pytest.raises(ValueError, match="after"):
        earlier.members_on(WORLD_LATER)
