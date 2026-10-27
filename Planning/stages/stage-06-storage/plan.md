# Stage 06 — Storage layer (DuckDB + Parquet lake) · Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking; tick only after the step's tests have run and passed in this chat.

- **Brief:** [brief.md](brief.md) (`Planning/stages/stage-06-storage/brief.md`)
- **Status:** DONE (2026-10-27; see [verification.md](verification.md))
- **Gate check:** Stage 05 is DONE (its [verification.md](../stage-05-clock-pit/verification.md) says ALL CRITERIA VERIFIED; evidence block also in `PROGRESS.md`); `uv run pytest -q` → 512 passed, exit 0 (2026-10-27) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution method:** executing-plans (native, inline TDD). The owner asked in chat (2026-10-27) to "firstly generate the plans for Stage 6 then carefully implement the plans and complete the plans" and then audit the stage against the plan, so the plan is executed straight after it is written; the open design choices below take the recommended option and are logged in the Decisions log. In place of a subagent review, a self-review of the whole diff runs before DONE and the owner-requested audit (plan vs work) runs after DONE.

## Context

Stage 05 is `DONE` (5/66). Stage 06 builds the embedded store every later data stage writes to: raw vendor payloads → curated Parquet tables → DuckDB views, with a required `available_at` column so point-in-time filtering (Stage 05 `pit_filter`, Stage 13 `DataView`) always has something to filter on. Later consumers checked: Stage 07 (`prices_daily` keyed `symbol,date`; `corporate_actions`; idempotent re-ingest), 09 (`fundamentals_facts`, restatements), 10 (`macro_observations`), 11 (`news`, a `symbols[]` list column), 12 (`events`), 14 (backfill + health summary), 16 (`factor_exposures` cache), 50 (DuckDB `orders`/`fills`), 62 (DuckDB `health_checks`), 64 (dashboard reads DuckDB read-only).

**Goal:** `Lake` (layout, validated partitioned Parquet writes with append/overwrite/upsert, filtered reads), a schema registry that makes `available_at` mandatory, a `Database` that exposes the curated tables as DuckDB views and runs parameterised read-only queries, per-table schema versions with simple migrations in `curated/_meta.json`, and an append-only DuckDB mirror of the Stage 03 audit log.

**Architecture:** `schema.py` declares tables (frozen Pydantic `Column`/`TableSchema`), holds the `SchemaRegistry` and turns a DataFrame into a validated Arrow table. `meta.py` reads and atomically rewrites `curated/_meta.json`. `lake.py` owns the directory layout and Parquet I/O (one `data.parquet` per partition, replaced atomically). `db.py` owns the DuckDB connection: session-only views over the lake, read-only `query`, and the `audit_events` table. Dependency direction: `schema` ← `meta` ← `lake` ← `db` (→ `atlas.core.audit`).

**Tech stack:** `pyarrow` (Parquet read/write, Arrow schema), `duckdb` (views, SQL), `pandas` 3 (the frame callers pass and get back).

**Spec:** brief above; design spec §11 (DuckDB + Parquet lake), §12 (`core/storage`, `data/` lake with `raw/ curated/ cache/ llm_cache/ runs/`); stage protocol §4 (units, errors, config, logging, vectorised code), §5 (`tmp_lake` shared fixture, look-ahead tests); Stage 03 handoff (mirror reads the JSONL; it is the source of truth); Stage 05 handoff (`available_at` must load as numpy-backed `DatetimeTZDtype`).

## Global constraints

- Timestamps are stored as Arrow `timestamp[us, tz=UTC]` and read back as `datetime64[us, UTC]` (numpy-backed, so `pit_filter` accepts them); trading days are Arrow `date32` and read back as `datetime.date` objects.
- Every non-static table declares `available_at` as a non-nullable `timestamp` column; a write with a missing or null `available_at` fails with `DataError`.
- No SQL is built by string formatting: values go in as `?`/`$name` parameters, view creation uses the DuckDB relation API (`con.register(name, con.read_parquet(...))`), and table names must match `^[a-z][a-z0-9_]*$`.
- Errors: `DataError` for bad data and lake state (unknown table, validation failure, version mismatch, corrupt meta, diverging audit mirror); `ValueError` for bad arguments (invalid schema declaration, unknown write mode, bad filter operator, keys ≠ primary key). Never swallowed.
- Logging through `atlas.core.logging.get_logger`; one `info` line per write and per mirror.
- No wall-clock reads (ruff TID251); timing in the performance test uses `time.perf_counter` (monotonic, not banned).
- mypy strict on `atlas.core.*` (already configured); `pyarrow` has no `py.typed`, so it gets an `ignore_missing_imports` override.
- Test basenames unique (no `__init__.py`): `tests/unit/core/storage/test_storage_*.py`.
- **One commit for the stage:** `stage-06: Storage layer (DuckDB + Parquet lake)` (+ attribution trailer), after DONE. No per-task commits.

## Collisions with the overall plan

None found with the roadmap or the design spec. Two points checked and resolved without a collision:
- Brief Task 4 "no string-formatted SQL" vs DuckDB DDL, which cannot take parameters (`CREATE VIEW … read_parquet(?)` → "This type of statement can't be prepared", probed 2026-10-27): views are created with the relation API instead, so no SQL text is ever formatted.
- duckdb 2.0's `relation.create_view` stores the view in the database file (probed). Views over lake paths must not persist (paths move; the dashboard opens the file read-only), so views are session-only `register`ed relations (D7).

## Design decisions (also logged in PROGRESS.md › Decisions log)

| # | Decision | Why |
|---|---|---|
| D1 | Column types are a closed `ColumnType` enum: `string`, `int64`, `float64`, `bool`, `date`, `timestamp`. `decimal` and list columns are not built (Backlog: Stage 11 needs `symbols[]`, Stages 50/52 need `Decimal`). | YAGNI; the registry is extensible when a stage needs a new type. |
| D2 | `Column` and `TableSchema` are frozen `AtlasModel`s (they cross packages: data stages declare tables). `TableSchema` validates itself: unique names; non-empty primary key of declared non-nullable columns; non-static tables need `available_at: timestamp, nullable=False`; partition columns are in the primary key (or `year`, see D4); name not reserved (`audit_events`). | Stage protocol §4 (Pydantic for cross-package contracts); a bad declaration fails at import, not at first write. |
| D3 | Write validation is strict: the frame's columns must equal the schema's (order free); each column must already have a compatible dtype (no silent parsing of strings into numbers or dates); tz-aware timestamps only (any zone, converted to UTC; sub-microsecond values rejected); `date` columns take `datetime.date` objects or naive midnight `datetime64`; floats reject ±inf; non-nullable columns reject nulls/NaN. | Fail loudly on vendor drift; PIT needs an exact UTC `available_at`. |
| D4 | Partitioning: `partition_by` (schema default, overridable per write) lists primary-key columns of type string/int64/date/bool, plus the virtual `year`, which is derived from `schema.year_column` (a date or timestamp PK column). Each partition is a directory `col=value/` (value percent-encoded, so `/` and `..` stay inside the table) holding exactly one `data.parquet`. Unpartitioned tables hold `curated/<table>/data.parquet`. Partition columns stay inside the files. | Partitions determined by the key mean a key lives in exactly one partition, so append/upsert touch only the affected partitions. |
| D5 | Modes: `append` adds rows and fails (`DataError`, nothing written) if a batch key already exists or repeats in the batch; `upsert` keeps the last occurrence of each key in the batch, then replaces existing rows with the same key (later ingestion wins); `overwrite` replaces the whole table (batch keys must be unique). `keys` defaults to the primary key and, if given, must equal it as a set (`ValueError`). | Brief interface passes `keys`; allowing other keys would break primary-key uniqueness. |
| D6 | Durability: each partition is written to a hidden temp file in its directory and moved into place with `os.replace` (atomic). A multi-partition write is not atomic across partitions; a retried upsert is idempotent, so re-running repairs it. Overwrite builds the new table in a hidden sibling directory and swaps directories. One writer process per lake. | Simple and crash-safe per partition without a manifest. |
| D7 | `Database(lake, *, read_only=False)` opens `<lake root>/atlas.duckdb` with `TimeZone = 'UTC'`; every registered table gets a session-only view (`con.register`) over its Parquet glob, or over an empty typed Arrow table if not yet written. Views are rebuilt when the lake's in-process `generation` counter has changed (and by `refresh()`, for writes from another process). `get_database(lake)` returns one shared `Database` per database file per process. | Brief: "one per process, views over the Parquet globs". |
| D8 | `Database.query(sql, params)` accepts exactly one `SELECT` statement (checked with `duckdb.extract_statements`) and returns a pandas frame via Arrow (`date` → `datetime.date`, `timestamptz` → `datetime64[us, UTC]`, same as `read_table`). Writes, DDL and multi-statement SQL raise `ValueError`. | Parameter safety, and the only route to change `audit_events` is the mirror (append-only). |
| D9 | `curated/_meta.json` = `{"format": 1, "tables": {name: {"version", "partition_by", "fingerprint"}}}`; `fingerprint` is SHA-256 of the canonical column list. Reads and writes need stored version == registered version and the same fingerprint, else `DataError` (run `Lake.migrate`, or bump the version). Migrations are `fn(DataFrame) -> DataFrame` registered per `(table, from_version)`; `Lake.migrate(name)` applies the chain, validates against the current schema, overwrites and records the new version. | Brief: "schema version per table stored in `curated/_meta.json`"; the fingerprint catches a schema edit without a version bump. |
| D10 | Audit mirror: `Database.mirror_audit_log(path) -> int` verifies the file's hash chain (`AuditLog.verify_chain`), requires one `run_id` per file, checks that rows already mirrored for that run are an exact prefix of the file (same `event_id`/`hash` in order), then inserts only the new events in one transaction. `audit_events` has `event_id` as primary key and `(run_id, seq)` unique; `payload` is DuckDB `JSON`. | Append-only, idempotent re-mirroring, and a rewritten log can no longer pass silently once mirrored (partly addresses the Stage 03 "no external anchor" backlog item). |
| D11 | `read_table` returns rows sorted by primary key with a fresh `RangeIndex`; filters are `(column, op, value)` with `op` in `= == != < <= > >= in not in`, pushed down to pyarrow. | Deterministic output for reproducible backtests. |
| D12 | No new config keys: the lake root comes from `cfg.data.lake_path` (resolved by the caller); the layout names (`raw/ curated/ cache/ runs/`, `_meta.json`, `atlas.duckdb`) are fixed by the brief/spec, not tunable thresholds. | Stage protocol §4 targets limits and thresholds. |

## File structure

| File | Responsibility |
|---|---|
| `src/atlas/core/storage/__init__.py` (create) | Re-exports `Lake`, `Database`, `get_database`, `TableSchema`, `Column`, `ColumnType`, `SchemaRegistry`, `DEFAULT_REGISTRY` |
| `src/atlas/core/storage/schema.py` (create) | `ColumnType`, `Column`, `TableSchema`, `SchemaRegistry`, `DEFAULT_REGISTRY`, `validate_frame` |
| `src/atlas/core/storage/meta.py` (create) | `TableMeta`, `MetaStore` |
| `src/atlas/core/storage/lake.py` (create) | `Lake`, `WriteResult` |
| `src/atlas/core/storage/db.py` (create) | `Database`, `get_database`, `audit_events` DDL and mirror |
| `tests/unit/core/storage/test_storage_{schema,lake,upsert,meta,db,audit_mirror}.py` (create) | Tests |
| `tests/conftest.py` (modify) | `tmp_lake` fixture (stage protocol §5) |
| `pyproject.toml`, `uv.lock` (modify) | deps `duckdb>=1.1`, `pyarrow>=17`; mypy override for `pyarrow.*` |
| `tests/unit/test_package_layout.py` (modify only if it pins the `core` contents) | — |

## Review focus

1. **Time zones:** an `available_at` column in `America/New_York` must be stored as the same instant in UTC; naive timestamps rejected → `test_timestamp_column_normalised_to_utc`, `test_naive_timestamp_rejected` (Task 1).
2. **Unknown availability / bad numbers:** null `available_at`, NaN in a non-nullable float and ±inf are rejected → `test_null_available_at_rejected`, `test_non_nullable_nan_rejected`, `test_infinite_float_rejected` (Task 1).
3. **Path safety:** a partition value like `BRK/B` or `..` stays inside the table directory and reads back unchanged → `test_partition_value_with_slash_stays_inside_table` (Task 2).
4. **SQL injection:** a parameter containing SQL is data; stacked statements and writes are refused → `test_query_params_are_not_sql`, `test_query_rejects_multiple_statements`, `test_query_rejects_writes` (Task 4).
5. **PIT hand-off:** what `read_table` and `query` return works with Stage 05's `pit_filter` → `test_read_output_works_with_pit_filter` (Task 2), `test_query_timestamps_are_utc` (Task 4).

---

### Task 0: Kick-off (gate check, stage folder, plan) — records only

- [x] **Step 1:** Environment: `UV_CACHE_DIR="$TMPDIR/uv-cache" UV_PROJECT_ENVIRONMENT=.venv.nosync uv sync --group dev`, `ln -s .venv.nosync .venv`.
- [x] **Step 2: Gate check.** PROGRESS.md shows Stage 05 `DONE`; its `verification.md` says ALL CRITERIA VERIFIED. `uv run pytest -q | tee …/artifacts/gate-check-pytest.txt` → `512 passed`.
- [x] **Step 3:** Copy `_template/{worklog.md,verification.md,artifacts/}`; write this plan; set Stage 06 `IN PROGRESS` in PROGRESS.md; log the kick-off in `worklog.md`.

### Task 1: Dependencies, schema registry and write validation (brief task 1)

**Files:** modify `pyproject.toml`, `uv.lock`; create `src/atlas/core/storage/{__init__.py,schema.py}`, `tests/unit/core/storage/test_storage_schema.py`.

**Interfaces — Produces:**
- `class ColumnType(StrEnum)`: `STRING, INT64, FLOAT64, BOOL, DATE, TIMESTAMP` (values lower-case).
- `AVAILABLE_AT = "available_at"`, `YEAR = "year"`, `RESERVED_TABLES = frozenset({"audit_events"})`.
- `class Column(AtlasModel)`: `name: str` (identifier), `dtype: ColumnType`, `nullable: bool = False`.
- `class TableSchema(AtlasModel)`: `name`, `columns: tuple[Column, ...]`, `primary_key: tuple[str, ...]`, `partition_by: tuple[str, ...] = ()`, `year_column: str | None = None`, `version: int = 1` (≥1), `static: bool = False`, `description: str = ""`; methods `column_names -> tuple[str, ...]`, `column(name) -> Column`, `arrow_schema() -> pa.Schema`, `fingerprint() -> str`, `check_partitioning(cols: Sequence[str]) -> tuple[str, ...]` (`ValueError` if invalid).
- `Migration = Callable[[pd.DataFrame], pd.DataFrame]`; `class SchemaRegistry`: `register(schema) -> TableSchema` (same schema again is a no-op; different schema under a taken name → `ValueError`), `get(name) -> TableSchema` (`DataError` if unknown), `__contains__`, `names() -> tuple[str, ...]`, `copy() -> SchemaRegistry`, `add_migration(name, from_version, fn)`, `migration(name, from_version) -> Migration | None`.
- `DEFAULT_REGISTRY = SchemaRegistry()` (later stages register their tables here at import).
- `validate_frame(schema, df) -> pa.Table` (columns in schema order, Arrow types from `arrow_schema()`; `DataError` on any rule in D3).

- [x] **Step 1:** `uv add "duckdb>=1.1" "pyarrow>=17"`; add the mypy override `module = ["pyarrow", "pyarrow.*"]`, `ignore_missing_imports = true`; `uv run pytest -q` still 512 passed.
- [x] **Step 2: Failing tests** in `test_storage_schema.py` (helper `prices_schema()` = `prices_daily`: `symbol` string, `date` date, `close` float64, `volume` int64, `available_at` timestamp; PK `(symbol, date)`, `partition_by=("symbol",)`):
  - `test_schema_requires_available_at` — non-static schema without `available_at` → `ValueError` matching `available_at`; `available_at` typed `date` or `nullable=True` → `ValueError`.
  - `test_static_schema_may_omit_available_at` — `static=True` without it is valid.
  - `test_schema_rejects_bad_declarations` — parametrized: duplicate column, empty PK, PK column not declared, nullable PK column, partition column outside PK, `year` partition without `year_column`, partition on a float column, name `"Bad-Name"`, name `"audit_events"` → `ValueError`.
  - `test_arrow_schema_types` — `prices_schema().arrow_schema()` field types: `string`, `date32[day]`, `double`, `int64`, `timestamp[us, tz=UTC]`.
  - `test_registry_register_get_copy` — register twice (same) OK; different schema same name → `ValueError`; `get("nope")` → `DataError`; `copy()` is independent.
  - `test_validate_frame_missing_available_at_fails` — frame without the column → `DataError` matching `available_at`.
  - `test_validate_frame_extra_or_missing_columns_fail` → `DataError` naming the column.
  - `test_null_available_at_rejected`, `test_non_nullable_nan_rejected`, `test_infinite_float_rejected` → `DataError`.
  - `test_wrong_dtypes_rejected` — parametrized: ints as strings in `volume`, string dates, bools in `close`, int column in `symbol` → `DataError`.
  - `test_timestamp_column_normalised_to_utc` — `available_at` in `America/New_York` → Arrow value equals the UTC instant.
  - `test_naive_timestamp_rejected`, `test_sub_microsecond_timestamp_rejected` → `DataError`.
  - `test_date_column_accepts_dates_and_midnight_datetime64` — both accepted; `datetime` objects or non-midnight `datetime64` → `DataError`.
  - `test_nullable_columns_accept_nulls` — nullable float NaN and nullable string None become Arrow nulls.
- [x] **Step 3: Run** `uv run pytest tests/unit/core/storage/test_storage_schema.py -q` → FAIL (`ModuleNotFoundError: atlas.core.storage`).
- [x] **Step 4: Implement** `schema.py` (`model_validator(mode="after")` for the declaration rules; `validate_frame` checks with `pandas.api.types`, builds each column with `pa.array(..., type=…, from_pandas=True)`, casts timestamps `safe=True` so truncation raises) and `__init__.py`.
- [x] **Step 5: Run** → PASS; `uv run mypy src/atlas` → Success.

### Task 2: Meta store, lake layout, partitioned write/read (brief task 2)

**Files:** create `src/atlas/core/storage/{meta.py,lake.py}`, `tests/unit/core/storage/test_storage_{meta,lake}.py`; modify `tests/conftest.py`.

**Interfaces:**
- Consumes: Task 1 (`TableSchema`, `SchemaRegistry`, `DEFAULT_REGISTRY`, `validate_frame`).
- Produces: `@dataclass(frozen=True) TableMeta(version: int, partition_by: tuple[str, ...], fingerprint: str)`; `MetaStore(path)` with `get(name) -> TableMeta | None`, `put(name, meta) -> None` (atomic rewrite), `tables() -> dict[str, TableMeta]` (corrupt/unknown-format file → `DataError`).
- `WriteMode = Literal["append", "overwrite", "upsert"]`; `@dataclass(frozen=True) WriteResult(table: str, mode: WriteMode, rows: int, partitions: int)`.
- `Lake(root: Path, registry: SchemaRegistry | None = None)` (creates `raw/ curated/ cache/ runs/`): properties `root, raw_dir, curated_dir, cache_dir, runs_dir, registry, generation: int`; `raw_path(vendor, dataset) -> Path` (created; names `^[a-z0-9][a-z0-9_-]*$`, else `ValueError`); `table_path(name) -> Path`; `table_files(name) -> tuple[Path, ...]`; `has_table(name) -> bool` (written at least once); `table_schema(name) -> TableSchema`; `write_table(name, df, partition_cols=None, mode="append", keys=None) -> WriteResult`; `read_table(name, filters=None, columns=None) -> pd.DataFrame`.
- Fixture `tmp_lake` → `Lake(tmp_path / "lake", registry=DEFAULT_REGISTRY.copy())`.

- [x] **Step 1: Failing tests** — `test_storage_meta.py`: `test_meta_roundtrip`, `test_meta_missing_file_is_empty`, `test_meta_corrupt_file_raises` (`DataError`), `test_meta_write_is_atomic_json` (file parses after `put`; no temp files left).
  `test_storage_lake.py`:
  - `test_lake_creates_layout` — `raw/ curated/ cache/ runs/` exist; `raw_path("yfinance", "daily") == root/"raw"/"yfinance"/"daily"`; `raw_path("../x", "d")` → `ValueError`.
  - `test_roundtrip_dtypes` — write then read `prices_daily` (incl. a nullable float with NaN) → `assert_frame_equal` with expected dtypes `str`, `object` (dates), `float64`, `int64`, `datetime64[us, UTC]`; sorted by PK.
  - `test_partition_layout_by_symbol` — files are `curated/prices_daily/symbol=AAPL/data.parquet` and `…/symbol=MSFT/data.parquet`.
  - `test_partition_by_year` — schema with `year_column="date"`, `partition_by=("year",)`: dirs `year=2023`, `year=2024`; read back has no `year` column.
  - `test_partition_value_with_slash_stays_inside_table` — symbols `BRK/B` and `..` → every file under `table_path`, values read back equal.
  - `test_append_rejects_existing_or_repeated_keys` — second append of an overlapping key → `DataError`, table unchanged; repeated key inside one batch → `DataError`.
  - `test_overwrite_replaces_table` — old partitions gone, only the new rows remain.
  - `test_read_filters_and_columns` — `filters=[("symbol", "in", ["AAPL"]), ("date", ">=", date(2024,1,3))]`, `columns=["date", "close"]` → expected rows, column order as requested; unknown column or op → `ValueError`.
  - `test_read_unwritten_registered_table_is_empty` — empty frame with the schema's columns and dtypes; `has_table` False; unknown table → `DataError`.
  - `test_partition_change_requires_overwrite` — append with different `partition_cols` on an existing table → `ValueError`; overwrite with them works and is recorded in meta.
  - `test_read_output_works_with_pit_filter` — `pit_filter(read, AsOf.for_session(...))` keeps exactly the rows with `available_at < cutoff`.
  - `test_write_logs_and_bumps_generation` — `generation` increases by 1 per write.
- [x] **Step 2: Run** `uv run pytest tests/unit/core/storage -q` → FAIL (import errors).
- [x] **Step 3: Implement** `meta.py` and `lake.py` (append/overwrite only; upsert in Task 3). Partition directory names: `f"{col}={quote(str(value), safe='')}"`; values from the batch via a pandas `groupby` over the partition key columns; files read with `pyarrow.dataset` and converted with `to_pandas()` (no `ArrowDtype`).
- [x] **Step 4: Run** → PASS; mypy → Success.

### Task 3: Upsert semantics, idempotency and the 1M-row target (brief task 3)

**Files:** modify `src/atlas/core/storage/lake.py`; create `tests/unit/core/storage/test_storage_upsert.py`.

**Interfaces — Consumes:** Task 2 `Lake`. **Produces:** `write_table(..., mode="upsert")`.

- [x] **Step 1: Failing tests:**
  - `test_upsert_later_ingestion_wins` — upsert AAPL 2024-01-02 close 1.0, then close 2.0 → one row, close 2.0; untouched keys/partitions keep their values (MSFT file's mtime unchanged).
  - `test_upsert_batch_duplicates_keep_last` — batch with the same key twice → last row kept.
  - `test_upsert_is_idempotent` — same batch twice → identical `read_table` output and the same file set.
  - `test_upsert_into_new_table_creates_it`; `test_upsert_keys_must_match_primary_key` — `keys=["symbol"]` → `ValueError`; `keys=["date", "symbol"]` OK.
  - `test_upsert_one_million_rows_under_five_seconds` — `prices_daily` partitioned by symbol; seed 1,000,000 rows (500 symbols × 2,000 days; not timed); time an upsert of 1,000,000 rows (same symbols, days 1,000–2,999: half overlap, half new) with `time.perf_counter` → `< 5.0` s; then 1,500,000 rows total and the overlapping rows carry the new `close`. Elapsed seconds printed for the artifact.
- [x] **Step 2: Run** → FAIL (`mode="upsert"` not implemented).
- [x] **Step 3: Implement** upsert: read only the affected partitions' existing files, concatenate with the batch (Arrow), mark duplicates with pandas `duplicated(keep="last")` on the key columns only (dates as `datetime64`, not objects), filter, then sort by partition key and write each partition slice.
- [x] **Step 4: Run** → PASS; record the timing line in `worklog.md`.

### Task 4: DuckDB `Database`, views and parameterised queries (brief task 4)

**Files:** create `src/atlas/core/storage/db.py`, `tests/unit/core/storage/test_storage_db.py`.

**Interfaces — Consumes:** `Lake` (`registry`, `generation`, `table_path`, `has_table`, schemas). **Produces:** `DB_FILENAME = "atlas.duckdb"`; `Database(lake, *, read_only=False)` with `path`, `lake`, `refresh()`, `query(sql: str, params: Sequence[object] | Mapping[str, object] | None = None) -> pd.DataFrame`, `close()`, context manager; `get_database(lake) -> Database`.

- [x] **Step 1: Failing tests:**
  - `test_query_over_lake_view` — after a write, `db.query("select * from prices_daily where symbol = ?", ["AAPL"])` returns the AAPL rows.
  - `test_named_params` — `$sym` with `{"sym": "AAPL"}`.
  - `test_query_params_are_not_sql` — param `"AAPL' OR '1'='1"` → 0 rows.
  - `test_query_rejects_multiple_statements` — `"select 1; drop table audit_events"` → `ValueError`; `audit_events` still exists.
  - `test_query_rejects_writes` — `insert`/`delete`/`create table`/`copy` → `ValueError`.
  - `test_views_see_new_writes` — write, query, write more (new partition and an upsert), query → new rows visible without calling `refresh()`.
  - `test_unwritten_table_view_is_empty_and_typed` — `select * from prices_daily` → 0 rows with the schema's columns.
  - `test_query_timestamps_are_utc` — `available_at` dtype `datetime64[us, UTC]`; `pit_filter` accepts the result.
  - `test_views_are_not_persisted` — close, reopen raw `duckdb.connect(path)`: no view named `prices_daily` in `duckdb_views()`.
  - `test_get_database_is_one_per_process` — `get_database(lake) is get_database(lake)`; after `close()` a new one is returned.
  - `test_read_only_database` — after creation, `Database(lake, read_only=True)` can query.
- [x] **Step 2: Run** → FAIL (`ModuleNotFoundError: atlas.core.storage.db`).
- [x] **Step 3: Implement** with `duckdb.connect(str(path), read_only=…)`, `SET TimeZone = 'UTC'`, `con.register(name, con.read_parquet(glob, hive_partitioning=False))`, `duckdb.extract_statements` for the SELECT-only check, `con.sql(sql, params=…).to_arrow_table().to_pandas()`; a `threading.Lock` around connection use.
- [x] **Step 4: Run** → PASS; mypy → Success.

### Task 5: Schema versions, migrations and the audit mirror (brief task 5)

**Files:** modify `src/atlas/core/storage/{lake.py,db.py}`; create `tests/unit/core/storage/test_storage_audit_mirror.py`; extend `test_storage_meta.py`.

**Interfaces — Produces:** `Lake.migrate(name) -> int` (the table's version afterwards); `Database.mirror_audit_log(path: Path) -> int` (rows inserted); `AUDIT_TABLE = "audit_events"` with columns `event_id, run_id, seq, ts, as_of, actor, event_type, payload, prev_hash, hash`.

- [x] **Step 1: Failing tests** — meta/migrations (`test_storage_meta.py`):
  - `test_first_write_records_version` — `_meta.json` has `version 1`, `partition_by ["symbol"]`, the fingerprint.
  - `test_version_mismatch_blocks_read_and_write` — registry with `version=2` (one more column) over v1 data → `read_table`/`write_table` raise `DataError` matching `migrate`.
  - `test_migrate_applies_chain` — v1 → v3 with two registered steps (add a column, then rename/fill) → data matches v3, meta version 3, `migrate` again returns 3 and changes nothing.
  - `test_migrate_missing_step_fails_cleanly` — no step for v2 → `DataError`; data and meta unchanged.
  - `test_registered_version_older_than_data_fails` → `DataError`.
  - `test_schema_change_without_version_bump_fails` — same version, different columns → `DataError` matching `fingerprint`.
  Audit mirror (`test_storage_audit_mirror.py`, events written with `AuditLog` inside `run_context`):
  - `test_mirror_inserts_all_events` — 3 events → returns 3; `select … order by seq` matches the JSONL (ids, hashes, `ts` UTC, `as_of`, `payload->>'k'`).
  - `test_mirror_is_idempotent_and_incremental` — mirror again → 0; record 2 more → 2; total 5.
  - `test_mirror_refuses_broken_chain` — edit one line → `DataError`, nothing inserted.
  - `test_mirror_detects_rewritten_history` — mirror, then replace the file with a freshly chained log of the same run with different events → `DataError`.
  - `test_audit_events_is_append_only_via_query` — `query("delete from audit_events")` / `update` → `ValueError`; row count unchanged.
  - `test_mirror_empty_or_missing_file` — empty file → 0; missing file → `DataError`.
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement** version/fingerprint checks in `Lake` (one `_check_meta(name)` used by read, write and migrate); `migrate` reads raw files with `pyarrow.dataset` (no cast), applies steps, `validate_frame` against the current schema, writes with the overwrite path and stores the new meta. Mirror: `CREATE TABLE IF NOT EXISTS audit_events (…)` (constant SQL) at open (not in read-only mode); `executemany` with `?` parameters inside `BEGIN`/`COMMIT` (rollback on error).
- [x] **Step 4: Run** `uv run pytest tests/unit/core/storage -q` → PASS; `uv run mypy src/atlas` → Success.

### Task 6: Verification, records, commit

- [x] **Step 1:** Self-review of the whole diff against this plan and the brief; fix findings test-first; log them.
- [x] **Step 2:** Run and save to `artifacts/`: `uv run pytest -q` (`pytest.txt`), `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/atlas` (`lint-type.txt`), `uv run pytest tests/unit/core/storage -q` (`verify.txt`, the brief's Verify), `make check` (`make-check.txt`), the storage tests verbose with the 1M-row timing (`stage-tests-verbose.txt`), the docstring check over new public names (`docstrings.txt`).
- [x] **Step 3:** Write `verification.md` (Rule 1.5) and the PROGRESS.md handoff note + evidence summary; Decisions-log rows D1–D12; Backlog items; tick 06, set `DONE`, current stage → 07 `NOT STARTED`.
- [x] **Step 4:** Commit `stage-06: Storage layer (DuckDB + Parquet lake)`.
