"""The gateway's value types: messages, tools, usage, results."""

import pytest
from pydantic import ValidationError

from atlas.llm.schemas import LLMResult, Message, ToolCall, ToolSpec, Usage


def test_message_constructors() -> None:
    assert Message.system("be brief") == Message(role="system", content="be brief")
    assert Message.user("hi").role == "user"
    assert Message.assistant("ok").role == "assistant"
    tool = Message.tool("call_1", "42")
    assert (tool.role, tool.tool_call_id, tool.content) == ("tool", "call_1", "42")


def test_message_rules() -> None:
    call = ToolCall(id="call_1", name="lookup", arguments={"symbol": "AAA"})
    assert Message.assistant("", tool_calls=(call,)).tool_calls == (call,)
    with pytest.raises(ValidationError, match="tool_call_id"):
        Message(role="tool", content="42")
    with pytest.raises(ValidationError, match="tool_call_id"):
        Message(role="user", content="hi", tool_call_id="call_1")
    with pytest.raises(ValidationError, match="tool_calls"):
        Message(role="user", content="hi", tool_calls=(call,))
    with pytest.raises(ValidationError):
        Message(role="narrator", content="hi")  # type: ignore[arg-type]


def test_models_are_frozen_and_strict() -> None:
    message = Message.user("hi")
    with pytest.raises(ValidationError):
        message.content = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Message(role="user", content="hi", extra=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1)


def test_tool_spec_name_is_an_identifier() -> None:
    spec = ToolSpec(name="get_news", description="News", parameters={"type": "object"})
    assert spec.parameters["type"] == "object"
    with pytest.raises(ValidationError, match="name"):
        ToolSpec(name="get news", description="News", parameters={"type": "object"})


def test_usage_adds_and_totals() -> None:
    a = Usage(input_tokens=10, cached_input_tokens=5, output_tokens=7, reasoning_tokens=3)
    b = Usage(input_tokens=1, cache_write_tokens=2, output_tokens=4)
    total = a + b
    assert total == Usage(
        input_tokens=11,
        cached_input_tokens=5,
        cache_write_tokens=2,
        output_tokens=11,
        reasoning_tokens=3,
    )
    assert total.prompt_tokens == 18  # input + cached + cache-write
    assert total.total_tokens == 29
    assert Usage() + Usage() == Usage()


def test_reasoning_tokens_are_part_of_output() -> None:
    with pytest.raises(ValidationError, match="reasoning_tokens"):
        Usage(output_tokens=3, reasoning_tokens=4)


def test_result_defaults() -> None:
    result = LLMResult(
        content="hi", usage=Usage(), cost_usd=0.0, model="deepseek-flash", latency_ms=1.0
    )
    assert result.parsed is None
    assert result.cache_hit is False
    assert result.tool_calls == ()
    assert result.attempts == 1
    with pytest.raises(ValidationError):
        LLMResult(content="hi", usage=Usage(), cost_usd=-1.0, model="m", latency_ms=1.0)
