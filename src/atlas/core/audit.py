"""Append-only, hash-chained audit trail of decision-relevant events.

Each run writes JSON lines to `<lake>/runs/<run_id>/audit.jsonl`. Every event stores the hash
of the event before it, and its own hash is SHA-256 of that previous hash followed by the
canonical JSON of the event (without the `hash` field), so editing, deleting or reordering
any line breaks the chain and `AuditLog.verify_chain` returns `False`.

Several processes may write one file: each append holds an exclusive `flock` on it and first
re-reads the chain's tail if another process has appended since (POSIX only).
"""

import fcntl
import hashlib
import json
import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from pathlib import Path
from typing import IO, Annotated, Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
)

from atlas.core.errors import AtlasError
from atlas.core.run_context import current_run_context, run_dir
from atlas.core.types.ids import new_ulid

GENESIS_HASH = "0" * 64
"""`prev_hash` of the first event in a log."""

AUDIT_FILENAME = "audit.jsonl"

_Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class EventType(StrEnum):
    """Kinds of audit event. Each value equals its name. Later stages add their own kinds."""

    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    DATA_SYNC = "DATA_SYNC"
    ANALYST_REPORT = "ANALYST_REPORT"
    THESIS = "THESIS"
    PROPOSAL = "PROPOSAL"
    ALLOCATION = "ALLOCATION"
    RISK_DECISION = "RISK_DECISION"
    COMPLIANCE = "COMPLIANCE"
    ORDER = "ORDER"
    FILL = "FILL"
    LIMIT_BREACH = "LIMIT_BREACH"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    OVERRIDE = "OVERRIDE"
    LESSON = "LESSON"


def _encode(obj: object) -> object:
    if isinstance(obj, Decimal):
        if not obj.is_finite():
            raise ValueError(f"non-finite Decimal {obj} is not allowed in canonical JSON")
        return str(obj)
    if isinstance(obj, datetime):  # before `date`: datetime is a date subclass
        if obj.tzinfo is None or obj.utcoffset() is None:
            raise ValueError(f"naive datetime {obj.isoformat()} is not allowed; attach a tz")
        return obj.astimezone(UTC).isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="python")
    raise TypeError(f"{type(obj).__name__} is not allowed in canonical JSON")


def canonical_json(obj: object) -> str:
    """Deterministic JSON text for hashing: sorted keys, no whitespace, UTF-8 kept as is.

    Encodes `Decimal` as its exact string (`Decimal("1.10")` → `"1.10"`), tz-aware `datetime`
    as a UTC ISO-8601 string, `date` as `YYYY-MM-DD`, an `Enum` as its value and a Pydantic
    model as its fields. Raises `ValueError` for NaN/infinite numbers and naive datetimes and
    `TypeError` for any other type (sets included: they have no order).
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_encode,
    )


class AuditEvent(BaseModel):
    """One line of the audit log.

    `event_id` is a 26-char ULID from the current id source and `ts` (tz-aware UTC,
    millisecond precision) is the time embedded in it, i.e. when the event was recorded: the
    wall clock by default, the simulated clock under `use_ids(SeededIds(seed, clock))`.
    `run_id` and `as_of` (the trading day being decided, or `None`) come from the run context.
    `payload` holds JSON values only (see `canonical_json`). `hash` is SHA-256 hex of
    `prev_hash` + the canonical JSON of every other field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    run_id: str
    ts: AwareDatetime
    as_of: date | None
    actor: Annotated[str, Field(min_length=1)]
    event_type: EventType
    payload: dict[str, JsonValue]
    prev_hash: _Sha256Hex
    hash: _Sha256Hex

    @field_validator("ts")
    @classmethod
    def _ts_is_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("ts must be in UTC")
        return value


def _chain_hash(prev_hash: str, body: Mapping[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(body)).encode("utf-8")).hexdigest()


def _last_verified_hash(path: Path) -> str | None:
    """Walk the whole file; return the last event's hash (`GENESIS_HASH` when empty), or
    `None` if any line is unreadable, invalid, mis-linked or mis-hashed, or the file does not
    end with a newline (a half-written last line)."""
    data = path.read_bytes()
    if not data:
        return GENESIS_HASH
    if not data.endswith(b"\n"):
        return None
    try:
        lines = data.decode("utf-8").split("\n")[:-1]
    except UnicodeDecodeError:
        return None
    prev = GENESIS_HASH
    for line in lines:
        try:
            raw = json.loads(line)
            AuditEvent.model_validate(raw)
        except (ValueError, ValidationError):  # JSONDecodeError is a ValueError
            return None
        body = {key: value for key, value in raw.items() if key != "hash"}
        if raw["prev_hash"] != prev or raw["hash"] != _chain_hash(prev, body):
            return None
        prev = raw["hash"]
    return prev


@dataclass
class _ChainState:
    """Tail of one audit file, shared by every `AuditLog` on that file in this process.
    `last_hash` is `None` once a write has failed: the file can no longer be trusted. `size`
    is the file's length in bytes after this process last read or wrote it; any other length
    means another process has appended since."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    last_hash: str | None = GENESIS_HASH
    size: int = 0


@contextmanager
def _flocked(fh: IO[Any], operation: int) -> Iterator[None]:
    """Hold `fcntl.flock(fh, operation)` (shared or exclusive) for the `with` block."""
    fcntl.flock(fh.fileno(), operation)
    try:
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


_STATES: dict[Path, _ChainState] = {}
_STATES_LOCK = threading.Lock()


def _state_for(path: Path) -> _ChainState:
    with _STATES_LOCK:
        return _STATES.setdefault(path.resolve(), _ChainState())


class AuditLog:
    """Append-only JSONL audit log with a SHA-256 hash chain.

    Opening an existing file verifies its whole chain first and raises `AtlasError` if it is
    broken: a tampered or half-written log is never extended. Appends are serialised by a lock
    shared by every `AuditLog` on the same file in this process and by an exclusive `flock`
    across processes; an append that finds the file grown by another process verifies the
    whole chain again and continues from its real tail. Each line is flushed and fsynced
    before `record` returns.
    """

    def __init__(self, path: Path) -> None:
        """Open (or prepare to create) the log at `path`; parent directories are created."""
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._state = _state_for(path)
        with self._state.lock:
            last: str | None = GENESIS_HASH
            size = 0
            if path.exists():
                with path.open("rb") as fh, _flocked(fh, fcntl.LOCK_SH):
                    last = _last_verified_hash(path)
                    size = os.fstat(fh.fileno()).st_size
            if last is None:
                raise AtlasError(
                    f"audit log {path} fails hash-chain verification; refusing to append"
                )
            self._state.last_hash, self._state.size = last, size

    @property
    def path(self) -> Path:
        """The JSONL file this log appends to."""
        return self._path

    def record(self, event_type: EventType, actor: str, payload: Mapping[str, Any]) -> AuditEvent:
        """Append one event and return it.

        `actor` names who acted (an agent, pod or component; non-empty). `payload` is
        converted to JSON values by `canonical_json` rules first, so a `ValueError` or
        `TypeError` there writes nothing. Needs an active run context (`AtlasError` otherwise).
        """
        ctx = current_run_context()
        if ctx is None:
            raise AtlasError("AuditLog.record needs an active run context (use run_context())")
        normalised = json.loads(canonical_json(dict(payload)))
        with self._state.lock:
            if self._state.last_hash is None:
                raise AtlasError(f"audit log {self._path} is unusable after a failed write")
            with self._path.open("a", encoding="utf-8") as fh, _flocked(fh, fcntl.LOCK_EX):
                prev_hash = self._tail(os.fstat(fh.fileno()).st_size)
                ulid = new_ulid()
                draft = AuditEvent(
                    event_id=str(ulid),
                    run_id=ctx.run_id,
                    ts=ulid.datetime,
                    as_of=ctx.as_of,
                    actor=actor,
                    event_type=event_type,
                    payload=normalised,
                    prev_hash=prev_hash,
                    hash=GENESIS_HASH,  # placeholder, replaced below
                )
                body = draft.model_dump(mode="json", exclude={"hash"})
                event = draft.model_copy(update={"hash": _chain_hash(prev_hash, body)})
                line = canonical_json(event.model_dump(mode="json")) + "\n"
                try:
                    fh.write(line)
                    fh.flush()
                    os.fsync(fh.fileno())
                except BaseException:
                    self._state.last_hash = None
                    raise
                self._state.last_hash = event.hash
                self._state.size = os.fstat(fh.fileno()).st_size
        return event

    def _tail(self, size: int) -> str:
        """The hash to chain the next event to, given the file's current `size`; caller holds
        the exclusive file lock. If another process appended, re-verify the whole file."""
        last = self._state.last_hash
        if size != self._state.size:
            last = _last_verified_hash(self._path)
            self._state.last_hash, self._state.size = last, size
        if last is None:
            raise AtlasError(f"audit log {self._path} fails hash-chain verification")
        return last

    @staticmethod
    def verify_chain(path: Path) -> bool:
        """`True` if every line of `path` is a valid event correctly linked and hashed (an
        empty file is valid); `False` if the file is missing or anything does not check out."""
        if not path.is_file():
            return False
        return _last_verified_hash(path) is not None


def audit_path(lake_path: Path, run_id: str) -> Path:
    """Where a run's audit log lives: `<lake_path>/runs/<run_id>/audit.jsonl`."""
    return run_dir(lake_path, run_id) / AUDIT_FILENAME
