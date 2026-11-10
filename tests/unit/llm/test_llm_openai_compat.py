"""OpenAI-compatible provider (DeepSeek by default) against a mocked HTTP API (respx)."""

import json

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr

from atlas.core.errors import LLMError, LLMTransientError
from atlas.core.types.base import FrozenDict
from atlas.llm.providers.openai_compat import OpenAICompatibleProvider
from atlas.llm.schemas import Message, ProviderRequest, ToolCall, ToolSpec, Usage

BASE = "https://api.deepseek.com"
URL = f"{BASE}/chat/completions"
KEY = "sk-test-not-a-real-key"


class Verdict(BaseModel):
    rating: str
    confidence: float


def provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(BASE, SecretStr(KEY))


def request(**overrides: object) -> ProviderRequest:
    values: dict[str, object] = {
        "model": "deepseek-flash",
        "tier": "quick",
        "messages": (Message.system("You are an analyst."), Message.user("hi")),
        "max_tokens": 300,
        "timeout_seconds": 12.0,
        "temperature": 0.2,
        "thinking": "disabled",
        "metadata": FrozenDict({"agent": "analyst"}),
    }
    values.update(overrides)
    return ProviderRequest(**values)  # type: ignore[arg-type]


def answer(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": "x",
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "hello"},
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "prompt_cache_hit_tokens": 100,
            "prompt_cache_miss_tokens": 20,
            "completion_tokens": 30,
            "total_tokens": 150,
            "completion_tokens_details": {"reasoning_tokens": 12},
        },
    }
    body.update(overrides)
    return body


@respx.mock
def test_request_shape_and_answer() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=answer()))
    response = provider().complete(request())
    sent = route.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert sent.headers["content-type"] == "application/json"
    body = json.loads(sent.content)
    assert body == {
        "model": "deepseek-flash",
        "messages": [
            {"role": "system", "content": "You are an analyst."},
            {"role": "user", "content": "hi"},
        ],
        "max_tokens": 300,
        "temperature": 0.2,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    assert response.content == "hello"
    assert response.model == "deepseek-flash"
    assert response.finish_reason == "stop"
    assert response.tool_calls == ()
    # prompt 120 = 100 cache hits + 20 misses; 12 of the 30 output tokens were reasoning.
    assert response.usage == Usage(
        input_tokens=20, cached_input_tokens=100, output_tokens=30, reasoning_tokens=12
    )
    assert provider().name == "openai_compatible"


@respx.mock
def test_base_url_trailing_slash_and_timeout() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=answer()))
    OpenAICompatibleProvider(BASE + "/", SecretStr(KEY)).complete(request())
    assert route.called
    assert route.calls.last.request.extensions["timeout"]["read"] == 12.0


@respx.mock
@pytest.mark.parametrize(
    ("thinking", "expected"), [("enabled", {"type": "enabled"}), ("omit", None)]
)
def test_thinking_field(thinking: str, expected: dict[str, str] | None) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=answer()))
    provider().complete(request(thinking=thinking))
    body = json.loads(route.calls.last.request.content)
    assert body.get("thinking") == expected


@respx.mock
def test_schema_asks_for_json_and_shows_the_schema() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=answer()))
    provider().complete(request(schema=Verdict))
    body = json.loads(route.calls.last.request.content)
    assert body["response_format"] == {"type": "json_object"}
    system = body["messages"][0]
    assert system["role"] == "system"
    assert system["content"].startswith("You are an analyst.")
    assert "JSON" in system["content"]
    assert json.dumps(Verdict.model_json_schema(), sort_keys=True) in system["content"]
    assert len(body["messages"]) == 2  # the schema text joins the system message


@respx.mock
def test_schema_without_a_system_message_adds_one() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=answer()))
    provider().complete(request(schema=Verdict, messages=(Message.user("hi"),)))
    messages = json.loads(route.calls.last.request.content)["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "JSON" in messages[0]["content"]


@respx.mock
def test_tools_and_tool_messages_are_translated() -> None:
    tool_answer = answer(
        choices=[
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_9",
                            "type": "function",
                            "function": {"name": "get_news", "arguments": '{"symbol": "BBB"}'},
                        }
                    ],
                },
            }
        ]
    )
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=tool_answer))
    spec = ToolSpec(
        name="get_news",
        description="News for a symbol",
        parameters={"type": "object", "properties": {"symbol": {"type": "string"}}},
    )
    earlier = ToolCall(id="call_1", name="get_news", arguments={"symbol": "AAA"})
    response = provider().complete(
        request(
            tools=(spec,),
            messages=(
                Message.user("news?"),
                Message.assistant("", tool_calls=(earlier,)),
                Message.tool("call_1", "AAA beat estimates"),
            ),
        )
    )
    body = json.loads(route.calls.last.request.content)
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_news",
                "description": "News for a symbol",
                "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}},
            },
        }
    ]
    assert body["messages"][1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_news", "arguments": '{"symbol": "AAA"}'},
            }
        ],
    }
    assert body["messages"][2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "AAA beat estimates",
    }
    assert response.content == ""
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls == (
        ToolCall(id="call_9", name="get_news", arguments={"symbol": "BBB"}),
    )


@respx.mock
def test_openai_style_cached_tokens() -> None:
    usage = {
        "prompt_tokens": 50,
        "completion_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": 40},
    }
    respx.post(URL).mock(return_value=httpx.Response(200, json=answer(usage=usage)))
    assert provider().complete(request()).usage == Usage(
        input_tokens=10, cached_input_tokens=40, output_tokens=5
    )


@respx.mock
def test_usage_without_cache_details() -> None:
    usage = {"prompt_tokens": 50, "completion_tokens": 5}
    respx.post(URL).mock(return_value=httpx.Response(200, json=answer(usage=usage)))
    assert provider().complete(request()).usage == Usage(input_tokens=50, output_tokens=5)


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 502, 503, 529])
def test_rate_limit_and_server_errors_are_transient(status: int) -> None:
    respx.post(URL).mock(return_value=httpx.Response(status, json={"error": {"message": "busy"}}))
    with pytest.raises(LLMTransientError, match=str(status)) as excinfo:
        provider().complete(request())
    assert excinfo.value.retry_after is None


@respx.mock
def test_retry_after_header_is_read() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "7"}, text="slow")
    )
    with pytest.raises(LLMTransientError) as excinfo:
        provider().complete(request())
    assert excinfo.value.retry_after == 7.0


@respx.mock
def test_unreadable_retry_after_is_ignored() -> None:
    headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
    respx.post(URL).mock(return_value=httpx.Response(503, headers=headers, text="slow"))
    with pytest.raises(LLMTransientError) as excinfo:
        provider().complete(request())
    assert excinfo.value.retry_after is None


@respx.mock
@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("refused"),
        httpx.RemoteProtocolError("eof"),
    ],
)
def test_timeouts_and_dropped_connections_are_transient(error: Exception) -> None:
    respx.post(URL).mock(side_effect=error)
    with pytest.raises(LLMTransientError):
        provider().complete(request())


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 402, 404, 422])
def test_client_errors_are_fatal_and_never_show_the_key(status: int) -> None:
    body = {"error": {"message": f"bad request, key {KEY} rejected"}}
    respx.post(URL).mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(LLMError, match=str(status)) as excinfo:
        provider().complete(request())
    assert not isinstance(excinfo.value, LLMTransientError)
    assert KEY not in str(excinfo.value)
    assert "bad request" in str(excinfo.value)


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]},  # no usage
        {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        {"usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        {"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": "many"}},
        {"choices": [{"message": {"content": "hi"}}], "usage": {"completion_tokens": 1}},
        ["not", "an", "object"],
    ],
)
def test_answer_that_cannot_be_costed_or_read_is_an_error(body: object) -> None:  # review focus 2
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(LLMError, match="unexpected answer"):
        provider().complete(request())


@respx.mock
def test_non_json_answer_is_an_error() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(LLMError, match="unexpected answer"):
        provider().complete(request())


@respx.mock
def test_bad_tool_arguments_are_an_error() -> None:
    broken = answer(
        choices=[
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c",
                            "type": "function",
                            "function": {"name": "f", "arguments": "{oops"},
                        }
                    ],
                },
            }
        ]
    )
    respx.post(URL).mock(return_value=httpx.Response(200, json=broken))
    with pytest.raises(LLMError, match="unexpected answer"):
        provider().complete(request())


def test_key_is_not_in_repr() -> None:
    assert KEY not in repr(provider())
    assert KEY not in str(vars(provider()))


def test_empty_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="API key"):
        OpenAICompatibleProvider(BASE, SecretStr(""))


# --- Audit (mutation survivors) ------------------------------------------------------------


@respx.mock
def test_served_model_and_missing_optional_fields() -> None:
    served = answer(model="DeepSeek-V4.1-Flash-1017")
    respx.post(URL).mock(return_value=httpx.Response(200, json=served))
    assert provider().complete(request()).model == "DeepSeek-V4.1-Flash-1017"
    bare = {
        "choices": [{"message": {"content": "hi"}}],  # no model, no finish_reason
        "usage": {"prompt_tokens": 3, "completion_tokens": 0},  # zero output tokens is valid
    }
    respx.post(URL).mock(return_value=httpx.Response(200, json=bare))
    response = provider().complete(request())
    assert (response.model, response.finish_reason) == ("deepseek-flash", "stop")
    assert response.usage == Usage(input_tokens=3, output_tokens=0)


@respx.mock
def test_null_cache_and_reasoning_details_count_as_zero() -> None:
    usage = {
        "prompt_tokens": 50,
        "completion_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": None},
        "completion_tokens_details": {"reasoning_tokens": None},
    }
    respx.post(URL).mock(return_value=httpx.Response(200, json=answer(usage=usage)))
    assert provider().complete(request()).usage == Usage(input_tokens=50, output_tokens=5)


@respx.mock
@pytest.mark.parametrize("bad", [-1, True, 1.5, None])
def test_token_counts_must_be_whole_non_negative_numbers(bad: object) -> None:
    usage = {"prompt_tokens": 5, "completion_tokens": bad}
    respx.post(URL).mock(return_value=httpx.Response(200, json=answer(usage=usage)))
    with pytest.raises(LLMError, match=r"unexpected answer.*completion_tokens"):
        provider().complete(request())


@respx.mock
def test_tool_call_without_arguments_and_with_nested_arguments() -> None:
    reply = answer(
        choices=[
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "today", "arguments": ""},
                        }
                    ],
                },
            }
        ]
    )
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=reply))
    nested = ToolCall(
        id="c0", name="screen", arguments={"filter": {"sector": ["Energy", "Utilities"]}}
    )
    spec = ToolSpec(
        name="screen",
        description="Screen",
        parameters={"type": "object", "properties": {"filter": {"type": "object"}}},
    )
    response = provider().complete(
        request(
            tools=(spec,),
            messages=(
                Message.user("go"),
                Message.assistant("", tool_calls=(nested,)),
                Message.tool("c0", "done"),
            ),
        )
    )
    assert response.tool_calls == (ToolCall(id="c1", name="today", arguments={}),)
    body = json.loads(route.calls.last.request.content)
    sent_call = body["messages"][1]["tool_calls"][0]["function"]
    assert json.loads(sent_call["arguments"]) == {"filter": {"sector": ["Energy", "Utilities"]}}
    assert body["tools"][0]["function"]["parameters"]["properties"] == {
        "filter": {"type": "object"}
    }


@respx.mock
def test_tool_arguments_that_are_not_an_object_are_an_error() -> None:
    reply = answer(
        choices=[
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c",
                            "type": "function",
                            "function": {"name": "f", "arguments": "[1]"},
                        }
                    ],
                }
            }
        ]
    )
    respx.post(URL).mock(return_value=httpx.Response(200, json=reply))
    with pytest.raises(LLMError, match=r"unexpected answer.*not a JSON object"):
        provider().complete(request())


@respx.mock
@pytest.mark.parametrize(("header", "expected"), [("0", 0.0), ("0.5", 0.5), ("-3", None)])
def test_retry_after_edge_values(header: str, expected: float | None) -> None:
    respx.post(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": header}))
    with pytest.raises(LLMTransientError) as excinfo:
        provider().complete(request())
    assert excinfo.value.retry_after == expected


@respx.mock
def test_error_messages_name_the_failure_and_mask_the_key() -> None:
    respx.post(URL).mock(side_effect=httpx.ReadTimeout(f"timed out with {KEY}"))
    with pytest.raises(
        LLMTransientError, match=r"deepseek-flash: ReadTimeout: timed out"
    ) as transient:
        provider().complete(request())
    assert KEY not in str(transient.value)
    assert "***" in str(transient.value)
    respx.post(URL).mock(return_value=httpx.Response(200, json={"usage": {}}))
    with pytest.raises(
        LLMError, match=r"unexpected answer from the provider \(KeyError: 'choices'\)"
    ):
        provider().complete(request())
    with pytest.raises(ValueError, match=r"^the OpenAI-compatible provider needs an API key"):
        OpenAICompatibleProvider(BASE, SecretStr(""))
