"""Stage 26 Task 7: citations inside a run: one repair turn, then `low_quality` with the
confidence capped (acceptance: uncited claims are caught)."""

import json

import pytest
from agent_helpers import EchoAnalyst, StepClock, report
from agent_world import AGENT_PROMPTS, World

from atlas.agents.prompting import PromptRegistry
from atlas.core.config import AtlasConfig
from atlas.core.types.research import AnalystReport, Direction, Signal
from atlas.llm import FakeLLM, LLMGateway


def echo(fake: FakeLLM, **agents: object) -> EchoAnalyst:
    cfg = AtlasConfig.model_validate({"agents": agents})
    return EchoAnalyst(
        LLMGateway(fake, cfg),
        config=cfg,
        prompts=PromptRegistry([AGENT_PROMPTS]),
        clock=StepClock(),
    )


def steps(fake: FakeLLM) -> list[str]:
    return [r.metadata["step"] for r in fake.calls]


def uncited(confidence: float = 0.8) -> AnalystReport:
    base = report("news:alpaca-2", confidence=confidence)
    loose = Signal(name="valuation", direction=Direction.SHORT, strength=0.4, evidence_ids=())
    return base.model_copy(update={"signals": (*base.signals, loose)})


def test_an_unknown_id_is_repaired(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, report("news:alpaca-9"), report("news:alpaca-2"))
    out = echo(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1", "evidence_repair"]
    repair = fake_llm.calls[1]
    assert repair.tools == ()
    previous, ask = repair.messages[-2:]
    assert previous.role == "assistant" and "news:alpaca-9" in previous.content
    assert ask.role == "user" and "news:alpaca-9" in ask.content
    assert "news:alpaca-2, news:alpaca-1" in ask.content
    assert out.low_quality is False and out.trace.status == "ok"
    assert out.output.confidence == pytest.approx(0.8)
    assert out.citations.ok and out.trace.llm_calls == 2


def test_still_unknown_after_the_repair_is_low_quality(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, report("news:alpaca-9"))
    out = echo(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1", "evidence_repair"]
    assert out.low_quality is True and out.trace.status == "low_quality"
    assert out.output.confidence == pytest.approx(0.3)
    assert out.citations.unknown == ("news:alpaca-9",)
    assert out.trace.unknown_ids == ("news:alpaca-9",)
    assert json.loads(out.trace.output_json or "")["confidence"] == pytest.approx(0.3)
    assert out.evidence == ()


def test_an_uncited_claim_is_caught(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, uncited())
    out = echo(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert out.low_quality is True
    assert out.citations.uncited_claims == ("signals[1]",)
    assert "signals[1]" in fake_llm.calls[1].messages[-1].content
    assert [e.id for e in out.evidence] == ["news:alpaca-2"]


def test_an_uncited_claim_can_be_repaired(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, uncited(), report("news:alpaca-1"))
    out = echo(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert out.low_quality is False and out.output == report("news:alpaca-1")


def test_a_future_article_is_unknown(world: World, fake_llm: FakeLLM) -> None:
    # alpaca-10 is in the dirty lake but published after the cutoff: never shown, so unknown.
    fake_llm.add("echo_analyst", None, report("news:alpaca-10"))
    out = echo(fake_llm).run(world.ctx("dirty", inputs={"symbol": "AAA"}))
    assert out.low_quality is True and out.citations.unknown == ("news:alpaca-10",)
    assert "news:alpaca-10" not in fake_llm.calls[0].messages[1].content


def test_no_repair_when_retries_are_zero(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, report("news:alpaca-9"))
    out = echo(fake_llm, evidence_repair_retries=0).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1"] and out.low_quality is True


def test_the_cap_comes_from_config_and_lower_values_stay(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, report("news:alpaca-9", confidence=0.9))
    out = echo(fake_llm, low_quality_confidence_cap=0.5).run(world.ctx(inputs={"symbol": "AAA"}))
    assert out.output.confidence == pytest.approx(0.5)
    fake_llm.add("echo_analyst", None, report("news:alpaca-9", confidence=0.1))
    out = echo(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert out.low_quality is True and out.output.confidence == pytest.approx(0.1)


def test_a_valid_answer_makes_one_call(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, report("news:alpaca-1"))
    out = echo(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1"] and not out.low_quality
