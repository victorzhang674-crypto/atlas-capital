"""OpenAI-compatible chat-completions provider; DeepSeek is the default endpoint (spec A2).

One `POST <base_url>/chat/completions` per call, over httpx. Structured output uses
`response_format: json_object` with the JSON Schema written into the system text, because
DeepSeek offers no `json_schema` format and refuses a forced tool choice in thinking mode.
"""

import json
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr

from atlas.core.errors import LLMError, LLMTransientError
from atlas.llm.schemas import (
    Message,
    ProviderRequest,
    ProviderResponse,
    ToolCall,
    ToolSpec,
    Usage,
)

MAX_ERROR_BODY_CHARS = 300
"""An error answer's body is cut to this many characters in the exception message."""

SCHEMA_INSTRUCTION = (
    "Answer with one JSON object and nothing else. It must be valid against this JSON Schema:\n"
)

_TRANSIENT_STATUS = frozenset({408, 409, 429})


def _schema_text(schema: type[BaseModel]) -> str:
    return SCHEMA_INSTRUCTION + json.dumps(schema.model_json_schema(), sort_keys=True)


def _plain(value: Any) -> Any:
    """A deep, JSON-plain copy (dicts and lists) of a JSON value."""
    return json.loads(json.dumps(value))


def _wire_message(message: Message) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_call_id is not None:
        wire["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(dict(call.arguments)),
                },
            }
            for call in message.tool_calls
        ]
    return wire


def _wire_messages(request: ProviderRequest) -> list[dict[str, Any]]:
    messages = [_wire_message(m) for m in request.messages]
    if request.schema is None:
        return messages
    text = _schema_text(request.schema)
    if messages and messages[0]["role"] == "system":
        messages[0] = {**messages[0], "content": f"{messages[0]['content']}\n\n{text}"}
    else:
        messages.insert(0, {"role": "system", "content": text})
    return messages


def _wire_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": _plain(dict(tool.parameters)),
        },
    }


def _count(usage: Mapping[str, Any], key: str) -> int:
    value = usage[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"usage.{key} is not a token count: {value!r}")
    return value


def _usage(raw: object) -> Usage:
    """Map a chat-completions `usage` object: cache hits come from DeepSeek's
    `prompt_cache_hit_tokens`, else OpenAI's `prompt_tokens_details.cached_tokens`."""
    if not isinstance(raw, Mapping):
        raise ValueError("no usage object")
    prompt, output = _count(raw, "prompt_tokens"), _count(raw, "completion_tokens")
    cached = 0
    if "prompt_cache_hit_tokens" in raw:
        cached = _count(raw, "prompt_cache_hit_tokens")
    elif isinstance(raw.get("prompt_tokens_details"), Mapping):
        cached = int(raw["prompt_tokens_details"].get("cached_tokens") or 0)
    reasoning = 0
    if isinstance(raw.get("completion_tokens_details"), Mapping):
        reasoning = int(raw["completion_tokens_details"].get("reasoning_tokens") or 0)
    return Usage(
        input_tokens=prompt - cached,
        cached_input_tokens=cached,
        output_tokens=output,
        reasoning_tokens=min(reasoning, output),
    )


def _tool_calls(raw: object) -> tuple[ToolCall, ...]:
    calls = []
    for item in raw if isinstance(raw, list) else []:
        function = item["function"]
        arguments = json.loads(function.get("arguments") or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("tool call arguments are not a JSON object")
        calls.append(ToolCall(id=item["id"], name=function["name"], arguments=arguments))
    return tuple(calls)


def _retry_after(response: httpx.Response) -> float | None:
    """The `Retry-After` header in seconds; an HTTP-date or junk value is ignored."""
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class OpenAICompatibleProvider:
    """Chat completions on an OpenAI-compatible server (`base_url`, bearer `api_key`).

    `http` is an optional `httpx.Client` (tests, proxies). The key is kept as a `SecretStr`
    and never appears in a repr, a log line or an error message.
    """

    name = "openai_compatible"

    def __init__(
        self, base_url: str, api_key: SecretStr, *, http: httpx.Client | None = None
    ) -> None:
        if not api_key.get_secret_value():
            raise ValueError("the OpenAI-compatible provider needs an API key (DEEPSEEK_API_KEY)")
        self._endpoint = base_url.rstrip("/")
        self._url = f"{self._endpoint}/chat/completions"
        self._api_key = api_key
        self._http = http or httpx.Client()

    @property
    def endpoint(self) -> str:
        """The base URL without a trailing slash: which vendor answers (part of the cache key,
        so two OpenAI-compatible vendors never share recordings)."""
        return self._endpoint

    def __repr__(self) -> str:
        return f"OpenAICompatibleProvider(url={self._url!r})"

    def _body(self, request: ProviderRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": _wire_messages(request),
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": False,
        }
        if request.thinking != "omit":
            body["thinking"] = {"type": request.thinking}
        if request.schema is not None:
            body["response_format"] = {"type": "json_object"}
        if request.tools:
            body["tools"] = [_wire_tool(tool) for tool in request.tools]
        return body

    def _scrub(self, text: str) -> str:
        return text.replace(self._api_key.get_secret_value(), "***")[:MAX_ERROR_BODY_CHARS]

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Send `request` once. HTTP 408/409/429/5xx, timeouts and transport errors raise
        `LLMTransientError`; any other failure, or an answer without readable choices and
        usage (it could not be costed), raises `LLMError`."""
        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        try:
            response = self._http.post(
                self._url,
                json=self._body(request),
                headers=headers,
                timeout=request.timeout_seconds,
            )
        except httpx.TransportError as exc:  # timeouts, refused or dropped connections
            raise LLMTransientError(
                f"{request.model}: {type(exc).__name__}: {self._scrub(str(exc))}"
            ) from exc
        status = response.status_code
        if status in _TRANSIENT_STATUS or status >= 500:
            raise LLMTransientError(
                f"{request.model}: HTTP {status}: {self._scrub(response.text)}",
                retry_after=_retry_after(response),
            )
        if status != 200:
            raise LLMError(f"{request.model}: HTTP {status}: {self._scrub(response.text)}")
        try:
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
            return ProviderResponse(
                content=message.get("content") or "",
                usage=_usage(payload.get("usage")),
                model=str(payload.get("model") or request.model),
                tool_calls=_tool_calls(message.get("tool_calls")),
                finish_reason=str(choice.get("finish_reason") or "stop"),
            )
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMError(
                f"{request.model}: unexpected answer from the provider "
                f"({type(exc).__name__}: {self._scrub(str(exc))})"
            ) from exc
