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
- On merged `main` (`b640a80`, fast-forward from `edb554d`; run in the owner's Terminal panel): `uv run pytest -q` → 627 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 74 files already formatted; `mypy src/atlas` → Success (40 files); `uv run pytest tests/unit/core/storage -q` → 115 passed; `make check` → exit=0; checkout clean before and after · raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt) (script: [artifacts/merged-main-check.sh](artifacts/merged-main-check.sh))

**Result:** ALL CRITERIA VERIFIED

#### Phase 0 checkpoint follow-up — 2026-11-02
The checkpoint found the 1M-row upsert over 5 s on a heavily loaded machine (11.34 s in the full suite; 5.66–12.41 s alone; load average 14–16) with no change to the upsert code or its libraries since this stage (see worklog, 2026-11-02). Owner-approved change: best of up to 3 timings, each on a fresh lake, with the 5 s limit unchanged. Re-run in this chat on the final working tree (base commit `473629f`):
- `uv run pytest -v -s -W error tests/unit/core/storage/test_storage_upsert.py::test_upsert_one_million_rows_under_five_seconds` → PASSED; `1M-row upsert into a 1M-row table (500 partitions): 0.89 s (0.89 s)` (first attempt) · raw: [artifacts/checkpoint-2026-11-02-speed-idle.txt](artifacts/checkpoint-2026-11-02-speed-idle.txt); the failing run before the change: [artifacts/checkpoint-2026-11-02-loaded-run-before-fix.txt](artifacts/checkpoint-2026-11-02-loaded-run-before-fix.txt)
- Brief's Verify `uv run pytest tests/unit/core/storage -q` → 126 passed, exit 0 · raw: [artifacts/checkpoint-2026-11-02-verify.txt](artifacts/checkpoint-2026-11-02-verify.txt)
- `uv run pytest -q` → 1678 passed, 0 failed, 11 deselected (live/llm/integration), exit 0 · raw: [artifacts/checkpoint-2026-11-02-pytest.txt](artifacts/checkpoint-2026-11-02-pytest.txt)
- `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 191 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 84 source files · raw: [artifacts/checkpoint-2026-11-02-lint-type.txt](artifacts/checkpoint-2026-11-02-lint-type.txt)
- `make check` → lint, format check, mypy clean; 1678 passed; exit 0 · raw: [artifacts/checkpoint-2026-11-02-make-check.txt](artifacts/checkpoint-2026-11-02-make-check.txt)
- Machine load during these runs: 5.07 → 5.33 (`uptime`) · raw: [artifacts/checkpoint-2026-11-02-load.txt](artifacts/checkpoint-2026-11-02-load.txt)
- Both acceptance criteria hold again: the 1M-row upsert takes 0.89 s (< 5 s) on an idle machine; all tests green (1678 passed, `make check` exit 0).
- On merged `main` (`d9cffb6`, fast-forward from `473629f`; run in the owner's Terminal panel, the sandbox cannot write the main checkout): `uv run pytest -q` → 1678 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 191 files already formatted; `mypy src/atlas` → Success (84 files); 1M-row upsert → 0.73 s PASSED; `make check` → exit 0; `git branch --merged main` lists `claude/phase-one-checkpoint-28fcd7`; checkout clean before and after; all 11 commands exit 0 · raw: [artifacts/checkpoint-2026-11-02-merged-main-check.txt](artifacts/checkpoint-2026-11-02-merged-main-check.txt) (script: same name, `.sh`)

**Result:** ALL CRITERIA VERIFIED

#### Phase 0 checkpoint follow-up (Tier 3) — 2026-11-02
Owner-requested follow-up (see worklog, 2026-11-02): DuckDB file access confined to the lake root and locked. Re-run in this chat on the final working tree (base commit `e458f2f`):
- Brief's Verify `uv run pytest tests/unit/core/storage -q` → 136 passed, exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier3-verify.txt](artifacts/checkpoint-2026-11-02-tier3-verify.txt)
- The 9 new failing cases failed on `e458f2f`'s code and PASS now · raw: [artifacts/checkpoint-2026-11-02-tier3-red-before-fix.txt](artifacts/checkpoint-2026-11-02-tier3-red-before-fix.txt)
- `uv run pytest -q` → 2108 passed, 0 failed, 11 deselected (live/llm/integration), exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier3-pytest.txt](artifacts/checkpoint-2026-11-02-tier3-pytest.txt)
- `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 205 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 88 source files · raw: [artifacts/checkpoint-2026-11-02-tier3-lint-type.txt](artifacts/checkpoint-2026-11-02-tier3-lint-type.txt)
- `make check` → lint, format check, mypy clean; 2108 passed; exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier3-make-check.txt](artifacts/checkpoint-2026-11-02-tier3-make-check.txt)
- Machine load during these runs: 11.68 → 6.43 (`uptime`) · raw: [artifacts/checkpoint-2026-11-02-tier3-load.txt](artifacts/checkpoint-2026-11-02-tier3-load.txt)
- The acceptance criterion still holds: 1M-row upsert 0.69 s (under 5 s) PASSED · raw: [artifacts/checkpoint-2026-11-02-tier3-speed.txt](artifacts/checkpoint-2026-11-02-tier3-speed.txt); all tests green.
- On merged `main` (`d846205`, fast-forward from `e458f2f`; run in the owner's Terminal panel, the sandbox cannot write the main checkout; this run also covers a docstring-only wording fix to `audit.py` made after the working-tree runs): `uv run pytest -q` → 2108 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 205 files already formatted; `mypy src/atlas` → Success (88 files); the three briefs' Verify commands → 788 passed (03), 275 passed + mypy core Success (04), 136 passed (06); 1M-row upsert → 1.12 s PASSED; `make check` → exit 0 (2108 passed); `git branch --merged main` lists `claude/phase-one-checkpoint-28fcd7`; checkout clean before and after; all 14 commands exit 0 · raw: [stage-03 artifacts/checkpoint-2026-11-02-tier3-merged-main-check.txt](../stage-03-logging-audit/artifacts/checkpoint-2026-11-02-tier3-merged-main-check.txt) (script: same name, `.sh`)

**Result:** ALL CRITERIA VERIFIED

#### Phase 0 checkpoint follow-up (Tier 4) — 2026-11-02
Owner-requested follow-up (see worklog, 2026-11-02): duckdb/pyarrow floors raised to the lowest versions the suite passes on; nanobind shutdown lines investigated (not fixed). Re-run in this chat on the final working tree (base commit `81ada1e`):
- Brief's Verify `uv run pytest tests/unit/core/storage -q` → 136 passed, exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier4-verify.txt](artifacts/checkpoint-2026-11-02-tier4-verify.txt)
- Old floors fail (hang at 1.1.0 + 17.0.0; 17 failed at 1.2.0 + 17.0.0; per-version search) and the new floor test failed on `81ada1e` · raw: [artifacts/checkpoint-2026-11-02-tier4-red-before-fix.txt](artifacts/checkpoint-2026-11-02-tier4-red-before-fix.txt)
- New floors duckdb 2.0.0 + pyarrow 19.0.0: full suite 2116 passed, 0 failed, exit 0; integration 1 passed · raw: [artifacts/checkpoint-2026-11-02-tier4-floors.txt](artifacts/checkpoint-2026-11-02-tier4-floors.txt)
- nanobind investigation (reproduction, what was ruled out, not fixed) · raw: [artifacts/checkpoint-2026-11-02-tier4-nanobind.txt](artifacts/checkpoint-2026-11-02-tier4-nanobind.txt)
- `uv lock --check` → exit 0; `uv run pytest -q` → 2116 passed, 0 failed, 12 deselected, exit 0; `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 206 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 88 source files; `make check` → exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier4-verify.txt](artifacts/checkpoint-2026-11-02-tier4-verify.txt)
- The stage's acceptance criteria still hold: all storage tests green on the locked versions and on the new floors.
- On merged `main` (`be247fb`, fast-forward from `81ada1e`; run in the owner's Terminal panel, the sandbox cannot write the main checkout): `uv lock --check` exit 0; `uv run pytest -q` → 2116 passed; `uv run pytest -m integration -q` → 1 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 206 files already formatted; `mypy src/atlas` → Success (88 files); the briefs' Verify commands → 207 passed (02), 136 passed (06); `uv run pre-commit run --all-files` → all four hooks Passed; `make check` → exit 0 (2116 passed); `git branch --merged main` lists `claude/phase-one-checkpoint-28fcd7`; checkout clean before and after; all commands exit 0 · raw: [stage-01 artifacts/checkpoint-2026-11-02-tier4-merged-main-check.txt](../stage-01-project-scaffolding/artifacts/checkpoint-2026-11-02-tier4-merged-main-check.txt) (script: same name, `.sh`)

**Result:** ALL CRITERIA VERIFIED
