"""Value types of the LLM gateway: messages, tools, token usage, results and the provider seam.

Token counts are whole tokens as the provider reports them. Costs are USD. Latency is
milliseconds of wall time.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import BaseModel, Field, model_validator

from atlas.core.config import LLMTier
from atlas.core.types.base import AtlasModel, FrozenDict, FrozenMap

Tier = LLMTier
"""Model tier of a call: `quick` (cheap, fast) or `deep` (long-form reasoning)."""

Role = Literal["system", "user", "assistant", "tool"]

ThinkingMode = Literal["enabled", "disabled", "omit"]

_Count = Annotated[int, Field(ge=0)]


class ToolCall(AtlasModel):
    """A tool invocation asked for by the model: the provider's call `id`, the tool `name`
    and its JSON-object `arguments`."""

    id: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]
    arguments: FrozenMap[str, Any] = Field(default_factory=lambda: FrozenDict({}))


class Message(AtlasModel):
    """One chat message.

    `assistant` messages may carry `tool_calls`; a `tool` message is the result of one call
    and names it in `tool_call_id`. Untrusted text (news, filings, tool output) belongs in
    `atlas.llm.hygiene.data_block`s inside `content`.
    """

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def _check_tool_fields(self) -> Self:
        if (self.role == "tool") != (self.tool_call_id is not None):
            raise ValueError("tool_call_id is required on a tool message and only allowed there")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("tool_calls are only allowed on an assistant message")
        return self

    @classmethod
    def system(cls, content: str) -> Self:
        """A system message (instructions from Atlas code)."""
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> Self:
        """A user message."""
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str, *, tool_calls: tuple[ToolCall, ...] = ()) -> Self:
        """An assistant message, optionally with the tool calls it asked for."""
        return cls(role="assistant", content=content, tool_calls=tool_calls)

    @classmethod
    def tool(cls, tool_call_id: str, content: str) -> Self:
        """The result of the tool call `tool_call_id`."""
        return cls(role="tool", content=content, tool_call_id=tool_call_id)


class ToolSpec(AtlasModel):
    """A tool offered to the model: `parameters` is the JSON Schema of its arguments object."""

    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")]
    description: str
    parameters: FrozenMap[str, Any]


class Usage(AtlasModel):
    """Tokens of one or more provider responses.

    `input_tokens` are uncached prompt tokens, `cached_input_tokens` prompt tokens read from
    the provider's cache, `cache_write_tokens` prompt tokens written to it; the three are
    disjoint. `output_tokens` are generated tokens and include `reasoning_tokens`.
    """

    input_tokens: _Count = 0
    cached_input_tokens: _Count = 0
    cache_write_tokens: _Count = 0
    output_tokens: _Count = 0
    reasoning_tokens: _Count = 0

    @model_validator(mode="after")
    def _reasoning_within_output(self) -> Self:
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError(
                f"reasoning_tokens ({self.reasoning_tokens}) are part of output_tokens "
                f"({self.output_tokens}) and cannot exceed them"
            )
        return self

    @property
    def prompt_tokens(self) -> int:
        """All prompt tokens: uncached + cache reads + cache writes."""
        return self.input_tokens + self.cached_input_tokens + self.cache_write_tokens

    @property
    def total_tokens(self) -> int:
        """Prompt tokens plus output tokens."""
        return self.prompt_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


class LLMResult(AtlasModel):
    """What `LLMGateway.complete` returns.

    `content` is the model's text; `parsed` the validated instance of the requested schema
    (else `None`); `usage` and `cost_usd` (USD) sum every billed provider response of the
    call (`attempts` of them: 2 after a repair retry, 0 for a cache hit); `latency_ms` is wall
    time of the whole call in milliseconds, waits between retries included; `cache_hit` is
    `True` when the record/replay cache (Stage 25) answered: no provider was called, `usage`
    is empty and `cost_usd` is 0.
    """

    content: str
    parsed: BaseModel | None = None
    usage: Usage
    cost_usd: Annotated[float, Field(ge=0)]
    model: str
    latency_ms: Annotated[float, Field(ge=0)]
    cache_hit: bool = False
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"
    attempts: Annotated[int, Field(ge=0)] = 1


@dataclass(frozen=True)
class ProviderRequest:
    """One request to a provider, already resolved by the gateway: `model` is the tier's
    model id, `max_tokens` the output token cap, `timeout_seconds` the HTTP timeout,
    `schema` the Pydantic model the answer must match (the provider asks for JSON of that
    schema; the gateway validates), `metadata` free-form labels (`agent`, `step`, ...)."""

    model: str
    tier: Tier
    messages: tuple[Message, ...]
    max_tokens: int
    timeout_seconds: float
    schema: type[BaseModel] | None = None
    tools: tuple[ToolSpec, ...] = ()
    temperature: float = 0.0
    thinking: ThinkingMode = "omit"
    metadata: Mapping[str, str] = field(default_factory=lambda: FrozenDict({}))


@dataclass(frozen=True)
class ProviderResponse:
    """One provider answer: the text, any tool calls, the billed `usage`, the `model` that
    answered and the provider's reason for stopping (`stop`, `length`, `tool_calls`, ...)."""

    content: str
    usage: Usage
    model: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"


class LLMProvider(Protocol):
    """A model vendor behind the gateway.

    `complete` makes exactly one attempt. It raises `LLMTransientError` for failures worth
    retrying (rate limit, 5xx, timeout, dropped connection) and `LLMError` for the rest; the
    gateway owns retries, budgets, validation and costing.
    """

    @property
    def name(self) -> str:
        """Short provider name for the usage ledger (`openai_compatible`, `anthropic`, `fake`)."""
        ...

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Send `request` once and return the answer."""
        ...
