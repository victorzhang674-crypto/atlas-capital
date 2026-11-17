"""Phase 3 checkpoint (Stage 24 follow-up): FakeLLM replies computed from the request.

A script keyed by agent and step cannot tell two symbols apart, and the Stage 28 graph asks
the same agent about several symbols at once, in no fixed order. A reply may therefore be a
function of the request: it reads the prompt (the ticker, the evidence ids) and answers.
"""

import threading

import pytest
from pydantic import BaseModel

from atlas.core.errors import LLMError
from atlas.core.types.base import FrozenDict
from atlas.llm import FakeLLM, FakeReply
from atlas.llm.schemas import Message, ProviderRequest, ToolCall


class Verdict(BaseModel):
    ticker: str


def request(agent: str, step: str, text: str) -> ProviderRequest:
    return ProviderRequest(
        model="deepseek-flash",
        tier="quick",
        messages=(Message.user(text),),
        max_tokens=100,
        timeout_seconds=30.0,
        metadata=FrozenDict({"agent": agent, "step": step}),
    )


def _ticker(req: ProviderRequest) -> str:
    return req.messages[-1].content.split()[-1]


def test_a_function_reply_answers_from_the_request_every_time() -> None:
    fake = FakeLLM({"analyst": lambda req: f"view on {_ticker(req)}"})
    assert fake.complete(request("analyst", "round-1", "research AAA")).content == "view on AAA"
    assert fake.complete(request("analyst", "round-1", "research BBB")).content == "view on BBB"
    assert fake.complete(request("analyst", "round-2", "research AAA")).content == "view on AAA"
    assert len(fake.calls) == 3


def test_a_function_may_return_every_kind_of_reply() -> None:
    call = ToolCall(id="c1", name="news", arguments={"symbol": "AAA"})
    fake = FakeLLM()
    fake.add("a", "model", lambda req: Verdict(ticker=_ticker(req)))
    fake.add("a", "mapping", lambda req: {"ticker": _ticker(req)})
    fake.add("a", "reply", lambda req: FakeReply(tool_calls=(call,)))
    fake.add("a", "error", lambda req: LLMError(f"no view on {_ticker(req)}"))
    assert fake.complete(request("a", "model", "x AAA")).content == '{"ticker":"AAA"}'
    assert fake.complete(request("a", "mapping", "x BBB")).content == '{"ticker": "BBB"}'
    answer = fake.complete(request("a", "reply", "x AAA"))
    assert answer.tool_calls == (call,) and answer.finish_reason == "tool_calls"
    with pytest.raises(LLMError, match="no view on CCC"):
        fake.complete(request("a", "error", "x CCC"))


def test_functions_work_in_sequences_with_add_and_as_the_default() -> None:
    fake = FakeLLM(default=lambda req: f"default {_ticker(req)}")
    fake.add("a", "s", lambda req: f"first {_ticker(req)}", "second")
    assert fake.complete(request("a", "s", "x AAA")).content == "first AAA"
    assert fake.complete(request("a", "s", "x AAA")).content == "second"
    with pytest.raises(LLMError, match="exhausted"):
        fake.complete(request("a", "s", "x AAA"))
    assert fake.complete(request("other", "s", "x BBB")).content == "default BBB"
    listed = FakeLLM({("a", "s"): [lambda req: "one", lambda req: "two"]})
    assert [listed.complete(request("a", "s", "x")).content for _ in range(2)] == ["one", "two"]


def test_a_function_that_raises_or_returns_a_function_fails_loudly() -> None:
    def broken(req: ProviderRequest) -> str:
        raise KeyError("bug in the test's answer function")

    fake = FakeLLM({"a": broken, "b": lambda req: lambda inner: "nested"})  # type: ignore[dict-item]
    with pytest.raises(KeyError, match="bug in the test"):
        fake.complete(request("a", "s", "x"))
    with pytest.raises(TypeError, match="must return a reply"):
        fake.complete(request("b", "s", "x"))


def test_usage_of_a_function_reply_is_counted_from_its_text() -> None:
    fake = FakeLLM({"a": lambda req: "12345678"})
    usage = fake.complete(request("a", "s", "abcd")).usage
    assert (usage.input_tokens, usage.output_tokens) == (1, 2)


def test_parallel_requests_each_get_their_own_answer() -> None:
    fake = FakeLLM({"analyst": lambda req: Verdict(ticker=_ticker(req))})
    symbols = [f"S{i:02d}" for i in range(40)]
    gate = threading.Barrier(8)
    wrong: list[str] = []

    def worker(part: list[str]) -> None:
        gate.wait(timeout=30)
        for symbol in part:
            answer = fake.complete(request("analyst", "round-1", f"research {symbol}"))
            if answer.content != f'{{"ticker":"{symbol}"}}':
                wrong.append(symbol)

    threads = [threading.Thread(target=worker, args=(symbols[i::8],)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert wrong == [] and len(fake.calls) == 40
