"""The fund graph's error policy: a failing node is recorded, never a crash (Stage 28).

A node that does agent work (an analyst, the debate, the director) runs through `guarded`.
An `Exception` from it becomes a `NodeError` in the state's `errors` and the research
subgraph then drops the symbol for the day: none of its reports or its thesis reach the fund
state (spec §3.6, fail safe: no new risk). Other symbols are not affected.

What is *not* turned into a record:
- `BaseException`s that are not `Exception`s (`KeyboardInterrupt`, `SystemExit`, ...): the
  run stops and can be resumed from its last checkpoint (`atlas.orchestration.checkpoint`);
- LangGraph's control-flow exceptions (`GraphBubbleUp`: interrupts, parent commands);
- a conflicting write to the state, which the reducers raise as `OrchestrationError` (a bug).

A `LookAheadError` or a `CacheMiss` (`INTEGRITY_ERRORS`) is recorded the same way, with
`integrity=True`: the run's results can no longer be trusted as a faithful replay or a
point-in-time run. The graph still only drops the symbol; a caller that must not go on with
fewer names (`atlas research --replay`, a backtest day) checks `integrity_errors(errors)`.
"""

import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from typing import Annotated, Any, Protocol, cast

from langgraph.errors import GraphBubbleUp
from pydantic import Field, StringConstraints

from atlas.core.errors import CacheMiss, LookAheadError
from atlas.core.logging import get_logger
from atlas.core.types import AtlasModel, Symbol

_log = get_logger(__name__)

MAX_MESSAGE_CHARS = 500
"""Longest error message kept in a `NodeError` (longer ones are cut, ending in `...`)."""

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

INTEGRITY_ERRORS: tuple[type[Exception], ...] = (LookAheadError, CacheMiss)
"""Exceptions that mean the run itself is invalid, not just one symbol's work: data from
after the cutoff, or a replay that no longer matches its recordings."""


class NodeError(AtlasModel):
    """One failed node: which `node`, on which `symbol` (`None` for a fund-level node), on
    decision day `as_of`; `kind` is the exception's class name and `message` its text (cut at
    `MAX_MESSAGE_CHARS`); `integrity` is true for an `INTEGRITY_ERRORS` exception (default
    false, so records written before the field existed still load)."""

    node: _Text
    symbol: Symbol | None
    as_of: date
    kind: _Text
    message: Annotated[str, Field(max_length=MAX_MESSAGE_CHARS)]
    integrity: bool = False


class NodeFn[S](Protocol):
    """A LangGraph node function: takes the state (by the name `state`, as LangGraph's node
    protocol requires) and returns the update."""

    def __call__(self, state: S) -> dict[str, Any]: ...


def _cut(text: str) -> str:
    """`text`, cut to `MAX_MESSAGE_CHARS` (ending in `...`) when longer."""
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return text[: MAX_MESSAGE_CHARS - 3] + "..."


def guarded[S](
    node: str,
    work: Callable[[S], dict[str, Any]],
    *,
    limiter: threading.Semaphore | None = None,
) -> NodeFn[S]:
    """A LangGraph node that runs `work(state)` and returns its update; if `work` raises an
    `Exception`, the node logs it (error level, with the traceback) and returns
    `{"errors": [NodeError]}` instead. `symbol` and `as_of` of the record are read from the
    state (`symbol` absent = a fund-level node). `GraphBubbleUp` and `BaseException`s that are
    not `Exception`s propagate. `limiter`, when given, is held only while `work` runs (the
    graph-wide limit on agent calls running at once)."""

    def run(state: S) -> dict[str, Any]:
        try:
            if limiter is None:
                return work(state)
            with limiter:
                return work(state)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            fields = cast(Mapping[str, Any], state)
            symbol = fields.get("symbol")
            _log.error(
                "orchestration_node_failed",
                node=node,
                symbol=symbol,
                kind=type(exc).__name__,
                integrity=isinstance(exc, INTEGRITY_ERRORS),
                exc_info=True,
            )
            error = NodeError(
                node=node,
                symbol=symbol,
                as_of=fields["as_of"],
                kind=type(exc).__name__,
                message=_cut(str(exc)),
                integrity=isinstance(exc, INTEGRITY_ERRORS),
            )
            return {"errors": [error]}

    run.__name__ = node
    return run


def failed_symbols(errors: Iterable[NodeError] | None) -> tuple[str, ...]:
    """The symbols with at least one error, sorted, each once (fund-level errors have no
    symbol and are left out)."""
    return tuple(sorted({e.symbol for e in errors or () if e.symbol is not None}))


def integrity_errors(errors: Iterable[NodeError] | None) -> tuple[NodeError, ...]:
    """The errors marked `integrity` (a `LookAheadError` or a `CacheMiss`), in order. A replay
    or a backtest day fails when this is not empty; `failed_symbols` lists every dropped
    symbol whatever the cause."""
    return tuple(e for e in errors or () if e.integrity)
