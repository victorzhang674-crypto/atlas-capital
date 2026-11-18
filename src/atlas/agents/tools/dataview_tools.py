"""The built-in point-in-time tools over `DataView`: `news`, `market_news`, `fundamentals`,
`price_history`, and `default_tools()`, a registry holding them.

Each tool reads only through the context's `DataView` and returns `Evidence` with a stable id;
the registry (`registry.py`) checks the arguments, the point-in-time rule and the ids.
"""

import math
from collections.abc import Sequence
from datetime import date, datetime
from typing import Annotated

import pandas as pd
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from atlas.agents.context import AgentContext
from atlas.agents.tools.registry import Tool, ToolRegistry
from atlas.core.errors import DataError
from atlas.core.types.instruments import normalize_symbol
from atlas.core.types.research import Evidence

_Symbol = Annotated[
    str,
    Field(min_length=1, max_length=20, description="Ticker symbol, for example AAPL"),
    AfterValidator(normalize_symbol),
]
"""A ticker argument, normalised (`normalize_symbol`); a malformed one fails validation, so
the model gets an error text instead of the run crashing."""


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NewsArgs(_Args):
    """Arguments of the `news` tool."""

    symbol: _Symbol
    lookback_days: int | None = Field(
        default=None, ge=1, le=3650, description="Calendar days to look back (default: config)"
    )
    limit: int | None = Field(default=None, ge=1, le=100, description="Most articles to return")


class MarketNewsArgs(_Args):
    """Arguments of the `market_news` tool."""

    lookback_days: int | None = Field(
        default=None, ge=1, le=3650, description="Calendar days to look back (default: config)"
    )
    limit: int | None = Field(default=None, ge=1, le=100, description="Most articles to return")


class SymbolArgs(_Args):
    """Arguments of a one-symbol tool."""

    symbol: _Symbol


class PriceHistoryArgs(_Args):
    """Arguments of the `price_history` tool."""

    symbol: _Symbol
    lookback: int = Field(default=21, ge=2, le=756, description="Sessions to look back")


def _news(ctx: AgentContext, args: NewsArgs) -> Sequence[Evidence]:
    return ctx.view.news(args.symbol, args.lookback_days, args.limit)


def _market_news(ctx: AgentContext, args: MarketNewsArgs) -> Sequence[Evidence]:
    return ctx.view.market_news(args.lookback_days, args.limit)


def _day(value: object) -> date:
    stamp = pd.Timestamp(value)  # type: ignore[arg-type]
    return stamp.date()


def _instant(value: object) -> datetime:
    """A lake `available_at` (always tz-aware UTC) as an aware `datetime`."""
    return pd.Timestamp(value).tz_convert("UTC").to_pydatetime()  # type: ignore[arg-type]


def _fundamentals(ctx: AgentContext, args: SymbolArgs) -> Sequence[Evidence]:
    detail = ctx.view.fundamentals_detail(args.symbol)
    symbol = args.symbol
    items = []
    for row in detail.to_dict(orient="records"):
        value = float(row["value"])
        if not math.isfinite(value) or row["source"] == "stale" or pd.isna(row["period_end"]):
            continue
        period_end, metric = _day(row["period_end"]), row["metric"]
        items.append(
            Evidence(
                id=f"fund:{symbol}:{period_end.isoformat()}:{metric}",
                source="sec_edgar",
                as_of=_instant(row["available_at"]),
                text=f"{symbol} {metric} = {value:.6g} (period ending {period_end})",
            )
        )
    return items


def _price_history(ctx: AgentContext, args: PriceHistoryArgs) -> Sequence[Evidence]:
    if args.symbol in ctx.view.blocked:  # `prices` would silently leave it out
        raise DataError(f"symbol {args.symbol} is blocked by a critical data-quality issue")
    bars = ctx.view.prices([args.symbol], args.lookback)
    if bars.empty:
        return []
    symbol = str(bars["symbol"].iloc[0])
    first, last = _day(bars["date"].iloc[0]), _day(bars["date"].iloc[-1])
    n = len(bars)
    total = float(bars["adj_close"].iloc[-1] / bars["adj_close"].iloc[0] - 1.0)
    text = (
        f"{symbol} daily bars {first}..{last} ({n} sessions): last close "
        f"{float(bars['close'].iloc[-1]):.2f} USD; total return {total:+.2%} (adjusted close "
        f"{float(bars['adj_close'].iloc[0]):.2f} -> {float(bars['adj_close'].iloc[-1]):.2f}); "
        f"adjusted close range {float(bars['adj_close'].min()):.2f} to "
        f"{float(bars['adj_close'].max()):.2f} (USD; adjusted for splits and dividends, so "
        "comparable across the window)"
    )
    return [
        Evidence(
            id=f"px:{symbol}:{last.isoformat()}:{n}d",
            source="prices",
            as_of=_instant(bars["available_at"].max()),
            text=text,
        )
    ]


NEWS = Tool(
    name="news",
    description="News articles about one stock known today, newest first.",
    args=NewsArgs,
    fn=_news,
)
MARKET_NEWS = Tool(
    name="market_news",
    description="Market-wide news articles known today, newest first.",
    args=MarketNewsArgs,
    fn=_market_news,
)
FUNDAMENTALS = Tool(
    name="fundamentals",
    description=(
        "The latest known fundamentals of one stock (TTM flows, balance-sheet items, ratios), "
        "each with its fiscal period end."
    ),
    args=SymbolArgs,
    fn=_fundamentals,
)
PRICE_HISTORY = Tool(
    name="price_history",
    description="A summary of one stock's daily prices over the last sessions known today.",
    args=PriceHistoryArgs,
    fn=_price_history,
    version=2,  # v2 (audit A8, A9): blocked symbols are an error; split-adjusted range
)


def default_tools() -> ToolRegistry:
    """A registry with the built-in DataView tools: `news`, `market_news`, `fundamentals`,
    `price_history`."""
    return ToolRegistry([NEWS, MARKET_NEWS, FUNDAMENTALS, PRICE_HISTORY])


__all__ = [
    "FUNDAMENTALS",
    "MARKET_NEWS",
    "NEWS",
    "PRICE_HISTORY",
    "MarketNewsArgs",
    "NewsArgs",
    "PriceHistoryArgs",
    "SymbolArgs",
    "default_tools",
]
