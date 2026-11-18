"""No repair turn when the run showed no evidence (Phase 3 checkpoint Tier 2 item 3).

With no evidence shown, a failing citation check cannot be repaired: any id the model cites
is unknown, and an output that cites nothing has an uncited root (its schema can cite). The
repair turn was paid for nothing; the output now goes straight to `low_quality` (confidence
capped), as it would have after the repair.
"""

import pytest
from agent_helpers import StepClock, ToolAnalyst, report
from agent_world import AGENT_PROMPTS, World
from structlog.testing import capture_logs

from atlas.agents.prompting import PromptRegistry
from atlas.core.config import AtlasConfig
from atlas.llm import FakeLLM, FakeReply, LLMGateway
from atlas.llm.schemas import ToolCall


def tool_agent(fake: FakeLLM) -> ToolAnalyst:
    cfg = AtlasConfig()
    return ToolAnalyst(
        LLMGateway(fake, cfg),
        config=cfg,
        prompts=PromptRegistry([AGENT_PROMPTS]),
        clock=StepClock(),
    )


def steps(fake: FakeLLM) -> list[str]:
    return [r.metadata["step"] for r in fake.calls]


def test_an_answer_with_no_evidence_shown_is_not_sent_for_repair(
    world: World, fake_llm: FakeLLM
) -> None:
    fake_llm.add("tool_analyst", "round-1", report("news:alpaca-9", confidence=0.9))
    fake_llm.add("tool_analyst", "evidence_repair", AssertionError("no repair turn"))
    with capture_logs() as logs:
        out = tool_agent(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1"]
    assert out.low_quality is True and out.trace.status == "low_quality"
    assert out.output.confidence == pytest.approx(0.3)
    assert out.citations.unknown == ("news:alpaca-9",)
    assert out.trace.llm_calls == 1 and out.trace.evidence_ids == ()
    assert any(e["event"] == "agent.repair_skipped" for e in logs)


def test_a_tool_round_that_found_nothing_is_not_repaired_either(
    world: World, fake_llm: FakeLLM
) -> None:
    call = ToolCall(id="c1", name="news", arguments={"symbol": "ZZZ"})
    fake_llm.add("tool_analyst", "round-1", FakeReply(tool_calls=(call,)))
    fake_llm.add("tool_analyst", "round-2", report("news:alpaca-1"))
    fake_llm.add("tool_analyst", "evidence_repair", AssertionError("no repair turn"))
    out = tool_agent(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1", "round-2"]
    assert out.low_quality is True and out.trace.evidence_ids == ()


def test_with_evidence_shown_the_repair_turn_still_runs(world: World, fake_llm: FakeLLM) -> None:
    call = ToolCall(id="c1", name="news", arguments={"symbol": "AAA"})
    fake_llm.add("tool_analyst", "round-1", FakeReply(tool_calls=(call,)))
    fake_llm.add("tool_analyst", "round-2", report("news:alpaca-9"))
    fake_llm.add("tool_analyst", "evidence_repair", report("news:alpaca-1"))
    out = tool_agent(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1", "round-2", "evidence_repair"]
    assert out.low_quality is False
