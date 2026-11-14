"""Stage 26 Task 3: the built-in DataView tools on the Stage 13 world, and the leak test.

The dirty lake holds, besides everything the clean lake holds, rows in every table that
became known only after the as-of (2024-03-01 08:00 New York): later news, a 10-K filed on
2024-03-04, bars from 2024-03-01 on, a dividend recorded late. Tools see the data only
through the context's `DataView`, so on the dirty lake they must return exactly what they
return on the clean one.
"""

import pytest
from agent_world import World
from view_helpers import WORLD_LATER

from atlas.agents.tools import default_tools
from atlas.llm.schemas import ToolCall

FUTURE_NEWS = {"news:alpaca-10", "news:alpaca-11", "news:alpaca-12", "news:alpaca-13"}

CASES = [
    ("news", {"symbol": "AAA"}),
    ("news", {"symbol": "AAA", "lookback_days": 3}),
    ("news", {"symbol": "AAA", "limit": 1}),
    ("market_news", {}),
    ("fundamentals", {"symbol": "AAA"}),
    ("fundamentals", {"symbol": "BBB"}),
    ("price_history", {"symbol": "AAA"}),
    ("price_history", {"symbol": "CCC", "lookback": 5}),
    ("price_history", {"symbol": "BBB", "lookback": 30}),
]


def run(world: World, which: str, name: str, args: dict[str, object], **kw: object) -> object:
    return default_tools().call(world.ctx(which, **kw), ToolCall(id="c", name=name, arguments=args))


@pytest.mark.parametrize(("name", "args"), CASES)
def test_tools_cannot_see_beyond_as_of(world: World, name: str, args: dict[str, object]) -> None:
    clean = run(world, "clean", name, args)
    dirty = run(world, "dirty", name, args)
    assert dirty == clean
    assert dirty.error is None and dirty.evidence
    cutoff = world.view("dirty").as_of
    assert all(cutoff.knows(e.as_of) for e in dirty.evidence)
    assert not FUTURE_NEWS & {e.id for e in dirty.evidence}
    assert "2024-03-04" not in dirty.text and "2024-03-01" not in dirty.text.split("Z ", 1)[0]


def test_the_leak_test_can_see_a_leak(world: World) -> None:
    # At a later as-of the dirty lake's late rows are known: the same calls now differ.
    later = run(world, "dirty", "news", {"symbol": "AAA"}, day=WORLD_LATER)
    assert "news:alpaca-12" in {e.id for e in later.evidence}
    fund = run(world, "dirty", "fundamentals", {"symbol": "AAA"}, day=WORLD_LATER)
    assert fund != run(world, "clean", "fundamentals", {"symbol": "AAA"})


def test_news_tool(world: World) -> None:
    out = run(world, "clean", "news", {"symbol": "AAA"})
    assert [e.id for e in out.evidence] == ["news:alpaca-2", "news:alpaca-1"]
    assert "[E:news:alpaca-2] 2024-02-29 20:00Z AAA improved profitability" in out.text
    assert out.text.startswith('<data source="tool:news">')


def test_market_news_tool(world: World) -> None:
    out = run(world, "clean", "market_news", {})
    assert [e.id for e in out.evidence] == ["news:alpaca-3"]


def test_fundamentals_tool(world: World) -> None:
    out = run(world, "clean", "fundamentals", {"symbol": "AAA"})
    ids = {e.id for e in out.evidence}
    assert "fund:AAA:2023-12-31:revenue_ttm" in ids
    revenue = next(e for e in out.evidence if e.id == "fund:AAA:2023-12-31:revenue_ttm")
    assert "AAA revenue_ttm = 4.3e+08 (period ending 2023-12-31)" in revenue.text
    view = world.view("clean")
    values = view.fundamentals("AAA")
    for item in out.evidence:
        metric = item.id.rsplit(":", 1)[1]
        assert metric in values.index


def test_price_history_tool(world: World) -> None:
    out = run(world, "clean", "price_history", {"symbol": "AAA", "lookback": 5})
    (item,) = out.evidence
    assert item.id == "px:AAA:2024-02-29:5d"
    bars = world.view("clean").prices(["AAA"], 5)
    assert f"last close {bars['close'].iloc[-1]:.2f} USD" in item.text
    ret = bars["adj_close"].iloc[-1] / bars["adj_close"].iloc[0] - 1
    assert f"total return {ret:+.2%}" in item.text
    assert "2024-02-23..2024-02-29 (5 sessions)" in item.text


def test_blocked_symbol_is_an_error_text_or_no_data(world: World) -> None:
    news = run(world, "clean", "news", {"symbol": "BAD"})
    assert news.error is not None and "BAD" in news.error
    prices = run(world, "clean", "price_history", {"symbol": "BAD"})
    assert prices.evidence == () and "(no data)" in prices.text


@pytest.mark.parametrize("name", ["news", "fundamentals", "price_history"])
def test_a_malformed_symbol_is_an_error_text(world: World, name: str) -> None:
    out = run(world, "clean", name, {"symbol": "a b!"})
    assert out.error is not None and f"invalid arguments for {name}: symbol" in out.error


def test_symbols_are_normalised(world: World) -> None:
    lower = run(world, "clean", "fundamentals", {"symbol": " aaa "})
    assert lower == run(world, "clean", "fundamentals", {"symbol": "AAA"}).__class__(
        call=lower.call, evidence=lower.evidence, text=lower.text
    )
    assert all(e.id.startswith("fund:AAA:") for e in lower.evidence)
