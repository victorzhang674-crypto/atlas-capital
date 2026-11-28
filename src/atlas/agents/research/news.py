"""`NewsAnalyst`: reads one stock's recent news, its sector peers' news, its upcoming events and
its past earnings reactions, and writes a cited `NewsReport` (Stage 31).

`run(AgentContext(view, inputs={"symbol": "AAPL"}))` prefetches the four tools' evidence for the
symbol (`news_tools.prefetch`), offers the tools for a peer, and asks the quick tier for a
`NewsReport` (an `AnalystReport` plus `catalysts`: each with an optional date, a description,
the one evidence id it rests on and its expected impact). The summary has six labelled sections
(`SECTIONS`); the signals are exactly `SIGNALS`.

`check_output` holds the report to that shape, to "every number cited" (Stage 29's rule) and to
the catalyst rules (`catalyst_problems`): at most `max_catalysts`; a date on or after the as-of
day; an event's own date when the catalyst rests on an event; evidence that is an article, an
event or a reaction, never a coverage, summary or gap item. `check_evidence` holds each
description's numbers to the item it cites. Problems get the framework's repair turn; any left
make the report `low_quality` (confidence capped).

The empty-news guard (the brief): with no company article (or no news table: a gap), the
report must say so, its `news_flow` must be FLAT, and its confidence is at most
`empty_news_confidence_cap` (`agents.news`). `check_output` asks for it (repair turn);
`finalize` enforces the cap whatever the model answered. A blocked symbol fails the run
(`DataError`): no report, no new risk.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from atlas.agents.base import BaseAgent
from atlas.agents.context import AgentContext
from atlas.agents.evidence import EvidenceLedger
from atlas.agents.research import checks
from atlas.agents.tools.dataview_tools import default_tools
from atlas.agents.tools.news_tools import NEWS_TOOLS, company_article_count, prefetch
from atlas.agents.tools.registry import ToolRegistry
from atlas.core.config import NewsAnalystConfig
from atlas.core.errors import ConfigError
from atlas.core.types.instruments import normalize_symbol
from atlas.core.types.research import Direction, Evidence, NewsCatalyst, NewsReport
from atlas.llm.gateway import LLMGateway

SIGNALS = ("news_flow", "catalysts", "event_risk")
"""The signals of a news report, each exactly once."""

SECTIONS = (
    "Material vs noise",
    "Catalysts",
    "Risk events",
    "Narrative shifts",
    "What the market may be missing",
    "Event calendar",
)
"""The labelled sections of the summary, in order (`<label>:` at the start of a line)."""

MEMORY_QUERY = "{symbol} news and events: catalysts, risks, narrative shifts, earnings"
"""What the analyst asks its memory for (Stage 27 recall, bound to the run's as-of)."""

CATALYST_EVIDENCE = ("news:", "evt:", "rx:")
"""The evidence a catalyst may rest on: an article, an event or a reaction."""

_EVENT_DAY = re.compile(r"^evt:[^:]+:[^:]+:(\d{4}-\d{2}-\d{2})$")
_NO_NEWS = re.compile(
    r"\b(?:no|zero|0)\s+(?:[a-z-]+\s+){0,3}?(?:news|articles?|headlines?|stories|coverage)\b",
    re.IGNORECASE,
)


def says_no_news(text: str) -> bool:
    """Whether `text` says there is no news ("no news articles", "0 articles", "zero recent
    headlines": a no-word within three words of a news noun)."""
    return _NO_NEWS.search(text) is not None


def news_registry() -> ToolRegistry:
    """A registry with the built-in DataView tools (`default_tools`) and the four tools of
    `NEWS_TOOLS`."""
    return ToolRegistry([*default_tools().tools, *NEWS_TOOLS])


def catalyst_problems(catalyst: NewsCatalyst, index: int, as_of: date) -> list[str]:
    """What is wrong with one catalyst: evidence that is not an article, event or reaction; a
    date before `as_of`; no date or another date than the event's own when it rests on an
    event."""
    name = f"catalysts[{index}]"
    problems = []
    evidence_id = catalyst.evidence_id
    if not evidence_id.startswith(CATALYST_EVIDENCE) or evidence_id.endswith(":none"):
        problems.append(
            f"{name}: evidence_id {evidence_id} must be a news:, evt: or rx: item (an article, "
            "an event or a reaction), not a coverage, summary, none or gap item"
        )
    if catalyst.date is not None and catalyst.date < as_of:
        problems.append(
            f"{name}: date {catalyst.date.isoformat()} is before the as-of day "
            f"{as_of.isoformat()}: a catalyst is ahead, or has no date"
        )
    event = _EVENT_DAY.match(evidence_id)
    if event is not None and (catalyst.date is None or catalyst.date.isoformat() != event[1]):
        problems.append(
            f"{name}: it rests on {evidence_id}, so its date must be {event[1]} "
            f"(not {catalyst.date.isoformat() if catalyst.date else 'null'})"
        )
    return problems


class NewsAnalyst(BaseAgent[NewsReport]):
    """The news and event analyst (spec §6: LLM reading evidence, quick tier, an
    `AnalystReport`; the brief's `catalysts` ride in its `NewsReport`).

    `ctx.inputs["symbol"]` names the stock. `tool_registry` defaults to `news_registry()`; the
    other keyword arguments are `BaseAgent`'s.
    """

    name = "news_analyst"
    tier = "quick"
    prompt_template = "news_analyst.j2"
    output_schema = NewsReport
    tools = tuple(tool.name for tool in NEWS_TOOLS)

    def __init__(self, gateway: LLMGateway, **kwargs: Any) -> None:
        if kwargs.get("tool_registry") is None:
            kwargs["tool_registry"] = news_registry()
        super().__init__(gateway, **kwargs)

    @staticmethod
    def symbol(ctx: AgentContext) -> str:
        """The run's stock, normalised; `ConfigError` without one."""
        raw = ctx.inputs.get("symbol")
        if not isinstance(raw, str) or not raw.strip():
            raise ConfigError("news_analyst needs inputs['symbol'] (a ticker)")
        return normalize_symbol(raw)

    def gather(self, ctx: AgentContext) -> Sequence[Evidence]:
        return prefetch(ctx.view, self.symbol(ctx))

    def prompt_inputs(self, ctx: AgentContext) -> Mapping[str, Any]:
        symbol = self.symbol(ctx)
        settings = ctx.view.config.agents.news
        memories = ctx.recall(self.name, MEMORY_QUERY.format(symbol=symbol), symbol=symbol)
        return {
            "symbol": symbol,
            "memories": memories,
            "max_catalysts": settings.max_catalysts,
            "lookback_days": settings.lookback_days,
            "empty_cap": f"{settings.empty_news_confidence_cap:g}",
        }

    def _no_news(self, ctx: AgentContext) -> bool:
        return company_article_count(ctx.view, self.symbol(ctx)) == 0

    def check_output(self, ctx: AgentContext, output: NewsReport) -> Sequence[str]:
        symbol = self.symbol(ctx)
        settings = ctx.view.config.agents.news
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
        if len(output.catalysts) > settings.max_catalysts:
            problems.append(
                f"at most {settings.max_catalysts} catalysts, not {len(output.catalysts)}: "
                "keep the ones that matter most"
            )
        for i, catalyst in enumerate(output.catalysts):
            problems += catalyst_problems(catalyst, i, ctx.day)
        if self._no_news(ctx):
            problems += self._empty_news_problems(output, settings)
        return problems

    @staticmethod
    def _empty_news_problems(output: NewsReport, settings: NewsAnalystConfig) -> list[str]:
        cap = settings.empty_news_confidence_cap
        problems = []
        if not says_no_news(output.summary):
            problems.append(
                "there is no company news in the window (its coverage item says 0 articles): "
                'say so plainly in "Material vs noise:" (for example "There are no news '
                'articles about this stock in the last weeks")'
            )
        if output.confidence > cap:
            problems.append(
                f"with no company news the confidence must be at most {cap:g}, not "
                f"{output.confidence:g}"
            )
        for signal in output.signals:
            if signal.name == "news_flow" and signal.direction is not Direction.FLAT:
                problems.append(
                    "with no company news the news_flow signal must be FLAT, not "
                    f"{signal.direction.value}"
                )
        return problems

    def check_evidence(
        self, ctx: AgentContext, output: NewsReport, evidence: EvidenceLedger
    ) -> Sequence[str]:
        texts = {item.id: item.text for item in evidence.items}
        described = [f"{c.description} [E:{c.evidence_id}]" for c in output.catalysts]
        return checks.grounding_problems(
            output.summary, output.risks, texts, {"catalysts": described}
        )

    def finalize(self, ctx: AgentContext, output: NewsReport) -> NewsReport:
        """The guard's ceiling: with no company news the confidence is at most
        `empty_news_confidence_cap`, whatever the model answered."""
        cap = ctx.view.config.agents.news.empty_news_confidence_cap
        if output.confidence > cap and self._no_news(ctx):
            return output.model_copy(update={"confidence": cap})
        return output


__all__ = [
    "CATALYST_EVIDENCE",
    "MEMORY_QUERY",
    "SECTIONS",
    "SIGNALS",
    "NewsAnalyst",
    "catalyst_problems",
    "news_registry",
    "says_no_news",
]
