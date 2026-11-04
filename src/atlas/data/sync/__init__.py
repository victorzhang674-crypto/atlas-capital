"""Data sync: resumable backfill and incremental update of the whole lake (Stage 14).

A sync is an ordered list of steps (universe → prices → fundamentals → macro → news →
events → sentiment → quality). Each step covers a set of keys (symbols, macro series ids or
one pseudo-key such as `_all`), belongs to one provider (whose thread pool runs it) and
fetches a chunk of keys over a date window by calling a Stage 07-13 ingest function.

The checkpoint `<lake>/<data.sync.state_file>` (`cache/sync_state.json`) records, per step
and key, the covered window `[from, to]` (inclusive dates), when it was synced and whether
the vendor had data (`ok`) or none (`empty`). It is written atomically after every finished
chunk, so a crashed backfill resumes where it stopped and `update` fetches only new days:

- windowed steps (prices, news, news scoring, social posts) fetch what a key's coverage
  lacks: `[to + 1 - update_overlap_days, end]`, or the whole window when the request starts
  before `from`;
- snapshot steps (universe, fundamentals, macro, earnings, scheduled events, macro calendar,
  reactions, quality) refetch a key whose snapshot is older than the run's end date.

Modules (split from one 1 767-line `sync.py` in the Phase 1 checkpoint, code unchanged):
`state` (checkpoint and planning), `engine` (running a step's chunks), `datasets` (names,
sources, report, decision days), `steps` (one builder per step), `run` (`run_sync`,
`run_update`) and `status` (`lake_status`). Import from `atlas.data.sync`.
"""

from atlas.data.sync.datasets import (
    ALL_KEY,
    DATASET_STEPS,
    DATASETS,
    SyncReport,
    SyncSources,
    last_published_session,
    next_decision_day,
    parse_datasets,
    state_path,
)
from atlas.data.sync.engine import (
    AUDIT_ACTOR,
    MAX_AUDIT_REASONS,
    ChunkOutcome,
    FetchFn,
    Provider,
    StepMode,
    StepReport,
    StepSpec,
    SyncProgress,
    finish_step,
    run_step,
    serial_lake,
)
from atlas.data.sync.run import (
    run_sync,
    run_update,
)
from atlas.data.sync.state import (
    MAX_RUNS,
    STATE_FORMAT,
    Chunk,
    Coverage,
    KeyStatus,
    SyncState,
    canonical_state_json,
    group_chunks,
    plan_snapshot,
    plan_window,
    split_window,
)
from atlas.data.sync.status import (
    STATUS_COLUMNS,
    STATUS_TABLES,
    CoverageLine,
    LakeStatus,
    lake_status,
)

__all__ = [
    "ALL_KEY",
    "AUDIT_ACTOR",
    "DATASETS",
    "DATASET_STEPS",
    "MAX_AUDIT_REASONS",
    "MAX_RUNS",
    "STATE_FORMAT",
    "STATUS_COLUMNS",
    "STATUS_TABLES",
    "Chunk",
    "ChunkOutcome",
    "Coverage",
    "CoverageLine",
    "FetchFn",
    "KeyStatus",
    "LakeStatus",
    "Provider",
    "StepMode",
    "StepReport",
    "StepSpec",
    "SyncProgress",
    "SyncReport",
    "SyncSources",
    "SyncState",
    "canonical_state_json",
    "finish_step",
    "group_chunks",
    "lake_status",
    "last_published_session",
    "next_decision_day",
    "parse_datasets",
    "plan_snapshot",
    "plan_window",
    "run_step",
    "run_sync",
    "run_update",
    "serial_lake",
    "split_window",
    "state_path",
]
