"""Stage 29 Task 3: the tools are point in time. The dirty lake adds a quarter filed after the
as-of, later bars and a late split; at the as-of every tool returns exactly what it returns
on the clean lake, and nothing it returns was published at or after the cutoff."""

import pytest
from fundamental_world import AS_OF, FLAGGED, FundamentalWorld

from atlas.agents.tools import fundamental_tools as ft
from atlas.data.view import guard_evidence

SYMBOLS = ("AAA", "CCC", "EEE", "III", FLAGGED, "LLL")


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_later_rows_never_change_what_a_tool_returns(fworld: FundamentalWorld, symbol: str) -> None:
    clean, dirty = fworld.view("clean"), fworld.view("dirty")
    assert ft.prefetch(dirty, symbol) == ft.prefetch(clean, symbol)


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_every_item_was_public_before_the_cutoff(fworld: FundamentalWorld, symbol: str) -> None:
    view = fworld.view("dirty")
    items = ft.prefetch(view, symbol)
    assert guard_evidence(items, view.as_of) == items
    assert all(e.as_of < view.as_of.cutoff for e in items)


def test_a_later_as_of_sees_the_later_quarter(fworld: FundamentalWorld) -> None:
    """The same tools on the dirty lake after the late filing show it (the rows are real)."""
    from datetime import date

    later = fworld.view("dirty", day=date(2024, 5, 8))
    ids = [e.id for e in ft.fundamentals_summary(later, "AAA")]
    assert "fsum:AAA:2024-03-31:revenue" in ids
    assert date(2024, 5, 8) > AS_OF
