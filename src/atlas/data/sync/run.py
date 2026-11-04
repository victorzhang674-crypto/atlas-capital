"""Sync runs (Stage 14): `run_sync` and `run_update`, which run the chosen datasets' steps in
order against the checkpoint."""

import time
from collections.abc import Iterable, Sequence
from datetime import date, timedelta

from atlas.core.audit import AuditLog
from atlas.core.clock import get_clock
from atlas.core.config import AtlasConfig
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import get_logger
from atlas.core.run_context import current_run_context
from atlas.core.storage import Lake
from atlas.data.providers.base import check_request
from atlas.data.sync.datasets import (
    DATASET_STEPS,
    SyncReport,
    SyncSources,
    parse_datasets,
    state_path,
)
from atlas.data.sync.engine import StepReport, SyncProgress, finish_step, run_step, serial_lake
from atlas.data.sync.state import SyncState
from atlas.data.sync.steps import _BUILDERS, _Run
from atlas.data.universe import refresh_universe_reference

_DAY = timedelta(days=1)

_log = get_logger(__name__)


def run_sync(
    lake: Lake,
    config: AtlasConfig,
    sources: SyncSources,
    *,
    start: date | None = None,
    end: date | None = None,
    datasets: Iterable[str] | None = None,
    symbols: Sequence[str] | None = None,
    command: str = "sync",
    progress: SyncProgress | None = None,
    audit: AuditLog | None = None,
) -> SyncReport:
    """Backfill or update the lake for `start..end` (inclusive dates).

    `start` defaults to `data.sync.default_start`, `end` to the clock's New York date (an
    installed clock is required). `datasets` (default: all of `DATASETS`) run in `DATASETS`
    order. `symbols` replaces the default list (members of `data.sync.universe` at any time
    in the window, plus every ticker-list universe's tickers); EDGAR steps skip the ETF
    tickers and the scheduled-events snapshot takes the members on `end` only. Price windows
    end at the latest session whose bar is published. Each step resumes from the checkpoint
    (see the module docstring) and writes one `DATA_SYNC` event to `audit`. A step that
    cannot run (`ConfigError` / `DataError`, e.g. a missing API key) is reported with its
    `error` and the others still run; any other exception stops the run (finished chunks stay
    checkpointed).
    """
    clock = get_clock()
    first = start if start is not None else config.data.sync.default_start
    last = end if end is not None else clock.today()
    if first > last:
        raise ValueError(f"start ({first}) must not be after end ({last})")
    chosen = parse_datasets(datasets)
    wanted = None if symbols is None else check_request(symbols, first, last)
    locked = serial_lake(lake)
    state = SyncState.load(state_path(locked, config))
    run = _Run(locked, config, sources, state, first, last, wanted)
    reports: list[StepReport] = []
    crashed: str | None = None
    ctx = current_run_context()
    try:
        for dataset in chosen:
            for name in DATASET_STEPS[dataset]:
                reports.extend(_run_one(run, dataset, name, progress, audit))
                if name == "prices" and run.pages is not None:
                    _refresh_trade_dates(run)
    except BaseException as exc:
        crashed = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        run.resources.close()
        state.add_run(
            {
                "command": command,
                "run_id": ctx.run_id if ctx is not None else None,
                "start": first.isoformat(),
                "end": last.isoformat(),
                "datasets": list(chosen),
                "finished_at": get_clock().now().isoformat(),
                "errors": [r.step for r in reports if r.error is not None],
                "crashed": crashed,
            }
        )
        state.save()
    return SyncReport(command, first, last, tuple(reports), state.path)


def _run_one(
    run: _Run,
    dataset: str,
    name: str,
    progress: SyncProgress | None,
    audit: AuditLog | None,
) -> list[StepReport]:
    began = time.perf_counter()
    try:
        spec = _BUILDERS[name](run)
    except (ConfigError, DataError) as exc:
        report = StepReport(
            step=name,
            dataset=dataset,
            provider="-",
            mode="-",
            start=run.start,
            end=run.end,
            seconds=time.perf_counter() - began,
            error=str(exc),
        )
        _log.warning("sync.step_error", step=name, error=str(exc))
        finish_step(report, progress=progress, audit=audit)
        return [report]
    if spec is None:
        return []
    workers = getattr(run.settings.workers, spec.provider)
    return [run_step(run.lake, run.state, spec, workers=workers, progress=progress, audit=audit)]


def _refresh_trade_dates(run: _Run) -> None:
    """Rebuild the reference tables from this run's pages once prices are in, so instruments
    carry first and last trade dates."""
    assert run.pages is not None
    try:
        refresh_universe_reference(run.lake, *run.pages, config=run.config)
    except DataError as exc:
        _log.warning("sync.trade_dates_failed", error=str(exc))


def run_update(
    lake: Lake,
    config: AtlasConfig,
    sources: SyncSources,
    *,
    datasets: Iterable[str] | None = None,
    symbols: Sequence[str] | None = None,
    progress: SyncProgress | None = None,
    audit: AuditLog | None = None,
) -> SyncReport:
    """Bring the lake up to date: a `run_sync` from the earliest covered date in the
    checkpoint to today, which fetches each key only from its last covered date (new index
    members in full). Without a checkpoint raises `DataError` (run a sync first)."""
    state = SyncState.load(state_path(lake, config))
    first = state.min_start()
    if first is None:
        raise DataError(f"no sync state at {state.path}: run `atlas data sync` first")
    return run_sync(
        lake,
        config,
        sources,
        start=first,
        datasets=datasets,
        symbols=symbols,
        command="update",
        progress=progress,
        audit=audit,
    )
