"""The Parquet lake: directory layout, validated partitioned writes and filtered reads.

Layout under `root` (`cfg.data.lake_path`, resolved by the caller):

    raw/<vendor>/<dataset>/        vendor payloads as fetched (Phase 1 writes them)
    curated/<table>/[col=value/…]data.parquet
    curated/_meta.json             version, partitioning and fingerprint per table
    cache/  runs/

Each partition directory holds exactly one `data.parquet`, replaced atomically on every
write that touches it. Partition columns are part of the primary key, so a key lives in one
partition and append/upsert only read and rewrite the partitions in the batch. A write that
spans several partitions is not atomic across them; re-running an upsert repairs a crash.
One writer process per lake.
"""

import os
import re
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, get_args
from urllib.parse import quote

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage.meta import META_FILENAME, MetaStore, TableMeta, atomic_write
from atlas.core.storage.schema import (
    DEFAULT_REGISTRY,
    IDENTIFIER,
    YEAR,
    ColumnType,
    SchemaRegistry,
    TableSchema,
    validate_frame,
)

DATA_FILE = "data.parquet"
"""The one Parquet file in every partition directory."""

WriteMode = Literal["append", "overwrite", "upsert"]
Filter = tuple[str, str, Any]
"""A read filter `(column, op, value)`; `op` is one of `= == != < <= > >= in not in`."""

_MODES: tuple[str, ...] = get_args(WriteMode)
_OPS = frozenset({"=", "==", "!=", "<", "<=", ">", ">=", "in", "not in"})
_RAW_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_TABLE_RE = re.compile(IDENTIFIER)

_log = get_logger(__name__)


@dataclass(frozen=True)
class WriteResult:
    """Outcome of `Lake.write_table`: `rows` in the batch (after upsert's in-batch dedupe)
    and the number of partition files written."""

    table: str
    mode: WriteMode
    rows: int
    partitions: int


class Lake:
    """The data lake rooted at `root`; curated tables follow schemas from `registry`.

    `registry` defaults to `DEFAULT_REGISTRY`. `generation` counts writes made through this
    object, so `Database` knows when to rebuild its views. One writer at a time: concurrent
    writes from several threads or processes are not supported.
    """

    def __init__(self, root: Path, registry: SchemaRegistry | None = None) -> None:
        """Open the lake at `root`, creating `raw/ curated/ cache/ runs/` if missing."""
        self._root = Path(root)
        self._registry = DEFAULT_REGISTRY if registry is None else registry
        for sub in (self.raw_dir, self.curated_dir, self.cache_dir, self.runs_dir):
            sub.mkdir(parents=True, exist_ok=True)
        self._meta = MetaStore(self.curated_dir / META_FILENAME)
        self._generation = 0

    @property
    def root(self) -> Path:
        """The lake root directory."""
        return self._root

    @property
    def raw_dir(self) -> Path:
        """`<root>/raw`: vendor payloads."""
        return self._root / "raw"

    @property
    def curated_dir(self) -> Path:
        """`<root>/curated`: validated Parquet tables and `_meta.json`."""
        return self._root / "curated"

    @property
    def cache_dir(self) -> Path:
        """`<root>/cache`: disposable caches."""
        return self._root / "cache"

    @property
    def runs_dir(self) -> Path:
        """`<root>/runs`: per-run outputs (audit logs live in `runs/<run_id>/`)."""
        return self._root / "runs"

    @property
    def registry(self) -> SchemaRegistry:
        """The schemas this lake validates against."""
        return self._registry

    @property
    def generation(self) -> int:
        """Number of successful writes through this object (starts at 0)."""
        return self._generation

    def raw_path(self, vendor: str, dataset: str) -> Path:
        """`raw/<vendor>/<dataset>/`, created if missing. Names are lower-case letters,
        digits, `_` and `-` (`ValueError` otherwise, so no path can escape `raw/`)."""
        for part in (vendor, dataset):
            if not _RAW_NAME_RE.match(part):
                raise ValueError(f"invalid raw vendor/dataset name {part!r}")
        path = self.raw_dir / vendor / dataset
        path.mkdir(parents=True, exist_ok=True)
        return path

    def table_path(self, name: str) -> Path:
        """`curated/<name>/` (not created). `ValueError` for a name that is not an identifier."""
        if not _TABLE_RE.match(name):
            raise ValueError(f"invalid table name {name!r}")
        return self.curated_dir / name

    def table_files(self, name: str) -> tuple[Path, ...]:
        """The table's Parquet files, sorted (empty if it has none)."""
        path = self.table_path(name)
        if not path.is_dir():
            return ()
        return tuple(sorted(path.rglob(DATA_FILE)))

    def has_table(self, name: str) -> bool:
        """`True` once the table has been written (even if later overwritten with no rows)."""
        self.table_path(name)
        return self._meta.get(name) is not None

    def table_schema(self, name: str) -> TableSchema:
        """The registered schema of `name` (`DataError` if none)."""
        return self._registry.get(name)

    # --- writing ------------------------------------------------------------------------------

    def write_table(
        self,
        name: str,
        df: pd.DataFrame,
        partition_cols: Sequence[str] | None = None,
        mode: WriteMode = "append",
        keys: Sequence[str] | None = None,
    ) -> WriteResult:
        """Validate `df` against the schema of `name` and write it.

        `mode`: `append` adds rows (`DataError` if a key already exists or repeats in the
        batch); `upsert` keeps the last row per key in the batch and replaces stored rows with
        the same key (later ingestion wins); `overwrite` replaces the whole table. `keys`
        defaults to the primary key and must equal it as a set. `partition_cols` defaults to
        the stored partitioning (or the schema's for a new table); changing it needs
        `overwrite`. Nothing is written if validation fails.
        """
        if mode not in _MODES:
            raise ValueError(f"unknown write mode {mode!r}; expected one of {_MODES}")
        schema = self.table_schema(name)
        stored = self._check_meta(schema)
        key_cols = _resolve_keys(schema, keys)
        parts = self._resolve_partitioning(schema, stored, partition_cols, mode)
        table = validate_frame(schema, df)
        if mode == "upsert":
            table = _keep_last(table, key_cols)
        elif _key_frame(table, key_cols).duplicated().any():
            raise DataError(f"{name}: duplicate primary keys in the batch")
        if mode == "upsert":
            written = self._upsert(schema, table, parts, key_cols)
        elif mode == "overwrite":
            written = self._overwrite(schema, table, parts)
        else:
            written = self._append(schema, table, parts, key_cols)
        self._meta.put(
            name,
            TableMeta(version=schema.version, partition_by=parts, fingerprint=schema.fingerprint()),
        )
        self._generation += 1
        result = WriteResult(table=name, mode=mode, rows=table.num_rows, partitions=written)
        _log.info("lake.write", table=name, write_mode=mode, rows=result.rows, partitions=written)
        return result

    def _check_meta(self, schema: TableSchema) -> TableMeta | None:
        """The stored record, after checking it matches the registered schema (`DataError`)."""
        stored = self._meta.get(schema.name)
        if stored is None:
            return None
        if stored.version > schema.version:
            raise DataError(
                f"{schema.name}: stored data is at schema version {stored.version}, newer than "
                f"the registered version {schema.version}"
            )
        if stored.version < schema.version:
            raise DataError(
                f"{schema.name}: stored data is at schema version {stored.version}; run "
                f"Lake.migrate({schema.name!r}) to reach version {schema.version}"
            )
        if stored.fingerprint != schema.fingerprint():
            raise DataError(
                f"{schema.name}: the registered columns differ from the stored ones at version "
                f"{stored.version} (fingerprint mismatch); bump the version and add a migration"
            )
        return stored

    def migrate(self, name: str) -> int:
        """Bring table `name` up to its registered schema version; return that version.

        Applies the registered steps `stored version → … → registered version` to the rows,
        validates the result against the current schema and rewrites the table (same
        partitioning). A missing step, a step error or invalid rows raise and change nothing.
        A table that was never written, or is already current, is left as it is.
        """
        schema = self.table_schema(name)
        stored = self._meta.get(name)
        if stored is None or stored.version >= schema.version:
            self._check_meta(schema)  # current: verify the fingerprint; newer: raise
            return schema.version
        steps = []
        for version in range(stored.version, schema.version):
            step = self._registry.migration(name, version)
            if step is None:
                raise DataError(f"{name}: no migration registered from version {version}")
            steps.append(step)
        parts = schema.check_partitioning(stored.partition_by)
        files = self.table_files(name)
        written = 0
        if files:
            frame: pd.DataFrame = (
                ds.dataset([str(f) for f in files], format="parquet").to_table().to_pandas()
            )
            for step in steps:
                frame = step(frame)
            table = validate_frame(schema, frame)
            if _key_frame(table, schema.primary_key).duplicated().any():
                raise DataError(f"{name}: migrated rows have duplicate primary keys")
            written = self._overwrite(schema, table, parts)
        self._meta.put(
            name,
            TableMeta(version=schema.version, partition_by=parts, fingerprint=schema.fingerprint()),
        )
        self._generation += 1
        _log.info(
            "lake.migrate",
            table=name,
            from_version=stored.version,
            to_version=schema.version,
            partitions=written,
        )
        return schema.version

    def _resolve_partitioning(
        self,
        schema: TableSchema,
        stored: TableMeta | None,
        partition_cols: Sequence[str] | None,
        mode: WriteMode,
    ) -> tuple[str, ...]:
        requested = None if partition_cols is None else schema.check_partitioning(partition_cols)
        if stored is None:
            return schema.partition_by if requested is None else requested
        if requested is not None and requested != stored.partition_by and mode != "overwrite":
            raise ValueError(
                f"{schema.name} is partitioned by {list(stored.partition_by)}; changing the "
                "partitioning needs mode='overwrite'"
            )
        return stored.partition_by if requested is None else requested

    def _append(
        self,
        schema: TableSchema,
        table: pa.Table,
        parts: tuple[str, ...],
        key_cols: tuple[str, ...],
    ) -> int:
        merged: list[tuple[Path, pa.Table]] = []
        for rel, chunk in _split(schema, table, parts):
            path = self.table_path(schema.name) / rel / DATA_FILE
            if path.exists():
                existing = _read_file(schema, path)
                keys = pd.concat(
                    [_key_frame(existing, key_cols), _key_frame(chunk, key_cols)],
                    ignore_index=True,
                )
                if keys.duplicated().any():
                    raise DataError(
                        f"{schema.name}: the batch has keys that already exist in "
                        f"{rel or 'the table'}; use mode='upsert' to replace them"
                    )
                chunk = pa.concat_tables([existing, chunk])
            merged.append((path, chunk))
        for path, chunk in merged:  # every check passed: now write
            _write_file(path, chunk)
        return len(merged)

    def _upsert(
        self,
        schema: TableSchema,
        table: pa.Table,
        parts: tuple[str, ...],
        key_cols: tuple[str, ...],
    ) -> int:
        chunks = _split(schema, table, parts)
        for rel, chunk in chunks:
            path = self.table_path(schema.name) / rel / DATA_FILE
            if path.exists():  # stored rows first, so the batch's row wins each key
                chunk = _keep_last(pa.concat_tables([_read_file(schema, path), chunk]), key_cols)
            _write_file(path, chunk)
        return len(chunks)

    def _overwrite(self, schema: TableSchema, table: pa.Table, parts: tuple[str, ...]) -> int:
        target = self.table_path(schema.name)
        staging = Path(
            tempfile.mkdtemp(dir=self.curated_dir, prefix=f".{schema.name}.", suffix=".new")
        )
        retired = staging.with_suffix(".old")
        try:
            written = 0
            for rel, chunk in _split(schema, table, parts):
                _write_file(staging / rel / DATA_FILE, chunk)
                written += 1
            if target.exists():
                os.replace(target, retired)
            os.replace(staging, target)
        except BaseException:
            if retired.exists() and not target.exists():
                os.replace(retired, target)  # put the old table back
            shutil.rmtree(staging, ignore_errors=True)
            raise
        shutil.rmtree(retired, ignore_errors=True)
        return written

    # --- reading ------------------------------------------------------------------------------

    def read_table(
        self,
        name: str,
        filters: Sequence[Filter] | None = None,
        columns: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """Rows of `name` matching every filter, sorted by primary key, with a fresh index.

        `columns` selects and orders the output columns (default: all, in schema order). Types
        follow the schema table in `atlas.core.storage.schema` (timestamps are
        `datetime64[us, UTC]`, dates are `datetime.date`). A registered table that was never
        written reads as an empty, correctly typed frame. Unknown columns or operators raise
        `ValueError`; a naive `datetime` compared with a timestamp column raises `ValueError`.
        """
        schema = self.table_schema(name)
        self._check_meta(schema)
        wanted = list(schema.column_names if columns is None else columns)
        unknown = [c for c in wanted if c not in schema.column_names]
        if unknown:
            raise ValueError(f"{name}: unknown columns {unknown}")
        expression = _filter_expression(schema, filters or ())
        files = self.table_files(name)
        if files:
            dataset = ds.dataset(
                [str(f) for f in files], format="parquet", schema=schema.arrow_schema()
            )
            table = dataset.to_table(filter=expression)
        else:
            table = schema.arrow_schema().empty_table()
        table = table.sort_by([(k, "ascending") for k in schema.primary_key]).select(wanted)
        return to_pandas(schema, table)


def to_pandas(schema: TableSchema, table: pa.Table) -> pd.DataFrame:
    """Arrow → pandas with the lake's read types (nullable ints → `Int64`, nullable bools →
    `boolean`; numpy-backed timestamps, so `pit_filter` accepts them)."""
    frame: pd.DataFrame = table.to_pandas()
    for name in frame.columns:
        col = schema.column(str(name))
        if col.nullable and col.dtype is ColumnType.INT64:
            frame[name] = frame[name].astype("Int64")
        elif col.nullable and col.dtype is ColumnType.BOOL:
            frame[name] = frame[name].astype("boolean")
    return frame


def _resolve_keys(schema: TableSchema, keys: Sequence[str] | None) -> tuple[str, ...]:
    if keys is None:
        return schema.primary_key
    if sorted(keys) != sorted(schema.primary_key):
        raise ValueError(
            f"{schema.name}: keys {list(keys)} must equal the primary key "
            f"{list(schema.primary_key)}"
        )
    return schema.primary_key


def _key_frame(table: pa.Table, key_cols: Sequence[str]) -> pd.DataFrame:
    """The key columns as pandas, dates as `datetime64` (fast to hash, unlike objects)."""
    frame: pd.DataFrame = table.select(list(key_cols)).to_pandas(date_as_object=False)
    return frame


def _keep_last(table: pa.Table, key_cols: Sequence[str]) -> pa.Table:
    """`table` without earlier rows whose key repeats later (the last occurrence wins)."""
    keep = ~_key_frame(table, key_cols).duplicated(keep="last").to_numpy()
    return table if keep.all() else table.filter(pa.array(keep))


def _partition_values(schema: TableSchema, table: pa.Table, token: str) -> pd.Series:
    if token == YEAR and YEAR not in schema.column_names:
        assert schema.year_column is not None  # guaranteed by check_partitioning
        series: pd.Series = pc.year(table.column(schema.year_column)).to_pandas()
        return series
    values: pd.Series = table.column(token).to_pandas()
    return values


def _partition_dir(parts: tuple[str, ...], values: tuple[object, ...]) -> str:
    segments = []
    for token, value in zip(parts, values, strict=True):
        text = str(value).lower() if isinstance(value, bool | np.bool_) else str(value)
        segments.append(f"{token}={quote(text, safe='')}")
    return "/".join(segments)


def _split(
    schema: TableSchema, table: pa.Table, parts: tuple[str, ...]
) -> list[tuple[str, pa.Table]]:
    """The batch cut into `(relative partition dir, rows)`; `""` for an unpartitioned table."""
    if not parts:
        return [("", table)] if table.num_rows else []
    keys = pd.DataFrame({token: _partition_values(schema, table, token) for token in parts})
    groups = keys.groupby(list(parts), sort=True).indices
    out = []
    for value, positions in groups.items():
        values = value if isinstance(value, tuple) else (value,)
        out.append((_partition_dir(parts, values), table.take(positions)))
    return out


def _read_file(schema: TableSchema, path: Path) -> pa.Table:
    return pq.read_table(path, schema=schema.arrow_schema())


def _write_file(path: Path, table: pa.Table) -> None:
    atomic_write(path, lambda fh: pq.write_table(table, fh))


def _filter_expression(schema: TableSchema, filters: Sequence[Filter]) -> pc.Expression | None:
    expression = None
    for column, op, value in filters:
        if column not in schema.column_names:
            raise ValueError(f"{schema.name}: cannot filter on unknown column {column!r}")
        if op not in _OPS:
            raise ValueError(f"unknown filter operator {op!r}; expected one of {sorted(_OPS)}")
        arrow_type = schema.arrow_schema().field(column).type
        field = pc.field(column)
        try:
            if op in ("in", "not in"):
                values = pa.array(
                    [_filter_value(schema, column, v) for v in value], type=arrow_type
                )
                term = field.isin(values)
                if op == "not in":
                    term = ~term
            else:
                scalar = pa.scalar(_filter_value(schema, column, value), type=arrow_type)
                term = {
                    "=": field == scalar,
                    "==": field == scalar,
                    "!=": field != scalar,
                    "<": field < scalar,
                    "<=": field <= scalar,
                    ">": field > scalar,
                    ">=": field >= scalar,
                }[op]
        except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError) as exc:
            raise ValueError(f"{schema.name}: bad filter value for {column!r}: {exc}") from exc
        expression = term if expression is None else expression & term
    return expression


def _filter_value(schema: TableSchema, column: str, value: object) -> object:
    is_timestamp = schema.column(column).dtype is ColumnType.TIMESTAMP
    if is_timestamp and isinstance(value, datetime) and value.utcoffset() is None:
        raise ValueError(f"{schema.name}: filter on {column!r} needs a tz-aware datetime")
    return value
