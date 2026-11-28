"""Stage 31 Tasks 5-7: the news analyst's tools on the news world (`news_world`).

Hand-computed: the clean lake holds AAA's articles a6, a1, a2 (newest first), P01's p3, p2, p1,
P02's q1, the earnings of AAA on 2024-03-07 (after the close) and its ex-dividend date on
2024-03-14, P01's earnings on 2024-03-05 (before the open), P02's estimated date 2024-03-07 and
AAA's 11 reactions, 8 of them shown."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from news_world import (
    AS_OF,
    LAST_EIGHT,
    PEERS_BY_ADV,
    NewsWorld,
    _event,
    utc,
    write_world,
)

from atlas.agents.context import AgentContext
from atlas.agents.tools import news_tools as nt
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.core.types.research import Evidence
from atlas.data.view import DataView

DAY = "2024-03-01"


def ids(items: list[Evidence]) -> list[str]:
    return [e.id for e in items]


def configured(**news: object) -> AtlasConfig:
    return AtlasConfig.model_validate({"agents": {"news": news}})


def blocked_lake(root: Path) -> Lake:
    """The world with AAA's price frozen: a stale-price issue blocks it."""
    lake = Lake(root, registry=DEFAULT_REGISTRY.copy())
    write_world(lake, future=False)
    bars = lake.read_table("prices_daily")
    mine = bars["symbol"] == "AAA"
    for col in ("open", "high", "low", "close", "adj_close"):
        bars.loc[mine, col] = 100.0
    lake.write_table("prices_daily", bars, mode="upsert")
    return lake


def lake_without(root: Path, *skip: str) -> Lake:
    """The world with the tables `skip` never written."""
    full = Lake(root / "full", registry=DEFAULT_REGISTRY.copy())
    write_world(full, future=False)
    lake = Lake(root / "part", registry=DEFAULT_REGISTRY.copy())
    for name in (
        "prices_daily",
        "corporate_actions",
        "instruments",
        "index_membership",
        "news",
        "events",
        "macro_events",
        "earnings_reactions",
    ):
        if name not in skip:
            lake.write_table(name, full.read_table(name), mode="upsert")
    return lake


# --- get_company_news -------------------------------------------------------------------------


def test_company_news_lists_the_coverage_then_the_articles_newest_first(nworld: NewsWorld) -> None:
    items = nt.company_news(nworld.view(), "AAA")
    assert ids(items) == [
        f"ncov:AAA:{DAY}:company",
        "news:alpaca-a6",
        "news:alpaca-a1",
        "news:alpaca-a2",
    ]
    assert items[1].as_of > items[2].as_of > items[3].as_of
    assert items[2].text.startswith("AAA raises full-year guidance")  # the PIT text, unchanged


def test_the_coverage_item_counts_articles_and_names_the_window(nworld: NewsWorld) -> None:
    cov = nt.company_news(nworld.view(), "AAA")[0]
    assert cov.source == "atlas"
    assert cov.text == (
        f"AAA company news on {DAY}: 3 articles in the 21 calendar days to {DAY} (duplicates "
        "and roundups removed); newest published 2024-02-29"
    )
    assert cov.as_of == nworld.view().as_of.cutoff - nt.EPSILON


def test_one_article_is_singular(nworld: NewsWorld) -> None:
    view = nworld.view(config=configured(lookback_days=1))  # 2024-02-29 13:00Z on: only p3
    items = nt.company_news(view, "P01")
    assert ids(items) == [f"ncov:P01:{DAY}:company", "news:alpaca-p3"]
    assert "1 article in the 1 calendar days to" in items[0].text


def test_the_window_comes_from_the_config(nworld: NewsWorld) -> None:
    view = nworld.view(config=configured(lookback_days=2))  # since 2024-02-28 13:00Z
    assert ids(nt.company_news(view, "AAA"))[1:] == ["news:alpaca-a6", "news:alpaca-a1"]
    wide = nworld.view(config=configured(lookback_days=60))
    assert "news:alpaca-a5" in ids(nt.company_news(wide, "AAA"))  # the January article


def test_a_capped_list_says_that_more_exist(nworld: NewsWorld) -> None:
    items = nt.company_news(nworld.view(config=configured(max_articles=2)), "AAA")
    assert len([e for e in items if e.id.startswith("news:")]) == 2
    assert "2 articles in the 21 calendar days" in items[0].text
    assert "more articles exist than the 2 shown (the least relevant are left out)" in items[0].text


def test_no_article_is_a_citable_zero(nworld: NewsWorld) -> None:
    items = nt.company_news(nworld.view(), "NOS")
    assert ids(items) == [f"ncov:NOS:{DAY}:company"]
    assert items[0].text == (
        f"NOS company news on {DAY}: 0 articles in the 21 calendar days to {DAY} (duplicates "
        "and roundups removed)"
    )


def test_company_news_of_a_blocked_symbol_is_refused(tmp_path: Path) -> None:
    view = DataView(blocked_lake(tmp_path / "stale"), AS_OF, AtlasConfig())
    with pytest.raises(DataError, match="AAA is blocked"):
        nt.company_news(view, "AAA")


def test_a_missing_news_table_is_a_data_error(tmp_path: Path) -> None:
    view = DataView(lake_without(tmp_path, "news"), AS_OF, AtlasConfig())
    with pytest.raises(DataError, match="news is empty"):
        nt.company_news(view, "AAA")


# --- get_sector_news --------------------------------------------------------------------------


def test_sector_news_reads_the_peers_by_dollar_volume(nworld: NewsWorld) -> None:
    items = nt.sector_news(nworld.view(), "AAA")
    assert ids(items) == [
        f"ncov:AAA:{DAY}:sector",
        "news:alpaca-p3",
        "news:alpaca-p2",
        "news:alpaca-q1",
        "news:alpaca-p1",
    ]
    # the article AAA shares with P01 (a6) is in the company list: it is not repeated here;
    # P01's fourth article (p4) is over the per-peer cap; H01's is another sector's
    assert items[0].text == (
        f"AAA sector news on {DAY}: Information Technology peers read by dollar volume: "
        "P01 (3 articles), P02 (1 article), P04 (0 articles), P03 (0 articles), "
        f"NOS (0 articles); 4 articles shown from the last 21 calendar days (articles already "
        "in the company list left out)"
    )
    assert PEERS_BY_ADV == ("P01", "P02", "P04", "P03", "NOS")


def test_sector_news_is_capped_per_peer_and_in_all(nworld: NewsWorld) -> None:
    one = nt.sector_news(nworld.view(config=configured(sector_articles_per_peer=1)), "AAA")
    assert ids(one)[1:] == ["news:alpaca-p3", "news:alpaca-q1"]
    top = nt.sector_news(
        nworld.view(config=configured(sector_articles=2, sector_articles_per_peer=2)), "AAA"
    )
    assert ids(top)[1:] == ["news:alpaca-p3", "news:alpaca-p2"]
    assert "2 articles shown" in top[0].text


def test_the_peer_count_comes_from_the_config(nworld: NewsWorld) -> None:
    items = nt.sector_news(nworld.view(config=configured(sector_peers=2)), "AAA")
    assert "P01 (3 articles), P02 (1 article);" in items[0].text
    assert "P04" not in items[0].text


def test_the_company_is_not_its_own_peer_and_other_sectors_are_left_out(
    nworld: NewsWorld,
) -> None:
    text = nt.sector_news(nworld.view(), "AAA")[0].text
    assert "AAA (" not in text and "H01" not in text
    health = nt.sector_news(nworld.view(), "H02")
    assert ids(health) == ["ncov:H02:2024-03-01:sector", "news:alpaca-h1"]
    assert "Health Care peers read by dollar volume: H01 (1 article)" in health[0].text


def test_a_symbol_without_a_sector_gets_an_explicit_item(nworld: NewsWorld) -> None:
    items = nt.sector_news(nworld.view(), "NOSEC")
    assert ids(items) == [f"ncov:NOSEC:{DAY}:sector"]
    assert items[0].text == (
        f"NOSEC sector news on {DAY}: NOSEC has no GICS sector in the instruments table, so no "
        "sector news was read (0 peers, 0 articles)"
    )


def test_a_sector_with_no_other_member_gets_an_explicit_item(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "alone", registry=DEFAULT_REGISTRY.copy())
    write_world(lake, future=False)
    inst = lake.read_table("instruments")
    inst.loc[inst["symbol"] == "AAA", "sector"] = "Utilities"
    lake.write_table("instruments", inst, mode="overwrite")
    items = nt.sector_news(DataView(lake, AS_OF, AtlasConfig()), "AAA")
    assert ids(items) == [f"ncov:AAA:{DAY}:sector"]
    assert "no other Utilities member of sp500" in items[0].text
    assert "(0 peers, 0 articles)" in items[0].text


def test_an_unknown_symbol_is_a_data_error(nworld: NewsWorld) -> None:
    with pytest.raises(DataError, match="no instrument 'ZZZ'"):
        nt.sector_news(nworld.view(), "ZZZ")


# --- get_upcoming_events ----------------------------------------------------------------------


def test_upcoming_events_of_the_stock(nworld: NewsWorld) -> None:
    items = nt.upcoming_events(nworld.view(), "AAA")
    assert ids(items) == ["evt:AAA:earnings:2024-03-07", "evt:AAA:ex_dividend:2024-03-14"]
    earnings, dividend = items
    assert earnings.text == (
        "AAA earnings expected on 2024-03-07 after the market close (AMC), 6 calendar days "
        "ahead; a scheduled date (latest published 2024-02-20)"
    )
    assert dividend.text == (
        "AAA ex-dividend date 2024-03-14, 13 calendar days ahead; a scheduled date (latest "
        "published 2024-02-20)"
    )
    assert earnings.as_of.isoformat() == "2024-02-20T12:00:00+00:00"


def test_an_older_snapshot_and_macro_events_are_not_listed(nworld: NewsWorld) -> None:
    items = nt.upcoming_events(nworld.view(), "AAA")
    assert "2024-03-08" not in " ".join(e.id for e in items)  # the superseded schedule
    assert all("cpi" not in e.id for e in items)


def test_a_morning_report_and_an_estimated_date(nworld: NewsWorld) -> None:
    (p01,) = nt.upcoming_events(nworld.view(), "P01")
    assert p01.id == "evt:P01:earnings:2024-03-05"
    assert p01.text.startswith("P01 earnings expected on 2024-03-05 before the market open (BMO)")
    assert "4 calendar days ahead" in p01.text
    (p02,) = nt.upcoming_events(nworld.view(), "P02")
    assert p02.id == "evt:P02:earnings:2024-03-07"
    assert p02.text == (
        "P02 earnings estimated for 2024-03-07 after the market close (AMC), 6 calendar days "
        "ahead: no date is scheduled yet, so this is the last report date plus 52 weeks (an "
        "estimate, not a confirmed date)"
    )


def test_the_horizon_comes_from_the_config(nworld: NewsWorld) -> None:
    short = nt.upcoming_events(nworld.view(config=configured(events_horizon_days=10)), "AAA")
    assert ids(short) == ["evt:AAA:earnings:2024-03-07"]  # the ex-dividend date is 13 days out


def test_no_event_is_a_citable_none(nworld: NewsWorld) -> None:
    (none,) = nt.upcoming_events(nworld.view(), "NOS")
    assert none.id == f"evt:NOS:{DAY}:none"
    assert none.text == (
        f"No earnings or ex-dividend event of NOS is known within the 30 calendar days from {DAY}."
    )
    assert none.as_of == nworld.view().as_of.cutoff - nt.EPSILON


def test_an_event_already_reported_today_is_labelled(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "today", registry=DEFAULT_REGISTRY.copy())
    write_world(lake, future=False)
    events = lake.read_table("events")
    events = events[events["event_id"] != "yahoo-P01-2024-03-05"]
    lake.write_table("events", events, mode="overwrite")
    # an actual, date-only report dated the as-of day (a filing known earlier)
    row = pd.DataFrame(
        [_event("sec-p01-9", "P01", "earnings", AS_OF, None, "unknown", "sec_8k", utc(2024, 2, 20))]
    )
    for col in ("event_time", "available_at"):
        row[col] = pd.to_datetime(row[col], utc=True).astype("datetime64[us, UTC]")
    lake.write_table("events", row, mode="upsert")
    (item,) = nt.upcoming_events(DataView(lake, AS_OF, AtlasConfig()), "P01")
    assert item.text.startswith("P01 earnings reported on 2024-03-01 (filing sec_8k)")


# --- get_past_earnings_reactions --------------------------------------------------------------


def test_past_reactions_are_the_last_eight_newest_first(nworld: NewsWorld) -> None:
    items = nt.past_reactions(nworld.view(), "AAA")
    assert ids(items) == [
        f"rxs:AAA:{DAY}:summary",
        "rx:AAA:2024-02-01",
        "rx:AAA:2023-10-31",
        "rx:AAA:2023-08-01",
        "rx:AAA:2023-05-02",
        "rx:AAA:2023-01-31",
        "rx:AAA:2022-11-01",
        "rx:AAA:2022-08-02",
        "rx:AAA:2022-05-03",
    ]
    assert len(LAST_EIGHT) == 8


def test_a_reaction_item_gives_the_four_measures_in_percent(nworld: NewsWorld) -> None:
    last = nt.past_reactions(nworld.view(), "AAA")[1]
    assert last.text == (
        "AAA earnings of 2024-02-01 (reaction session 2024-02-02): gap +1.0%, open-to-close "
        "+2.0%, abnormal open-to-close +1.5% and 3-session abnormal return +3.0% against SPY"
    )
    assert last.source == "earnings_reactions"
    assert last.as_of.isoformat() == "2024-02-06T21:15:00+00:00"  # known when its window closed


def test_the_summary_is_computed_by_the_tool(nworld: NewsWorld) -> None:
    summary = nt.past_reactions(nworld.view(), "AAA")[0]
    # CARs, newest first: +3, +3, -5, +2, -3, +5, -0.5, +1 (percent)
    assert summary.text == (
        "AAA last 8 earnings reactions (2022-05-03 to 2024-02-01): mean absolute 3-session "
        "abnormal return 2.8%, 5 of 8 positive; largest +5.0% (2022-11-01), smallest -5.0% "
        "(2023-08-01)"
    )
    assert summary.as_of == nworld.view().as_of.cutoff - nt.EPSILON


def test_the_count_comes_from_the_config(nworld: NewsWorld) -> None:
    items = nt.past_reactions(nworld.view(config=configured(reactions=2)), "AAA")
    assert ids(items) == [f"rxs:AAA:{DAY}:summary", "rx:AAA:2024-02-01", "rx:AAA:2023-10-31"]
    assert "last 2 earnings reactions (2023-10-31 to 2024-02-01)" in items[0].text
    assert "2 of 2 positive" in items[0].text and "smallest +3.0% (2023-10-31)" in items[0].text


def test_no_reaction_is_a_citable_zero(nworld: NewsWorld) -> None:
    (summary,) = nt.past_reactions(nworld.view(), "NOS")
    assert summary.id == f"rxs:NOS:{DAY}:summary"
    assert summary.text == f"NOS: no earnings reaction is known on {DAY} (0 reactions)"


# --- prefetch and the tools -------------------------------------------------------------------


def test_prefetch_is_the_four_tools_in_order(nworld: NewsWorld) -> None:
    view = nworld.view()
    got = nt.prefetch(view, "AAA")
    assert got == [
        *nt.company_news(view, "AAA"),
        *nt.upcoming_events(view, "AAA"),
        *nt.past_reactions(view, "AAA"),
        *nt.sector_news(view, "AAA"),
    ]
    assert len({e.id for e in got}) == len(got)


def test_a_symbol_with_nothing_still_gets_every_coverage_item(nworld: NewsWorld) -> None:
    got = ids(nt.prefetch(nworld.view(), "NOS"))
    assert got[0] == f"ncov:NOS:{DAY}:company"
    assert f"evt:NOS:{DAY}:none" in got and f"rxs:NOS:{DAY}:summary" in got
    assert f"ncov:NOS:{DAY}:sector" in got


def test_a_tool_that_fails_becomes_a_gap_item(tmp_path: Path) -> None:
    gap = lake_without(tmp_path, "news", "events", "macro_events", "earnings_reactions")
    got = nt.prefetch(DataView(gap, AS_OF, AtlasConfig()), "AAA")
    assert [e.id for e in got if e.id.startswith("gap:")] == [
        f"gap:AAA:{DAY}:get_company_news",
        f"gap:AAA:{DAY}:get_sector_news",
    ]
    assert "news is empty" in got[0].text
    assert f"evt:AAA:{DAY}:none" in ids(got)  # the events and reactions tables read as empty


def test_a_blocked_symbol_gets_no_prefetch(tmp_path: Path) -> None:
    view = DataView(blocked_lake(tmp_path / "blocked"), AS_OF, AtlasConfig())
    with pytest.raises(DataError, match="AAA is blocked"):
        nt.prefetch(view, "AAA")


def test_the_tools(nworld: NewsWorld) -> None:
    assert [t.name for t in nt.NEWS_TOOLS] == [
        "get_company_news",
        "get_upcoming_events",
        "get_past_earnings_reactions",
        "get_sector_news",
    ]
    assert all(t.version == 1 and t.args is nt.NewsArgs for t in nt.NEWS_TOOLS)
    assert all(t.description.strip() for t in nt.NEWS_TOOLS)
    view = nworld.view()
    ctx = AgentContext(view, inputs={"symbol": "AAA"})
    args = nt.NewsArgs(symbol=" p02 ")
    assert args.symbol == "P02"
    company, events, reactions, sector = (list(t.fn(ctx, args)) for t in nt.NEWS_TOOLS)
    assert company == nt.company_news(view, "P02")
    assert events == nt.upcoming_events(view, "P02")
    assert reactions == nt.past_reactions(view, "P02")
    assert sector == nt.sector_news(view, "P02")


def test_a_bad_ticker_fails_the_arguments() -> None:
    with pytest.raises(ValueError, match="symbol"):
        nt.NewsArgs(symbol="not a ticker!")
    with pytest.raises(ValueError, match="extra"):
        nt.NewsArgs.model_validate({"symbol": "AAA", "days": 3})


# --- audit: the guard's count ------------------------------------------------------------------


def test_an_unreadable_news_table_counts_as_no_article_and_says_why(tmp_path: Path) -> None:
    """`company_article_count` turns the missing table into 0 (the guard's fail-safe side) and
    logs the reason: the exception is not swallowed silently (its text is also the `gap:` item)."""
    from structlog.testing import capture_logs

    view = DataView(lake_without(tmp_path, "news"), AS_OF, AtlasConfig())
    with capture_logs() as logs:
        assert nt.company_article_count(view, "AAA") == 0
    (entry,) = [e for e in logs if e["event"] == "news.company_count_unavailable"]
    assert entry["symbol"] == "AAA" and "news is empty" in entry["reason"]
    whole = DataView(lake_without(tmp_path / "whole"), AS_OF, AtlasConfig())
    assert nt.company_article_count(whole, "P01") == 5  # p1..p4 and the one shared with AAA
    assert nt.company_article_count(whole, "NOS") == 0
    with pytest.raises(DataError, match="AAA is blocked"):  # a blocked symbol is not "no news"
        nt.company_article_count(
            DataView(blocked_lake(tmp_path / "b"), AS_OF, AtlasConfig()), "AAA"
        )


def _lake_with_events(tmp_path: Path, rows: list[dict[str, object]]) -> DataView:
    lake = Lake(tmp_path / "ev", registry=DEFAULT_REGISTRY.copy())
    write_world(lake, future=False)
    frame = pd.DataFrame(rows)
    for col in ("event_time", "available_at"):
        frame[col] = pd.to_datetime(frame[col], utc=True).astype("datetime64[us, UTC]")
    lake.write_table("events", frame, mode="upsert")
    return DataView(lake, AS_OF, AtlasConfig())


def test_an_event_of_another_type_gets_a_plain_item(tmp_path: Path) -> None:
    row = _event(
        "x-nos-1", "NOS", "split_vote", date(2024, 3, 5), None, "unknown", "yahoo", utc(2024, 2, 20)
    )
    (item,) = nt.upcoming_events(_lake_with_events(tmp_path, [row]), "NOS")
    assert item.id == "evt:NOS:split_vote:2024-03-05"
    assert item.text == "NOS split vote on 2024-03-05, 4 calendar days ahead (source yahoo)"


def test_an_ex_dividend_date_already_in_the_bars_is_labelled(tmp_path: Path) -> None:
    row = _event(
        "div-NOS-1",
        "NOS",
        "ex_dividend",
        AS_OF,
        None,
        "unknown",
        "corporate_actions",
        utc(2024, 2, 20),
    )
    (item,) = nt.upcoming_events(_lake_with_events(tmp_path, [row]), "NOS")
    assert item.text == "NOS ex-dividend date 2024-03-01 (already in the price data)"


def test_a_peer_without_an_instrument_row_is_not_a_peer_and_is_logged(tmp_path: Path) -> None:
    from structlog.testing import capture_logs

    lake = Lake(tmp_path / "noinst", registry=DEFAULT_REGISTRY.copy())
    write_world(lake, future=False)
    inst = lake.read_table("instruments")
    lake.write_table("instruments", inst[inst["symbol"] != "P02"], mode="overwrite")
    with capture_logs() as logs:
        text = nt.sector_news(DataView(lake, AS_OF, AtlasConfig()), "AAA")[0].text
    assert "P02" not in text and "P01 (3 articles)" in text
    (skipped,) = [e for e in logs if e["event"] == "news.peer_skipped"]
    assert skipped["symbol"] == "P02" and "no instrument 'P02'" in skipped["reason"]


# --- audit: mutation testing --------------------------------------------------------------


@pytest.mark.parametrize("build", [nt.upcoming_events, nt.past_reactions, nt.sector_news])
def test_every_tool_refuses_a_blocked_symbol(tmp_path: Path, build: object) -> None:
    view = DataView(blocked_lake(tmp_path / "blk"), AS_OF, AtlasConfig())
    with pytest.raises(DataError, match="AAA is blocked"):
        build(view, "AAA")  # type: ignore[operator]


def test_a_day_with_no_event_today_is_not_called_today() -> None:
    assert nt._ahead(0) == "today"
    assert nt._ahead(1) == "1 calendar day ahead"
    assert nt._ahead(6) == "6 calendar days ahead"


def test_an_unknown_time_of_day_reads_as_not_known(tmp_path: Path) -> None:
    row = _event(
        "x-nos-2", "NOS", "earnings", date(2024, 3, 5), None, "weird", "yahoo", utc(2024, 2, 20)
    )
    (item,) = nt.upcoming_events(_lake_with_events(tmp_path, [row]), "NOS")
    assert "2024-03-05 time of day not known, 4 calendar days ahead" in item.text
    assert item.source == "events"


def test_the_exact_article_count_is_not_called_capped(nworld: NewsWorld) -> None:
    items = nt.company_news(nworld.view(config=configured(max_articles=3)), "AAA")  # AAA has 3
    assert "more articles exist" not in items[0].text and len(items) == 4


def test_the_smallest_reaction_keeps_its_sign_and_zero_is_not_positive(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "zero", registry=DEFAULT_REGISTRY.copy())
    write_world(lake, future=False)
    rx = lake.read_table("earnings_reactions")
    rx.loc[rx["event_id"] == "sec-aaa-1", "car"] = 0.0
    lake.write_table("earnings_reactions", rx, mode="upsert")
    cfg = configured(reactions=2)  # 2024-02-01 (0.0%) and 2023-10-31 (+3.0%)
    text = nt.past_reactions(DataView(lake, AS_OF, cfg), "AAA")[0].text
    assert "1 of 2 positive" in text and "smallest +0.0% (2024-02-01)" in text
    assert "largest +3.0% (2023-10-31)" in text


def test_a_peer_without_volume_ranks_last(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "novol", registry=DEFAULT_REGISTRY.copy())
    write_world(lake, future=False)
    bars = lake.read_table("prices_daily")
    lake.write_table("prices_daily", bars[bars["symbol"] != "P01"], mode="overwrite")
    text = nt.sector_news(DataView(lake, AS_OF, AtlasConfig()), "AAA")[0].text
    assert (
        "P02 (1 article), P04 (0 articles), P03 (0 articles), NOS (0 articles), P01 (3 articles)"
        in text
    )


def test_a_tool_of_the_prefetch_that_fails_names_itself(
    nworld: NewsWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(view: DataView, symbol: str) -> list[Evidence]:
        raise DataError("down")

    monkeypatch.setattr(nt, "upcoming_events", boom)
    monkeypatch.setattr(nt, "past_reactions", boom)
    got = [e.id for e in nt.prefetch(nworld.view(), "AAA") if e.id.startswith("gap:")]
    assert got == [
        f"gap:AAA:{DAY}:get_upcoming_events",
        f"gap:AAA:{DAY}:get_past_earnings_reactions",
    ]


def test_the_view_is_asked_for_one_article_more_than_the_cap_and_for_the_peers_share(
    nworld: NewsWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extra article tells "more exist"; a peer's read leaves room for the company's own
    articles that are then left out. Both limits are passed on: the view's default (50) would
    cut a bigger cap."""
    asked: list[tuple[str, int | None, int | None]] = []
    real = DataView.news

    def spy(self: DataView, symbol: str, lookback: int | None = None, limit: int | None = None):  # type: ignore[no-untyped-def]
        asked.append((symbol, lookback, limit))
        return real(self, symbol, lookback, limit)

    monkeypatch.setattr(DataView, "news", spy)
    view = nworld.view(config=configured(max_articles=60, lookback_days=30, sector_peers=2))
    nt.sector_news(view, "AAA")
    assert asked == [
        ("AAA", 30, 61),  # the company's own list: the cap plus one
        ("P01", 30, 3 + 3),  # per-peer cap 3 plus the 3 company articles
        ("P02", 30, 3 + 3),
    ]
