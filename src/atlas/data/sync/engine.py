"""Sync engine (Stage 14): a step (`StepSpec`) runs its chunks on its provider's thread pool,
records coverage after each chunk and reports (`StepReport`, `DATA_SYNC` audit events)."""

import contextvars
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

import pandas as pd

from atlas.core.audit import AuditLog, EventType
from atlas.core.clock import get_clock
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import get_logger
from atlas.core.storage import Lake, WriteMode, WriteResult
from atlas.core.types.base import FrozenDict
from atlas.data.sync.state import (
    Chunk,
    SyncState,
    group_chunks,
    plan_snapshot,
    plan_window,
    split_window,
)

Provider = Literal["yahoo", "sec", "fred", "alpaca", "wikipedia", "local"]
"""The thread pool a step runs on (`data.sync.workers` sizes each)."""
StepMode = Literal["window", "snapshot"]

AUDIT_ACTOR = "data.sync"
"""`actor` of the `DATA_SYNC` audit events."""

MAX_AUDIT_REASONS = 20
"""Failure reasons kept in one `DATA_SYNC` payload (the `StepReport` keeps them all)."""

_log = get_logger(__name__)

# --- engine ----------------------------------------------------------------------------------


class _SerialLake(Lake):
    """A `Lake` whose writes hold one lock, so the step threads of a sync never interleave
    two read-modify-write cycles (the lake supports one writer at a time)."""

    def __init__(self, root: Path, registry: Any = None) -> None:
        super().__init__(root, registry)
        self._write_lock = threading.RLock()

    def write_table(
        self,
        name: str,
        df: pd.DataFrame,
        partition_cols: Sequence[str] | None = None,
        mode: WriteMode = "append",
        keys: Sequence[str] | None = None,
    ) -> WriteResult:
        """`Lake.write_table` under the lake's write lock."""
        with self._write_lock:
            return super().write_table(name, df, partition_cols, mode, keys)


def serial_lake(lake: Lake) -> Lake:
    """A lake on the same root and registry whose writes are serialised across threads
    (`lake` itself if it already is one). Reads see the same files."""
    if isinstance(lake, _SerialLake):
        return lake
    return _SerialLake(lake.root, lake.registry)


@dataclass(frozen=True)
class ChunkOutcome:
    """What one fetch of a chunk did: `rows` written, and each key's fate — `ok` (data
    stored), `empty` (the vendor has none: covered, not retried) or `failed` (reason; left
    uncovered so the next run retries it)."""

    rows: int = 0
    ok: tuple[str, ...] = ()
    empty: tuple[str, ...] = ()
    failed: Mapping[str, str] = field(default_factory=dict)


FetchFn = Callable[[tuple[str, ...], date, date], ChunkOutcome]
"""`fetch(keys, start, end)`: ingest `keys` for the inclusive window `start..end`."""


@dataclass(frozen=True)
class StepSpec:
    """One step of a sync: fetch `keys` over `start..end` (inclusive) in chunks of
    `chunk_size` keys on the `provider`'s pool. `window` steps fetch only what each key's
    coverage lacks (re-fetching `overlap_days` calendar days) in sub-windows of `window_days`
    days (`None`: one window), checkpointed after each, and ask an `empty` key again only
    every `empty_retry_days` days (`None`: always); `snapshot` steps refetch a key synced for
    an earlier end date. `dataset` is the CLI dataset the step belongs to."""

    name: str
    dataset: str
    provider: Provider
    mode: StepMode
    keys: tuple[str, ...]
    start: date
    end: date
    chunk_size: int
    fetch: FetchFn
    window_days: int | None = None
    overlap_days: int = 0
    empty_retry_days: int | None = None


@dataclass(frozen=True)
class StepReport:
    """Outcome of one step: `keys` asked, `skipped` (already covered), `ok`, `empty`,
    `failed` (key → reason), `rows` written, `chunks` run, wall time `seconds`, and `error`
    when the step could not run at all (then nothing was fetched)."""

    step: str
    dataset: str
    provider: str
    mode: str
    start: date
    end: date
    keys: int = 0
    skipped: int = 0
    ok: int = 0
    empty: int = 0
    failed: FrozenDict[str, str] = field(default_factory=lambda: FrozenDict({}))
    rows: int = 0
    chunks: int = 0
    seconds: float = 0.0
    error: str | None = None

    def payload(self) -> dict[str, Any]:
        """The `DATA_SYNC` audit payload (JSON values; at most `MAX_AUDIT_REASONS` reasons,
        in key order)."""
        reasons = dict(sorted(self.failed.items())[:MAX_AUDIT_REASONS])
        return {
            "step": self.step,
            "dataset": self.dataset,
            "provider": self.provider,
            "mode": self.mode,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "keys": self.keys,
            "skipped": self.skipped,
            "ok": self.ok,
            "empty": self.empty,
            "failed": len(self.failed),
            "failure_reasons": reasons,
            "rows": self.rows,
            "chunks": self.chunks,
            "seconds": round(self.seconds, 3),
            "error": self.error,
        }


class SyncProgress(Protocol):
    """Receives progress from `run_step` (the CLI draws Rich progress bars). Called from
    worker threads: implementations must be thread-safe."""

    def start_step(self, step: str, total: int) -> None:
        """A step with `total` keys starts."""
        ...

    def advance(self, step: str, n: int) -> None:
        """`n` more keys of `step` are done (or were already covered)."""
        ...

    def end_step(self, report: StepReport) -> None:
        """A step finished (or could not run)."""
        ...


@dataclass
class _Tally:
    rows: int = 0
    ok: set[str] = field(default_factory=set)
    empty: set[str] = field(default_factory=set)
    failed: dict[str, str] = field(default_factory=dict)


def _planned(state: SyncState, spec: StepSpec) -> dict[str, tuple[date, date]]:
    windows: dict[str, tuple[date, date]] = {}
    now = get_clock().now() if spec.empty_retry_days is not None else None
    for key in dict.fromkeys(spec.keys):
        coverage = state.coverage(spec.name, key)
        if spec.mode == "window":
            window = plan_window(
                coverage,
                spec.start,
                spec.end,
                spec.overlap_days,
                now=now,
                empty_retry_days=spec.empty_retry_days,
            )
            if window is not None:
                windows[key] = window
        elif spec.start <= spec.end and plan_snapshot(coverage, spec.end):
            windows[key] = (spec.start, spec.end)
    return windows


def _run_chunk(state: SyncState, spec: StepSpec, chunk: Chunk, stop: threading.Event) -> _Tally:
    """Fetch `chunk` sub-window by sub-window, checkpointing after each; a key that fails is
    not fetched for later sub-windows (one that comes back empty is). Once `stop` is set
    (another chunk crashed) no further window is started; a crash here sets it."""
    tally = _Tally()
    live = list(chunk.keys)
    windows = split_window(
        chunk.start, chunk.end, spec.window_days if spec.mode == "window" else None
    )
    for first, last in windows:
        if not live or stop.is_set():
            break
        try:
            outcome = spec.fetch(tuple(live), first, last)
        except (DataError, ConfigError) as exc:
            outcome = ChunkOutcome(failed={k: str(exc) for k in live})
            _log.warning("sync.chunk_failed", step=spec.name, keys=len(live), error=str(exc))
        except BaseException:
            stop.set()
            raise
        now = get_clock().now()
        good, none = set(outcome.ok), set(outcome.empty)
        ok = [k for k in live if k in good]
        empty = [k for k in live if k in none and k not in good]
        if ok:
            state.record(spec.name, ok, first, last, synced_at=now)
        if empty:
            state.record(spec.name, empty, first, last, synced_at=now, empty=True)
        state.save()
        done = good | none
        for key in live:
            if key not in done:
                tally.failed[key] = outcome.failed.get(key, "no outcome reported for this key")
        tally.rows += outcome.rows
        tally.ok.update(ok)
        tally.empty.update(empty)
        # a failed key stops here (its coverage must stay contiguous); an empty one goes on,
        # since a later window may have data (a listing after the window start)
        live = [k for k in live if k in done]
    tally.empty -= tally.ok
    return tally


def run_step(
    lake: Lake,
    state: SyncState,
    spec: StepSpec,
    *,
    workers: int = 1,
    progress: SyncProgress | None = None,
    audit: AuditLog | None = None,
) -> StepReport:
    """Run one step: plan the keys its coverage lacks, fetch them in chunks on a pool of
    `workers` threads (each in a copy of the caller's context, so the clock and run context
    hold), checkpoint `state` after every chunk window, and return the report. One
    `DATA_SYNC` event is written to `audit` when given.

    An exception other than `DataError` / `ConfigError` from a fetch (a bug, an interrupt)
    stops the step: chunks not started are cancelled, finished ones stay checkpointed, and
    the exception propagates. `lake` should be a `serial_lake` when `workers > 1`.
    """
    del lake  # the fetch functions close over the lake; kept for symmetry with run_sync
    began = time.perf_counter()
    windows = _planned(state, spec)
    unique = tuple(dict.fromkeys(spec.keys))
    skipped = len(unique) - len(windows)
    chunks = group_chunks(windows, spec.chunk_size)
    if progress is not None:
        progress.start_step(spec.name, len(unique))
        if skipped:
            progress.advance(spec.name, skipped)
    total = _Tally()
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix=spec.name) as pool:
        futures: dict[Future[_Tally], Chunk] = {
            pool.submit(contextvars.copy_context().run, _run_chunk, state, spec, chunk, stop): chunk
            for chunk in chunks
        }
        pending = set(futures)
        try:
            while pending:
                done, pending = wait(pending, return_when=FIRST_EXCEPTION)
                for future in done:
                    tally = future.result()  # re-raises a crash
                    total.rows += tally.rows
                    total.ok |= tally.ok
                    total.empty |= tally.empty
                    total.failed.update(tally.failed)
                    if progress is not None:
                        progress.advance(spec.name, len(futures[future].keys))
        except BaseException:
            stop.set()
            for future in pending:
                future.cancel()
            raise
    report = StepReport(
        step=spec.name,
        dataset=spec.dataset,
        provider=spec.provider,
        mode=spec.mode,
        start=spec.start,
        end=spec.end,
        keys=len(unique),
        skipped=skipped,
        ok=len(total.ok),
        empty=len(total.empty),
        failed=FrozenDict(dict(sorted(total.failed.items()))),
        rows=total.rows,
        chunks=len(chunks),
        seconds=time.perf_counter() - began,
    )
    finish_step(report, progress=progress, audit=audit)
    return report


def finish_step(
    report: StepReport, *, progress: SyncProgress | None = None, audit: AuditLog | None = None
) -> None:
    """Log `report`, tell `progress` and write its `DATA_SYNC` audit event."""
    _log.info("sync.step", **report.payload())
    if progress is not None:
        progress.end_step(report)
    if audit is not None:
        audit.record(EventType.DATA_SYNC, AUDIT_ACTOR, report.payload())
