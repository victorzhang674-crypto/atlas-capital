"""The research graph: one subgraph per symbol, fanned out over the research queue (Stage 28).

**Research subgraph** (one symbol, `ResearchState`): the analysts run in parallel from
`START`; `join` waits for all of them; then `debate`, then `director`, then `publish`, the only
node that writes the symbol's `reports` and `theses` to the fund state. After `join`, `debate`
and `director` a router ends the subgraph early when the symbol has an error, so a failing
symbol is dropped for the day with nothing half-written (error policy:
`atlas.orchestration.errors`).

**Fund graph** (`FundState`): `prepare` de-duplicates the research queue, then one LangGraph
`Send` per symbol starts a research subgraph (all in one step, in parallel), and `collect` joins
them. An empty queue goes straight to `collect`.

**Agents** plug in through a `ResearchTeam`: plain callables taking a `ResearchTask` (symbol,
decision day). Stage 28 uses the stubs of `atlas.orchestration.stubs`; real agents (Stages
29-34) wrap `BaseAgent.run` into the same slots. Their outputs are checked (type, ticker,
`as_of`, analyst name): a wrong one is a `DataError`, i.e. a dropped symbol.

**Concurrency** (`orchestration.max_concurrency` = N): at most N agent calls run at once in the
whole graph (a semaphore held only around each call), and at most N symbols are in flight
(LangGraph's `max_concurrency`, which LangGraph applies per graph loop and passes down to the
subgraphs; the semaphore is what bounds the total).
"""

import hashlib
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from functools import cached_property
from types import MappingProxyType
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer, Send
from langsmith.run_helpers import tracing_context
from pydantic import TypeAdapter, ValidationError

from atlas.core.errors import DataError, OrchestrationError
from atlas.core.logging import get_logger
from atlas.core.types import AnalystReport, DebateTurn, InvestmentThesis, Symbol
from atlas.orchestration.errors import NodeFn, failed_symbols, guarded
from atlas.orchestration.state import FundState, ResearchInput, ResearchOutput, ResearchState

_log = get_logger(__name__)
_SYMBOL: TypeAdapter[str] = TypeAdapter(Symbol)

RESERVED_NODES = frozenset(
    {"prepare", "research", "collect", "join", "debate", "director", "publish"}
)
"""Node names of the two graphs; an analyst may not use one."""

_NAME = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class ResearchTask:
    """What an agent researches: `symbol` on decision day `as_of`."""

    symbol: str
    as_of: date


AnalystFn = Callable[[ResearchTask], AnalystReport]
"""An analyst: the task → its report on the symbol."""
DebateFn = Callable[[ResearchTask, Mapping[str, AnalystReport]], Sequence[DebateTurn]]
"""The debate: the task and the reports (analyst → report, read-only) → the turns."""
DirectorFn = Callable[
    [ResearchTask, Mapping[str, AnalystReport], Sequence[DebateTurn]], InvestmentThesis
]
"""The research director: the task, the reports and the debate → the thesis."""


@dataclass(frozen=True)
class ResearchTeam:
    """The agents of a research subgraph: `analysts` (name → analyst, run in parallel, in
    this order in the graph), `debate` and `director`. Analyst names are node names:
    `[a-z][a-z0-9_]*`, not one of `RESERVED_NODES`."""

    analysts: Mapping[str, AnalystFn]
    debate: DebateFn
    director: DirectorFn

    def __post_init__(self) -> None:
        if not self.analysts:
            raise ValueError("a research team needs at least one analyst")
        for name in self.analysts:
            if not _NAME.fullmatch(name) or name in RESERVED_NODES:
                raise ValueError(
                    f"analyst name {name!r} must match [a-z][a-z0-9_]* and not be one of "
                    f"{sorted(RESERVED_NODES)}"
                )
        object.__setattr__(self, "analysts", MappingProxyType(dict(self.analysts)))


def _task(state: Mapping[str, Any]) -> ResearchTask:
    return ResearchTask(symbol=state["symbol"], as_of=state["as_of"])


def _check(task: ResearchTask, out: object, expected: type, who: str) -> None:
    """Raise `DataError` unless `out` is an `expected` on the task's ticker and day."""
    if not isinstance(out, expected):
        raise DataError(f"{who} returned {type(out).__name__}, expected {expected.__name__}")
    ticker, as_of = out.ticker, out.as_of  # type: ignore[attr-defined]
    if ticker != task.symbol:
        raise DataError(f"{who} returned ticker {ticker!r} for {task.symbol!r}")
    if as_of != task.as_of:
        raise DataError(f"{who} returned as_of {as_of} for {task.as_of}")


def _analyst_work(name: str, run: AnalystFn) -> Callable[[ResearchState], dict[str, Any]]:
    def work(state: ResearchState) -> dict[str, Any]:
        task = _task(state)
        out = run(task)
        _check(task, out, AnalystReport, f"analyst {name!r}")
        if out.analyst != name:
            raise DataError(f"analyst {name!r} returned a report by analyst {out.analyst!r}")
        return {"analyst_reports": {name: out}}

    return work


def _in_roster_order(
    reports: Mapping[str, AnalystReport], roster: Sequence[str]
) -> dict[str, AnalystReport]:
    """`reports` keyed in the team's analyst order (LangGraph merges parallel writes in its own
    order; agents and the fund state must see one fixed order)."""
    return {name: reports[name] for name in roster if name in reports}


def _debate_work(run: DebateFn, roster: Sequence[str]) -> Callable[[ResearchState], dict[str, Any]]:
    def work(state: ResearchState) -> dict[str, Any]:
        reports = MappingProxyType(_in_roster_order(state["analyst_reports"], roster))
        turns = list(run(_task(state), reports))
        for turn in turns:
            if not isinstance(turn, DebateTurn):
                raise DataError(
                    f"debate returned a {type(turn).__name__}, expected DebateTurn items"
                )
        return {"debate": turns}

    return work


def _director_work(
    run: DirectorFn, roster: Sequence[str]
) -> Callable[[ResearchState], dict[str, Any]]:
    def work(state: ResearchState) -> dict[str, Any]:
        task = _task(state)
        reports = MappingProxyType(_in_roster_order(state["analyst_reports"], roster))
        out = run(task, reports, tuple(state.get("debate", ())))
        _check(task, out, InvestmentThesis, "director")
        return {"thesis": out}

    return work


def _join(state: ResearchState) -> dict[str, Any]:
    """Wait for every analyst (the node has an edge from each)."""
    return {}


def _publisher(roster: Sequence[str]) -> NodeFn[ResearchState]:
    def publish(state: ResearchState) -> dict[str, Any]:
        """Hand the symbol's reports (in roster order) and thesis to the fund state."""
        symbol = state["symbol"]
        return {
            "reports": {symbol: _in_roster_order(state["analyst_reports"], roster)},
            "theses": {symbol: state["thesis"]},
        }

    return publish


def _next_unless_failed(target: str) -> Callable[[ResearchState], str]:
    def route(state: ResearchState) -> str:
        return END if state.get("errors") else target

    route.__name__ = f"to_{target}"
    return route


def build_research_subgraph(
    team: ResearchTeam, *, limiter: threading.Semaphore | None = None
) -> CompiledStateGraph[ResearchState, None, ResearchInput, ResearchOutput]:
    """The compiled research subgraph of `team` for one symbol (input `ResearchInput`,
    output `ResearchOutput`); `limiter` bounds the agent calls running at once."""
    graph = StateGraph(ResearchState, input_schema=ResearchInput, output_schema=ResearchOutput)
    for name, run in team.analysts.items():
        graph.add_node(name, guarded(name, _analyst_work(name, run), limiter=limiter))
        graph.add_edge(START, name)
    graph.add_node("join", _join)
    graph.add_edge(list(team.analysts), "join")
    roster = tuple(team.analysts)
    graph.add_node("debate", guarded("debate", _debate_work(team.debate, roster), limiter=limiter))
    graph.add_node(
        "director", guarded("director", _director_work(team.director, roster), limiter=limiter)
    )
    graph.add_node("publish", _publisher(roster))
    graph.add_conditional_edges("join", _next_unless_failed("debate"), ["debate", END])
    graph.add_conditional_edges("debate", _next_unless_failed("director"), ["director", END])
    graph.add_conditional_edges("director", _next_unless_failed("publish"), ["publish", END])
    graph.add_edge("publish", END)
    return graph.compile()


# --- the fund graph ---------------------------------------------------------------------


def _prepare(state: FundState) -> dict[str, Any]:
    """Check the inputs and de-duplicate the research queue (first occurrence kept)."""
    as_of = state.get("as_of")
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise OrchestrationError(f"as_of must be a date (the decision day), got {as_of!r}")
    queue: list[str] = []
    for raw in state.get("research_queue") or ():
        try:
            symbol = _SYMBOL.validate_python(raw)
        except ValidationError as exc:
            raise OrchestrationError(f"research_queue: invalid symbol {raw!r}") from exc
        if symbol not in queue:
            queue.append(symbol)
    return {"research_queue": queue}


def _fan_out(state: FundState) -> list[Send] | str:
    """One `Send` to the research subgraph per queued symbol; `collect` when there is none."""
    sends = [
        Send("research", {"symbol": symbol, "as_of": state["as_of"]})
        for symbol in state["research_queue"]
    ]
    return sends or "collect"


def _collect(state: FundState) -> dict[str, Any]:
    """Join the research subgraphs; log what was researched and what was dropped."""
    _log.info(
        "orchestration_research_done",
        as_of=str(state["as_of"]),
        queued=len(state["research_queue"]),
        researched=len(state.get("theses") or {}),
        dropped=list(failed_symbols(state.get("errors"))),
    )
    return {}


GRAPH_METADATA_KEY = "atlas_graph"
"""Checkpoint metadata key holding the fingerprint of the graph a run was started with."""


def invoke_untraced(
    app: CompiledStateGraph, inputs: FundState | None, config: RunnableConfig, **kwargs: Any
) -> FundState:
    """`app.invoke(inputs, config, **kwargs)` with LangSmith tracing off, whatever the
    environment says (`LANGSMITH_TRACING` would otherwise upload the whole run, prompts and
    state included, to an outside service)."""
    with tracing_context(enabled=False):
        return cast(FundState, app.invoke(inputs, config, **kwargs))


@dataclass(frozen=True)
class ResearchGraph:
    """The fund-level research graph: `builder` (uncompiled, so a runner can compile it with
    its checkpointer) and the `max_concurrency` it was built for. The agent-call semaphore
    belongs to the built graph: runs of one `ResearchGraph` at the same time share its limit."""

    builder: StateGraph[FundState]
    max_concurrency: int

    def compile(self, checkpointer: Checkpointer = None) -> CompiledStateGraph:
        """The compiled graph (with `checkpointer`, if given)."""
        return self.builder.compile(checkpointer=checkpointer)

    @cached_property
    def fingerprint(self) -> str:
        """SHA-256 of the graph's structure (its mermaid export: node names and edges, the
        analyst roster included; not the concurrency limit). A checkpointed run records it,
        and a resume with a graph built differently is refused. The export's text comes from
        LangGraph, so a LangGraph upgrade that changes it also refuses older runs (fail safe:
        finish or abandon runs before upgrading)."""
        return hashlib.sha256(self.mermaid().encode()).hexdigest()

    def config(self, **configurable: Any) -> RunnableConfig:
        """The LangGraph run config: the concurrency limit, any `configurable` values and the
        graph fingerprint as run metadata (stored with every checkpoint)."""
        return {
            "max_concurrency": self.max_concurrency,
            "configurable": dict(configurable),
            "metadata": {GRAPH_METADATA_KEY: self.fingerprint},
        }

    def check_inputs(self, inputs: FundState) -> None:
        """Raise `OrchestrationError` if `inputs` would fail in `prepare` (bad `as_of`, bad
        symbol), so a checkpointed run can refuse them before it writes anything."""
        _prepare(inputs)

    def invoke(self, inputs: FundState) -> FundState:
        """Run the graph once in memory, without a checkpoint (tests, quick runs); see
        `atlas.orchestration.checkpoint` for checkpointed, resumable runs."""
        return invoke_untraced(self.compile(), inputs, self.config())

    def mermaid(self) -> str:
        """The graph as a mermaid flowchart, research subgraph expanded (`docs/orchestration.md`
        holds the one of the default stub team)."""
        return self.compile().get_graph(xray=1).draw_mermaid()


def build_research_graph(team: ResearchTeam, *, max_concurrency: int) -> ResearchGraph:
    """The research graph of `team`: `prepare` → one research subgraph per queued symbol
    (`Send`) → `collect`. At most `max_concurrency` agent calls run at once and at most that
    many symbols are in flight (`orchestration.max_concurrency`)."""
    if max_concurrency < 1:
        raise ValueError(f"max_concurrency must be at least 1, got {max_concurrency}")
    limiter = threading.BoundedSemaphore(max_concurrency)
    graph = StateGraph(FundState)
    graph.add_node("prepare", _prepare)
    graph.add_node("research", build_research_subgraph(team, limiter=limiter))
    graph.add_node("collect", _collect)
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges("prepare", _fan_out, ["research", "collect"])
    graph.add_edge("research", "collect")
    graph.add_edge("collect", END)
    return ResearchGraph(builder=graph, max_concurrency=max_concurrency)
