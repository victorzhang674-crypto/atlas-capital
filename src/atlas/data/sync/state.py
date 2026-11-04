"""Sync checkpoint and planning (Stage 14): the per-step, per-key covered windows in
`sync_state.json` (`SyncState`) and how a run turns them into fetch windows and chunks."""

import json
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Literal

from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage.meta import atomic_write

STATE_FORMAT = 1
"""Version of the `sync_state.json` layout."""

MAX_RUNS = 50
"""Number of run entries kept in the state file (oldest dropped first)."""

KeyStatus = Literal["ok", "empty"]

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
