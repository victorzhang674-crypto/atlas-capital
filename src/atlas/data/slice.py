"""`slice_lake`: copy the rows one point-in-time run can read for a few symbols into a new,
small lake (Stage 29; `atlas data slice`).

A recorded agent run (the LLM cache export) replays only on the rows its tools read, and CI
has no lake. A slice keeps, from each table it copies, the rows of `symbols` (a `symbol`
column, or a `symbols` list column that names one of them) that are known at the as-of
(`available_at` strictly before the cutoff; static tables have no `available_at` and keep
every row of the symbols), dated on or after `since` where the table has a date key (its
`year_column`, else the first date column of its primary key; never in a static table: a
membership interval that started earlier may still hold). Tables without a symbol
column are copied only when named, then whole (subject to `since` and the as-of).

The default tables are every written source table with a symbol column except the derived
stores in `DERIVED_TABLES` (they are rebuilt from the source tables, not read by `DataView`).
The source lake is only read. The slice has the source's schemas and a `slice.json`
manifest (as-of, cutoff, symbols, since, rows per table); it never records the source path.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

import pandas as pd

from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf, pit_filter
from atlas.core.storage import AVAILABLE_AT, ColumnType, Lake, TableSchema
from atlas.core.types.instruments import normalize_symbol

MANIFEST = "slice.json"
"""The manifest file at the root of a slice."""

DERIVED_TABLES = frozenset(
    {
        "data_quality_issues",
        "factor_exposures",
        "factor_returns",
        "regime_history",
        "risk_model_snapshots",
    }
)
"""Stores computed from the source tables; not copied unless named."""

_log = get_logger(__name__)


@dataclass(frozen=True)
class SliceReport:
    """What `slice_lake` wrote: the slice root `out`, its `as_of`, `symbols`, `since`, and
    the rows written per table (`rows`, in copy order; 0 = nothing to copy)."""

    out: Path
    as_of: AsOf
    symbols: tuple[str, ...]
    since: date | None
    rows: dict[str, int]

    @property
    def total(self) -> int:
        """Rows written over all tables."""
        return sum(self.rows.values())


def _has_symbols(schema: TableSchema) -> bool:
    names = schema.column_names
    return "symbol" in names or (
        "symbols" in names and schema.column("symbols").dtype is ColumnType.STRING_LIST
    )


def slice_tables(source: Lake, tables: Sequence[str] | None = None) -> tuple[str, ...]:
    """The tables a slice copies: `tables` (each registered and written in `source`;
    `ValueError` otherwise), or by default every written table with a symbol column except
    `DERIVED_TABLES`, sorted."""
    registry = source.registry
    if tables is not None:
        names = tuple(dict.fromkeys(t.strip() for t in tables if t.strip()))
        if not names:
            raise ValueError("no tables given")
        unknown = [t for t in names if t not in registry]
        if unknown:
            raise ValueError(f"unknown table(s) {unknown}; registered: {list(registry.names())}")
        empty = [t for t in names if not source.has_table(t)]
        if empty:
            raise ValueError(f"table(s) {empty} have never been written in the source lake")
        return names
    return tuple(
        name
        for name in registry.names()
        if name not in DERIVED_TABLES
        and source.has_table(name)
        and _has_symbols(registry.get(name))
    )


def date_column(schema: TableSchema) -> str | None:
    """The column `since` trims on: the `year_column`, else the first primary-key column of
    type date; `None` for a static table (reference data and membership intervals, which an
    earlier start does not make stale) and when there is neither."""
    if schema.static:
        return None
    if schema.year_column is not None:
        return schema.year_column
    for name in schema.primary_key:
        if schema.column(name).dtype is ColumnType.DATE:
            return name
    return None


def _since_value(schema: TableSchema, column: str, since: date) -> date | datetime:
    if schema.column(column).dtype is ColumnType.DATE:
        return since
    return datetime.combine(since, time(0), tzinfo=UTC)


def _rows(
    source: Lake, name: str, symbols: tuple[str, ...], as_of: AsOf, since: date | None
) -> pd.DataFrame:
    schema = source.table_schema(name)
    filters: list[tuple[str, str, object]] = []
    if "symbol" in schema.column_names:
        filters.append(("symbol", "in", list(symbols)))
    column = date_column(schema)
    if since is not None and column is not None:
        filters.append((column, ">=", _since_value(schema, column, since)))
    frame = source.read_table(name, filters=filters)
    if "symbol" not in schema.column_names and "symbols" in schema.column_names:
        wanted = set(symbols)
        keep = frame["symbols"].map(lambda xs: xs is not None and bool(wanted & set(xs)))
        frame = frame[keep.astype(bool)]
    if AVAILABLE_AT in frame.columns:
        frame = pit_filter(frame, as_of)
    return frame.reset_index(drop=True)


def slice_lake(
    source: Lake,
    out: Path,
    symbols: Sequence[str],
    as_of: AsOf,
    *,
    since: date | None = None,
    tables: Sequence[str] | None = None,
) -> SliceReport:
    """Write the slice of `source` for `symbols` at `as_of` into a new lake at `out` (see
    the module docstring). Raises `DataError` when `out` exists and is not empty, and
    `ValueError` for no symbols or a bad table list."""
    if isinstance(symbols, str):
        raise ValueError("symbols must be a sequence of tickers, not one string")
    wanted = tuple(dict.fromkeys(normalize_symbol(s) for s in symbols))
    if not wanted:
        raise ValueError("no symbols given")
    names = slice_tables(source, tables)
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise DataError(f"{out} is not empty: a slice goes into a new folder")
    target = Lake(out, registry=source.registry)
    rows: dict[str, int] = {}
    for name in names:
        frame = _rows(source, name, wanted, as_of, since)
        if not frame.empty:
            target.write_table(name, frame, mode="overwrite")
        rows[name] = len(frame)
        _log.info("data.slice.table", table=name, rows=len(frame))
    manifest = {
        "as_of": as_of.day.isoformat(),
        "cutoff": as_of.cutoff.astimezone(UTC).isoformat(),
        "symbols": list(wanted),
        "since": since.isoformat() if since is not None else None,
        "tables": rows,
    }
    (out / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return SliceReport(out=out, as_of=as_of, symbols=wanted, since=since, rows=rows)


__all__ = ["DERIVED_TABLES", "MANIFEST", "SliceReport", "date_column", "slice_lake", "slice_tables"]
