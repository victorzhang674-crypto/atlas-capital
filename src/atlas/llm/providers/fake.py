"""`FakeLLM`: a scripted provider so agents can be unit-tested with no network and no key."""

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from atlas.core.errors import LLMError
from atlas.llm.schemas import ProviderRequest, ProviderResponse, ToolCall, Usage

CHARS_PER_TOKEN = 4
"""FakeLLM's token count: one token per 4 characters, rounded up."""


@dataclass(frozen=True)
class FakeReply:
    """A scripted answer with full control: text, tool calls, and the usage to report
    (`None` = counted from the text lengths)."""

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage | None = None
    finish_reason: str | None = None


Scripted = str | BaseModel | Mapping[str, object] | Exception | FakeReply
"""One scripted reply: text, a model or mapping (sent as JSON), an exception to raise, or a
`FakeReply`."""

_Key = tuple[str | None, str | None]


def _tokens(chars: int) -> int:
    return math.ceil(chars / CHARS_PER_TOKEN)


class FakeLLM:
    """Scripted responses keyed by the request's `agent` and `step` metadata.

    A request is answered by the script for `(agent, step)`, else for `(agent, None)` (any
    step of that agent; a plain `"agent"` key means the same), else by `default`; with none
    of them it raises `LLMError`. A script holding one reply repeats it for ever; a script
    holding a list is consumed in order and raises `LLMError` when exhausted, so a test that
    makes more calls than it scripted fails loudly. Every request is kept in `calls`.

    Reported usage is deterministic: one token per 4 characters (rounded up) of the message
    contents and of the reply, unless a `FakeReply` gives its own.
    """

    name = "fake"

    def __init__(
        self,
        script: Mapping[_Key | str, Scripted | Sequence[Scripted]] | None = None,
        *,
        default: Scripted | None = None,
    ) -> None:
        self._scripts: dict[_Key, Scripted | list[Scripted]] = {}
        self._default = default
        self.calls: list[ProviderRequest] = []
        for key, value in (script or {}).items():
            agent, step = (key, None) if isinstance(key, str) else key
            if isinstance(value, (str, BaseModel, Mapping, Exception, FakeReply)):
                self._scripts[(agent, step)] = value
            else:
                self._scripts[(agent, step)] = list(value)

    def add(self, agent: str | None, step: str | None, *replies: Scripted) -> None:
        """Script `replies` for `(agent, step)` (`step=None`: any step). One reply repeats
        for ever; several are consumed in order. Replaces an earlier script for the key."""
        if not replies:
            raise ValueError("add() needs at least one reply")
        self._scripts[(agent, step)] = replies[0] if len(replies) == 1 else list(replies)

    def _next(self, request: ProviderRequest) -> Scripted:
        agent, step = request.metadata.get("agent"), request.metadata.get("step")
        for key in ((agent, step), (agent, None)):
            if key not in self._scripts:
                continue
            scripted = self._scripts[key]
            if not isinstance(scripted, list):
                return scripted
            if not scripted:
                raise LLMError(f"FakeLLM script for agent={agent!r} step={step!r} is exhausted")
            return scripted.pop(0)
        if self._default is not None:
            return self._default
        raise LLMError(f"FakeLLM has no scripted response for agent={agent!r} step={step!r}")

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Answer `request` from the script (see the class docstring)."""
        self.calls.append(request)
        scripted = self._next(request)
        if isinstance(scripted, Exception):
            raise scripted
        if isinstance(scripted, BaseModel):
            reply = FakeReply(content=scripted.model_dump_json())
        elif isinstance(scripted, FakeReply):
            reply = scripted
        elif isinstance(scripted, str):
            reply = FakeReply(content=scripted)
        else:
            reply = FakeReply(content=json.dumps(dict(scripted)))
        usage = reply.usage or Usage(
            input_tokens=_tokens(sum(len(m.content) for m in request.messages)),
            output_tokens=_tokens(len(reply.content)),
        )
        finish = reply.finish_reason or ("tool_calls" if reply.tool_calls else "stop")
        return ProviderResponse(
            content=reply.content,
            usage=usage,
            model=request.model,
            tool_calls=reply.tool_calls,
            finish_reason=finish,
        )
