"""Stage 31 Task 7: the news tools are point in time. The dirty lake adds articles (and a
correction of AAA's first one), a later earnings schedule, a report on 2024-03-04 and its
reaction, all known only after the as-of: nothing of it may show, and no item may be timed after
the decision cutoff."""

from datetime import date

import pytest
from news_world import AS_OF, MEMBERS, NewsWorld

from atlas.agents.tools import news_tools as nt
from atlas.core.config import AtlasConfig
from atlas.data.view import DataView


@pytest.mark.parametrize("symbol", ["AAA", "P01", "P02", "NOS", "H01"])
def test_rows_known_later_never_show(nworld: NewsWorld, symbol: str) -> None:
    clean = nt.prefetch(nworld.view("clean"), symbol)
    dirty = nt.prefetch(nworld.view("dirty"), symbol)
    assert dirty == clean


def test_no_item_is_timed_after_the_cutoff(nworld: NewsWorld) -> None:
    view = nworld.view("dirty")
    for symbol in MEMBERS:
        for item in nt.prefetch(view, symbol):
            assert item.as_of < view.as_of.cutoff, item.id


def test_the_correction_shows_only_once_it_is_public(nworld: NewsWorld) -> None:
    def summary(day: date) -> str:
        view = DataView(nworld.dirty, day, AtlasConfig())
        second = next(e for e in nt.company_news(view, "AAA") if e.id == "news:alpaca-a2")
        return second.text

    assert "former treasurer" in summary(AS_OF)  # the version known at the as-of
    # the correction, public from 2024-03-01 14:00Z
    assert "former auditor" in summary(date(2024, 3, 4))


def test_a_later_view_sees_the_later_rows(nworld: NewsWorld) -> None:
    """The dirty rows are real: a view after them reads them (the tests above are not empty)."""
    later = DataView(nworld.dirty, date(2024, 3, 6), AtlasConfig())
    news = [e.id for e in nt.company_news(later, "AAA")]
    assert "news:alpaca-a7" in news and "news:alpaca-a8" in news
    monday = DataView(nworld.dirty, date(2024, 3, 4), AtlasConfig())
    events = [e.id for e in nt.upcoming_events(monday, "AAA")]
    assert "evt:AAA:earnings:2024-03-08" in events  # the later schedule
    reactions = [
        e.id
        for e in nt.past_reactions(DataView(nworld.dirty, date(2024, 3, 8), AtlasConfig()), "AAA")
    ]
    assert "rx:AAA:2024-03-04" in reactions
