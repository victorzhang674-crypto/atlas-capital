"""Stage 30 Task 7: the technical tools are point in time. The dirty lake adds bars to
2024-03-15 and a 4:1 UPP split known only after the as-of: nothing of it may
show, and no item may be timed after the decision cutoff."""

from datetime import date

import pytest
from technical_world import CONFIG, MEMBERS, TechnicalWorld

from atlas.agents.tools import technical_tools as tt


@pytest.mark.parametrize("symbol", ["UPP", "SPL", "NEW", "M07"])
def test_rows_known_later_never_show(tworld: TechnicalWorld, symbol: str) -> None:
    clean = tt.prefetch(tworld.view("clean"), symbol)
    dirty = tt.prefetch(tworld.view("dirty"), symbol)
    assert dirty == clean


def test_no_item_is_timed_after_the_cutoff(tworld: TechnicalWorld) -> None:
    view = tworld.view("dirty")
    for symbol in MEMBERS:
        for item in tt.prefetch(view, symbol):
            assert item.as_of < view.as_of.cutoff, item.id


def test_a_later_view_sees_the_later_rows(tworld: TechnicalWorld) -> None:
    """The dirty rows are real: a view after them reads them (the test above is not empty)."""
    wide = CONFIG.model_copy(
        update={
            "agents": CONFIG.agents.model_copy(
                update={
                    "technical": CONFIG.agents.technical.model_copy(update={"ohlc_sessions": 15})
                }
            )
        }
    )
    later = tworld.view("dirty", day=date(2024, 3, 18), config=wide)
    ohlc = tt.price_summary(later, "UPP")[0]
    assert ohlc.id == "pxs:UPP:2024-03-15:ohlc"
    clean = tt.price_summary(tworld.view("clean"), "UPP")[0].text
    old_close = float(clean.rsplit(" C ", 1)[1].split()[0])
    # the 4:1 split known by 2024-03-18 rescales 2024-02-29's bar to today's shares
    row = next(r for r in ohlc.text.split("; ") if r.startswith("2024-02-29"))
    assert f" C {old_close / 4:.2f} " in row
