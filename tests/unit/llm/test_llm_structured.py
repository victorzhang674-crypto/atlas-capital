"""Structured output: Pydantic validation, one repair retry, then `LLMError` (brief task 2)."""

from collections.abc import Callable

import pytest
from pydantic import BaseModel, Field

from atlas.core.errors import LLMError
from atlas.llm import FakeLLM, FakeReply, LLMGateway
from atlas.llm.schemas import Message, ToolCall, Usage

MakeGateway = Callable[..., LLMGateway]


class Verdict(BaseModel):
    rating: str
    confidence: float = Field(ge=0, le=1)


GOOD = '{"rating": "buy", "confidence": 0.7}'
BAD = '{"rating": "buy", "confidence": 1.7}'  # confidence out of range


def test_valid_json_is_parsed(make_gateway: MakeGateway) -> None:
    fake = FakeLLM(default=GOOD)
    result = make_gateway(fake).complete([Message.user("rate AAA")], schema=Verdict)
    assert result.parsed == Verdict(rating="buy", confidence=0.7)
    assert result.content == GOOD
    assert result.attempts == 1
    assert fake.calls[0].schema is Verdict
    assert len(fake.calls) == 1


def test_no_schema_means_no_parsing(make_gateway: MakeGateway) -> None:
    result = make_gateway(FakeLLM(default="not json at all")).complete([Message.user("hi")])
    assert result.parsed is None


@pytest.mark.parametrize(
    "wrapped",
    [f"```json\n{GOOD}\n```", f"```\n{GOOD}\n```", f"  \n{GOOD}\n  ", f"```JSON\n{GOOD}```"],
)
def test_fenced_json_is_accepted_without_a_repair_call(  # review focus 1
    make_gateway: MakeGateway, wrapped: str
) -> None:
    fake = FakeLLM(default=wrapped)
    result = make_gateway(fake).complete([Message.user("rate AAA")], schema=Verdict)
    assert result.parsed == Verdict(rating="buy", confidence=0.7)
    assert len(fake.calls) == 1


def test_invalid_then_valid_is_repaired_once(make_gateway: MakeGateway) -> None:
    fake = FakeLLM()
    fake.add(
        "analyst",
        None,
        FakeReply(content=BAD, usage=Usage(input_tokens=100, output_tokens=20)),
        FakeReply(content=GOOD, usage=Usage(input_tokens=150, output_tokens=30)),
    )
    result = make_gateway(fake).complete(
        [Message.user("rate AAA")], schema=Verdict, metadata={"agent": "analyst"}
    )
    assert result.parsed == Verdict(rating="buy", confidence=0.7)
    assert result.content == GOOD
    assert result.attempts == 2
    assert len(fake.calls) == 2
    # Usage and cost are the sum of both billed responses (off-peak deepseek-flash rates).
    assert result.usage == Usage(input_tokens=250, output_tokens=50)
    assert result.cost_usd == pytest.approx((250 * 0.15 + 50 * 0.6) / 1e6, rel=1e-12)
    # The repair request repeats the conversation, then the bad answer and the error.
    first, second = fake.calls
    assert second.messages[: len(first.messages)] == first.messages
    bad_answer, repair = second.messages[len(first.messages) :]
    assert bad_answer == Message.assistant(BAD)
    assert repair.role == "user"
    assert "confidence" in repair.content  # the validation error names the field
    assert "less than or equal to 1" in repair.content
    assert "Verdict" in repair.content
    assert second.schema is Verdict


def test_invalid_twice_raises_llm_error(make_gateway: MakeGateway) -> None:
    fake = FakeLLM()
    fake.add("analyst", None, "not json", BAD)
    with pytest.raises(LLMError, match=r"Verdict.*after 1 repair") as excinfo:
        make_gateway(fake).complete(
            [Message.user("rate AAA")], schema=Verdict, metadata={"agent": "analyst"}
        )
    assert len(fake.calls) == 2  # exactly one repair retry, never a third call
    assert "confidence" in str(excinfo.value)


def test_non_object_json_is_invalid(make_gateway: MakeGateway) -> None:
    fake = FakeLLM()
    fake.add(None, None, "[1, 2, 3]", GOOD)
    result = make_gateway(fake).complete([Message.user("rate AAA")], schema=Verdict)
    assert result.attempts == 2
    assert result.parsed == Verdict(rating="buy", confidence=0.7)


def test_empty_answer_is_repaired_without_an_empty_assistant_turn(
    make_gateway: MakeGateway,
) -> None:
    fake = FakeLLM()
    fake.add(None, None, FakeReply(content="", finish_reason="length"), GOOD)
    result = make_gateway(fake).complete([Message.user("rate AAA")], schema=Verdict)
    assert result.parsed == Verdict(rating="buy", confidence=0.7)
    second = fake.calls[1]
    assert all(m.content for m in second.messages)  # no empty assistant message is sent back
    assert second.messages[-1].role == "user"


def test_tool_calls_are_returned_unvalidated(make_gateway: MakeGateway) -> None:
    call = ToolCall(id="c1", name="get_news", arguments={"symbol": "AAA"})
    fake = FakeLLM(default=FakeReply(content="", tool_calls=(call,)))
    result = make_gateway(fake).complete([Message.user("rate AAA")], schema=Verdict)
    assert result.tool_calls == (call,)
    assert result.parsed is None
    assert result.finish_reason == "tool_calls"
    assert len(fake.calls) == 1


# --- Audit (mutation survivors) ------------------------------------------------------------


class Leg(BaseModel):
    symbol: str
    weight: float = Field(ge=-1, le=1)


class Book(BaseModel):
    name: str
    legs: list[Leg]


def test_repair_message_lists_each_error_with_its_path(make_gateway: MakeGateway) -> None:
    fake = FakeLLM()
    bad = '{"legs": [{"symbol": "AAA", "weight": 0.5}, {"symbol": "BBB", "weight": 7}]}'
    good = '{"name": "x", "legs": []}'
    fake.add(None, None, bad, good)
    make_gateway(fake).complete([Message.user("book")], schema=Book)
    repair = fake.calls[1].messages[-1].content
    lines = repair.splitlines()
    assert "name: Field required" in lines  # one error per line ...
    assert "legs.1.weight: Input should be less than or equal to 1" in lines  # ... dotted path
    assert "errors.pydantic.dev" not in repair  # no documentation URLs
    assert lines[-1] == "Answer again with one corrected JSON object and nothing else."


def test_final_error_keeps_the_validation_error_as_its_cause(make_gateway: MakeGateway) -> None:
    from pydantic import ValidationError

    fake = FakeLLM(default="not json")
    with pytest.raises(LLMError) as excinfo:
        make_gateway(fake).complete([Message.user("rate AAA")], schema=Verdict)
    assert isinstance(excinfo.value.__cause__, ValidationError)
    assert "<root>" not in str(excinfo.value) or "Invalid JSON" in str(excinfo.value)
