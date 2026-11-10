"""Anthropic provider (optional) against a fake SDK client: no network, no key."""

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, SecretStr

from atlas.core.errors import LLMError, LLMTransientError
from atlas.core.types.base import FrozenDict
from atlas.llm.providers.anthropic import AnthropicProvider
from atlas.llm.schemas import Message, ProviderRequest, ToolCall, ToolSpec, Usage


class Verdict(BaseModel):
    rating: str
    confidence: float


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def sdk_message(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "content": [SimpleNamespace(type="thinking", thinking=""), text_block("hello")],
        "model": "claude-haiku-5-5",
        "stop_reason": "end_turn",
        "stop_details": None,
        "usage": SimpleNamespace(
            input_tokens=20,
            cache_read_input_tokens=100,
            cache_creation_input_tokens=7,
            output_tokens=30,
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeMessages:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make(result: Any = None) -> tuple[AnthropicProvider, FakeMessages]:
    messages = FakeMessages(result if result is not None else sdk_message())
    return AnthropicProvider(client=SimpleNamespace(messages=messages)), messages


def request(**overrides: Any) -> ProviderRequest:
    values: dict[str, Any] = {
        "model": "claude-haiku-5-5",
        "tier": "quick",
        "messages": (Message.system("You are an analyst."), Message.user("hi")),
        "max_tokens": 300,
        "timeout_seconds": 12.0,
        "temperature": 0.2,
        "thinking": "disabled",
        "metadata": FrozenDict({"agent": "analyst"}),
    }
    values.update(overrides)
    return ProviderRequest(**values)


def status_error(cls: type[anthropic.APIStatusError], status: int, **headers: str) -> Exception:
    response = httpx2.Response(
        status,
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"),
        headers=headers,
    )
    return cls("boom", response=response, body=None)


def test_request_shape_and_answer() -> None:
    provider, messages = make()
    response = provider.complete(request())
    assert messages.calls == [
        {
            "model": "claude-haiku-5-5",
            "max_tokens": 300,
            "system": "You are an analyst.",
            "messages": [{"role": "user", "content": "hi"}],
            "timeout": 12.0,
        }
    ]  # no temperature, no thinking, no tool_choice: current Claude models reject them
    assert response.content == "hello"  # thinking blocks are skipped
    assert response.model == "claude-haiku-5-5"
    assert response.finish_reason == "stop"
    assert response.usage == Usage(
        input_tokens=20, cached_input_tokens=100, cache_write_tokens=7, output_tokens=30
    )
    assert provider.name == "anthropic"


def test_system_messages_are_joined_and_optional() -> None:
    provider, messages = make()
    provider.complete(
        request(messages=(Message.system("A."), Message.system("B."), Message.user("hi")))
    )
    assert messages.calls[0]["system"] == "A.\n\nB."
    provider.complete(request(messages=(Message.user("hi"),)))
    assert "system" not in messages.calls[1]


def test_schema_uses_output_config_format() -> None:
    provider, messages = make()
    provider.complete(request(schema=Verdict))
    assert messages.calls[0]["output_config"] == {
        "format": {"type": "json_schema", "schema": anthropic.transform_schema(Verdict)}
    }
    schema = messages.calls[0]["output_config"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"rating", "confidence"}


def test_tools_and_tool_turns_are_translated() -> None:
    tool_use = SimpleNamespace(
        type="tool_use", id="toolu_9", name="get_news", input={"symbol": "BBB"}
    )
    provider, messages = make(sdk_message(content=[tool_use], stop_reason="tool_use"))
    spec = ToolSpec(
        name="get_news",
        description="News for a symbol",
        parameters={"type": "object", "properties": {"symbol": {"type": "string"}}},
    )
    first = ToolCall(id="toolu_1", name="get_news", arguments={"symbol": "AAA"})
    second = ToolCall(id="toolu_2", name="get_news", arguments={"symbol": "CCC"})
    response = provider.complete(
        request(
            tools=(spec,),
            messages=(
                Message.user("news?"),
                Message.assistant("Looking.", tool_calls=(first, second)),
                Message.tool("toolu_1", "AAA beat estimates"),
                Message.tool("toolu_2", "CCC missed"),
                Message.user("and?"),
            ),
        )
    )
    sent = messages.calls[0]
    assert sent["tools"] == [
        {
            "name": "get_news",
            "description": "News for a symbol",
            "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}},
        }
    ]
    assert "tool_choice" not in sent
    assert sent["messages"] == [
        {"role": "user", "content": "news?"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Looking."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "get_news",
                    "input": {"symbol": "AAA"},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_2",
                    "name": "get_news",
                    "input": {"symbol": "CCC"},
                },
            ],
        },
        {  # both results travel in ONE user message
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "AAA beat estimates"},
                {"type": "tool_result", "tool_use_id": "toolu_2", "content": "CCC missed"},
            ],
        },
        {"role": "user", "content": "and?"},
    ]
    assert response.tool_calls == (
        ToolCall(id="toolu_9", name="get_news", arguments={"symbol": "BBB"}),
    )
    assert response.finish_reason == "tool_calls"
    assert response.content == ""


def test_text_blocks_are_joined_and_stop_reasons_mapped() -> None:
    provider, _ = make(
        sdk_message(content=[text_block("a"), text_block("b")], stop_reason="max_tokens")
    )
    response = provider.complete(request())
    assert response.content == "ab"
    assert response.finish_reason == "length"


def test_missing_cache_counts_are_zero() -> None:
    usage = SimpleNamespace(
        input_tokens=5,
        cache_read_input_tokens=None,
        cache_creation_input_tokens=None,
        output_tokens=2,
    )
    provider, _ = make(sdk_message(usage=usage))
    assert provider.complete(request()).usage == Usage(input_tokens=5, output_tokens=2)


def test_refusal_is_an_error() -> None:
    details = SimpleNamespace(category="cyber", explanation="declined")
    provider, _ = make(sdk_message(content=[], stop_reason="refusal", stop_details=details))
    with pytest.raises(LLMError, match=r"refus.*cyber") as excinfo:
        provider.complete(request())
    assert not isinstance(excinfo.value, LLMTransientError)


@pytest.mark.parametrize(
    "error",
    [
        status_error(anthropic.RateLimitError, 429),
        status_error(anthropic.InternalServerError, 500),
        status_error(anthropic.APIStatusError, 529),
        anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com")),
        anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")),
    ],
)
def test_transient_sdk_errors(error: Exception) -> None:
    provider, _ = make(error)
    with pytest.raises(LLMTransientError):
        provider.complete(request())


def test_retry_after_is_read_from_the_sdk_error() -> None:
    provider, _ = make(status_error(anthropic.RateLimitError, 429, **{"retry-after": "9"}))
    with pytest.raises(LLMTransientError) as excinfo:
        provider.complete(request())
    assert excinfo.value.retry_after == 9.0


@pytest.mark.parametrize(
    ("cls", "status"),
    [
        (anthropic.BadRequestError, 400),
        (anthropic.AuthenticationError, 401),
        (anthropic.PermissionDeniedError, 403),
        (anthropic.NotFoundError, 404),
    ],
)
def test_other_sdk_status_errors_are_fatal(
    cls: type[anthropic.APIStatusError], status: int
) -> None:
    provider, _ = make(status_error(cls, status))
    with pytest.raises(LLMError, match=str(status)) as excinfo:
        provider.complete(request())
    assert not isinstance(excinfo.value, LLMTransientError)


def test_answer_without_usage_is_an_error() -> None:
    provider, _ = make(sdk_message(usage=None))
    with pytest.raises(LLMError, match="unexpected answer"):
        provider.complete(request())


def test_real_client_is_built_without_sdk_retries() -> None:
    provider = AnthropicProvider(SecretStr("sk-ant-test-not-real"))
    client = provider._client
    assert isinstance(client, anthropic.Anthropic)
    assert client.max_retries == 0  # the gateway owns retries
    assert "sk-ant-test-not-real" not in repr(provider)


# --- Audit (mutation survivors) ------------------------------------------------------------


def test_key_is_passed_to_the_sdk_and_optional() -> None:
    with_key = AnthropicProvider(SecretStr("sk-ant-test-not-real"))
    assert with_key._client.api_key == "sk-ant-test-not-real"
    assert repr(with_key) == "AnthropicProvider()"


def test_served_model_and_unknown_stop_reason_pass_through() -> None:
    provider, _ = make(sdk_message(model="claude-haiku-5-5-served", stop_reason="pause_turn"))
    response = provider.complete(request())
    assert response.model == "claude-haiku-5-5-served"
    assert response.finish_reason == "pause_turn"
    provider, _ = make(sdk_message(model=None))
    assert provider.complete(request()).model == "claude-haiku-5-5"


def test_tool_result_placement_rules() -> None:
    provider, messages = make()
    nested = ToolCall(id="t1", name="screen", arguments={"filter": {"sector": ["Energy"]}})
    provider.complete(
        request(
            messages=(
                Message.tool("t0", "orphan result first"),
                Message.user("plain text"),
                Message.tool("t0b", "after a plain user turn"),
                Message.assistant("", tool_calls=(nested,)),
                Message.tool("t1", "after the assistant turn"),
            )
        )
    )
    sent = messages.calls[0]["messages"]
    assert sent == [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t0", "content": "orphan result first"}
            ],
        },
        {"role": "user", "content": "plain text"},  # a text turn is never merged into
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t0b", "content": "after a plain user turn"}
            ],
        },
        {
            "role": "assistant",  # no empty text block; nested arguments become plain JSON
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "screen",
                    "input": {"filter": {"sector": ["Energy"]}},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "after the assistant turn"}
            ],
        },
    ]
    assert type(sent[3]["content"][0]["input"]["filter"]) is dict


@pytest.mark.parametrize(
    ("header", "expected"), [("0", 0.0), ("0.5", 0.5), ("-3", None), ("soon", None)]
)
def test_retry_after_edge_values(header: str, expected: float | None) -> None:
    provider, _ = make(status_error(anthropic.RateLimitError, 429, **{"retry-after": header}))
    with pytest.raises(LLMTransientError) as excinfo:
        provider.complete(request())
    assert excinfo.value.retry_after == expected


def test_no_retry_after_header_means_none() -> None:
    provider, _ = make(status_error(anthropic.RateLimitError, 429))
    with pytest.raises(
        LLMTransientError, match=r"claude-haiku-5-5: HTTP 429: RateLimitError"
    ) as excinfo:
        provider.complete(request())
    assert excinfo.value.retry_after is None


def test_error_messages_name_the_failure() -> None:
    timeout = anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    provider, _ = make(timeout)
    with pytest.raises(LLMTransientError, match=r"^claude-haiku-5-5: APITimeoutError$"):
        provider.complete(request())
    provider, _ = make(sdk_message(usage=None))
    with pytest.raises(LLMError, match=r"unexpected answer from the provider \(AttributeError\)"):
        provider.complete(request())
    provider, _ = make(sdk_message(content=[], stop_reason="refusal", stop_details=None))
    with pytest.raises(LLMError, match=r"refused the request \(category: None\)"):
        provider.complete(request())
