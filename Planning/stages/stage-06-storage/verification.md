# Stage 06 — Storage layer (DuckDB + Parquet lake) · Verification evidence

Fill this in **only** with output from commands actually run in this stage's final chat (Project.md Rule 1). Save full raw outputs under `artifacts/` and quote the key lines here.

#### Verification evidence — Stage 06 — 2026-10-27
- Commands run (this chat, after the self-review fixes, on the final working tree; base commit `edb554d`):
  - `uv run pytest -q` → 626 passed, 0 failed, 0 deselected, exit 0 (512 before the stage + 114 new) · raw: [artifacts/pytest.txt](artifacts/pytest.txt)
  - `uv run ruff check .` → All checks passed! (TID251 wall-clock ban still active) · raw: [artifacts/lint-type.txt](artifacts/lint-type.txt)
  - `uv run ruff format --check .` → 74 files already formatted · raw: [artifacts/lint-type.txt](artifacts/lint-type.txt)
  - `uv run mypy src/atlas` → Success: no issues found in 40 source files · raw: [artifacts/lint-type.txt](artifacts/lint-type.txt)
  - `uv run pytest tests/unit/core/storage -q` (brief's Verify) → 114 passed, exit 0 · raw: [artifacts/verify.txt](artifacts/verify.txt)
  - `make check` → lint, format check, mypy clean; 626 passed; exit 0 · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
  - `uv run pytest tests/unit/core/storage -v -s -W error` → 114 PASSED, 0 FAILED/ERROR, no warnings; prints `1M-row upsert into a 1M-row table (500 partitions): 0.60 s` · raw: [artifacts/stage-tests-verbose.txt](artifacts/stage-tests-verbose.txt)
  - Docstring check (`artifacts/docstrings.py`) → 5 modules, 51 public classes/functions/methods/properties checked; missing docstrings: none · raw: [artifacts/docstrings.txt](artifacts/docstrings.txt)
  - Gate check (start of stage) → `uv run pytest -q` 512 passed, exit 0 · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
  - Note: the full-suite runs (`pytest.txt`, `make-check.txt`) end with `nanobind: leaked … instances/types/functions` lines printed by duckdb's bindings at interpreter shutdown, after the results. They do not change the exit code (0) or any result; cause traced in the worklog (Task 5): hypothesis's module-constant scan holding duckdb objects. Backlog item.
- Acceptance criteria (brief.md):
  - [x] **1M-row synthetic upsert completes in under 5 s locally.** Evidence: `test_upsert_one_million_rows_under_five_seconds` PASSED; it seeds a 1,000,000-row `prices_daily` table (500 symbol partitions), then times (`time.perf_counter`) an upsert of 1,000,000 rows (500,000 overlapping keys, 500,000 new) and asserts `elapsed < 5.0`, 1,500,000 rows afterwards and the new values on the overlapping keys. Measured 0.60 s in the final run (`artifacts/stage-tests-verbose.txt`); earlier runs 0.59 / 0.61 / 0.58 s (worklog, Task 3).
  - [x] **All tests green.** Evidence: `uv run pytest -q` → 626 passed, 0 failed, 0 deselected; `make check` exit 0; the brief's Verify → 114 passed.
  - Brief's "Tests" list, each covered (all PASSED in `artifacts/stage-tests-verbose.txt`):
    - Round-trip dtypes → `test_roundtrip_dtypes`, `test_read_unwritten_registered_table_is_empty`, `test_partition_dir_names_for_each_type`, `test_timestamp_column_normalised_to_utc`.
    - Upsert dedupe → `test_upsert_later_ingestion_wins`, `test_upsert_batch_duplicates_keep_last`, `test_upsert_adds_new_keys_and_partitions`.
    - Schema rejection → `test_schema_requires_available_at`, `test_validate_frame_missing_available_at_fails`, `test_schema_rejects_bad_declarations` (11 cases), `test_wrong_dtypes_rejected` (6), `test_null_available_at_rejected`, `test_naive_timestamp_rejected`.
    - Query parameter safety → `test_query_params_are_not_sql`, `test_query_rejects_multiple_statements`, `test_query_rejects_writes` (9 cases), `test_named_params`.
    - Idempotent re-write → `test_upsert_is_idempotent`, `test_mirror_is_idempotent_and_incremental`, `test_migrate_applies_chain` (second `migrate` rewrites nothing).
  - In-scope items, each covered: lake layout (`test_lake_creates_layout`); `Lake.write_table`/`read_table`/`has_table`/`table_schema` (`test_storage_lake.py`); schema registry with required `available_at` (`test_storage_schema.py`); `Database` views and `query` (`test_storage_db.py`, incl. `test_views_are_not_persisted`, `test_views_see_new_writes`, `test_get_database_is_one_per_process`); upsert (`test_storage_upsert.py`); schema version per table in `curated/_meta.json` with migrations (`test_first_write_records_version`, `test_migrate_applies_chain`, `test_version_mismatch_blocks_read_and_write`, `test_schema_change_without_version_bump_fails`); `audit_events` append-only mirror (`test_storage_audit_mirror.py`, incl. `test_audit_events_is_append_only_via_query`, `test_mirror_detects_rewritten_history`).
- Waivers: none

#### Follow-up after the post-DONE audit — 2026-10-27
Owner-requested follow-up (see worklog, "Post-DONE audit"): log-line tests added and two clobbered log keys fixed (`write_mode`, `mirrored_run_id`). Re-run in this chat on the final working tree (base commit `10e7394`):
- `uv run pytest -q` → 627 passed, 0 failed, 0 deselected, exit 0 (626 + `test_mirror_logs_one_info_line`; `test_write_bumps_generation` renamed `test_write_logs_and_bumps_generation`) · raw: [artifacts/followup-pytest.txt](artifacts/followup-pytest.txt)
- `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 74 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 40 source files · raw: [artifacts/followup-lint-type.txt](artifacts/followup-lint-type.txt)
- `uv run pytest tests/unit/core/storage -q` (brief's Verify) → 115 passed, exit 0 · raw: [artifacts/followup-verify.txt](artifacts/followup-verify.txt)
- `make check` → lint, format check, mypy clean; 627 passed; exit 0 · raw: [artifacts/followup-make-check.txt](artifacts/followup-make-check.txt)
- New log tests + 1M-row upsert, `-v -s -W error` → 3 passed; `1M-row upsert into a 1M-row table (500 partitions): 0.69 s` (< 5 s) · raw: [artifacts/followup-new-tests.txt](artifacts/followup-new-tests.txt)
- Both acceptance criteria still hold: the 1M-row upsert passes in 0.69 s; all tests green (627 passed, `make check` exit 0).

**Result:** ALL CRITERIA VERIFIED
