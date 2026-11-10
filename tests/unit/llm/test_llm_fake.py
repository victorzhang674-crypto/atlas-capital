"""FakeLLM: scripted provider responses keyed by agent and step."""

import pytest
from pydantic import BaseModel

from atlas.core.errors import LLMError, LLMTransientError
from atlas.core.types.base import FrozenDict
from atlas.llm import FakeLLM, FakeReply
from atlas.llm.schemas import Message, ProviderRequest, ToolCall, Usage


class Verdict(BaseModel):
    rating: str
    score: float


def request(
    agent: str | None = None, step: str | None = None, text: str = "hello"
) -> ProviderRequest:
    metadata = {k: v for k, v in (("agent", agent), ("step", step)) if v is not None}
    return ProviderRequest(
        model="deepseek-flash",
        tier="quick",
        messages=(Message.user(text),),
        max_tokens=100,
        timeout_seconds=30.0,
        metadata=FrozenDict(metadata),
    )


def test_keyed_by_agent_and_step() -> None:
    fake = FakeLLM({("analyst", "draft"): "draft text", ("analyst", "final"): "final text"})
    assert fake.complete(request("analyst", "draft")).content == "draft text"
    assert fake.complete(request("analyst", "final")).content == "final text"


def test_agent_key_covers_every_step_and_exact_key_wins() -> None:
    fake = FakeLLM({"analyst": "any step", ("analyst", "final"): "the final"})
    assert fake.complete(request("analyst", "draft")).content == "any step"
    assert fake.complete(request("analyst", "final")).content == "the final"
    assert fake.complete(request("analyst")).content == "any step"


def test_default_and_missing_script() -> None:
    assert FakeLLM(default="fallback").complete(request("anyone", "x")).content == "fallback"
    with pytest.raises(LLMError, match=r"no scripted response.*analyst.*draft"):
        FakeLLM({"pm": "x"}).complete(request("analyst", "draft"))


def test_sequence_is_consumed_in_order_then_fails() -> None:
    fake = FakeLLM()
    fake.add("analyst", "draft", "first", "second")
    assert fake.complete(request("analyst", "draft")).content == "first"
    assert fake.complete(request("analyst", "draft")).content == "second"
    with pytest.raises(LLMError, match="exhausted"):
        fake.complete(request("analyst", "draft"))


def test_single_reply_repeats() -> None:
    fake = FakeLLM({("analyst", None): "same"})
    assert [fake.complete(request("analyst", s)).content for s in ("a", "b", "c")] == ["same"] * 3


def test_reply_kinds() -> None:
    call = ToolCall(id="c1", name="get_news", arguments={"symbol": "AAA"})
    fake = FakeLLM()
    fake.add(
        "a",
        None,
        Verdict(rating="buy", score=0.7),
        {"rating": "sell", "score": 0.1},
        FakeReply(content="", tool_calls=(call,), usage=Usage(input_tokens=9, output_tokens=2)),
        LLMTransientError("rate limited", retry_after=3.0),
    )
    assert fake.complete(request("a")).content == '{"rating":"buy","score":0.7}'
    assert fake.complete(request("a")).content == '{"rating": "sell", "score": 0.1}'
    third = fake.complete(request("a"))
    assert third.tool_calls == (call,)
    assert third.usage == Usage(input_tokens=9, output_tokens=2)
    assert third.finish_reason == "tool_calls"
    with pytest.raises(LLMTransientError, match="rate limited"):
        fake.complete(request("a"))


def test_usage_is_deterministic_from_text_lengths() -> None:
    fake = FakeLLM(default="12345678")  # 8 characters -> 2 tokens
    response = fake.complete(request(text="x" * 10))  # 10 characters -> 3 tokens
    assert response.usage == Usage(input_tokens=3, output_tokens=2)
    assert response.model == "deepseek-flash"
    assert response.finish_reason == "stop"


def test_calls_are_recorded() -> None:
    fake = FakeLLM(default="ok")
    first, second = request("analyst", "draft"), request("pm", "size")
    fake.complete(first)
    fake.complete(second)
    assert fake.calls == [first, second]
    assert fake.name == "fake"


# --- Audit (mutation survivors) ------------------------------------------------------------


def test_list_in_the_constructor_script_is_consumed_in_order() -> None:
    fake = FakeLLM({("analyst", "draft"): ["first", "second"]})
    assert fake.complete(request("analyst", "draft")).content == "first"
    assert fake.complete(request("analyst", "draft")).content == "second"
    with pytest.raises(LLMError, match="exhausted"):
        fake.complete(request("analyst", "draft"))


def test_add_with_one_reply_repeats_and_with_none_is_refused() -> None:
    fake = FakeLLM()
    fake.add("analyst", None, "same")
    assert [fake.complete(request("analyst")).content for _ in range(3)] == ["same"] * 3
    with pytest.raises(ValueError, match="at least one reply"):
        fake.add("analyst", None)
