"""Declarations of curated lake tables and validation of the frames written to them.

A `TableSchema` names a table's columns, their types, its primary key and its default
partitioning. Every table that is not static reference data must carry `available_at`, a
non-nullable UTC timestamp saying when each row became known (point-in-time, spec §3), so
`atlas.core.pit.pit_filter` always has a column to filter on.

Column types and how they are stored (Arrow) and read back (pandas):

| `ColumnType` | Arrow | pandas on read |
|---|---|---|
| `string` | `string` | `str` |
| `int64` | `int64` | `int64` (`Int64` if nullable) |
| `float64` | `double` | `float64` (nulls as NaN) |
| `bool` | `bool` | `bool` (`boolean` if nullable) |
| `date` | `date32` | `object` holding `datetime.date` (trading days) |
| `timestamp` | `timestamp[us, tz=UTC]` | `datetime64[us, UTC]` |
| `string_list` | `list<string>` | `object` holding `tuple[str, ...]` (Stage 11 `news.symbols`) |

A `string_list` cell is a list, tuple or numpy array of strings (no null items), or a missing
value (`None`, `NaN`, `pd.NA`: null); it may not be part of the primary key or the
partitioning, and the lake does not filter on it.
"""

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Sequence
from enum import StrEnum
from typing import Annotated, Self

import numpy as np
import pandas as pd
import pyarrow as pa
from pydantic import Field, model_validator

from atlas.core.errors import DataError
from atlas.core.types.base import AtlasModel

IDENTIFIER = r"^[a-z][a-z0-9_]*$"
"""Pattern for table and column names (they become DuckDB view and column names)."""

AVAILABLE_AT = "available_at"
"""The point-in-time column every non-static table must declare."""

YEAR = "year"
"""Virtual partition token: the calendar year of `TableSchema.year_column`."""

RESERVED_TABLES = frozenset({"audit_events"})
"""Names owned by native DuckDB tables, so no lake table may use them."""

_IDENTIFIER_RE = re.compile(IDENTIFIER)

Migration = Callable[[pd.DataFrame], pd.DataFrame]
"""One migration step: takes a table's rows at version N, returns them at version N + 1."""


class ColumnType(StrEnum):
    """Logical column types of the lake (see the module table for storage)."""

    STRING = "string"
    INT64 = "int64"
    FLOAT64 = "float64"
    BOOL = "bool"
    DATE = "date"
    TIMESTAMP = "timestamp"
    STRING_LIST = "string_list"


_ARROW_TYPES: dict[ColumnType, pa.DataType] = {
    ColumnType.STRING: pa.string(),
    ColumnType.INT64: pa.int64(),
    ColumnType.FLOAT64: pa.float64(),
    ColumnType.BOOL: pa.bool_(),
    ColumnType.DATE: pa.date32(),
    ColumnType.TIMESTAMP: pa.timestamp("us", tz="UTC"),
    ColumnType.STRING_LIST: pa.list_(pa.string()),
}
_PARTITION_TYPES = frozenset(
    {ColumnType.STRING, ColumnType.INT64, ColumnType.DATE, ColumnType.BOOL}
)

_Identifier = Annotated[str, Field(pattern=IDENTIFIER)]


class Column(AtlasModel):
    """One column: its name, logical type and whether nulls (NaN for floats) are allowed."""

    name: _Identifier
    dtype: ColumnType
    nullable: bool = False


class TableSchema(AtlasModel):
    """Declaration of one curated table.

    `primary_key` names the columns that identify a row (upserts replace rows by it).
    `partition_by` is the default partitioning: primary-key columns of type string, int64,
    date or bool, or the virtual `year`, which is the year of `year_column` (a date or
    timestamp primary-key column). `version` is the schema version recorded in
    `curated/_meta.json`; bump it, and register a migration, whenever the columns change.
    `static` tables (reference data with no point-in-time meaning) may omit `available_at`.
    """

    name: _Identifier
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]
    partition_by: tuple[str, ...] = ()
    year_column: str | None = None
    version: Annotated[int, Field(ge=1)] = 1
    static: bool = False
    description: str = ""

    @model_validator(mode="after")
    def _check_declaration(self) -> Self:
        if self.name in RESERVED_TABLES:
            raise ValueError(f"table name {self.name!r} is reserved")
        names = [c.name for c in self.columns]
        if not names:
            raise ValueError(f"{self.name}: a table needs at least one column")
        if len(set(names)) != len(names):
            raise ValueError(f"{self.name}: duplicate column names in {names}")
        if not self.primary_key:
            raise ValueError(f"{self.name}: the primary key must not be empty")
        if len(set(self.primary_key)) != len(self.primary_key):
            raise ValueError(f"{self.name}: duplicate primary key columns")
        by_name = {c.name: c for c in self.columns}
        for key in self.primary_key:
            if key not in by_name:
                raise ValueError(f"{self.name}: primary key column {key!r} is not declared")
            if by_name[key].nullable:
                raise ValueError(f"{self.name}: primary key column {key!r} must not be nullable")
            if by_name[key].dtype is ColumnType.STRING_LIST:
                raise ValueError(f"{self.name}: primary key column {key!r} must not be a list")
        if not self.static:
            avail = by_name.get(AVAILABLE_AT)
            if avail is None or avail.dtype is not ColumnType.TIMESTAMP or avail.nullable:
                raise ValueError(
                    f"{self.name}: a non-static table must declare {AVAILABLE_AT!r} as a "
                    "non-nullable timestamp column"
                )
        if self.year_column is not None:
            if YEAR in by_name:
                raise ValueError(f"{self.name}: year_column conflicts with a column named 'year'")
            year_source = by_name.get(self.year_column)
            if (
                year_source is None
                or self.year_column not in self.primary_key
                or year_source.dtype not in (ColumnType.DATE, ColumnType.TIMESTAMP)
            ):
                raise ValueError(
                    f"{self.name}: year_column must be a date or timestamp primary key column"
                )
        self.check_partitioning(self.partition_by)
        return self

    @property
    def column_names(self) -> tuple[str, ...]:
        """Column names in declaration order."""
        return tuple(c.name for c in self.columns)

    def column(self, name: str) -> Column:
        """The column called `name` (`KeyError` if there is none)."""
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(name)

    def check_partitioning(self, cols: Sequence[str]) -> tuple[str, ...]:
        """Validate a partitioning (`ValueError` if invalid) and return it as a tuple.

        Each entry is a primary-key column of a partitionable type, or `year` when
        `year_column` is set, so every key value lives in exactly one partition.
        """
        result = tuple(cols)
        if len(set(result)) != len(result):
            raise ValueError(f"{self.name}: duplicate partition columns in {list(result)}")
        names = set(self.column_names)
        for token in result:
            if token == YEAR and YEAR not in names:
                if self.year_column is None:
                    raise ValueError(f"{self.name}: partition 'year' needs year_column")
                continue
            if token not in self.primary_key:
                raise ValueError(f"{self.name}: partition column {token!r} is not in the key")
            if self.column(token).dtype not in _PARTITION_TYPES:
                raise ValueError(
                    f"{self.name}: cannot partition on {token!r} ({self.column(token).dtype})"
                )
        return result

    def arrow_schema(self) -> pa.Schema:
        """The Arrow schema files of this table are written with (UTC microsecond timestamps)."""
        return pa.schema(
            [pa.field(c.name, _ARROW_TYPES[c.dtype], nullable=c.nullable) for c in self.columns]
        )

    def fingerprint(self) -> str:
        """SHA-256 hex of the columns (name, type, nullability) and the primary key.

        Stored in `curated/_meta.json`; a different fingerprint at the same `version` means
        the declaration changed without a version bump.
        """
        body = {
            "columns": [[c.name, c.dtype.value, c.nullable] for c in self.columns],
            "primary_key": list(self.primary_key),
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


class SchemaRegistry:
    """Table schemas by name, plus their migration steps.

    Data stages register their tables in `DEFAULT_REGISTRY` at import time; tests use a
    `copy()` so they never change the shared one.
    """

    def __init__(self, schemas: Iterable[TableSchema] = ()) -> None:
        """A registry holding `schemas` (none by default)."""
        self._schemas: dict[str, TableSchema] = {}
        self._migrations: dict[tuple[str, int], Migration] = {}
        for schema in schemas:
            self.register(schema)

    def register(self, schema: TableSchema) -> TableSchema:
        """Add `schema` and return the registered one. Registering an equal schema again is a
        no-op; a different schema under a taken name raises `ValueError`."""
        existing = self._schemas.get(schema.name)
        if existing is None:
            self._schemas[schema.name] = schema
            return schema
        if existing != schema:
            raise ValueError(f"a different schema is already registered as {schema.name!r}")
        return existing

    def get(self, name: str) -> TableSchema:
        """The schema registered as `name` (`DataError` if none)."""
        try:
            return self._schemas[name]
        except KeyError:
            raise DataError(f"no table schema registered as {name!r}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._schemas

    def names(self) -> tuple[str, ...]:
        """Registered table names, sorted."""
        return tuple(sorted(self._schemas))

    def copy(self) -> "SchemaRegistry":
        """An independent registry with the same schemas and migrations."""
        clone = SchemaRegistry(self._schemas.values())
        clone._migrations = dict(self._migrations)
        return clone

    def add_migration(self, name: str, from_version: int, fn: Migration) -> None:
        """Register the step that turns `name`'s rows at `from_version` into
        `from_version + 1`; `from_version` must be below the registered schema's version."""
        schema = self.get(name)
        if not 1 <= from_version < schema.version:
            raise ValueError(
                f"{name}: migration from version {from_version} is outside 1..{schema.version - 1}"
            )
        if (name, from_version) in self._migrations:
            raise ValueError(f"{name}: a migration from version {from_version} already exists")
        self._migrations[(name, from_version)] = fn

    def migration(self, name: str, from_version: int) -> Migration | None:
        """The step from `from_version`, or `None` if none is registered."""
        return self._migrations.get((name, from_version))


DEFAULT_REGISTRY = SchemaRegistry()
"""The process-wide registry; data stages register their curated tables here."""


def validate_frame(schema: TableSchema, df: pd.DataFrame) -> pa.Table:
    """Check `df` against `schema` and return it as an Arrow table in schema column order.

    Rules (any failure raises `DataError` naming the table and column): the columns are exactly
    the declared ones; each column already has a compatible dtype (nothing is parsed from
    strings); timestamps are tz-aware (any zone; stored as UTC microseconds, finer values are
    rejected); dates are `datetime.date` objects or naive midnight `datetime64`; floats are
    finite; non-nullable columns hold no nulls or NaN.
    """
    if not isinstance(df, pd.DataFrame):
        raise DataError(f"{schema.name}: expected a pandas DataFrame, got {type(df).__name__}")
    present = [str(c) for c in df.columns]
    if len(set(present)) != len(present):
        raise DataError(f"{schema.name}: duplicate columns in frame: {present}")
    missing = [c for c in schema.column_names if c not in present]
    extra = [c for c in present if c not in schema.column_names]
    if missing or extra:
        raise DataError(f"{schema.name}: missing columns {missing}, unexpected columns {extra}")
    arrays = [_to_arrow(schema.name, col, df[col.name]) for col in schema.columns]
    return pa.Table.from_arrays(arrays, schema=schema.arrow_schema())


def _to_arrow(table: str, col: Column, series: pd.Series) -> pa.Array:
    where = f"{table}.{col.name}"
    dtype = series.dtype
    is_bool = pd.api.types.is_bool_dtype(dtype)
    try:
        if col.dtype is ColumnType.STRING:
            inferred = pd.api.types.infer_dtype(series, skipna=True)
            if inferred not in ("string", "empty"):
                raise DataError(f"{where}: expected strings, got {inferred} ({dtype})")
            arr = pa.array(series, type=pa.string(), from_pandas=True)
        elif col.dtype is ColumnType.INT64:
            if is_bool or not pd.api.types.is_integer_dtype(dtype):
                raise DataError(f"{where}: expected an integer dtype, got {dtype}")
            arr = pa.array(series, type=pa.int64(), from_pandas=True)
        elif col.dtype is ColumnType.FLOAT64:
            if is_bool or not pd.api.types.is_numeric_dtype(dtype):
                raise DataError(f"{where}: expected a numeric dtype, got {dtype}")
            values = series.to_numpy(dtype="float64", na_value=np.nan)
            if np.isinf(values).any():
                raise DataError(f"{where}: infinite values are not allowed")
            arr = pa.array(values, type=pa.float64(), from_pandas=True)
        elif col.dtype is ColumnType.BOOL:
            if not is_bool:
                raise DataError(f"{where}: expected a bool dtype, got {dtype}")
            arr = pa.array(series, type=pa.bool_(), from_pandas=True)
        elif col.dtype is ColumnType.DATE:
            arr = _date_array(where, series)
        elif col.dtype is ColumnType.STRING_LIST:
            arr = _string_list_array(where, series)
        else:  # TIMESTAMP
            if not isinstance(dtype, pd.DatetimeTZDtype):
                raise DataError(f"{where}: expected a tz-aware datetime64 dtype, got {dtype}")
            utc = pa.array(series.dt.tz_convert("UTC"), from_pandas=True)
            arr = utc.cast(_ARROW_TYPES[ColumnType.TIMESTAMP], safe=True)
    except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError) as exc:
        raise DataError(f"{where}: cannot store values as {col.dtype}: {exc}") from exc
    if not col.nullable and arr.null_count:
        raise DataError(f"{where}: {arr.null_count} null value(s) in a non-nullable column")
    return arr


def _date_array(where: str, series: pd.Series) -> pa.Array:
    dtype = series.dtype
    if isinstance(dtype, pd.DatetimeTZDtype):
        raise DataError(f"{where}: dates must be naive; got tz-aware {dtype}")
    if pd.api.types.is_datetime64_dtype(dtype):
        present = series.dropna()
        if not (present == present.dt.normalize()).all():
            raise DataError(f"{where}: datetime64 dates must all be at midnight")
        return pa.array(series, from_pandas=True).cast(pa.date32())
    inferred = pd.api.types.infer_dtype(series, skipna=True)
    if inferred not in ("date", "empty"):
        raise DataError(f"{where}: expected datetime.date values, got {inferred} ({dtype})")
    return pa.array(series, type=pa.date32(), from_pandas=True)


def _string_list_array(where: str, series: pd.Series) -> pa.Array:
    cells: list[list[str] | None] = []
    for value in series:
        if value is None or value is pd.NA or (isinstance(value, float) and np.isnan(value)):
            cells.append(None)
            continue
        if not isinstance(value, list | tuple | np.ndarray):
            raise DataError(f"{where}: expected a list of strings, got {type(value).__name__}")
        items = list(value)
        if not all(isinstance(item, str) for item in items):
            raise DataError(f"{where}: list items must be strings, got {items!r}")
        cells.append(items)
    return pa.array(cells, type=_ARROW_TYPES[ColumnType.STRING_LIST])
