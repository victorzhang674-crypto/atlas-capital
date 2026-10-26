# Stage 06 — Storage layer (DuckDB + Parquet lake)
**Phase:** 0 · **Size:** M · **Depends on:** 05 · **Spec refs:** §11, §12

## Goal
A simple, fast, embedded data store: raw vendor payloads → curated Parquet tables → queryable through DuckDB, with PIT columns enforced.

## In scope
- Lake layout under `cfg.data.lake_path`: `raw/<vendor>/<dataset>/…`, `curated/<table>/` (Parquet, partitioned by year or symbol), `cache/`, `runs/`
- `Lake` class: `write_table(name, df, partition_cols, mode="append|overwrite|upsert", keys=[…])`, `read_table(name, filters, columns)`, `has_table`, `table_schema`
- Schema registry: each curated table declares columns, dtypes, primary key and the **required** `available_at` (except static reference tables); writes are validated
- `Database` (DuckDB connection, one per process, views over the Parquet globs); `query(sql, params) -> DataFrame`
- Upsert by keys (dedupe and keep the latest ingestion)
- Simple migrations: a schema version per table stored in `curated/_meta.json`
- Mirror `AuditLog` into a DuckDB table `audit_events` (append-only)

## Out of scope
Vendor fetching (Phase 1); remote storage.

## Files
`src/atlas/core/storage/{lake.py,schema.py,db.py,meta.py}`, tests

## Key interfaces
```python
lake = Lake(root); lake.write_table("prices_daily", df, keys=["symbol","date"], mode="upsert")
lake.read_table("prices_daily", filters=[("symbol","in",["AAPL"])], columns=[...])
db = Database(lake); db.query("select * from prices_daily where symbol = ?", ["AAPL"])
```

## Tasks
1. Schema registry + validation (missing `available_at` fails).
2. Write/read Parquet with partitions; round-trip test using `tmp_lake`.
3. Upsert semantics (later ingestion wins); idempotency test.
4. DuckDB views + parameterised queries (no string-formatted SQL).
5. Meta/migration version; audit mirror.

## Tests
Round-trip dtypes; upsert dedupe; schema rejection; query parameter safety; idempotent re-write.

## Acceptance criteria
- 1M-row synthetic upsert completes in under 5 s locally; all tests green.

## Verify
`uv run pytest tests/unit/core/storage -q`
