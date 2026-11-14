"""`BaseAgent`: one consistent way to build an agent (Stage 26).

An agent is a role (`name`, model `tier`), a versioned prompt template, point-in-time tools,
a structured output (`output_schema`), an evidence trail and a persisted trace. `run(ctx)`:

1. gathers pre-fetched evidence (`gather`, checked against `ctx.as_of`) and renders the
   prompt (`system` and `user` blocks) with the agent's inputs;
2. asks the model through `LLMGateway` with the output schema and the agent's tools;
3. checks the output's citations against the evidence the run showed the model;
4. persists an `AgentTrace` and, given an `AuditLog`, records an `AGENT_REPORT` event.

Any `AtlasError` (an `LLMError` from the gateway, a `ConfigError` from the prompt, a
`LookAheadError` from a tool) is recorded as a `failed` trace and re-raised: no output, so no
new risk (spec §3 principle 6).
"""

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel
from ulid import ULID

from atlas.agents.context import AgentContext
from atlas.agents.evidence import (
    CitationReport,
    EvidenceLedger,
    cap_confidence,
    check_citations,
)
from atlas.agents.prompting import PromptRegistry, PromptTemplate, RenderedPrompt
from atlas.agents.tools import ToolRegistry, default_tools
from atlas.agents.trace import AgentTrace, ToolCallRecord, TraceStatus, TraceStore
from atlas.core.audit import AuditLog, EventType, canonical_json
from atlas.core.clock import Clock, LiveClock
from atlas.core.config import AgentsConfig, AtlasConfig
from atlas.core.errors import AtlasError, ConfigError, LLMError
from atlas.core.logging import get_logger
from atlas.core.types.research import Evidence
from atlas.data.view import guard_evidence
from atlas.llm.gateway import LLMGateway
from atlas.llm.schemas import LLMResult, Message, Tier, ToolSpec, Usage

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

RESERVED_VARIABLES = ("agent", "as_of", "evidence", "output_schema")
"""Template variables `BaseAgent` sets itself; agent inputs cannot override them."""

WRAP_UP = (
    "You have used all {rounds} tool rounds. No more tools are available: answer now with the "
    "final JSON object, using only the data already shown in this conversation."
)
"""The user turn sent when the model still asks for tools after the last tool round."""

MAX_LISTED_IDS = 200
"""A repair message lists at most this many known evidence ids."""


def _repair_text(citations: CitationReport, ledger: EvidenceLedger) -> str:
    """The user turn asking the model to fix its citations."""
    ids = ledger.ids
    listed = ", ".join(ids[:MAX_LISTED_IDS]) + (", ..." if len(ids) > MAX_LISTED_IDS else "")
    return (
        "Your answer has citation problems:\n"
        f"{citations.describe()}\n"
        "Cite only these evidence ids (shown as [E:<id>] in the data and tool results): "
        f"{listed or '(none)'}.\n"
        "Every claim needs at least one of them; drop a claim you cannot support. Answer again "
        "with the corrected JSON object only."
    )


_log = get_logger(__name__)


@dataclass(frozen=True)
class AgentOutput[OutT: BaseModel]:
    """What `BaseAgent.run` returns: the parsed `output`, the `evidence` it cites (known items,
    in citation order), `low_quality` (citations still invalid after the repairs: the output's
    confidence was capped), the `citations` check and the run's `trace` (cost, usage, ...)."""

    output: OutT
    evidence: tuple[Evidence, ...]
    low_quality: bool
    citations: CitationReport
    trace: AgentTrace

    @property
    def cost_usd(self) -> float:
        """USD spent by the run's LLM calls (0 for cache hits)."""
        return self.trace.cost_usd

    @property
    def usage(self) -> Usage:
        """Tokens of the run's LLM calls."""
        return self.trace.usage


@dataclass
class _Run:
    """Mutable state of one run while it is in progress."""

    ledger: EvidenceLedger
    results: list[LLMResult] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    inputs_digest: str = ""


class BaseAgent[OutT: BaseModel]:
    """Base class of every LLM agent.

    Subclasses set the class attributes `name` (lower snake case; labels LLM calls, traces
    and audit events), `tier` (`quick` or `deep`), `prompt_template` (a template name in the
    prompt registry), `output_schema` (the Pydantic model of the answer) and `tools` (names
    in the tool registry; empty: no tools), and may override `gather` (evidence to show the
    model up front) and `prompt_inputs` (template variables; default: `ctx.inputs`).

    `config` defaults to `AtlasConfig()`, `prompts` to the shipped templates, `tool_registry`
    to `default_tools()`, `traces` to an in-memory `TraceStore`, `clock` (trace times) to
    `LiveClock()`. With `audit`, every run records an `AGENT_REPORT` event (this needs an
    active run context).
    """

    name: ClassVar[str] = ""
    tier: ClassVar[Tier] = "quick"
    prompt_template: ClassVar[str] = ""
    output_schema: type[OutT]
    tools: ClassVar[tuple[str, ...]] = ()

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        config: AtlasConfig | None = None,
        prompts: PromptRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
        traces: TraceStore | None = None,
        audit: AuditLog | None = None,
        clock: Clock | None = None,
    ) -> None:
        cls = type(self)
        if not _NAME_RE.fullmatch(cls.name):
            raise ConfigError(f"{cls.__name__}: name {cls.name!r} must be lower snake case")
        schema = getattr(cls, "output_schema", None)
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            raise ConfigError(f"{cls.__name__}: output_schema must be a Pydantic model class")
        self._gateway = gateway
        self._settings: AgentsConfig = (config or AtlasConfig()).agents
        self._prompts = prompts or PromptRegistry()
        self._template: PromptTemplate = self._prompts.get(cls.prompt_template)
        self._registry = tool_registry if tool_registry is not None else default_tools()
        self._specs: tuple[ToolSpec, ...] = self._registry.specs(cls.tools)
        self._versions = f"{self._template.label}|{self._registry.versions_label(cls.tools)}"
        self._traces = traces if traces is not None else TraceStore()
        self._audit = audit
        self._clock: Clock = clock or LiveClock()

    @property
    def prompt_version(self) -> str:
        """The prompt version sent with every call (`<template>@v<N>|<tool>@v<M>,...`): part
        of the LLM cache key, so a template or tool version bump is a cache miss."""
        return self._versions

    @property
    def traces(self) -> TraceStore:
        """Where this agent's traces go."""
        return self._traces

    # --- hooks ----------------------------------------------------------------------------------

    def gather(self, ctx: AgentContext) -> Sequence[Evidence]:
        """Evidence to show the model up front (in the `evidence` template variable)."""
        return ()

    def prompt_inputs(self, ctx: AgentContext) -> Mapping[str, Any]:
        """Template variables besides the reserved ones (default: `ctx.inputs`)."""
        return ctx.inputs

    # --- the run --------------------------------------------------------------------------------

    def run(self, ctx: AgentContext) -> AgentOutput[OutT]:
        """Run the agent for `ctx` (see the module docstring). Raises what stopped it."""
        started = self._clock.now()
        # A record id, not a decision: random even under a seeded id source (a same-seed
        # backtest re-run must not collide with its earlier rows in `agent_runs`).
        agent_run_id = str(ULID())
        state = _Run(ledger=EvidenceLedger())
        try:
            state.ledger.add(guard_evidence(list(self.gather(ctx)), ctx.as_of))
            rendered = self._render(ctx, state)
            output, citations = self._converse(ctx, rendered, state)
        except AtlasError as exc:
            self._finish(ctx, agent_run_id, started, state, status="failed", error=str(exc))
            raise
        low_quality = not citations.ok
        status: TraceStatus = "low_quality" if low_quality else "ok"
        if low_quality:
            output = cap_confidence(output, self._settings.low_quality_confidence_cap)
            _log.warning(
                "agent.low_quality",
                agent=self.name,
                agent_run_id=agent_run_id,
                unknown=list(citations.unknown),
                uncited=list(citations.uncited_claims),
            )
        trace = self._finish(
            ctx, agent_run_id, started, state, status=status, output=output, citations=citations
        )
        return AgentOutput(
            output=output,
            evidence=state.ledger.select(citations.cited),
            low_quality=low_quality,
            citations=citations,
            trace=trace,
        )

    def _render(self, ctx: AgentContext, state: _Run) -> RenderedPrompt:
        variables = dict(self.prompt_inputs(ctx))
        variables.update(
            agent=self.name,
            as_of=ctx.day,
            evidence=state.ledger.items,
            output_schema=self.output_schema.__name__,
        )
        rendered = self._prompts.render(self.prompt_template, variables)
        payload = {
            "agent": self.name,
            "cutoff": ctx.as_of.cutoff,
            "prompt": rendered.template.label,
            "prompt_digest": rendered.template.digest,
            "messages": [m.model_dump(mode="json") for m in rendered.messages],
        }
        state.inputs_digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        return rendered

    def _ask(
        self, messages: Sequence[Message], state: _Run, *, step: str, tools: Sequence[ToolSpec]
    ) -> LLMResult:
        result = self._gateway.complete(
            messages,
            tier=self.tier,
            schema=self.output_schema,
            tools=tools or None,
            metadata={"agent": self.name, "step": step, "prompt_version": self._versions},
        )
        state.results.append(result)
        return result

    def _parsed(self, result: LLMResult) -> OutT:
        if result.parsed is None or not isinstance(result.parsed, self.output_schema):
            raise LLMError(f"{self.name}: the model's answer has no {self.output_schema.__name__}")
        return result.parsed

    def _converse(
        self, ctx: AgentContext, rendered: RenderedPrompt, state: _Run
    ) -> tuple[OutT, CitationReport]:
        messages: list[Message] = list(rendered.messages)
        result = self._tool_loop(ctx, messages, state)
        output = self._parsed(result)
        citations = check_citations(output, state.ledger.ids)
        for _ in range(self._settings.evidence_repair_retries):
            if citations.ok:
                break
            messages.append(Message.assistant(result.content))
            messages.append(Message.user(_repair_text(citations, state.ledger)))
            result = self._ask(messages, state, step="evidence_repair", tools=())
            if result.tool_calls:
                raise LLMError(f"{self.name}: the model asked for tools in the evidence repair")
            output = self._parsed(result)
            citations = check_citations(output, state.ledger.ids)
        return output, citations

    def _tool_loop(self, ctx: AgentContext, messages: list[Message], state: _Run) -> LLMResult:
        """Rounds of model turns and tool calls until the model answers; after
        `max_tool_rounds` rounds that asked for tools, one wrap-up turn with no tools."""
        limit = self._settings.max_tool_rounds
        rounds = 0
        result = self._ask(messages, state, step="round-1", tools=self._specs)
        while result.tool_calls:
            if not self._specs:
                raise LLMError(f"{self.name}: the model asked for tools, but no tools are offered")
            rounds += 1
            messages.append(Message.assistant(result.content, tool_calls=result.tool_calls))
            for call in result.tool_calls:
                outcome = self._registry.call(ctx, call, allowed=self.tools)
                state.ledger.add(outcome.evidence)
                state.tool_calls.append(
                    ToolCallRecord(
                        round=rounds,
                        call_id=call.id,
                        name=call.name,
                        arguments=call.arguments,
                        evidence_ids=tuple(e.id for e in outcome.evidence),
                        error=outcome.error,
                    )
                )
                messages.append(Message.tool(call.id, outcome.text))
            if rounds >= limit:
                messages.append(Message.user(WRAP_UP.format(rounds=rounds)))
                result = self._ask(messages, state, step="wrap_up", tools=())
                if result.tool_calls:
                    raise LLMError(
                        f"{self.name}: the model still asked for tools after the wrap-up turn "
                        f"({rounds} tool rounds)"
                    )
                break
            result = self._ask(messages, state, step=f"round-{rounds + 1}", tools=self._specs)
        return result

    def _finish(
        self,
        ctx: AgentContext,
        agent_run_id: str,
        started: Any,
        state: _Run,
        *,
        status: TraceStatus,
        output: BaseModel | None = None,
        citations: CitationReport | None = None,
        error: str | None = None,
    ) -> AgentTrace:
        usage = Usage()
        for result in state.results:
            usage += result.usage
        trace = AgentTrace(
            agent_run_id=agent_run_id,
            run_id=ctx.run_id,
            agent=self.name,
            tier=self.tier,
            as_of=ctx.day,
            cutoff=ctx.as_of.cutoff,
            started_at=started,
            finished_at=self._clock.now(),
            status=status,
            prompt_name=self._template.name,
            prompt_version=self._template.version,
            prompt_digest=self._template.digest,
            versions=self._versions,
            inputs_digest=state.inputs_digest,
            tool_calls=tuple(state.tool_calls),
            evidence_ids=state.ledger.ids,
            cited_ids=citations.cited if citations else (),
            unknown_ids=citations.unknown if citations else (),
            uncited_claims=citations.uncited_claims if citations else (),
            output_json=output.model_dump_json() if output is not None else None,
            usage=usage,
            cost_usd=sum(r.cost_usd for r in state.results),
            llm_calls=len(state.results),
            cache_hits=sum(1 for r in state.results if r.cache_hit),
            error=error,
        )
        self._traces.save(trace)
        _log.info(
            "agent.run",
            agent=self.name,
            agent_run_id=agent_run_id,
            status=status,
            llm_calls=trace.llm_calls,
            tool_calls=len(trace.tool_calls),
            cost_usd=trace.cost_usd,
        )
        if self._audit is not None:
            self._audit.record(
                EventType.AGENT_REPORT,
                self.name,
                {
                    "agent_run_id": agent_run_id,
                    "status": status,
                    "as_of": ctx.day,
                    "prompt": self._template.label,
                    "versions": self._versions,
                    "output_sha256": (
                        hashlib.sha256(trace.output_json.encode()).hexdigest()
                        if trace.output_json is not None
                        else None
                    ),
                    "cost_usd": trace.cost_usd,
                    "error": error,
                },
            )
        return trace
