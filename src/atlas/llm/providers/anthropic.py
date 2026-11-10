"""Anthropic provider (optional; spec A2) on the official `anthropic` SDK.

Current Claude models reject sampling parameters and a forced tool choice and always reason,
so the request carries no `temperature`, `thinking` or `tool_choice`; structured output uses
`output_config.format` with the SDK's `transform_schema`. The SDK's own retries are switched
off: the gateway retries.
"""

import json
from typing import Any

from pydantic import SecretStr

from atlas.core.errors import ConfigError, LLMError, LLMTransientError
from atlas.llm.schemas import Message, ProviderRequest, ProviderResponse, ToolCall, Usage

_FINISH = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}
_TRANSIENT_STATUS = frozenset({408, 409, 429})


def _sdk() -> Any:
    """The `anthropic` module, imported on first use so the default provider needs no SDK."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - the dev environment installs the SDK
        raise ConfigError(
            "llm.provider is 'anthropic' but the anthropic SDK is not installed; "
            "run `uv sync --group llm`"
        ) from exc
    return anthropic


def _plain(value: Any) -> Any:
    """A deep, JSON-plain copy (dicts and lists) of a JSON value."""
    return json.loads(json.dumps(value))


def _wire_messages(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    """Non-system messages in Messages-API form. Consecutive tool results share one user
    message, as the API expects for parallel tool calls."""
    wire: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
            }
            last = wire[-1] if wire else None
            if (
                last is not None
                and last["role"] == "user"
                and isinstance(last["content"], list)
                and all(b["type"] == "tool_result" for b in last["content"])
            ):
                last["content"].append(block)
            else:
                wire.append({"role": "user", "content": [block]})
        elif message.tool_calls:
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {"type": "tool_use", "id": c.id, "name": c.name, "input": _plain(dict(c.arguments))}
                for c in message.tool_calls
            )
            wire.append({"role": "assistant", "content": blocks})
        else:
            wire.append({"role": message.role, "content": message.content})
    return wire


def _retry_after(exc: Any) -> float | None:
    try:
        seconds = float(exc.response.headers.get("retry-after", ""))
    except (AttributeError, ValueError, TypeError):
        return None
    return seconds if seconds >= 0 else None


class AnthropicProvider:
    """Claude through the Messages API.

    `api_key` comes from `ANTHROPIC_API_KEY` (`None` lets the SDK resolve its own
    credentials); `client` replaces the SDK client (tests). The key never appears in a repr,
    a log line or an error message.
    """

    name = "anthropic"

    def __init__(self, api_key: SecretStr | None = None, *, client: Any | None = None) -> None:
        if client is None:
            key = api_key.get_secret_value() if api_key is not None else None
            client = _sdk().Anthropic(api_key=key, max_retries=0)
        self._client = client

    def __repr__(self) -> str:
        return "AnthropicProvider()"

    def _kwargs(self, request: ProviderRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": request.model, "max_tokens": request.max_tokens}
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        if system:
            kwargs["system"] = system
        kwargs["messages"] = _wire_messages(request.messages)
        if request.tools:
            kwargs["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": _plain(dict(tool.parameters)),
                }
                for tool in request.tools
            ]
        if request.schema is not None:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": _sdk().transform_schema(request.schema)}
            }
        kwargs["timeout"] = request.timeout_seconds
        return kwargs

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Send `request` once. Rate limits, 5xx, timeouts and connection errors raise
        `LLMTransientError`; other API errors, a refusal, or an answer without usage raise
        `LLMError`."""
        sdk = _sdk()
        try:
            message = self._client.messages.create(**self._kwargs(request))
        except sdk.APIStatusError as exc:
            status = exc.status_code
            text = f"{request.model}: HTTP {status}: {type(exc).__name__}"
            if status in _TRANSIENT_STATUS or status >= 500:
                raise LLMTransientError(text, retry_after=_retry_after(exc)) from exc
            raise LLMError(text) from exc
        except sdk.APIConnectionError as exc:  # includes APITimeoutError
            raise LLMTransientError(f"{request.model}: {type(exc).__name__}") from exc
        if message.stop_reason == "refusal":
            category = getattr(message.stop_details, "category", None)
            raise LLMError(f"{request.model} refused the request (category: {category})")
        try:
            usage = Usage(
                input_tokens=message.usage.input_tokens,
                cached_input_tokens=message.usage.cache_read_input_tokens or 0,
                cache_write_tokens=message.usage.cache_creation_input_tokens or 0,
                output_tokens=message.usage.output_tokens,
            )
            text_parts = [b.text for b in message.content if b.type == "text"]
            calls = tuple(
                ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
                for b in message.content
                if b.type == "tool_use"
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise LLMError(
                f"{request.model}: unexpected answer from the provider ({type(exc).__name__})"
            ) from exc
        return ProviderResponse(
            content="".join(text_parts),
            usage=usage,
            model=str(message.model or request.model),
            tool_calls=calls,
            finish_reason=_FINISH.get(message.stop_reason, str(message.stop_reason)),
        )
