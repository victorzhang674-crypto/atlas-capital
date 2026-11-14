"""Stage 26 Task 5: `BaseAgent` end to end with the toy echo analyst (FakeLLM only)."""

import pytest
from agent_helpers import EchoAnalyst, StepClock, report
from agent_world import AGENT_PROMPTS, World

from atlas.agents.base import AgentOutput, BaseAgent
from atlas.agents.prompting import PromptRegistry
from atlas.agents.tools import default_tools
from atlas.core.config import AtlasConfig
from atlas.core.errors import ConfigError
from atlas.core.types.research import AnalystReport
from atlas.llm import FakeLLM, LLMCache, LLMGateway


def echo(fake: FakeLLM, **kw: object) -> EchoAnalyst:
    gateway = LLMGateway(fake, AtlasConfig())
    return EchoAnalyst(gateway, prompts=PromptRegistry([AGENT_PROMPTS]), clock=StepClock(), **kw)


def test_the_echo_analyst_runs_fully_offline(world: World, fake_llm: FakeLLM) -> None:
    answer = report("news:alpaca-2", "news:alpaca-1")
    fake_llm.add("echo_analyst", None, answer)
    out = echo(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert isinstance(out, AgentOutput)
    assert out.output == answer and isinstance(out.output, AnalystReport)
    assert out.low_quality is False and out.citations.ok
    assert [e.id for e in out.evidence] == ["news:alpaca-2", "news:alpaca-1"]
    assert out.trace.status == "ok"
    assert out.cost_usd == pytest.approx(out.trace.cost_usd) and out.cost_usd > 0
    assert out.usage.output_tokens > 0 and out.trace.llm_calls == 1
    (request,) = fake_llm.calls
    system, user = request.messages
    assert "echo analyst" in system.content and "Today is 2024-03-01" in system.content
    assert "[E:news:alpaca-2] 2024-02-29 20:00Z AAA improved profitability" in user.content
    assert "Write an AnalystReport for AAA as of 2024-03-01" in user.content
    assert request.schema is AnalystReport and request.tools == ()
    assert request.tier == "quick"


def test_every_call_carries_agent_step_and_prompt_version(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, report("news:alpaca-2"))
    agent = echo(fake_llm)
    agent.run(world.ctx(inputs={"symbol": "AAA"}))
    metadata = dict(fake_llm.calls[0].metadata)
    assert metadata == {
        "agent": "echo_analyst",
        "step": "round-1",
        "prompt_version": "echo_analyst.j2@v1|",
    }
    assert agent.prompt_version == "echo_analyst.j2@v1|"


def test_prompt_version_reaches_the_llm_cache_key(world: World, fake_llm: FakeLLM) -> None:
    # Stage 25 backlog: the prompt version is in the cache key, so a hit needs the same version.
    fake_llm.add("echo_analyst", None, report("news:alpaca-2"))
    cache = LLMCache()
    gateway = LLMGateway(fake_llm, AtlasConfig(), cache=cache, cache_mode="read_write")
    agent = EchoAnalyst(gateway, prompts=PromptRegistry([AGENT_PROMPTS]))
    ctx = world.ctx(inputs={"symbol": "AAA"})
    first, second = agent.run(ctx), agent.run(ctx)
    assert len(fake_llm.calls) == 1 and second.trace.cache_hits == 1
    assert second.output == first.output and second.cost_usd == 0.0
    (entry,) = cache.entries()
    assert '"prompt_version":"echo_analyst.j2@v1|"' in entry.request_json


def test_subclasses_are_checked(fake_llm: FakeLLM) -> None:
    gateway = LLMGateway(fake_llm, AtlasConfig())
    prompts = PromptRegistry([AGENT_PROMPTS])

    class NoName(EchoAnalyst):
        name = "Bad Name"

    class NoTemplate(EchoAnalyst):
        name = "no_template"
        prompt_template = "missing.j2"

    class NoSchema(EchoAnalyst):
        name = "no_schema"
        output_schema = dict  # type: ignore[assignment]

    class UnknownTool(EchoAnalyst):
        name = "unknown_tool"
        tools = ("teleport",)

    for cls, message in [
        (NoName, "name"),
        (NoTemplate, "missing.j2"),
        (NoSchema, "output_schema"),
        (UnknownTool, "teleport"),
    ]:
        with pytest.raises(ConfigError, match=message):
            cls(gateway, prompts=prompts, tool_registry=default_tools())

    class Bare(BaseAgent[AnalystReport]):
        pass

    with pytest.raises(ConfigError, match="name"):
        Bare(gateway, prompts=prompts)


def test_gathered_evidence_must_be_known_at_as_of(world: World, fake_llm: FakeLLM) -> None:
    from datetime import UTC, datetime

    from atlas.core.errors import LookAheadError
    from atlas.core.types.research import Evidence

    class Peeker(EchoAnalyst):
        name = "peeker"

        def gather(self, ctx):  # type: ignore[no-untyped-def]
            late = datetime(2024, 3, 4, tzinfo=UTC)
            return [Evidence(id="news:late", source="x", as_of=late, text="later")]

    agent = Peeker(LLMGateway(fake_llm, AtlasConfig()), prompts=PromptRegistry([AGENT_PROMPTS]))
    with pytest.raises(LookAheadError, match="news:late"):
        agent.run(world.ctx(inputs={"symbol": "AAA"}))
    assert fake_llm.calls == []


def test_reserved_template_variables_win_over_inputs(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, report("news:alpaca-2"))

    class Sneaky(EchoAnalyst):
        def prompt_inputs(self, ctx):  # type: ignore[no-untyped-def]
            return {"symbol": "AAA", "as_of": "2030-01-01", "evidence": []}

    Sneaky(LLMGateway(fake_llm, AtlasConfig()), prompts=PromptRegistry([AGENT_PROMPTS])).run(
        world.ctx(inputs={"symbol": "AAA"})
    )
    user = fake_llm.calls[0].messages[1].content
    assert "2030" not in user and "news:alpaca-2" in user
