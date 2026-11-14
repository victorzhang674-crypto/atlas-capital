"""Toy agents for the Stage 26 framework tests (no network: FakeLLM only)."""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from atlas.agents.base import BaseAgent
from atlas.agents.context import AgentContext
from atlas.core.types.research import AnalystReport, Direction, Evidence, Signal


class EchoAnalyst(BaseAgent[AnalystReport]):
    """Pre-fetches the news of `inputs["symbol"]` and echoes it as an `AnalystReport`."""

    name = "echo_analyst"
    tier = "quick"
    prompt_template = "echo_analyst.j2"
    output_schema = AnalystReport

    def gather(self, ctx: AgentContext) -> Sequence[Evidence]:
        return ctx.view.news(ctx.inputs["symbol"])

    def prompt_inputs(self, ctx: AgentContext) -> Mapping[str, Any]:
        return {"symbol": ctx.inputs["symbol"]}


class ToolAnalyst(EchoAnalyst):
    """The echo analyst with the built-in tools and nothing pre-fetched."""

    name = "tool_analyst"
    tools = ("news", "fundamentals", "price_history")

    def gather(self, ctx: AgentContext) -> Sequence[Evidence]:
        return ()


def report(
    *ids: str, confidence: float = 0.8, summary: str = "AAA news is upbeat."
) -> AnalystReport:
    """An `AnalystReport` on AAA for 2024-03-01 with one LONG signal per id."""
    return AnalystReport(
        ticker="AAA",
        as_of=date(2024, 3, 1),
        analyst="echo_analyst",
        summary=summary,
        signals=tuple(
            Signal(name=f"news_{i}", direction=Direction.LONG, strength=0.5, evidence_ids=(e,))
            for i, e in enumerate(ids)
        ),
        risks=("thin coverage",),
        confidence=confidence,
    )


T0 = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


class StepClock:
    """A wall clock that moves one second per reading."""

    def __init__(self, start: datetime = T0) -> None:
        self._now = start

    def now(self) -> datetime:
        from datetime import timedelta

        current = self._now
        self._now = current + timedelta(seconds=1)
        return current

    def today(self) -> date:
        return self._now.date()
