"""Stage 26 Task 3: the tool registry (specs, argument checks, error texts, PIT guard)."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from agent_world import World
from pydantic import BaseModel, ConfigDict, Field

from atlas.agents.context import AgentContext
from atlas.agents.evidence import evidence_tag
from atlas.agents.tools import Tool, ToolRegistry, default_tools
from atlas.core.errors import ConfigError, DataError, LookAheadError
from atlas.core.types.research import Evidence
from atlas.llm.schemas import ToolCall


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    word: str = Field(min_length=1, description="The word to echo")
    times: int = Field(default=1, ge=1, le=3)


def _echo(ctx: AgentContext, args: EchoArgs) -> Sequence[Evidence]:
    stamp = ctx.as_of.cutoff.replace(hour=0, minute=0)
    return [
        Evidence(id=f"echo:{args.word}:{i}", source="echo", as_of=stamp, text=args.word)
        for i in range(args.times)
    ]


ECHO = Tool(name="echo", description="Echo a word.", args=EchoArgs, fn=_echo, version=2)


def call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=arguments)


def test_tool_spec_comes_from_the_args_model() -> None:
    spec = ECHO.spec
    assert spec.name == "echo" and spec.description == "Echo a word."
    assert spec.parameters["type"] == "object"
    assert set(spec.parameters["properties"]) == {"word", "times"}
    assert spec.parameters["required"] == ["word"]
    assert ECHO.label == "echo@v2"


def test_tool_validation() -> None:
    with pytest.raises(ValueError, match="name"):
        Tool(name="bad name", description="x", args=EchoArgs, fn=_echo)
    with pytest.raises(ValueError, match="version"):
        Tool(name="echo", description="x", args=EchoArgs, fn=_echo, version=0)
    with pytest.raises(ValueError, match="description"):
        Tool(name="echo", description=" ", args=EchoArgs, fn=_echo)


def test_registry_basics() -> None:
    registry = ToolRegistry([ECHO])
    assert registry.names == ("echo",) and "echo" in registry
    assert registry.get("echo") is ECHO
    assert registry.specs(["echo"]) == (ECHO.spec,)
    with pytest.raises(ConfigError, match="already registered"):
        registry.register(ECHO)
    with pytest.raises(ConfigError, match="unknown tool 'nope'"):
        registry.specs(["nope"])
    with pytest.raises(ConfigError, match="unknown tool"):
        registry.get("nope")


def test_a_call_returns_evidence_tagged_text_in_a_data_block(world: World) -> None:
    outcome = ToolRegistry([ECHO]).call(world.ctx(), call("echo", word="hi", times=2))
    assert outcome.error is None
    assert [e.id for e in outcome.evidence] == ["echo:hi:0", "echo:hi:1"]
    assert outcome.text.startswith('<data source="tool:echo">\n')
    assert outcome.text.endswith("\n</data>")
    assert f"{evidence_tag('echo:hi:0')} 2024-03-01 00:00Z hi" in outcome.text
    assert outcome.call.id == "call-1"


def test_a_call_with_no_evidence_says_no_data(world: World) -> None:
    empty = Tool(name="empty", description="Nothing.", args=EchoArgs, fn=lambda c, a: [])
    outcome = ToolRegistry([empty]).call(world.ctx(), call("empty", word="x"))
    assert outcome.text == '<data source="tool:empty">\n(no data)\n</data>'
    assert outcome.evidence == () and outcome.error is None


@pytest.mark.parametrize(
    ("tool_call", "message"),
    [
        (call("nope"), "unknown tool 'nope'"),
        (call("echo"), "invalid arguments for echo: word"),
        (call("echo", word="x", times=9), "invalid arguments for echo: times"),
        (call("echo", word="x", colour="red"), "invalid arguments for echo: colour"),
    ],
)
def test_bad_calls_become_an_error_text_for_the_model(
    world: World, tool_call: ToolCall, message: str
) -> None:
    outcome = ToolRegistry([ECHO]).call(world.ctx(), tool_call)
    assert outcome.error is not None and message in outcome.error
    assert outcome.text.startswith("ERROR: ") and message in outcome.text
    assert outcome.evidence == ()


def test_a_tool_not_offered_to_the_agent_is_unknown(world: World) -> None:
    outcome = ToolRegistry([ECHO]).call(world.ctx(), call("echo", word="x"), allowed=["other"])
    assert outcome.error is not None and "unknown tool 'echo'" in outcome.error


def test_a_data_error_becomes_an_error_text(world: World) -> None:
    def blocked(ctx: AgentContext, args: EchoArgs) -> Sequence[Evidence]:
        raise DataError("symbol BAD is blocked by a critical quality issue")

    tool = Tool(name="blocked", description="Fails.", args=EchoArgs, fn=blocked)
    outcome = ToolRegistry([tool]).call(world.ctx(), call("blocked", word="x"))
    assert outcome.error == "blocked: symbol BAD is blocked by a critical quality issue"
    assert outcome.text == f"ERROR: {outcome.error}"


def test_other_exceptions_propagate(world: World) -> None:
    def broken(ctx: AgentContext, args: EchoArgs) -> Sequence[Evidence]:
        raise RuntimeError("bug")

    tool = Tool(name="broken", description="Bug.", args=EchoArgs, fn=broken)
    with pytest.raises(RuntimeError, match="bug"):
        ToolRegistry([tool]).call(world.ctx(), call("broken", word="x"))


def test_a_tool_returning_future_evidence_raises_lookahead(world: World) -> None:
    def peek(ctx: AgentContext, args: EchoArgs) -> Sequence[Evidence]:
        late = datetime(2024, 3, 4, 15, tzinfo=UTC)
        return [Evidence(id="news:future", source="x", as_of=late, text="from the future")]

    tool = Tool(name="peek", description="Leaks.", args=EchoArgs, fn=peek)
    with pytest.raises(LookAheadError, match="news:future"):
        ToolRegistry([tool]).call(world.ctx(), call("peek", word="x"))


def test_default_tools() -> None:
    registry = default_tools()
    assert registry.names == ("fundamentals", "market_news", "news", "price_history")
    for spec in registry.specs():
        assert spec.description and spec.parameters["type"] == "object"


def test_versions_label(world: World) -> None:
    registry = ToolRegistry([ECHO, *default_tools().tools])
    assert registry.versions_label(["news", "echo"]) == "echo@v2,news@v1"
    assert registry.versions_label([]) == ""
