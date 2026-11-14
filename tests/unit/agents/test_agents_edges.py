"""Stage 26: edge cases of the framework (default hooks, defensive failures, mappings,
template errors)."""

from pathlib import Path
from typing import Any

import pytest
from agent_helpers import EchoAnalyst, report
from agent_world import AGENT_PROMPTS, World
from pydantic import BaseModel

from atlas.agents.base import BaseAgent
from atlas.agents.evidence import check_citations
from atlas.agents.prompting import PROMPTS_DIR, PromptRegistry
from atlas.core.config import AtlasConfig
from atlas.core.errors import ConfigError, LLMError
from atlas.core.types.research import AnalystReport, Claim
from atlas.llm import FakeLLM, FakeReply, LLMGateway
from atlas.llm.schemas import LLMResult, ToolCall, Usage


class Plain(BaseAgent[AnalystReport]):
    """Uses the default hooks: nothing gathered, `ctx.inputs` as template variables."""

    name = "echo_analyst"
    prompt_template = "echo_analyst.j2"
    output_schema = AnalystReport


def test_default_hooks(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, report())
    agent = Plain(LLMGateway(fake_llm, AtlasConfig()), prompts=PromptRegistry([AGENT_PROMPTS]))
    out = agent.run(world.ctx(inputs={"symbol": "BBB"}))
    user = fake_llm.calls[0].messages[1].content
    assert "AnalystReport for BBB" in user and "(no data)" in user
    assert out.trace.evidence_ids == ()


def test_a_repair_turn_that_asks_for_tools_fails(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add(
        "echo_analyst",
        None,
        report("news:nope"),
        FakeReply(tool_calls=(ToolCall(id="c", name="news", arguments={"symbol": "AAA"}),)),
    )
    agent = EchoAnalyst(
        LLMGateway(fake_llm, AtlasConfig()), prompts=PromptRegistry([AGENT_PROMPTS])
    )
    with pytest.raises(LLMError, match="evidence repair"):
        agent.run(world.ctx(inputs={"symbol": "AAA"}))
    assert agent.traces.traces()[0].status == "failed"


class _NoParseGateway:
    """A gateway stand-in whose answers carry no parsed output."""

    def complete(self, messages: Any, **kw: Any) -> LLMResult:
        return LLMResult(content="{}", usage=Usage(), cost_usd=0.0, model="m", latency_ms=0.0)


def test_an_answer_without_a_parsed_output_fails(world: World) -> None:
    agent = EchoAnalyst(_NoParseGateway(), prompts=PromptRegistry([AGENT_PROMPTS]))  # type: ignore[arg-type]
    with pytest.raises(LLMError, match="has no AnalystReport"):
        agent.run(world.ctx(inputs={"symbol": "AAA"}))


def test_citations_inside_mappings() -> None:
    class Book(BaseModel):
        by_sector: dict[str, Claim]
        notes: dict[str, str]

    book = Book(
        by_sector={
            "tech": Claim(text="t", evidence_ids=("news:1",), strength=0.5),
            "energy": Claim(text="e", evidence_ids=(), strength=0.5),
        },
        notes={"x": "see [E:news:2]"},
    )
    result = check_citations(book, {"news:1"})
    assert result.uncited_claims == ("by_sector.energy",)
    assert result.unknown == ("news:2",)


def test_registry_folders_and_syntax_errors(tmp_path: Path) -> None:
    (tmp_path / "broken.j2").write_text("{# version: 1 #}\n{% block system %}{% if %}\n")
    registry = PromptRegistry([tmp_path])
    assert registry.folders == (tmp_path, PROMPTS_DIR)
    with pytest.raises(ConfigError, match="syntax error"):
        registry.get("broken.j2")


def test_a_partial_included_twice_is_counted_once(tmp_path: Path) -> None:
    (tmp_path / "_a.j2").write_text('{# version: 1 #}\n{% include "_c.j2" %}\n')
    (tmp_path / "_b.j2").write_text('{# version: 1 #}\n{% include "_c.j2" %}\n')
    (tmp_path / "_c.j2").write_text("{# version: 2 #}\nC\n")
    (tmp_path / "t.j2").write_text(
        '{# version: 1 #}\n{% block system %}{% include "_a.j2" %}{% include "_b.j2" %}'
        "{% endblock %}{% block user %}u{% endblock %}\n"
    )
    template = PromptRegistry([tmp_path]).get("t.j2")
    assert template.sources == ("_a.j2", "_b.j2", "_c.j2", "t.j2")
