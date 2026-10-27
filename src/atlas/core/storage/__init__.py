"""Embedded storage: the Parquet lake, its table schemas and the DuckDB query layer.

Layout under the lake root (`cfg.data.lake_path`): `raw/<vendor>/<dataset>/` for vendor
payloads, `curated/<table>/` for validated Parquet tables (plus `curated/_meta.json`),
`cache/`, `runs/` and the DuckDB file `atlas.duckdb`.
"""

from atlas.core.storage.db import Database, get_database
from atlas.core.storage.lake import Filter, Lake, WriteMode, WriteResult
from atlas.core.storage.schema import (
    AVAILABLE_AT,
    DEFAULT_REGISTRY,
    YEAR,
    Column,
    ColumnType,
    Migration,
    SchemaRegistry,
    TableSchema,
    validate_frame,
)

__all__ = [
    "AVAILABLE_AT",
    "DEFAULT_REGISTRY",
    "YEAR",
    "Column",
    "ColumnType",
    "Database",
    "Filter",
    "Lake",
    "Migration",
    "SchemaRegistry",
    "TableSchema",
    "WriteMode",
    "WriteResult",
    "get_database",
    "validate_frame",
]
