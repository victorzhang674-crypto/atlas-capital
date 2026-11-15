"""Stage 26 Task 6: the tool-calling loop, `max_tool_rounds` and the wrap-up turn."""

import pytest
from agent_helpers import EchoAnalyst, StepClock, ToolAnalyst, report
from agent_world import AGENT_PROMPTS, World

from atlas.agents.prompting import PromptRegistry
from atlas.agents.trace import TraceStore
from atlas.core.config import AtlasConfig
from atlas.core.errors import LLMError
from atlas.llm import FakeLLM, FakeReply, LLMGateway
from atlas.llm.schemas import ToolCall


def calls(*specs: tuple[str, str, dict[str, object]]) -> FakeReply:
    """A reply asking for tool calls `(id, name, arguments)`."""
    return FakeReply(tool_calls=tuple(ToolCall(id=i, name=n, arguments=a) for i, n, a in specs))


def tool_agent(fake: FakeLLM, rounds: int = 4, traces: TraceStore | None = None) -> ToolAnalyst:
    cfg = AtlasConfig.model_validate({"agents": {"max_tool_rounds": rounds}})
    return ToolAnalyst(
        LLMGateway(fake, cfg),
        config=cfg,
        prompts=PromptRegistry([AGENT_PROMPTS]),
        clock=StepClock(),
        traces=traces,
    )


def steps(fake: FakeLLM) -> list[str]:
    return [r.metadata["step"] for r in fake.calls]


def test_one_tool_round_then_an_answer(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add(
        "tool_analyst",
        None,
        calls(("c1", "news", {"symbol": "AAA"})),
        report("news:alpaca-2"),
    )
    out = tool_agent(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1", "round-2"]
    first, second = fake_llm.calls
    assert {t.name for t in first.tools} == {"news", "fundamentals", "price_history"}
    assert second.tools == first.tools
    assistant, tool = second.messages[-2:]
    assert assistant.role == "assistant" and assistant.tool_calls[0].name == "news"
    assert tool.role == "tool" and tool.tool_call_id == "c1"
    assert tool.content.startswith('<data source="tool:news">')
    assert "[E:news:alpaca-2]" in tool.content
    (record,) = out.trace.tool_calls
    assert (record.round, record.name, record.call_id) == (1, "news", "c1")
    assert record.evidence_ids == ("news:alpaca-2", "news:alpaca-1")
    assert dict(record.arguments) == {"symbol": "AAA"} and record.error is None
    assert out.citations.ok and [e.id for e in out.evidence] == ["news:alpaca-2"]
    assert out.trace.llm_calls == 2


def test_several_calls_in_one_round_run_in_order(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add(
        "tool_analyst",
        None,
        calls(("a", "fundamentals", {"symbol": "AAA"}), ("b", "price_history", {"symbol": "AAA"})),
        report("fund:AAA:2023-12-31:revenue_ttm", "px:AAA:2024-02-29:21d"),
    )
    out = tool_agent(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    tool_messages = [m for m in fake_llm.calls[1].messages if m.role == "tool"]
    assert [m.tool_call_id for m in tool_messages] == ["a", "b"]
    assert [r.name for r in out.trace.tool_calls] == ["fundamentals", "price_history"]
    assert {r.round for r in out.trace.tool_calls} == {1}
    assert out.citations.ok and out.low_quality is False


def test_a_model_that_keeps_calling_tools_gets_a_wrap_up_turn(
    world: World, fake_llm: FakeLLM
) -> None:
    fake_llm.add("tool_analyst", "round-1", calls(("c1", "news", {"symbol": "AAA"})))
    fake_llm.add("tool_analyst", "round-2", calls(("c2", "price_history", {"symbol": "AAA"})))
    fake_llm.add("tool_analyst", "round-3", AssertionError("no third tool round"))
    fake_llm.add("tool_analyst", "wrap_up", report("news:alpaca-1"))
    out = tool_agent(fake_llm, rounds=2).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1", "round-2", "wrap_up"]
    wrap = fake_llm.calls[-1]
    assert wrap.tools == ()
    last = wrap.messages[-1]
    assert last.role == "user" and "2 tool rounds" in last.content
    assert "no more tools" in last.content.lower()
    assert [r.round for r in out.trace.tool_calls] == [1, 2]
    assert out.trace.status == "ok" and out.trace.llm_calls == 3


def test_a_wrap_up_that_still_asks_for_tools_fails_safe(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("tool_analyst", None, calls(("c", "news", {"symbol": "AAA"})))
    traces = TraceStore()
    with pytest.raises(LLMError, match="tools after the wrap-up"):
        tool_agent(fake_llm, rounds=1, traces=traces).run(world.ctx(inputs={"symbol": "AAA"}))
    assert steps(fake_llm) == ["round-1", "wrap_up"]
    (trace,) = traces.traces()
    assert trace.status == "failed" and "wrap-up" in (trace.error or "")
    assert trace.output_json is None and trace.llm_calls == 2
    assert [r.name for r in trace.tool_calls] == ["news"]


def test_a_bad_call_is_answered_with_an_error_and_the_loop_goes_on(
    world: World, fake_llm: FakeLLM
) -> None:
    fake_llm.add(
        "tool_analyst",
        None,
        calls(("x", "teleport", {}), ("y", "news", {"symbol": ""})),
        report(),
        report(),  # the evidence repair (audit A3: an answer citing nothing is uncited)
    )
    out = tool_agent(fake_llm).run(world.ctx(inputs={"symbol": "AAA"}))
    tool_messages = [m for m in fake_llm.calls[1].messages if m.role == "tool"]
    assert tool_messages[0].content.startswith("ERROR: unknown tool 'teleport'")
    assert tool_messages[1].content.startswith("ERROR: invalid arguments for news: symbol")
    assert [r.error is not None for r in out.trace.tool_calls] == [True, True]
    assert [r.evidence_ids for r in out.trace.tool_calls] == [(), ()]
    assert out.trace.evidence_ids == () and out.trace.status == "low_quality"


def test_tool_calls_without_tools_offered_fail(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, calls(("c", "news", {"symbol": "AAA"})))
    prompts = PromptRegistry([AGENT_PROMPTS])
    agent = EchoAnalyst(LLMGateway(fake_llm, AtlasConfig()), prompts=prompts)
    with pytest.raises(LLMError, match="no tools"):
        agent.run(world.ctx(inputs={"symbol": "AAA"}))
    assert agent.traces.traces()[0].status == "failed"
