"""Stage 31: `BaseAgent.finalize`, the last deterministic adjustment of an answer.

The news analyst's empty-news guard needs a confidence ceiling (0.2) below the low-quality cap
(0.3) and one that holds even when the repair turn failed. `finalize(ctx, output)` runs once,
after the checks and the low-quality cap and before the trace is written, so the trace and the
returned output agree. The default is the identity, so recordings made before the hook replay
unchanged.
"""

import json

import pytest
from agent_helpers import EchoAnalyst, StepClock, report
from agent_world import AGENT_PROMPTS, World

from atlas.agents.context import AgentContext
from atlas.agents.prompting import PromptRegistry
from atlas.core.config import AtlasConfig
from atlas.core.types.research import AnalystReport
from atlas.llm import FakeLLM, LLMGateway


class Ceiling(EchoAnalyst):
    """The echo analyst whose confidence never exceeds 0.2."""

    name = "ceiling_analyst"
    calls: list[float]

    def finalize(self, ctx: AgentContext, output: AnalystReport) -> AnalystReport:
        self.calls.append(output.confidence)
        return output.model_copy(update={"confidence": min(output.confidence, 0.2)})


def ceiling(fake: FakeLLM) -> Ceiling:
    cfg = AtlasConfig()
    agent = Ceiling(
        LLMGateway(fake, cfg),
        config=cfg,
        prompts=PromptRegistry([AGENT_PROMPTS]),
        clock=StepClock(),
    )
    agent.calls = []
    return agent


def test_finalize_changes_the_output_and_the_trace_agrees(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("ceiling_analyst", None, report("news:alpaca-2", confidence=0.9))
    agent = ceiling(fake_llm)
    out = agent.run(world.ctx(inputs={"symbol": "AAA"}))
    assert out.output.confidence == pytest.approx(0.2)
    assert agent.calls == [0.9]  # once, on the model's answer
    assert json.loads(out.trace.output_json or "{}")["confidence"] == pytest.approx(0.2)
    assert out.trace.status == "ok" and not out.low_quality


def test_finalize_runs_after_the_low_quality_cap(world: World, fake_llm: FakeLLM) -> None:
    """An answer with unknown citations is capped to 0.3 first; `finalize` sees that value."""
    fake_llm.add(
        "ceiling_analyst",
        None,
        report("news:alpaca-9", confidence=0.9),
        report("news:alpaca-9", confidence=0.9),
    )
    agent = ceiling(fake_llm)
    out = agent.run(world.ctx(inputs={"symbol": "AAA"}))
    assert out.low_quality and agent.calls == [pytest.approx(0.3)]
    assert out.output.confidence == pytest.approx(0.2)


def test_the_default_finalize_is_the_identity(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, report("news:alpaca-2", confidence=0.9))
    cfg = AtlasConfig()
    agent = EchoAnalyst(
        LLMGateway(fake_llm, cfg), config=cfg, prompts=PromptRegistry([AGENT_PROMPTS])
    )
    ctx: AgentContext = world.ctx(inputs={"symbol": "AAA"})
    out = agent.run(ctx)
    assert out.output.confidence == pytest.approx(0.9)
    assert agent.finalize(ctx, out.output) is out.output


def test_a_failing_finalize_fails_the_run_and_is_traced(world: World, fake_llm: FakeLLM) -> None:
    class Broken(Ceiling):
        name = "broken_analyst"

        def finalize(self, ctx: AgentContext, output: AnalystReport) -> AnalystReport:
            raise RuntimeError("boom")

    fake_llm.add("broken_analyst", None, report("news:alpaca-2"))
    cfg = AtlasConfig()
    agent = Broken(
        LLMGateway(fake_llm, cfg),
        config=cfg,
        prompts=PromptRegistry([AGENT_PROMPTS]),
        clock=StepClock(),
    )
    with pytest.raises(RuntimeError, match="boom"):
        agent.run(world.ctx(inputs={"symbol": "AAA"}))
    (trace,) = agent.traces.traces("broken_analyst")
    assert trace.status == "failed" and "boom" in (trace.error or "")
