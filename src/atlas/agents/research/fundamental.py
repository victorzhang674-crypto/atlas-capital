"""`FundamentalAnalyst`: reads point-in-time fundamentals, factor ranks against its sector,
valuation and red flags, and writes a cited `AnalystReport` (Stage 29).

`run(AgentContext(view, inputs={"symbol": "AAPL"}))` prefetches the four tools' evidence for
the symbol (`fundamental_tools.prefetch`: one sector cross-section, normally one LLM round),
offers the four tools for a peer, and asks the quick tier for an `AnalystReport` whose summary
has six labelled sections (`SECTIONS`) and whose signals are exactly `SIGNALS`.
`check_output` holds the report to that shape and to "every number cited": a sentence of the
summary or a risk that states a number (ISO dates and evidence tags aside) must carry an
`[E:<id>]` tag. Problems get the framework's repair turn; any left make the report
`low_quality` (confidence capped). A blocked symbol, or one with no fundamentals, fails the
run (`DataError`): no report, no new risk.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from atlas.agents.base import BaseAgent
from atlas.agents.context import AgentContext
from atlas.agents.evidence import TAG_RE
from atlas.agents.tools.dataview_tools import default_tools
from atlas.agents.tools.fundamental_tools import FUNDAMENTAL_TOOLS, prefetch
from atlas.agents.tools.registry import ToolRegistry
from atlas.core.errors import ConfigError
from atlas.core.types.instruments import normalize_symbol
from atlas.core.types.research import AnalystReport, Evidence
from atlas.llm.gateway import LLMGateway

SIGNALS = ("quality", "valuation", "growth", "balance_sheet")
"""The signals of a fundamental report, each exactly once."""

SECTIONS = (
    "Business quality",
    "Growth trajectory",
    "Valuation",
    "Balance-sheet risk",
    "Red flags",
    "What would change my mind",
)
"""The labelled sections of the summary, in order (`<label>:` at the start of a line)."""

MEMORY_QUERY = "{symbol} fundamentals: business quality, growth, valuation, balance-sheet risk"
"""What the analyst asks its memory for (Stage 27 recall, bound to the run's as-of)."""

MAX_LISTED = 5
"""A problem lists at most this many offending sentences."""

_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DIGIT = re.compile(r"\d")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


def _section_re(label: str) -> re.Pattern[str]:
    return re.compile(rf"(?:^|\n)[ \t*#_>-]*{re.escape(label)}[ \t*_]*:", re.IGNORECASE)


_SECTION_RES = tuple(_section_re(label) for label in SECTIONS)


def sentences(text: str) -> list[str]:
    """`text` split into sentences (after `.`, `!`, `?` and at line breaks); a piece holding
    only evidence tags is joined to the sentence before it."""
    out: list[str] = []
    for piece in _SENTENCE_END.split(text):
        piece = piece.strip()
        if not piece:
            continue
        if out and not TAG_RE.sub("", piece).strip(" .;,"):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


def uncited_numbers(text: str) -> list[str]:
    """The sentences of `text` that state a number (digits outside evidence tags and ISO
    dates) without an `[E:<id>]` tag."""
    return [
        s
        for s in sentences(text)
        if _DIGIT.search(_ISO_DATE.sub("", TAG_RE.sub("", s))) and not TAG_RE.search(s)
    ]


def missing_sections(summary: str) -> list[str]:
    """The `SECTIONS` labels absent from `summary`, or out of order (each must start a line
    and come after the one before)."""
    missing, position = [], 0
    for label, pattern in zip(SECTIONS, _SECTION_RES, strict=True):
        found = pattern.search(summary, position)
        if found is None:
            missing.append(label)
        else:
            position = found.end()
    return missing


def _quote(sentences_: Sequence[str]) -> str:
    shown = "; ".join(f'"{s[:120]}"' for s in sentences_[:MAX_LISTED])
    more = f" and {len(sentences_) - MAX_LISTED} more" if len(sentences_) > MAX_LISTED else ""
    return shown + more


def fundamental_registry() -> ToolRegistry:
    """A registry with the built-in DataView tools (`default_tools`) and the four tools of
    `FUNDAMENTAL_TOOLS`."""
    return ToolRegistry([*default_tools().tools, *FUNDAMENTAL_TOOLS])


class FundamentalAnalyst(BaseAgent[AnalystReport]):
    """The fundamental analyst (spec §6: LLM + tools, quick tier, `AnalystReport`).

    `ctx.inputs["symbol"]` names the stock. `tool_registry` defaults to
    `fundamental_registry()` (the built-in DataView tools plus the four of Stage 29); the
    other keyword arguments are `BaseAgent`'s.
    """

    name = "fundamental_analyst"
    tier = "quick"
    prompt_template = "fundamental_analyst.j2"
    output_schema = AnalystReport
    tools = tuple(tool.name for tool in FUNDAMENTAL_TOOLS)

    def __init__(self, gateway: LLMGateway, **kwargs: Any) -> None:
        if kwargs.get("tool_registry") is None:
            kwargs["tool_registry"] = fundamental_registry()
        super().__init__(gateway, **kwargs)

    @staticmethod
    def symbol(ctx: AgentContext) -> str:
        """The run's stock, normalised; `ConfigError` without one."""
        raw = ctx.inputs.get("symbol")
        if not isinstance(raw, str) or not raw.strip():
            raise ConfigError("fundamental_analyst needs inputs['symbol'] (a ticker)")
        return normalize_symbol(raw)

    def gather(self, ctx: AgentContext) -> Sequence[Evidence]:
        return prefetch(ctx.view, self.symbol(ctx))

    def prompt_inputs(self, ctx: AgentContext) -> Mapping[str, Any]:
        symbol = self.symbol(ctx)
        memories = ctx.recall(self.name, MEMORY_QUERY.format(symbol=symbol), symbol=symbol)
        return {"symbol": symbol, "memories": memories}

    def check_output(self, ctx: AgentContext, output: AnalystReport) -> Sequence[str]:
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
        missing = missing_sections(output.summary)
        if missing:
            problems.append(
                "the summary needs these sections, in order, each starting a line with its "
                "label and a colon: " + ", ".join(f'"{m}:"' for m in missing)
            )
        uncited = uncited_numbers(output.summary)
        for i, risk in enumerate(output.risks):
            uncited += [f"risks[{i}]: {s}" for s in uncited_numbers(risk)]
        if uncited:
            problems.append(
                "every sentence that states a number needs an [E:<id>] tag of its evidence; "
                f"these have none: {_quote(uncited)}"
            )
        return problems


__all__ = [
    "MEMORY_QUERY",
    "SECTIONS",
    "SIGNALS",
    "FundamentalAnalyst",
    "fundamental_registry",
    "missing_sections",
    "sentences",
    "uncited_numbers",
]
