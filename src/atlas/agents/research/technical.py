"""`TechnicalAnalyst`: interprets one stock's precomputed technical features, prices, levels and
the market regime, and writes a cited `TechnicalReport` (Stage 30).

`run(AgentContext(view, inputs={"symbol": "AAPL"}))` prefetches both tools' evidence for the
symbol (`technical_tools.prefetch`: price summary, feature snapshot, market regime), offers
the two tools for a peer, and asks the quick tier for a `TechnicalReport` (an `AnalystReport`
whose signals carry `timing` and `suggested_stop_pct`). The summary has six labelled sections
(`SECTIONS`); the signals are exactly `SIGNALS`.

`inputs["regime"]` may hold the day's `RegimeState` (the fund graph's `FundState.regime`);
without it the regime is computed with `regime_at` from the view. A regime of another day is
a `ConfigError` before any LLM call.

`check_output` holds the report to that shape, to "every number cited" (Stage 29's rule) and
to the stop and timing rules (`stop_problems`): a LONG or SHORT signal needs a stop within
[`min_stop_pct`, `max_stop_pct`] (`agents.technical`); a FLAT signal has no stop and timing
`AVOID`. Problems get the framework's repair turn; any left make the report `low_quality`
(confidence capped). A blocked symbol, or one with no bars, fails the run (`DataError`): no
report, no new risk.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from atlas.agents.base import BaseAgent
from atlas.agents.context import AgentContext
from atlas.agents.research import checks
from atlas.agents.tools.dataview_tools import default_tools
from atlas.agents.tools.registry import ToolRegistry
from atlas.agents.tools.technical_tools import TECHNICAL_TOOLS, given_regime, prefetch
from atlas.core.config import TechnicalAnalystConfig
from atlas.core.errors import ConfigError
from atlas.core.types.instruments import normalize_symbol
from atlas.core.types.research import Direction, Evidence, TechnicalReport, TechnicalSignal, Timing
from atlas.llm.gateway import LLMGateway

SIGNALS = ("trend", "momentum", "volatility", "levels")
"""The signals of a technical report, each exactly once."""

SECTIONS = (
    "Trend",
    "Momentum",
    "Volatility regime",
    "Levels",
    "Timing and entry",
    "Invalidation",
)
"""The labelled sections of the summary, in order (`<label>:` at the start of a line)."""

MEMORY_QUERY = "{symbol} technicals: trend, momentum, volatility, levels, entry timing"
"""What the analyst asks its memory for (Stage 27 recall, bound to the run's as-of)."""


def technical_registry() -> ToolRegistry:
    """A registry with the built-in DataView tools (`default_tools`) and the two tools of
    `TECHNICAL_TOOLS`."""
    return ToolRegistry([*default_tools().tools, *TECHNICAL_TOOLS])


def stop_problems(signal: TechnicalSignal, settings: TechnicalAnalystConfig) -> list[str]:
    """What is wrong with `signal`'s stop and timing: a LONG or SHORT signal needs a
    `suggested_stop_pct` in [`min_stop_pct`, `max_stop_pct`]; a FLAT one has none and timing
    `AVOID`."""
    low, high = settings.min_stop_pct, settings.max_stop_pct
    stop = signal.suggested_stop_pct
    if signal.direction is Direction.FLAT:
        problems = []
        if stop is not None:
            problems.append(
                f"signal {signal.name} is FLAT: its suggested_stop_pct must be null "
                "(no position, no stop)"
            )
        if signal.timing is not Timing.AVOID:
            problems.append(
                f"signal {signal.name} is FLAT: its timing must be AVOID, not {signal.timing.value}"
            )
        return problems
    if stop is None:
        return [
            f"signal {signal.name} is {signal.direction.value} and needs a suggested_stop_pct "
            f"between {low:g} and {high:g} (a fraction of the last close)"
        ]
    if not low <= stop <= high:
        return [
            f"signal {signal.name}: suggested_stop_pct {stop:g} is outside [{low:g}, {high:g}] "
            "(a fraction of the last close: 0.08 = 8%)"
        ]
    return []


class TechnicalAnalyst(BaseAgent[TechnicalReport]):
    """The technical analyst (spec §6: LLM reading computed features, quick tier, an
    `AnalystReport`; the brief's `timing` and `suggested_stop_pct` ride in its signals).

    `ctx.inputs["symbol"]` names the stock; `ctx.inputs["regime"]` (optional) the day's
    `RegimeState`. `tool_registry` defaults to `technical_registry()`; the other keyword
    arguments are `BaseAgent`'s.
    """

    name = "technical_analyst"
    tier = "quick"
    prompt_template = "technical_analyst.j2"
    output_schema = TechnicalReport
    tools = tuple(tool.name for tool in TECHNICAL_TOOLS)

    def __init__(self, gateway: LLMGateway, **kwargs: Any) -> None:
        if kwargs.get("tool_registry") is None:
            kwargs["tool_registry"] = technical_registry()
        super().__init__(gateway, **kwargs)

    @staticmethod
    def symbol(ctx: AgentContext) -> str:
        """The run's stock, normalised; `ConfigError` without one."""
        raw = ctx.inputs.get("symbol")
        if not isinstance(raw, str) or not raw.strip():
            raise ConfigError("technical_analyst needs inputs['symbol'] (a ticker)")
        return normalize_symbol(raw)

    def gather(self, ctx: AgentContext) -> Sequence[Evidence]:
        return prefetch(ctx.view, self.symbol(ctx), given_regime(ctx))

    def prompt_inputs(self, ctx: AgentContext) -> Mapping[str, Any]:
        symbol = self.symbol(ctx)
        settings = ctx.view.config.agents.technical
        memories = ctx.recall(self.name, MEMORY_QUERY.format(symbol=symbol), symbol=symbol)
        return {
            "symbol": symbol,
            "memories": memories,
            "min_stop": f"{settings.min_stop_pct:g}",
            "max_stop": f"{settings.max_stop_pct:g}",
        }

    def check_output(self, ctx: AgentContext, output: TechnicalReport) -> Sequence[str]:
        symbol = self.symbol(ctx)
        problems = []
        if output.ticker != symbol:
            problems.append(f"ticker must be {symbol}, not {output.ticker}")
        if output.as_of != ctx.day:
            problems.append(f"as_of must be {ctx.day.isoformat()}, not {output.as_of.isoformat()}")
        if output.analyst != self.name:
            problems.append(f'analyst must be "{self.name}", not "{output.analyst}"')
        names = [s.name for s in output.signals]
        if sorted(names) != sorted(SIGNALS):
            problems.append(
                "signals must be exactly "
                + ", ".join(SIGNALS)
                + " (each once); got "
                + (", ".join(names) or "none")
            )
        missing = checks.missing_sections(output.summary, SECTIONS)
        if missing:
            problems.append(
                "the summary needs these sections, in order, each starting a line with its "
                "label and a colon: " + ", ".join(f'"{m}:"' for m in missing)
            )
        uncited = checks.uncited_numbers(output.summary)
        for i, risk in enumerate(output.risks):
            uncited += [f"risks[{i}]: {s}" for s in checks.uncited_numbers(risk)]
        if uncited:
            problems.append(
                "every sentence that states a number needs an [E:<id>] tag of its evidence; "
                f"these have none: {checks.quote(uncited)}"
            )
        settings = ctx.view.config.agents.technical
        for signal in output.signals:
            problems += stop_problems(signal, settings)
        return problems


__all__ = [
    "MEMORY_QUERY",
    "SECTIONS",
    "SIGNALS",
    "TechnicalAnalyst",
    "stop_problems",
    "technical_registry",
]
