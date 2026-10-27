"""DuckDB over the lake: session-only views of the curated tables and read-only queries.

The database file is `<lake root>/atlas.duckdb`. It stores only native tables (today the
append-only `audit_events` mirror). Curated Parquet tables are exposed as views registered on
this connection alone, never stored in the file, so moving the lake or opening the file
read-only (the dashboard) never meets stale paths. The connection runs with `TimeZone = 'UTC'`.

No SQL is built by formatting values into text: values are bound as `?` / `$name`
parameters, and views are created through the relation API.
"""

import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Self

import duckdb
import pandas as pd

from atlas.core.audit import AuditEvent, AuditLog, canonical_json
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage.lake import DATA_FILE, Lake

DB_FILENAME = "atlas.duckdb"
AUDIT_TABLE = "audit_events"

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id   VARCHAR PRIMARY KEY,
    run_id     VARCHAR NOT NULL,
    seq        INTEGER NOT NULL,
    ts         TIMESTAMPTZ NOT NULL,
    as_of      DATE,
    actor      VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL,
    payload    JSON NOT NULL,
    prev_hash  VARCHAR NOT NULL,
    hash       VARCHAR NOT NULL,
    UNIQUE (run_id, seq)
)
"""

_INSERT_AUDIT = "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
_STORED_AUDIT = "SELECT event_id, hash FROM audit_events WHERE run_id = ? ORDER BY seq"

_Params = Sequence[object] | Mapping[str, object] | None

_log = get_logger(__name__)


class Database:
    """One DuckDB connection to `<lake root>/atlas.duckdb`, with a view per registered table.

    Views are rebuilt before a query whenever `lake.generation` has moved (writes through the
    same `Lake` object) or a table was registered; call `refresh()` after writes made
    elsewhere. Thread-safe (one lock around the connection). Use `get_database(lake)` for the
    process-wide instance.
    """

    def __init__(self, lake: Lake, *, read_only: bool = False) -> None:
        """Open (creating if needed, unless `read_only`) the lake's database file and create
        `audit_events` if missing. `read_only` needs an existing file."""
        self._lake = lake
        self._path = lake.root / DB_FILENAME
        self._read_only = read_only
        self._lock = threading.RLock()
        self._views_state: tuple[int, tuple[str, ...]] | None = None
        self._con: duckdb.DuckDBPyConnection | None = duckdb.connect(
            str(self._path), read_only=read_only
        )
        try:
            self._con.execute("SET TimeZone = 'UTC'")
            if not read_only:
                self._con.execute(_AUDIT_DDL)
            self.refresh()
        except BaseException:
            self.close()
            raise

    @property
    def path(self) -> Path:
        """The DuckDB database file."""
        return self._path

    @property
    def lake(self) -> Lake:
        """The lake whose tables this database exposes."""
        return self._lake

    @property
    def read_only(self) -> bool:
        """`True` if the file was opened read-only."""
        return self._read_only

    @property
    def closed(self) -> bool:
        """`True` after `close()`."""
        return self._con is None

    def _connection(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            raise ValueError(f"database {self._path} is closed")
        return self._con

    def _current_state(self) -> tuple[int, tuple[str, ...]]:
        return (self._lake.generation, self._lake.registry.names())

    def refresh(self) -> None:
        """Re-register one view per table in the lake's registry: over the table's Parquet
        glob if it has files, else over an empty table with the schema's column types."""
        with self._lock:
            con = self._connection()
            for name in self._lake.registry.names():
                schema = self._lake.table_schema(name)
                if self._lake.table_files(name):
                    glob = str(self._lake.table_path(name) / "**" / DATA_FILE)
                    con.register(name, con.read_parquet(glob, hive_partitioning=False))
                else:
                    con.register(name, schema.arrow_schema().empty_table())
            self._views_state = self._current_state()

    def query(self, sql: str, params: _Params = None) -> pd.DataFrame:
        """Run one `SELECT` statement with bound `params` (`?` placeholders take a sequence,
        `$name` placeholders a mapping) and return the rows as pandas.

        Dates come back as `datetime.date`, timestamps with time zone as `datetime64[us, UTC]`.
        Anything other than exactly one `SELECT` (writes, DDL, `COPY`, `ATTACH`, `SET`, several
        statements) raises `ValueError`; DuckDB errors from running the query propagate.
        """
        try:
            statements = duckdb.extract_statements(sql)
        except duckdb.Error as exc:
            raise ValueError(f"cannot parse SQL: {exc}") from exc
        if len(statements) != 1:
            raise ValueError(f"query takes exactly one statement, got {len(statements)}")
        if statements[0].type != duckdb.StatementType.SELECT:
            raise ValueError(f"query only runs SELECT statements, got {statements[0].type.name}")
        with self._lock:
            con = self._connection()
            if self._views_state != self._current_state():
                self.refresh()
            relation = con.sql(sql, params=params)
            assert relation is not None  # a SELECT always yields a relation
            frame: pd.DataFrame = relation.to_arrow_table().to_pandas()
        return frame

    def mirror_audit_log(self, path: Path) -> int:
        """Copy the events of one run's audit log (`runs/<run_id>/audit.jsonl`) that are not
        yet in `audit_events`; return how many rows were inserted.

        The JSONL file is the source of truth and must pass `AuditLog.verify_chain` and hold
        one run. Rows already mirrored for that run must be an exact prefix of the file (same
        `event_id` and `hash`, in order), so a log rewritten after mirroring raises `DataError`
        instead of being copied. Re-mirroring is idempotent; inserts run in one transaction.
        `seq` is the event's 0-based line number. Raises `DataError` for a missing, broken or
        diverging log and `ValueError` on a read-only database.
        """
        if self._read_only:
            raise ValueError(f"cannot mirror an audit log into read-only database {self._path}")
        if not path.is_file():
            raise DataError(f"audit log {path} is missing")
        data = path.read_bytes()
        if not AuditLog.verify_chain(path) or not path.read_bytes().startswith(data):
            raise DataError(f"audit log {path} fails hash chain verification; not mirrored")
        lines = data.decode("utf-8").split("\n")[:-1]  # complete lines only
        events = [AuditEvent.model_validate_json(line) for line in lines]
        if not events:
            return 0
        run_ids = {event.run_id for event in events}
        if len(run_ids) != 1:
            raise DataError(f"audit log {path} holds {len(run_ids)} runs; the mirror needs one")
        run_id = events[0].run_id
        with self._lock:
            con = self._connection()
            con.execute("BEGIN TRANSACTION")
            try:
                stored = con.execute(_STORED_AUDIT, [run_id]).fetchall()
                known = [(e.event_id, e.hash) for e in events[: len(stored)]]
                if len(stored) > len(events) or [tuple(row) for row in stored] != known:
                    raise DataError(
                        f"audit log {path} diverges from the {len(stored)} event(s) already "
                        f"mirrored for run {run_id}"
                    )
                rows = [
                    (
                        e.event_id,
                        e.run_id,
                        seq,
                        e.ts,
                        e.as_of,
                        e.actor,
                        e.event_type.value,
                        canonical_json(e.payload),
                        e.prev_hash,
                        e.hash,
                    )
                    for seq, e in enumerate(events[len(stored) :], start=len(stored))
                ]
                if rows:
                    con.executemany(_INSERT_AUDIT, rows)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        _log.info("audit.mirror", mirrored_run_id=run_id, inserted=len(rows), total=len(events))
        return len(rows)

    def close(self) -> None:
        """Close the connection (idempotent) and forget it as the process-wide instance."""
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None
        with _OPEN_LOCK:
            key = _key(self._lake)
            if _OPEN.get(key) is self:
                del _OPEN[key]

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


_OPEN: dict[Path, Database] = {}
_OPEN_LOCK = threading.Lock()


def _key(lake: Lake) -> Path:
    return (lake.root / DB_FILENAME).resolve()


def get_database(lake: Lake) -> Database:
    """The process-wide read-write `Database` for `lake`'s database file, opened on first use.

    Lakes with the same root share one instance (bound to the first `Lake` object passed); a
    closed instance is replaced by a new one.
    """
    with _OPEN_LOCK:
        key = _key(lake)
        database = _OPEN.get(key)
        if database is None or database.closed:
            database = Database(lake)
            _OPEN[key] = database
        return database
