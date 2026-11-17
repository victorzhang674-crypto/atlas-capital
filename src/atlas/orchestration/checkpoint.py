"""Checkpointed runs of a fund graph: SQLite checkpoints, resume after a failure (Stage 28).

A run's checkpoints live in `<lake>/runs/<run_id>/checkpoints.sqlite` (`lake` is
`cfg.data.lake_path`, `data` by default; `run_id` is the run's 26-char ULID, as in
`atlas.core.run_context`). LangGraph's `thread_id` is the `run_id`.

Writes are durable step by step (`durability="sync"`): a step's checkpoint is on disk before
the next step starts, and each task's writes are saved as soon as the task finishes. A run
that fails (an exception that is not an agent failure, Ctrl-C) or whose process is killed is
continued by `resume_graph` from its last checkpoint: finished tasks are not run again, only
the unfinished ones. Agent failures do not stop a run at all: they are recorded in `errors`
(`atlas.orchestration.errors`).

`run_graph` checks the inputs before it writes anything and refuses a `run_id` that already
has a checkpoint (a new run never appends to an old one); `resume_graph` refuses one without,
and one started with a graph built differently (the graph fingerprint is stored as checkpoint
metadata). Resuming a finished run runs nothing and returns its final state. LangSmith tracing
is off for every run (`invoke_untraced`).

The serializer only rebuilds the types listed by `state_types()` (every model and enum the
state can hold); LangGraph would hand any other type back as a plain dict.
"""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from atlas.core.errors import OrchestrationError
from atlas.core.logging import get_logger
from atlas.core.run_context import run_dir
from atlas.orchestration.research_graph import (
    GRAPH_METADATA_KEY,
    ResearchGraph,
    invoke_untraced,
)
from atlas.orchestration.state import FundState, state_types

CHECKPOINT_FILE = "checkpoints.sqlite"
"""File name of a run's checkpoints inside its run folder."""

_RUN_ID = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")
_log = get_logger(__name__)


def checkpoint_path(lake_path: Path, run_id: str) -> Path:
    """`<lake_path>/runs/<run_id>/checkpoints.sqlite`; raises `OrchestrationError` unless
    `run_id` is a 26-char ULID."""
    if not _RUN_ID.fullmatch(run_id):
        raise OrchestrationError(f"run id must be a 26-char ULID, got {run_id!r}")
    return run_dir(lake_path, run_id) / CHECKPOINT_FILE


def serializer() -> JsonPlusSerializer:
    """LangGraph's serializer, allowed to rebuild exactly the state's types."""
    return JsonPlusSerializer(allowed_msgpack_modules=list(state_types()))


@contextmanager
def sqlite_checkpointer(path: Path) -> Iterator[SqliteSaver]:
    """A `SqliteSaver` on `path` (its folder is created), closed on exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    try:
        yield SqliteSaver(conn, serde=serializer())
    finally:
        conn.close()


def _has_checkpoint(saver: SqliteSaver, run_id: str) -> bool:
    return saver.get_tuple({"configurable": {"thread_id": run_id}}) is not None


def run_graph(
    graph: ResearchGraph, inputs: FundState, *, run_id: str, lake_path: Path
) -> FundState:
    """Run `graph` on `inputs` as run `run_id`, checkpointed under `lake_path`; returns the
    final state. Raises `OrchestrationError` for malformed inputs (before anything is
    written) or if the run already has a checkpoint."""
    path = checkpoint_path(lake_path, run_id)
    graph.check_inputs(inputs)
    with sqlite_checkpointer(path) as saver:
        if _has_checkpoint(saver, run_id):
            raise OrchestrationError(
                f"run {run_id} already has a checkpoint at {path}; resume it instead"
            )
        _log.info("orchestration_run_started", run_id=run_id, checkpoint=str(path))
        return invoke_untraced(
            graph.compile(saver), inputs, graph.config(thread_id=run_id), durability="sync"
        )


def resume_graph(graph: ResearchGraph, *, run_id: str, lake_path: Path) -> FundState:
    """Continue run `run_id` from its last checkpoint under `lake_path` and return its final
    state (a finished run's final state, without running anything). `graph` must be built
    like the original (same graph fingerprint). Raises `OrchestrationError` if the run has no
    checkpoint or was started with a graph built differently."""
    path = checkpoint_path(lake_path, run_id)
    if not path.is_file():
        raise OrchestrationError(f"run {run_id} has no checkpoint at {path}")
    with sqlite_checkpointer(path) as saver:
        latest = saver.get_tuple({"configurable": {"thread_id": run_id}})
        if latest is None:
            raise OrchestrationError(f"run {run_id} has no checkpoint at {path}")
        if latest.metadata.get(GRAPH_METADATA_KEY) != graph.fingerprint:
            raise OrchestrationError(
                f"run {run_id} was started with a graph built differently (another analyst "
                "roster or graph shape); resume it with the graph it was started with"
            )
        _log.info("orchestration_run_resumed", run_id=run_id, checkpoint=str(path))
        return invoke_untraced(
            graph.compile(saver), None, graph.config(thread_id=run_id), durability="sync"
        )
