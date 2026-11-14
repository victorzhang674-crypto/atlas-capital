"""Agent traces: one record per agent run (inputs digest, prompt version, tool calls, output,
cost), kept in the lake's `agent_runs` table or in memory."""

import json
import threading
from collections.abc import Mapping
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import Field

from atlas.core.audit import canonical_json
from atlas.core.storage import Database
from atlas.core.types.base import AtlasModel, FrozenDict, FrozenMap, UtcDatetime
from atlas.llm.schemas import Usage

TraceStatus = Literal["ok", "low_quality", "failed"]
"""`ok`; `low_quality` (citations still invalid after the repairs, confidence capped);
`failed` (an error stopped the run: no output, nothing to act on)."""


class ToolCallRecord(AtlasModel):
    """One tool call of a run: the tool `round` (1-based), the provider's `call_id`, the tool
    `name` and `arguments`, the ids of the evidence it returned and its `error`, if any."""

    round: Annotated[int, Field(ge=1)]
    call_id: str
    name: str
    arguments: FrozenMap[str, Any] = Field(default_factory=lambda: FrozenDict({}))
    evidence_ids: tuple[str, ...] = ()
    error: str | None = None


class AgentTrace(AtlasModel):
    """The record of one `BaseAgent.run`.

    `agent_run_id` identifies the run of the agent (a ULID); `run_id` the fund run it belongs
    to. `as_of` / `cutoff` are the decision day and instant; `started_at` / `finished_at`
    wall-clock UTC. `prompt_*` name the template, its version and digest; `versions` is the
    prompt version sent to the LLM cache (`<template>@v<N>|<tool>@v<M>,...`). `inputs_digest`
    is the SHA-256 of what the model was first shown (agent, cutoff, prompt, messages).
    `evidence_ids` are all ids shown to the model; `cited_ids`, `unknown_ids` and
    `uncited_claims` the citation check of the final output. `output_json` is the final
    output (`None` when the run failed). `usage` (tokens), `cost_usd` (USD), `llm_calls` and
    `cache_hits` sum the run's gateway calls. `error` says why a run failed.
    """

    agent_run_id: Annotated[str, Field(min_length=1)]
    run_id: str | None = None
    agent: str
    tier: str
    as_of: date
    cutoff: UtcDatetime
    started_at: UtcDatetime
    finished_at: UtcDatetime
    status: TraceStatus
    prompt_name: str
    prompt_version: Annotated[int, Field(ge=1)]
    prompt_digest: str
    versions: str
    inputs_digest: str
    tool_calls: tuple[ToolCallRecord, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    cited_ids: tuple[str, ...] = ()
    unknown_ids: tuple[str, ...] = ()
    uncited_claims: tuple[str, ...] = ()
    output_json: str | None = None
    usage: Usage = Field(default_factory=Usage)
    cost_usd: Annotated[float, Field(ge=0)] = 0.0
    llm_calls: Annotated[int, Field(ge=0)] = 0
    cache_hits: Annotated[int, Field(ge=0)] = 0
    error: str | None = None


_JSON_LISTS = ("evidence_ids", "cited_ids", "unknown_ids", "uncited_claims")


def trace_row(trace: AgentTrace) -> dict[str, object]:
    """`trace` as one `agent_runs` row: scalars as they are; tool calls, id lists and usage
    as canonical JSON text."""
    row = trace.model_dump(exclude={"tool_calls", "usage", *_JSON_LISTS})
    row["tool_calls"] = canonical_json([c.model_dump(mode="json") for c in trace.tool_calls])
    for name in _JSON_LISTS:
        row[name] = canonical_json(list(getattr(trace, name)))
    row["usage"] = canonical_json(trace.usage.model_dump(mode="json"))
    return row


def trace_from_row(row: Mapping[Any, Any]) -> AgentTrace:
    """The `AgentTrace` stored in an `agent_runs` row (`trace_row`'s inverse)."""
    data = dict(row)
    data["tool_calls"] = json.loads(data["tool_calls"])
    for name in _JSON_LISTS:
        data[name] = json.loads(data[name])
    data["usage"] = json.loads(data["usage"])
    return AgentTrace.model_validate(data)


class TraceStore:
    """Where agent traces go: the `database`'s native `agent_runs` table, or memory without
    one (enough for unit tests and one-off scripts). Thread-safe."""

    def __init__(self, database: Database | None = None) -> None:
        self._database = database
        self._lock = threading.Lock()
        self._traces: list[AgentTrace] = []

    def save(self, trace: AgentTrace) -> None:
        """Store one trace (`DataError` if its `agent_run_id` is stored already)."""
        if self._database is not None:
            self._database.insert_agent_runs([trace_row(trace)])
            return
        with self._lock:
            self._traces.append(trace)

    def traces(self, agent: str | None = None) -> tuple[AgentTrace, ...]:
        """Stored traces in the order saved, optionally of one `agent` only."""
        if self._database is None:
            with self._lock:
                found = tuple(self._traces)
            return found if agent is None else tuple(t for t in found if t.agent == agent)
        where, params = ("WHERE agent = ?", [agent]) if agent is not None else ("", [])
        frame = self._database.query(f"SELECT * FROM agent_runs {where} ORDER BY rowid", params)
        rows = frame.astype(object).where(frame.notna(), None).to_dict(orient="records")
        return tuple(trace_from_row(row) for row in rows)
