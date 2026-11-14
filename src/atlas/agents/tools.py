"""The tool registry: point-in-time tools that wrap `DataView` methods and return evidence.

A `Tool` reads data only through the `AgentContext`'s `DataView` and returns `Evidence`. The
registry validates the model's arguments against the tool's Pydantic args model, checks that
every returned item was public at the context's `as_of` (`LookAheadError` otherwise: a leak is
a bug and is never handed to the model) and renders the items as `[E:<id>] ...` lines inside a
data block, so the model can cite them.

Errors the model can act on (an unknown tool, invalid arguments, a `DataError` such as a
blocked symbol) come back as an `ERROR: ...` text for the model and are kept in the outcome;
any other exception propagates.
"""

import math
import re
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any

import pandas as pd
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from atlas.agents.context import AgentContext
from atlas.agents.evidence import check_evidence_id, evidence_lines
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import get_logger
from atlas.core.types.instruments import normalize_symbol
from atlas.core.types.research import Evidence
from atlas.data.view import guard_evidence
from atlas.llm.hygiene import data_block
from atlas.llm.schemas import ToolCall, ToolSpec

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
MAX_ERROR_CHARS = 1000
"""An error text for the model is cut to this many characters."""

_log = get_logger(__name__)

ToolFn = Callable[[AgentContext, Any], Sequence[Evidence]]
"""A tool's body: the context and the validated arguments in, evidence out."""


@dataclass(frozen=True)
class Tool:
    """One tool: `name` (letters, digits, `_`, `-`), `description` (for the model), the
    Pydantic `args` model of its arguments, the body `fn` and a `version` (bump it when the
    tool's behaviour changes: it is part of the LLM cache key through the prompt version)."""

    name: str
    description: str
    args: type[BaseModel]
    fn: ToolFn
    version: int = 1

    def __post_init__(self) -> None:
        if not _NAME_RE.fullmatch(self.name):
            raise ValueError(f"tool name {self.name!r}: use letters, digits, _ and -")
        if not self.description.strip():
            raise ValueError(f"tool {self.name!r} needs a description")
        if self.version < 1:
            raise ValueError(f"tool {self.name!r}: version must be at least 1")

    @property
    def spec(self) -> ToolSpec:
        """The tool as offered to the model (JSON Schema of `args`)."""
        return ToolSpec(
            name=self.name, description=self.description, parameters=self.args.model_json_schema()
        )

    @property
    def label(self) -> str:
        """`<name>@v<version>`."""
        return f"{self.name}@v{self.version}"


@dataclass(frozen=True)
class ToolOutcome:
    """The result of one tool call: the `call`, the `evidence` it returned, the `text` sent
    back to the model, and `error` (the message, without the `ERROR: ` prefix) when the call
    could not be served."""

    call: ToolCall
    evidence: tuple[Evidence, ...]
    text: str
    error: str | None = None


def _validation_text(exc: ValidationError) -> str:
    parts = [
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
        for err in exc.errors(include_url=False, include_input=False)
    ]
    return "; ".join(parts)


class ToolRegistry:
    """Tools by name. `call` serves one model tool call for an `AgentContext`."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        """Add `tool`; `ConfigError` if its name is taken."""
        if tool.name in self._tools:
            raise ConfigError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    @property
    def names(self) -> tuple[str, ...]:
        """The registered tool names, sorted."""
        return tuple(sorted(self._tools))

    @property
    def tools(self) -> tuple[Tool, ...]:
        """The registered tools, by name."""
        return tuple(self._tools[name] for name in self.names)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool:
        """The tool `name`; `ConfigError` if there is none."""
        try:
            return self._tools[name]
        except KeyError:
            raise ConfigError(f"unknown tool {name!r}; registered: {list(self.names)}") from None

    def specs(self, names: Sequence[str] | None = None) -> tuple[ToolSpec, ...]:
        """The specs of `names` (default: every tool), in the given order."""
        return tuple(self.get(name).spec for name in (self.names if names is None else names))

    def versions_label(self, names: Iterable[str]) -> str:
        """`<name>@v<version>` of each of `names`, sorted and comma-separated."""
        return ",".join(sorted(self.get(name).label for name in names))

    def call(
        self, ctx: AgentContext, call: ToolCall, *, allowed: Collection[str] | None = None
    ) -> ToolOutcome:
        """Serve `call` for `ctx`. A tool outside `allowed` (default: all) counts as unknown.

        Raises `LookAheadError` when the tool returns evidence published at or after the
        cutoff of `ctx.as_of`, and `ValueError` for an evidence id that cannot be tagged.
        """
        offered = self.names if allowed is None else tuple(n for n in allowed if n in self)
        if call.name not in offered:
            return self._error(call, f"unknown tool {call.name!r}; available: {list(offered)}")
        tool = self._tools[call.name]
        try:
            args = tool.args.model_validate(dict(call.arguments))
        except ValidationError as exc:
            return self._error(call, f"invalid arguments for {tool.name}: {_validation_text(exc)}")
        try:
            items = tuple(tool.fn(ctx, args))
        except DataError as exc:
            return self._error(call, f"{tool.name}: {exc}")
        guard_evidence(list(items), ctx.as_of)  # LookAheadError for anything not yet public
        for item in items:
            check_evidence_id(item.id)
        _log.info("agent.tool_call", tool=tool.name, evidence=len(items), call_id=call.id)
        text = data_block(evidence_lines(items), source=f"tool:{tool.name}")
        return ToolOutcome(call=call, evidence=items, text=text)

    @staticmethod
    def _error(call: ToolCall, message: str) -> ToolOutcome:
        message = message[:MAX_ERROR_CHARS]
        _log.warning("agent.tool_error", tool=call.name, call_id=call.id, error=message)
        return ToolOutcome(call=call, evidence=(), text=f"ERROR: {message}", error=message)


# --- built-in tools over DataView ------------------------------------------------------------

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
    stamp = pd.Timestamp(value)  # type: ignore[arg-type]
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return stamp.to_pydatetime()


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
        f"high {float(bars['high'].max()):.2f}, low {float(bars['low'].min()):.2f} "
        "(raw USD prices)"
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
)


def default_tools() -> ToolRegistry:
    """A registry with the built-in DataView tools: `news`, `market_news`, `fundamentals`,
    `price_history`."""
    return ToolRegistry([NEWS, MARKET_NEWS, FUNDAMENTALS, PRICE_HISTORY])
