"""The output-check hook (Phase 3 checkpoint Tier 2 item 4).

Stages 32-36 each need a check of the agent's answer beyond citations (sentiment near a
baseline, numbers equal to the tool numbers, p10 <= p50 <= p90, ...). `BaseAgent.check_output`
returns the problems; they share the citation repair turn (one turn for both) and the
`low_quality` rule (confidence capped). With no problems nothing changes, so recordings made
before the hook replay unchanged.
"""

from collections.abc import Sequence

import pytest
from agent_helpers import EchoAnalyst, StepClock, report
from agent_world import AGENT_PROMPTS, World

from atlas.agents.base import _repair_text
from atlas.agents.context import AgentContext
from atlas.agents.evidence import CitationReport, EvidenceLedger
from atlas.agents.prompting import PromptRegistry
from atlas.core.config import AtlasConfig
from atlas.core.types.research import AnalystReport
from atlas.llm import FakeLLM, LLMGateway

PROBLEM = "confidence must be at most 0.5 for this test"


class CappedAnalyst(EchoAnalyst):
    """The echo analyst with a check: confidence at most 0.5."""

    name = "capped_analyst"
    seen: list[tuple[str, float]]

    def check_output(self, ctx: AgentContext, output: AnalystReport) -> Sequence[str]:
        self.seen.append((ctx.inputs["symbol"], output.confidence))
        return [PROBLEM] if output.confidence > 0.5 else []


def capped(fake: FakeLLM, **agents: object) -> CappedAnalyst:
    cfg = AtlasConfig.model_validate({"agents": agents})
    agent = CappedAnalyst(
        LLMGateway(fake, cfg),
        config=cfg,
        prompts=PromptRegistry([AGENT_PROMPTS]),
        clock=StepClock(),
    )
    agent.seen = []
    return agent


def steps(fake: FakeLLM) -> list[str]:
    return [r.metadata["step"] for r in fake.calls]


def test_a_failed_check_gets_one_repair_turn(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add(
        "capped_analyst",
        None,
        report("news:alpaca-2", confidence=0.9),
        report("news:alpaca-2", confidence=0.4),
    )
    agent = capped(fake_llm)
    out = agent.run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1", "output_repair"]
    ask = fake_llm.calls[1].messages[-1]
    assert ask.role == "user" and PROBLEM in ask.content
    assert fake_llm.calls[1].tools == ()
    assert out.low_quality is False and out.problems == ()
    assert out.output.confidence == pytest.approx(0.4)
    assert agent.seen == [("AAA", 0.9), ("AAA", 0.4)]


def test_a_check_still_failing_is_low_quality(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("capped_analyst", None, report("news:alpaca-2", confidence=0.9))
    out = capped(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1", "output_repair"]
    assert out.citations.ok  # the citations were fine all along
    assert out.low_quality is True and out.trace.status == "low_quality"
    assert out.problems == (PROBLEM,)
    assert out.output.confidence == pytest.approx(0.3)  # capped as for bad citations


def test_citation_and_check_problems_share_one_turn(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add(
        "capped_analyst",
        None,
        report("news:alpaca-9", confidence=0.9),
        report("news:alpaca-2", confidence=0.4),
    )
    out = capped(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1", "evidence_repair"]
    ask = fake_llm.calls[1].messages[-1].content
    assert "news:alpaca-9" in ask and PROBLEM in ask
    assert out.low_quality is False


def test_no_repair_when_retries_are_zero(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("capped_analyst", None, report("news:alpaca-2", confidence=0.9))
    out = capped(fake_llm, evidence_repair_retries=0).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1"] and out.problems == (PROBLEM,)


def test_by_default_there_is_no_check(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, report("news:alpaca-2", confidence=0.9))
    cfg = AtlasConfig()
    agent = EchoAnalyst(
        LLMGateway(fake_llm, cfg), config=cfg, prompts=PromptRegistry([AGENT_PROMPTS])
    )
    out = agent.run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1"] and out.problems == () and not out.low_quality


def test_a_check_that_raises_fails_the_run(world: World, fake_llm: FakeLLM) -> None:
    class Broken(CappedAnalyst):
        def check_output(self, ctx: AgentContext, output: AnalystReport) -> Sequence[str]:
            raise RuntimeError("check crashed")

    fake_llm.add("capped_analyst", None, report("news:alpaca-2"))
    cfg = AtlasConfig()
    agent = Broken(LLMGateway(fake_llm, cfg), config=cfg, prompts=PromptRegistry([AGENT_PROMPTS]))
    with pytest.raises(RuntimeError, match="check crashed"):
        agent.run(world.ctx(inputs={"symbol": "AAA"}))
    [trace] = agent.traces.traces()
    assert trace.status == "failed"


def test_the_citation_repair_text_is_unchanged_without_check_problems() -> None:
    """Recorded runs replay only if the repair turn's text is byte-identical."""
    ledger = EvidenceLedger()
    citations = CitationReport(cited=("x:1",), unknown=("x:1",), uncited_claims=())
    text = _repair_text(citations, ledger)
    assert text == _repair_text(citations, ledger, ())
    assert text == (
        "Your answer has citation problems:\n"
        "Unknown evidence ids (not in the data or tool results): x:1\n"
        "Cite only these evidence ids (shown as [E:<id>] in the data and tool results): (none).\n"
        "Every claim needs at least one of them; drop a claim you cannot support. Answer again "
        "with the corrected JSON object only."
    )
