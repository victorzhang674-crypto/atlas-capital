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
"""

import contextvars
import json
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Literal, Protocol

import duckdb
import pandas as pd

from atlas.core.audit import AuditLog, EventType
from atlas.core.calendar import TradingCalendar, market_date
from atlas.core.clock import get_clock
from atlas.core.config import AtlasConfig, SyncConfig
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf, bar_available_at
from atlas.core.run_context import current_run_context
from atlas.core.storage import Lake, WriteMode, WriteResult
from atlas.core.storage.lake import DATA_FILE
from atlas.core.storage.meta import atomic_write
from atlas.core.types.base import FrozenDict
from atlas.core.types.instruments import normalize_symbol
from atlas.data.events import (
    EarningsCalendarProvider,
    build_upcoming_provider,
    ingest_earnings,
    ingest_earnings_reactions,
    ingest_macro_calendar,
    ingest_upcoming_events,
)
from atlas.data.fundamentals import (
    CikMap,
    ConceptMap,
    EdgarClient,
    build_cik_map,
    build_edgar_client,
    ingest_fundamentals,
    load_concept_map,
)
from atlas.data.macro import FredClient, build_fred_client, ingest_macro, load_macro_series
from atlas.data.news import NEWS, NewsProvider, build_news_provider, ingest_news
from atlas.data.news.alpaca_news import AlpacaNewsProvider
from atlas.data.prices import ingest_prices
from atlas.data.providers import PriceProvider
from atlas.data.providers.base import check_request
from atlas.data.providers.fallback import build_price_provider
from atlas.data.quality import (
    DATA_QUALITY_ISSUES,
    blocked_symbols,
    quality_report,
    read_quality_issues,
)
from atlas.data.reference import fetch_sp500_pages
from atlas.data.sentiment import (
    Lexicon,
    SocialProvider,
    build_social_providers,
    download_lm_lexicon,
    ingest_news_sentiment,
    ingest_social_posts,
    load_lexicon,
)
from atlas.data.universe import INDEX_MEMBERSHIP, Universe, refresh_universe_reference

STATE_FORMAT = 1
"""Version of the `sync_state.json` layout."""

MAX_RUNS = 50
"""Number of run entries kept in the state file (oldest dropped first)."""

KeyStatus = Literal["ok", "empty"]
Provider = Literal["yahoo", "sec", "fred", "alpaca", "wikipedia", "local"]
"""The thread pool a step runs on (`data.sync.workers` sizes each)."""
StepMode = Literal["window", "snapshot"]

AUDIT_ACTOR = "data.sync"
"""`actor` of the `DATA_SYNC` audit events."""

MAX_AUDIT_REASONS = 20
"""Failure reasons kept in one `DATA_SYNC` payload (the `StepReport` keeps them all)."""

_DAY = timedelta(days=1)
_log = get_logger(__name__)


# --- checkpoint ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Coverage:
    """What the sync has covered for one step and key: the inclusive window `start`..`end`
    (trading or calendar dates, as the step defines), the UTC time of the latest sync
    (`synced_at`) and whether the vendor returned data (`ok`) or nothing (`empty`)."""

    start: date
    end: date
    synced_at: datetime
    status: KeyStatus


def canonical_state_json(payload: Mapping[str, Any]) -> str:
    """The state file's text: sorted keys, two-space indent, trailing newline."""
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def _bad(path: Path, why: str) -> DataError:
    return DataError(
        f"sync state {path} is unreadable ({why}); fix or delete it (deleting restarts the "
        "backfill from scratch: stored data is kept and re-fetched idempotently)"
    )


def _parse_coverage(path: Path, raw: object) -> Coverage:
    if not isinstance(raw, dict) or set(raw) != {"from", "to", "synced_at", "status"}:
        raise _bad(path, f"bad entry {raw!r}")
    try:
        synced = datetime.fromisoformat(str(raw["synced_at"]))
        entry = Coverage(
            start=date.fromisoformat(str(raw["from"])),
            end=date.fromisoformat(str(raw["to"])),
            synced_at=synced,
            status=raw["status"],
        )
    except ValueError as exc:
        raise _bad(path, str(exc)) from exc
    if entry.status not in ("ok", "empty") or synced.utcoffset() is None:
        raise _bad(path, f"bad entry {raw!r}")
    return entry


class SyncState:
    """The sync checkpoint: per step and key, a `Coverage`; plus the last `MAX_RUNS` runs.

    Thread-safe: `record`, `add_run` and `save` hold one lock. `save` writes the whole file
    atomically (temp file + rename), so a crash never leaves a half-written state.
    """

    def __init__(self, path: Path) -> None:
        """An empty state that will be saved to `path` (use `load` to read one)."""
        self._path = Path(path)
        self._steps: dict[str, dict[str, Coverage]] = {}
        self._runs: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    @classmethod
    def load(cls, path: Path) -> "SyncState":
        """The state stored at `path`; empty when the file does not exist. A file that is not
        valid JSON or not in format `STATE_FORMAT` raises `DataError` (a backfill is never
        silently restarted)."""
        state = cls(path)
        if not state._path.exists():
            return state
        try:
            raw = json.loads(state._path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise _bad(state._path, f"not JSON: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("format") != STATE_FORMAT:
            raise _bad(state._path, f"expected format {STATE_FORMAT}")
        steps, runs = raw.get("steps"), raw.get("runs", [])
        if not isinstance(steps, dict) or not isinstance(runs, list):
            raise _bad(state._path, "steps must be an object and runs a list")
        for step, keys in steps.items():
            if not isinstance(keys, dict):
                raise _bad(state._path, f"step {step!r} is not an object")
            state._steps[str(step)] = {
                str(key): _parse_coverage(state._path, value) for key, value in keys.items()
            }
        state._runs = [dict(r) for r in runs if isinstance(r, dict)][-MAX_RUNS:]
        return state

    @property
    def path(self) -> Path:
        """The state file."""
        return self._path

    @property
    def runs(self) -> tuple[dict[str, Any], ...]:
        """The recorded runs, oldest first (copies)."""
        with self._lock:
            return tuple(dict(r) for r in self._runs)

    def steps(self) -> tuple[str, ...]:
        """Names of the steps with any recorded key, sorted."""
        with self._lock:
            return tuple(sorted(self._steps))

    def keys(self, step: str) -> dict[str, Coverage]:
        """The coverage of every key of `step` (a copy; empty when none)."""
        with self._lock:
            return dict(self._steps.get(step, {}))

    def coverage(self, step: str, key: str) -> Coverage | None:
        """The coverage of `key` in `step`, or `None`."""
        with self._lock:
            return self._steps.get(step, {}).get(key)

    def record(
        self,
        step: str,
        keys: Iterable[str],
        start: date,
        end: date,
        *,
        synced_at: datetime,
        empty: bool = False,
    ) -> None:
        """Mark `keys` of `step` as covered for `start`..`end` (inclusive), synced at
        `synced_at` (aware). A window that overlaps or touches the stored one extends it;
        a window with a gap replaces it. `empty` records that the vendor had no data; a key
        is `ok` once any of its windows had data. Not saved until `save()`."""
        if synced_at.utcoffset() is None:
            raise ValueError(f"naive datetime {synced_at!r}; pass a timezone-aware datetime")
        if start > end:
            raise ValueError(f"start ({start}) must not be after end ({end})")
        status: KeyStatus = "empty" if empty else "ok"
        with self._lock:
            table = self._steps.setdefault(step, {})
            for key in keys:
                old = table.get(key)
                if old is not None and start <= old.end + _DAY and end >= old.start - _DAY:
                    table[key] = Coverage(
                        start=min(start, old.start),
                        end=max(end, old.end),
                        synced_at=max(synced_at, old.synced_at),
                        status="ok" if "ok" in (old.status, status) else "empty",
                    )
                else:
                    table[key] = Coverage(start, end, synced_at, status)

    def truncate(self, step: str, keys: Iterable[str], end: date) -> None:
        """Forget what `keys` of `step` cover after `end`: a coverage ending later is cut back
        to `end`, and one starting after `end` is removed (the next run fetches it again)."""
        with self._lock:
            table = self._steps.get(step, {})
            for key in keys:
                old = table.get(key)
                if old is None or old.end <= end:
                    continue
                if old.start > end:
                    del table[key]
                else:
                    table[key] = Coverage(old.start, end, old.synced_at, old.status)

    def add_run(self, entry: Mapping[str, Any]) -> None:
        """Append a run summary (JSON values only); only the last `MAX_RUNS` are kept."""
        with self._lock:
            self._runs.append(dict(entry))
            del self._runs[:-MAX_RUNS]

    def min_start(self, steps: Iterable[str] | None = None) -> date | None:
        """The earliest covered `from` over `steps` (all steps when `None`), or `None`."""
        with self._lock:
            names = self._steps if steps is None else list(steps)
            starts = [c.start for s in names for c in self._steps.get(s, {}).values()]
        return min(starts) if starts else None

    def last_synced(self, steps: Iterable[str]) -> datetime | None:
        """The latest `synced_at` over the keys of `steps`, or `None` if none was synced."""
        with self._lock:
            stamps = [c.synced_at for s in steps for c in self._steps.get(s, {}).values()]
        return max(stamps) if stamps else None

    def save(self) -> None:
        """Write the state to `path` atomically (parent directories are created)."""
        with self._lock:
            payload = {
                "format": STATE_FORMAT,
                "steps": {
                    step: {
                        key: {
                            "from": c.start.isoformat(),
                            "to": c.end.isoformat(),
                            "synced_at": c.synced_at.isoformat(),
                            "status": c.status,
                        }
                        for key, c in sorted(keys.items())
                    }
                    for step, keys in sorted(self._steps.items())
                },
                "runs": list(self._runs),
            }
            text = canonical_state_json(payload)

            def _write(fh: BinaryIO) -> None:
                fh.write(text.encode("utf-8"))

            atomic_write(self._path, _write)


# --- planning --------------------------------------------------------------------------------


def plan_window(
    coverage: Coverage | None,
    start: date,
    end: date,
    overlap_days: int,
    *,
    now: datetime | None = None,
    empty_retry_days: int | None = None,
) -> tuple[date, date] | None:
    """The window `(first, last)` (inclusive) still to fetch for one key of a windowed step,
    or `None` when nothing is missing.

    No coverage, or a request starting before the covered `from`: the whole `start..end`.
    Covered up to `end`: `None`. An `empty` key (no vendor data anywhere in its covered
    history) last checked less than `empty_retry_days` days before `now`: `None` (pass both
    or neither). Otherwise from `overlap_days` calendar days before the day after the covered
    `to` (never before `start`) to `end`.
    """
    if empty_retry_days is not None and now is None:
        raise ValueError("pass now together with empty_retry_days")
    if start > end:
        return None
    if coverage is None or start < coverage.start:
        return (start, end)
    if coverage.end >= end:
        return None
    if (
        coverage.status == "empty"
        and empty_retry_days is not None
        and now is not None
        and now - coverage.synced_at < timedelta(days=empty_retry_days)
    ):
        return None
    first = max(start, coverage.end + _DAY - timedelta(days=overlap_days))
    return (first, end)


def plan_snapshot(coverage: Coverage | None, end: date) -> bool:
    """Whether a snapshot step must refetch a key: never synced, or synced for an earlier
    end date than `end`."""
    return coverage is None or coverage.end < end


@dataclass(frozen=True)
class Chunk:
    """A unit of sync work: `keys` fetched together for the inclusive window `start..end`."""

    keys: tuple[str, ...]
    start: date
    end: date


def group_chunks(windows: Mapping[str, tuple[date, date]], size: int) -> list[Chunk]:
    """Group keys sharing a window into chunks of at most `size` keys. Chunks of the first
    window seen come first; keys keep their order in `windows`."""
    if size < 1:
        raise ValueError(f"chunk size must be at least 1, got {size}")
    by_window: dict[tuple[date, date], list[str]] = {}
    for key, window in windows.items():
        by_window.setdefault(window, []).append(key)
    chunks: list[Chunk] = []
    for (first, last), keys in by_window.items():
        for i in range(0, len(keys), size):
            chunks.append(Chunk(keys=tuple(keys[i : i + size]), start=first, end=last))
    return chunks


def split_window(start: date, end: date, days: int | None) -> list[tuple[date, date]]:
    """`start..end` cut into consecutive inclusive windows of at most `days` days (one window
    when `days` is `None`)."""
    if days is None:
        return [(start, end)]
    if days < 1:
        raise ValueError(f"window days must be at least 1, got {days}")
    out: list[tuple[date, date]] = []
    first = start
    while first <= end:
        last = min(end, first + timedelta(days=days - 1))
        out.append((first, last))
        first = last + _DAY
    return out


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


# --- datasets --------------------------------------------------------------------------------

DATASETS: tuple[str, ...] = (
    "universe",
    "prices",
    "fundamentals",
    "macro",
    "news",
    "events",
    "sentiment",
    "quality",
)
"""Dataset names accepted by `run_sync` (and `--datasets`), in the order they run."""

DATASET_STEPS: Mapping[str, tuple[str, ...]] = FrozenDict(
    {
        "universe": ("universe",),
        "prices": ("prices",),
        "fundamentals": ("fundamentals",),
        "macro": ("macro",),
        "news": ("news",),
        "events": ("earnings", "upcoming", "macro_calendar", "reactions"),
        "sentiment": ("news_sentiment", "social"),
        "quality": ("quality",),
    }
)
"""The steps of each dataset, in run order (step names key the checkpoint)."""

ALL_KEY = "_all"
"""Pseudo-key of steps that cover the whole lake (reactions, news scoring)."""


@dataclass(frozen=True)
class SyncSources:
    """Lazy factories for everything a sync fetches from; each is called at most once per run,
    and only when a step that needs it runs (a missing API key then fails that step only).

    `cik_map(client)` builds the ticker → CIK map from the EDGAR client; `calendar_fred()`
    may return `None` (the macro calendar then keeps FOMC dates only); `upcoming_provider()`
    may return `None` (no scheduled-dates provider configured); `social_providers()` returns
    `()` when social posts are disabled; `universe_pages()` returns the constituents and
    change-log HTML; `lexicon()` returns the loaded Loughran-McDonald lexicon.
    """

    price_provider: Callable[[], PriceProvider]
    edgar: Callable[[], EdgarClient]
    cik_map: Callable[[EdgarClient], CikMap]
    fred: Callable[[], FredClient]
    calendar_fred: Callable[[], FredClient | None]
    news_provider: Callable[[], NewsProvider]
    upcoming_provider: Callable[[], EarningsCalendarProvider | None]
    social_providers: Callable[[], tuple[SocialProvider, ...]]
    universe_pages: Callable[[], tuple[str, str]]
    lexicon: Callable[[], Lexicon]

    @classmethod
    def live(cls, config: AtlasConfig, lake: Lake) -> "SyncSources":
        """The real vendors of the Stage 07-12 packages for `config` (secrets only from
        `config.secrets`; EDGAR and FRED answers archived under `lake`'s `raw/`).

        News uses Alpaca alone when its keys are set: during a backfill a failed Alpaca
        request must stay failed (and be retried), not be "covered" by Yahoo's recent-only
        news. Without the keys it is Yahoo (recent news only, with a warning).
        """

        def news() -> NewsProvider:
            alpaca = AlpacaNewsProvider.from_config(config)
            if alpaca is not None and config.data.providers.news == "alpaca":
                return alpaca
            return build_news_provider(config)

        def calendar_fred() -> FredClient | None:
            try:
                return build_fred_client(config, lake)
            except ConfigError as exc:
                _log.warning("sync.calendar_without_fred", reason=str(exc))
                return None

        def lexicon() -> Lexicon:
            path = config.data.sentiment.lexicon_file
            if not path.exists():
                download_lm_lexicon(config.data.sentiment)
            return load_lexicon(path)

        return cls(
            price_provider=lambda: build_price_provider(config),
            edgar=lambda: build_edgar_client(config, lake),
            cik_map=lambda client: build_cik_map(client, config),
            fred=lambda: build_fred_client(config, lake),
            calendar_fred=calendar_fred,
            news_provider=news,
            upcoming_provider=lambda: build_upcoming_provider(config),
            social_providers=lambda: build_social_providers(config),
            universe_pages=lambda: fetch_sp500_pages(config.data.reference),
            lexicon=lexicon,
        )


class _Resources:
    """Builds each source once per run (thread-safe) and closes what has a `close()`."""

    def __init__(self, sources: SyncSources) -> None:
        self._sources = sources
        self._made: dict[str, Any] = {}
        self._lock = threading.RLock()

    def get(self, name: str, *args: Any) -> Any:
        with self._lock:
            if name not in self._made:
                self._made[name] = getattr(self._sources, name)(*args)
            return self._made[name]

    def close(self) -> None:
        with self._lock:
            made = list(self._made.values())
            self._made.clear()
        for thing in made:
            items = thing if isinstance(thing, tuple) else (thing,)
            for item in items:
                close = getattr(item, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception as exc:  # closing must not hide the run's outcome
                        _log.warning(
                            "sync.close_failed", source=type(item).__name__, error=str(exc)
                        )


@dataclass(frozen=True)
class SyncReport:
    """Outcome of `run_sync`: the window (`start`..`end`, inclusive), one `StepReport` per
    step run, in order, and the checkpoint file. `ok` is `False` when a step could not run."""

    command: str
    start: date
    end: date
    steps: tuple[StepReport, ...]
    state_path: Path

    @property
    def ok(self) -> bool:
        """`True` when every step ran (keys may still have failed; see each report)."""
        return all(step.error is None for step in self.steps)

    @property
    def errors(self) -> tuple[StepReport, ...]:
        """The steps that could not run."""
        return tuple(step for step in self.steps if step.error is not None)


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _decision_after(calendar: TradingCalendar, config: AtlasConfig, now: datetime) -> date:
    day = market_date(now)
    if not calendar.is_session(day):
        return calendar.next_session(day)
    if AsOf.for_session(day, config=config.calendar).cutoff > now:
        return day
    return calendar.next_session(day)


def next_decision_day(config: AtlasConfig, now: datetime | None = None) -> date:
    """The first session whose decision cutoff (`calendar.decision_time`, New York) is after
    `now` (aware; default `get_clock().now()`): the day the next decision is made for."""
    instant = now if now is not None else get_clock().now()
    return _decision_after(TradingCalendar(config=config.calendar), config, instant)


def last_published_session(
    calendar: TradingCalendar, day: date, now: datetime, *, floor: date
) -> date | None:
    """The latest session on or before `day` whose daily bar is published at `now` (close +
    `calendar.bar_publish_lag_minutes`), not before `floor`; `None` when there is none."""
    current = day if calendar.is_session(day) else calendar.prev_session(day)
    while current >= floor:
        if bar_available_at(current, calendar) <= now:
            return current
        current = calendar.prev_session(current)
    return None


class _Run:
    """Everything one `run_sync` call shares between its steps."""

    def __init__(
        self,
        lake: Lake,
        config: AtlasConfig,
        sources: SyncSources,
        state: SyncState,
        start: date,
        end: date,
        symbols: tuple[str, ...] | None,
    ) -> None:
        self.lake = lake
        self.config = config
        self.resources = _Resources(sources)
        self.state = state
        self.start = start
        self.end = end
        self.explicit = symbols
        self.calendar = TradingCalendar(config=config.calendar)
        self.pages: tuple[str, str] | None = None
        self.news_from: date | None = None
        self._lock = threading.Lock()
        self._universe: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._concept_map: ConceptMap | None = None

    @property
    def settings(self) -> SyncConfig:
        return self.config.data.sync

    def etfs(self) -> tuple[str, ...]:
        """Tickers of every ticker-list universe, in configuration order."""
        tickers = [
            normalize_symbol(t)
            for u in self.config.data.universes.values()
            if u.index is None
            for t in u.tickers
        ]
        return tuple(dict.fromkeys(tickers))

    def index_id(self) -> str:
        name = self.settings.universe
        universes = self.config.data.universes
        if name not in universes or universes[name].index is None:
            raise ConfigError(f"data.sync.universe {name!r} is not an index universe")
        index = universes[name].index
        assert index is not None
        return index

    def universe(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """`(symbols, equities)`: every symbol to sync, and those that file with the SEC."""
        if self._universe is None:
            etfs = self.etfs()
            if self.explicit is not None:
                symbols = self.explicit
            else:
                members = _members_between(self.lake, self.index_id(), self.start, self.end)
                symbols = tuple(dict.fromkeys([*members, *etfs]))
            equities = tuple(s for s in symbols if s not in set(etfs))
            self._universe = (symbols, equities)
        return self._universe

    def concept_map(self) -> ConceptMap:
        with self._lock:
            if self._concept_map is None:
                self._concept_map = load_concept_map(self.config.data.fundamentals.xbrl_map_file)
            return self._concept_map

    def note_news(self, first: date) -> None:
        with self._lock:
            self.news_from = first if self.news_from is None else min(self.news_from, first)


def _members_between(lake: Lake, index_id: str, start: date, end: date) -> tuple[str, ...]:
    """Sorted symbols that were in `index_id` on any day of `start..end`."""
    if not lake.has_table(INDEX_MEMBERSHIP.name):
        raise DataError(
            "no index_membership table: run the universe dataset first (atlas data sync "
            "--datasets universe) or pass --symbols"
        )
    rows = lake.read_table(INDEX_MEMBERSHIP.name, filters=[("index_id", "=", index_id)])
    if rows.empty:
        raise DataError(f"no {index_id} rows in index_membership: run the universe dataset")
    ends = rows["end_date"]
    open_ended = ends.isna()
    alive = open_ended | (ends.where(~open_ended, date.max) > start)
    began = rows["start_date"] <= end
    return tuple(sorted(set(rows.loc[alive & began, "symbol"])))


def _classify(
    keys: Sequence[str],
    ok: Sequence[str],
    reasons: Mapping[str, str],
    empty_if: Callable[[str], bool],
) -> ChunkOutcome:
    """Split a Stage 07-12 ingest report into a `ChunkOutcome`: failures whose reason means
    "the vendor has nothing" are `empty`."""
    empty = tuple(k for k in keys if k in reasons and empty_if(reasons[k]))
    failed = {k: reasons[k] for k in keys if k in reasons and k not in set(empty)}
    return ChunkOutcome(ok=tuple(ok), empty=empty, failed=failed)


def _no_sec_data(reason: str) -> bool:
    return reason.startswith("no CIK") or reason == "no mapped facts in companyfacts"


def _step(
    run: _Run,
    name: str,
    dataset: str,
    provider: Provider,
    mode: StepMode,
    keys: tuple[str, ...],
    fetch: FetchFn,
    *,
    chunk_size: int = 1,
    start: date | None = None,
    end: date | None = None,
    window_days: int | None = None,
) -> StepSpec:
    return StepSpec(
        name=name,
        dataset=dataset,
        provider=provider,
        mode=mode,
        keys=keys,
        start=run.start if start is None else start,
        end=run.end if end is None else end,
        chunk_size=chunk_size,
        fetch=fetch,
        window_days=window_days,
        overlap_days=run.settings.update_overlap_days if mode == "window" else 0,
        empty_retry_days=run.settings.empty_retry_days if mode == "window" else None,
    )


def _universe_step(run: _Run) -> StepSpec:
    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        pages = run.resources.get("universe_pages")
        report = refresh_universe_reference(run.lake, *pages, config=run.config)
        run.pages = pages
        return ChunkOutcome(rows=report.instruments + report.intervals, ok=keys)

    return _step(run, "universe", "universe", "wikipedia", "snapshot", (run.index_id(),), fetch)


def _prices_step(run: _Run) -> StepSpec:
    symbols, _ = run.universe()
    provider = run.resources.get("price_provider")
    now = get_clock().now()
    last = last_published_session(run.calendar, run.end, now, floor=run.start)
    pool: Provider = "alpaca" if run.config.data.providers.prices == "alpaca" else "yahoo"

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_prices(
            run.lake,
            provider,
            keys,
            start,
            end,
            calendar=run.calendar,
            config=run.config.data.prices,
        )
        outcome = _classify(
            keys, report.symbols_ok, report.failure_reasons, lambda r: r == "no price data returned"
        )
        return ChunkOutcome(
            rows=report.rows + report.action_rows,
            ok=outcome.ok,
            empty=outcome.empty,
            failed=outcome.failed,
        )

    return _step(
        run,
        "prices",
        "prices",
        pool,
        "window",
        symbols,
        fetch,
        chunk_size=run.settings.chunk_size.prices,
        end=last if last is not None else run.start - _DAY,
    )


def _edgar(run: _Run) -> tuple[EdgarClient, CikMap]:
    client = run.resources.get("edgar")
    return client, run.resources.get("cik_map", client)


def _fundamentals_step(run: _Run) -> StepSpec:
    _, equities = run.universe()
    client, cik_map = _edgar(run)
    concept_map = run.concept_map()

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_fundamentals(
            run.lake,
            client,
            keys,
            cik_map=cik_map,
            config=run.config,
            calendar=run.calendar,
            concept_map=concept_map,
        )
        outcome = _classify(keys, report.symbols_ok, report.failure_reasons, _no_sec_data)
        return ChunkOutcome(report.rows, outcome.ok, outcome.empty, outcome.failed)

    return _step(
        run,
        "fundamentals",
        "fundamentals",
        "sec",
        "snapshot",
        equities,
        fetch,
        chunk_size=run.settings.chunk_size.fundamentals,
    )


def _macro_step(run: _Run) -> StepSpec:
    catalog = load_macro_series(run.config.data.macro.series_file)
    client = run.resources.get("fred")

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_macro(run.lake, client, keys, config=run.config, catalog=catalog)
        outcome = _classify(keys, report.series_ok, report.failure_reasons, lambda r: False)
        return ChunkOutcome(report.rows, outcome.ok, outcome.empty, outcome.failed)

    return _step(
        run,
        "macro",
        "macro",
        "fred",
        "snapshot",
        catalog.ids,
        fetch,
        chunk_size=run.settings.chunk_size.macro,
    )


def _news_step(run: _Run) -> StepSpec:
    symbols, _ = run.universe()
    provider = run.resources.get("news_provider")
    pool: Provider = "alpaca" if getattr(provider, "name", "") == "alpaca" else "yahoo"

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_news(run.lake, provider, keys, _midnight(start), _midnight(end + _DAY))
        if report.rows:
            run.note_news(start)
        return ChunkOutcome(rows=report.rows, ok=keys)

    return _step(
        run,
        "news",
        "news",
        pool,
        "window",
        symbols,
        fetch,
        chunk_size=run.settings.chunk_size.news,
        window_days=run.settings.news_window_days,
    )


def _earnings_step(run: _Run) -> StepSpec:
    _, equities = run.universe()
    client, cik_map = _edgar(run)

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_earnings(
            run.lake, client, keys, cik_map=cik_map, config=run.config, calendar=run.calendar
        )
        outcome = _classify(keys, report.symbols_ok, report.failure_reasons, _no_sec_data)
        return ChunkOutcome(report.rows, outcome.ok, outcome.empty, outcome.failed)

    return _step(
        run,
        "earnings",
        "events",
        "sec",
        "snapshot",
        equities,
        fetch,
        chunk_size=run.settings.chunk_size.earnings,
    )


def _upcoming_step(run: _Run) -> StepSpec | None:
    if run.end < get_clock().today():
        _log.info("sync.upcoming_skipped", reason="the run ends in the past; a snapshot is today's")
        return None
    provider = run.resources.get("upcoming_provider")
    if provider is None:
        _log.info("sync.upcoming_skipped", reason="data.events.upcoming_earnings is none")
        return None
    _, equities = run.universe()
    if run.explicit is None:  # only names trading now have scheduled dates
        current = set(_members_between(run.lake, run.index_id(), run.end, run.end))
        equities = tuple(s for s in equities if s in current)

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        try:
            return ChunkOutcome(rows=ingest_upcoming_events(run.lake, provider, keys), ok=keys)
        except DataError as exc:
            if len(keys) == 1:
                raise
            _log.warning("sync.upcoming_chunk_failed", keys=len(keys), error=str(exc))
        rows, ok, failed = 0, [], {}
        for key in keys:  # one bad ticker must not fail its whole chunk
            try:
                rows += ingest_upcoming_events(run.lake, provider, [key])
                ok.append(key)
            except DataError as exc:
                failed[key] = str(exc)
        return ChunkOutcome(rows=rows, ok=tuple(ok), failed=failed)

    return _step(
        run,
        "upcoming",
        "events",
        "yahoo",
        "snapshot",
        equities,
        fetch,
        chunk_size=run.settings.chunk_size.upcoming,
    )


def _macro_calendar_step(run: _Run) -> StepSpec:
    client = run.resources.get("calendar_fred")

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        rows = ingest_macro_calendar(run.lake, client, config=run.config)
        return ChunkOutcome(rows=rows, ok=keys)

    return _step(run, "macro_calendar", "events", "fred", "snapshot", ("_calendar",), fetch)


def _reactions_step(run: _Run) -> StepSpec:
    wanted = None if run.explicit is None else run.universe()[1]

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_earnings_reactions(
            run.lake, wanted, config=run.config, calendar=run.calendar
        )
        return ChunkOutcome(rows=report.rows, ok=keys)

    return _step(run, "reactions", "events", "local", "snapshot", (ALL_KEY,), fetch)


def _news_sentiment_step(run: _Run) -> StepSpec:
    if run.news_from is not None:  # news written this run: score it again from there
        run.state.truncate("news_sentiment", [ALL_KEY], run.news_from - _DAY)

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        if not run.lake.has_table(NEWS.name):
            return ChunkOutcome(empty=keys)
        lexicon = run.resources.get("lexicon")
        rows = ingest_news_sentiment(
            run.lake, lexicon, _midnight(start), _midnight(end + _DAY), config=run.config
        )
        return ChunkOutcome(rows=rows, ok=keys)

    return _step(
        run,
        "news_sentiment",
        "sentiment",
        "local",
        "window",
        (ALL_KEY,),
        fetch,
        window_days=run.settings.sentiment_window_days,
    )


def _social_step(run: _Run) -> StepSpec | None:
    providers = run.resources.get("social_providers")
    if not providers:
        _log.info("sync.social_skipped", reason="data.sentiment.social.enabled is false")
        return None
    symbols, _ = run.universe()

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_social_posts(
            run.lake, providers, keys, _midnight(start), _midnight(end + _DAY)
        )
        if len(report.providers_failed) == len(providers):
            reason = "; ".join(f"{k}: {v}" for k, v in report.failure_reasons.items())
            return ChunkOutcome(failed={k: reason for k in keys})
        return ChunkOutcome(rows=report.rows, ok=keys)

    return _step(
        run,
        "social",
        "sentiment",
        "local",
        "window",
        symbols,
        fetch,
        chunk_size=run.settings.chunk_size.social,
    )


def _quality_step(run: _Run) -> StepSpec:
    now = get_clock().now()
    if run.end >= market_date(now):
        day = _decision_after(run.calendar, run.config, now)
    else:
        day = run.calendar.next_session(run.end)

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = quality_report(
            run.lake,
            day,
            config=run.config,
            calendar=run.calendar,
            concept_map=run.concept_map(),
            persist=True,
        )
        return ChunkOutcome(rows=len(report), ok=keys)

    return _step(run, "quality", "quality", "local", "snapshot", ("_report",), fetch)


_BUILDERS: Mapping[str, Callable[[_Run], StepSpec | None]] = FrozenDict(
    {
        "universe": _universe_step,
        "prices": _prices_step,
        "fundamentals": _fundamentals_step,
        "macro": _macro_step,
        "news": _news_step,
        "earnings": _earnings_step,
        "upcoming": _upcoming_step,
        "macro_calendar": _macro_calendar_step,
        "reactions": _reactions_step,
        "news_sentiment": _news_sentiment_step,
        "social": _social_step,
        "quality": _quality_step,
    }
)


def parse_datasets(names: Iterable[str] | None) -> tuple[str, ...]:
    """The requested datasets in run order (all when `None`); an unknown name raises
    `ValueError` listing the known ones."""
    if names is None:
        return DATASETS
    wanted = [n.strip().lower() for n in names if n.strip()]
    unknown = sorted(set(wanted) - set(DATASETS))
    if unknown:
        raise ValueError(f"unknown datasets {unknown}; choose from {', '.join(DATASETS)}")
    if not wanted:
        raise ValueError(f"no datasets given; choose from {', '.join(DATASETS)}")
    return tuple(d for d in DATASETS if d in set(wanted))


def state_path(lake: Lake, config: AtlasConfig) -> Path:
    """The checkpoint file of `lake`: `<lake root>/<data.sync.state_file>`."""
    return lake.root / config.data.sync.state_file


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


# --- status ----------------------------------------------------------------------------------

STATUS_TABLES: Mapping[str, tuple[str, str | None, str | None]] = FrozenDict(
    {
        "index_membership": ("universe", "start_date", "symbol"),
        "instruments": ("universe", "first_trade_date", "symbol"),
        "prices_daily": ("prices", "date", "symbol"),
        "corporate_actions": ("prices", "ex_date", "symbol"),
        "fundamentals_facts": ("fundamentals", "period_end", "symbol"),
        "macro_series": ("macro", None, "series_id"),
        "macro_observations": ("macro", "obs_date", "series_id"),
        "news": ("news", "published_at", "symbols"),
        "events": ("events", "event_date", "symbol"),
        "macro_events": ("events", "event_date", None),
        "earnings_reactions": ("events", "reaction_date", "symbol"),
        "news_sentiment": ("sentiment", "published_at", None),
        "social_posts": ("sentiment", "created_at", "symbol"),
        "data_quality_issues": ("quality", "report_day", "symbol"),
    }
)
"""Tables `lake_status` summarises: `table -> (dataset, date column, key column)`; the key
column of `news` is the tag list (distinct tagged symbols are counted)."""

STATUS_COLUMNS: tuple[str, ...] = ("table", "dataset", "rows", "keys", "first", "last", "last_sync")


@dataclass(frozen=True)
class CoverageLine:
    """How much of a dataset the lake holds: `covered` of `total` (current index members, or
    catalog series), under `rule`; `missing` lists what is not covered (sorted)."""

    dataset: str
    rule: str
    covered: int
    total: int
    missing: tuple[str, ...] = ()

    @property
    def pct(self) -> float:
        """`covered / total` in percent (NaN when `total` is 0)."""
        return 100.0 * self.covered / self.total if self.total else float("nan")


@dataclass(frozen=True)
class LakeStatus:
    """What `lake_status` found on `as_of` (a New York date).

    `tables` has `STATUS_COLUMNS`: rows, distinct keys, first and last date (`date`; the UTC
    date for timestamp columns) and the latest sync of the table's dataset (aware UTC, from
    the checkpoint; `None` if never synced). `members` is the number of current members of
    `data.sync.universe`; `coverage` has one `CoverageLine` per dataset; `quality` counts the
    issues of the latest persisted quality report (`quality_day`) by severity, and `blocked`
    lists its symbols with a critical issue. `last_run` is the checkpoint's latest run entry;
    `note` explains why a part could not be computed (e.g. no membership yet)."""

    as_of: date
    tables: pd.DataFrame
    members: int
    coverage: tuple[CoverageLine, ...]
    quality_day: date | None
    quality: pd.DataFrame
    blocked: tuple[str, ...]
    last_run: dict[str, Any] | None = None
    note: str | None = None


def _glob(lake: Lake, table: str) -> str | None:
    if not lake.table_files(table):
        return None
    return str(lake.table_path(table) / "**" / DATA_FILE)


_SOURCE = "read_parquet($files, hive_partitioning = false)"


def _one(con: duckdb.DuckDBPyConnection, sql: str, files: str) -> tuple[Any, ...]:
    row = con.execute(sql.format(src=_SOURCE), {"files": files}).fetchone()
    assert row is not None  # an aggregate query always returns one row
    return tuple(row)


def _summaries(con: duckdb.DuckDBPyConnection, lake: Lake, state: SyncState) -> pd.DataFrame:
    rows = []
    for table, (dataset, day_col, key_col) in STATUS_TABLES.items():
        files = _glob(lake, table)
        count = keys = 0
        first = last = None
        if files is not None:
            span = f"CAST(min({day_col}) AS DATE), CAST(max({day_col}) AS DATE)"
            if day_col is None:
                span = "NULL, NULL"
            count, first, last = _one(con, f"SELECT count(*), {span} FROM {{src}}", files)
            if key_col == "symbols":
                sql = "SELECT count(DISTINCT s) FROM (SELECT unnest(symbols) AS s FROM {src})"
                (keys,) = _one(con, sql, files)
            elif key_col is not None:
                (keys,) = _one(con, f"SELECT count(DISTINCT {key_col}) FROM {{src}}", files)
        rows.append(
            {
                "table": table,
                "dataset": dataset,
                "rows": int(count),
                "keys": int(keys),
                "first": first,
                "last": last,
                "last_sync": state.last_synced(DATASET_STEPS[dataset]),
            }
        )
    return pd.DataFrame(rows, columns=list(STATUS_COLUMNS))


def _per_key(con: duckdb.DuckDBPyConnection, lake: Lake, table: str, sql: str) -> pd.DataFrame:
    files = _glob(lake, table)
    if files is None:
        return pd.DataFrame()
    return con.execute(sql.format(src=_SOURCE), {"files": files}).df()


def lake_status(lake: Lake, config: AtlasConfig, *, as_of: date | None = None) -> LakeStatus:
    """Summarise the lake on `as_of` (default: the clock's New York date) without writing
    anything (aggregates run in an in-memory DuckDB over the Parquet files, so it is safe while
    a sync is running in another process).

    Coverage (the M1 acceptance numbers), over the current members of `data.sync.universe`:
    `prices` — first and last stored bar at least `data.sync.status_min_price_years` years
    (of 365.25 days) apart; `fundamentals` — at least one stored fact; `news` — at least one
    tagged article; `events` — at least one earnings event. `macro` — catalog series
    (`data.macro.series_file`) with at least one observation.
    """
    day = as_of if as_of is not None else get_clock().today()
    settings = config.data.sync
    state = SyncState.load(state_path(lake, config))
    note: str | None = None
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET TimeZone = 'UTC'")
        tables = _summaries(con, lake, state)
        try:
            members = tuple(sorted(Universe(lake, config).members(day, settings.universe)))
        except DataError as exc:
            members = ()
            note = f"no current members ({exc}); run the universe dataset"
        spans = _per_key(
            con,
            lake,
            "prices_daily",
            "SELECT symbol, min(date) AS first, max(date) AS last FROM {src} GROUP BY symbol",
        )
        years = settings.status_min_price_years
        enough: set[str] = set()
        if not spans.empty:
            days = (pd.to_datetime(spans["last"]) - pd.to_datetime(spans["first"])).dt.days
            enough = set(spans.loc[days.to_numpy() >= years * 365.25, "symbol"])
        facts = _per_key(con, lake, "fundamentals_facts", "SELECT DISTINCT symbol FROM {src}")
        tagged = _per_key(con, lake, "news", "SELECT DISTINCT unnest(symbols) AS symbol FROM {src}")
        earnings = _per_key(
            con,
            lake,
            "events",
            "SELECT DISTINCT symbol FROM {src} WHERE event_type = 'earnings'",
        )
        observed = _per_key(con, lake, "macro_observations", "SELECT DISTINCT series_id FROM {src}")
    finally:
        con.close()

    def of_members(dataset: str, rule: str, have: set[str]) -> CoverageLine:
        missing = tuple(m for m in members if m not in have)
        return CoverageLine(dataset, rule, len(members) - len(missing), len(members), missing)

    def column(frame: pd.DataFrame, name: str) -> set[str]:
        return set() if frame.empty else {str(v) for v in frame[name]}

    catalog = load_macro_series(config.data.macro.series_file).ids
    have_series = column(observed, "series_id")
    coverage = (
        of_members("prices", f">= {years:g} years of daily prices", enough),
        of_members("fundamentals", "at least one stored XBRL fact", column(facts, "symbol")),
        CoverageLine(
            "macro",
            "catalog series with observations",
            sum(1 for s in catalog if s in have_series),
            len(catalog),
            tuple(s for s in catalog if s not in have_series),
        ),
        of_members("news", "at least one tagged article", column(tagged, "symbol")),
        of_members("events", "at least one earnings event", column(earnings, "symbol")),
    )
    quality_day, quality, blocked = _quality_summary(lake, config)
    runs = state.runs
    return LakeStatus(
        as_of=day,
        tables=tables,
        members=len(members),
        coverage=coverage,
        quality_day=quality_day,
        quality=quality,
        blocked=blocked,
        last_run=runs[-1] if runs else None,
        note=note,
    )


def _quality_summary(
    lake: Lake, config: AtlasConfig
) -> tuple[date | None, pd.DataFrame, tuple[str, ...]]:
    empty = pd.DataFrame({"severity": pd.Series(dtype="str"), "issues": pd.Series(dtype="int64")})
    if not lake.table_files(DATA_QUALITY_ISSUES.name):
        return None, empty, ()
    days = lake.read_table(DATA_QUALITY_ISSUES.name, columns=["report_day"])["report_day"]
    if days.empty:
        return None, empty, ()
    latest: date = max(days)
    issues = read_quality_issues(lake, latest, config=config)
    order = ["critical", "warning", "info"]
    counts = issues["severity"].value_counts()
    frame = pd.DataFrame(
        {
            "severity": [s for s in order if s in counts],
            "issues": [int(counts[s]) for s in order if s in counts],
        }
    )
    return latest, frame, tuple(sorted(blocked_symbols(issues)))


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
