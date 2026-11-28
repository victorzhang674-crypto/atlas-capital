"""Stage 31 Task 4: the news test world builds, and `DataView` reads what the tools will."""

from datetime import date

from news_world import (
    AS_OF,
    LAST_EIGHT,
    PEERS,
    PEERS_BY_ADV,
    REACTIONS,
    NewsWorld,
)


def test_the_view_reads_the_companys_own_articles(nworld: NewsWorld) -> None:
    ids = [e.id for e in nworld.view().news("AAA", 21, 50)]
    # the copy (yahoo-a1b), the roundup (a4) and the article outside the window (a5) are gone
    assert sorted(ids) == ["news:alpaca-a1", "news:alpaca-a2", "news:alpaca-a6"]


def test_the_dirty_lake_adds_only_rows_known_later(nworld: NewsWorld) -> None:
    clean = nworld.view("clean").news("AAA", 21, 50)
    dirty = nworld.view("dirty").news("AAA", 21, 50)
    assert dirty == clean  # later articles and the correction never show


def test_the_view_reads_events_and_reactions(nworld: NewsWorld) -> None:
    events = nworld.view().events(["AAA"], 30)
    company = events.loc[events["symbol"] == "AAA"]
    assert sorted(zip(company["event_type"], company["event_date"], strict=True)) == [
        ("earnings", date(2024, 3, 7)),
        ("ex_dividend", date(2024, 3, 14)),
    ]
    assert (events["symbol"].isna()).any()  # the CPI release comes with it: macro rows
    reactions = nworld.view().earnings_reactions("AAA", 8)
    assert len(reactions) == 8
    assert [round(c, 3) for c in reactions["car"]] == [r[4] for r in reversed(LAST_EIGHT)]
    assert len(REACTIONS) == 11


def test_the_view_ranks_the_peers_by_dollar_volume(nworld: NewsWorld) -> None:
    adv = nworld.view().adv(list(PEERS))["adv_usd"].sort_values(ascending=False)
    assert tuple(adv.index) == PEERS_BY_ADV


def test_the_world_holds_what_the_docstring_says(nworld: NewsWorld) -> None:
    view = nworld.view()
    assert view.day == AS_OF
    assert view.instrument("AAA").sector == "Information Technology"
    assert view.instrument("NOSEC").sector is None
    assert "NOSEC" in view.universe("sp500") and "SPY" not in view.universe("sp500")
    assert view.news("NOS", 21, 50) == []


def test_later_events_and_reactions_never_show_in_the_dirty_lake(nworld: NewsWorld) -> None:
    clean, dirty = nworld.view("clean"), nworld.view("dirty")
    assert dirty.events(["AAA"], 30).equals(clean.events(["AAA"], 30))
    assert dirty.earnings_reactions("AAA", 8).equals(clean.earnings_reactions("AAA", 8))
