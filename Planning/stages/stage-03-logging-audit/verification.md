# Stage 03 — Logging, run context & audit trail · Verification evidence

Fill this in **only** with output from commands actually run in this stage's final chat (Project.md Rule 1). Save full raw outputs under `artifacts/` and quote the key lines here.

#### Verification evidence — Stage 03 — 2026-10-26
- Commands run (this chat, after the last code change):
  - `uv run pytest -q` → 185 passed, 0 failed, 0 deselected, exit=0 · raw: artifacts/pytest.txt
  - `uv run ruff check .` → All checks passed!, exit=0 · raw: artifacts/lint-type.txt
  - `uv run ruff format --check .` → 35 files already formatted, exit=0 · raw: artifacts/lint-type.txt
  - `uv run mypy src/atlas` → Success: no issues found in 23 source files, exit=0 (strict on `atlas.core.*`) · raw: artifacts/lint-type.txt
  - `uv run pytest tests/unit/core -q` (brief's Verify) → 160 passed, exit=0 · raw: artifacts/verify-tests-core.txt
  - `make check` → ruff clean, 35 files formatted, mypy Success, 185 passed, exit=0 · raw: artifacts/make-check.txt
  - The four Stage 03 test files with `-v` → 68 PASSED, 0 FAILED · raw: artifacts/stage-tests-verbose.txt
  - Acceptance demo (one backtest run: `configure_logging` into the run dir, `AuditLog(audit_path(...))`, 4 events across two nested `as_of`/`stage` scopes) → 4 log lines, "every line carries this run_id: True"; 4 chained audit events (first `prev=000000000000`); `verify_chain (untouched): True`; `verify_chain (one payload edited): False`; exit=0 · raw: artifacts/acceptance-demo.txt
  - Public docstring check over `atlas.core.{errors,run_context,logging,audit}` → `public items without docstring: []` · raw: artifacts/docstrings.txt
  - Wall-clock grep `datetime\.now|date\.today|time\.time|utcnow` over `src/atlas` and `tests` → no match (grep exit 1) · raw: artifacts/wallclock-grep.txt
  - Follow-up, on merged `main` (main checkout, `bb1eca1`, clean before and after; 2026-10-26 14:01 EDT): `uv run pytest -q` → 185 passed · `uv run ruff check .` → All checks passed! · `uv run ruff format --check .` → 35 files already formatted · `uv run mypy src/atlas` → Success: no issues found in 23 source files · `uv run pytest tests/unit/core -q` → 160 passed · `make check` → all four clean, 185 passed; every command exit=0 · raw: artifacts/merged-main-check.txt
  - Mutation check (worklog, Task 5): with the hash comparison removed, `test_tamper_edited_payload` and `test_tamper_edited_line_text` FAILED; restored → 32 passed
- Acceptance criteria (brief.md):
  - [x] Every audit event is chained and verifiable — evidence: `test_record_chains_events` (first `prev_hash == GENESIS_HASH`, each next `prev_hash` = previous `hash`, hash = SHA-256 of prev_hash + canonical JSON), `test_reopen_continues_chain`, `test_rich_payload_round_trips_and_verifies`, `test_audit_path`; tamper detection: `test_tamper_edited_payload`, `test_tamper_edited_line_text`, `test_tamper_deleted_line`, `test_tamper_deleted_first_line`, `test_tamper_swapped_lines`, `test_tamper_rehashed_line_breaks_next_link`, `test_tamper_invalid_json_line`, `test_tamper_invalid_event_field`, `test_truncated_last_line_fails_and_reopen_refuses`, `test_missing_final_newline_fails` — all PASSED; artifacts/acceptance-demo.txt
  - [x] Logs carry the run_id — evidence: `test_every_file_line_is_json_with_run_id` (structlog and plain stdlib/third-party lines, nested scope adds `stage`), `test_console_is_pretty_text` (`run_id=` on the console), `test_outside_run_has_no_run_id` — all PASSED; artifacts/acceptance-demo.txt ("every line carries this run_id: True")
  - [x] Brief *Tests* list — chain integrity and tamper detection: as above; concurrent appends from threads keep the file valid (lock): `test_concurrent_threads_keep_chain_valid` (8 threads × 50, 400 lines, unique ids, chain valid), `test_two_instances_same_file_share_chain`; run_id propagation: `test_nested_inherits_and_overrides`, `test_copy_context_propagates`, `test_new_thread_has_no_context`, `test_nested_restored_after_exception` — all PASSED
  - [x] Brief *In scope* items — structlog JSON file + pretty console + `get_logger(name)`: `tests/unit/core/test_logging.py` (8 PASSED); `RunContext` with `run_id` (ULID), `mode`, `as_of`, `config_fingerprint`, `stage`: `tests/unit/core/test_run_context.py` (13 PASSED); `AuditEvent` fields and `EventType` seeded with the 15 kinds: `test_seeded_event_types`, `test_audit_event_is_frozen`; canonical JSON (sorted keys, Decimal/date encoders): 7 `canonical_json` tests; `errors.py` hierarchy: `tests/unit/core/test_errors.py` (15 PASSED) — all PASSED
  - [x] Definition of Done extras — docstrings on every new public item (artifacts/docstrings.txt); no new config key was added (log level is a parameter, plan D5), so no YAML change is needed
- Waivers: none

**Result:** ALL CRITERIA VERIFIED

#### Phase 0 checkpoint follow-up (Tier 1) — 2026-11-02
Owner-requested follow-up (see worklog, 2026-11-02): a caller's log value under a run key is kept in `shadowed` instead of being overwritten. Re-run in this chat on the final working tree (base commit `473629f`):
- Brief's Verify `uv run pytest tests/unit/core -q` → 704 passed, exit 0 · raw: [artifacts/checkpoint-2026-11-02-verify.txt](artifacts/checkpoint-2026-11-02-verify.txt)
- New `test_run_keys_win_and_caller_values_are_kept` PASSED (it FAILED before the fix); `test_every_file_line_is_json_with_run_id` still passes (logs carry the run_id)
- `uv run pytest -q` → 1678 passed, 0 failed, 11 deselected (live/llm/integration), exit 0 · raw: [artifacts/checkpoint-2026-11-02-pytest.txt](artifacts/checkpoint-2026-11-02-pytest.txt)
- `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 191 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 84 source files · raw: [artifacts/checkpoint-2026-11-02-lint-type.txt](artifacts/checkpoint-2026-11-02-lint-type.txt)
- `make check` → lint, format check, mypy clean; 1678 passed; exit 0 · raw: [artifacts/checkpoint-2026-11-02-make-check.txt](artifacts/checkpoint-2026-11-02-make-check.txt)
- Machine load during these runs: 5.07 → 5.33 (`uptime`) · raw: [artifacts/checkpoint-2026-11-02-load.txt](artifacts/checkpoint-2026-11-02-load.txt)
- The acceptance criterion still holds: every audit event is chained and verifiable (`tests/unit/core/test_audit.py` passes), logs carry the run_id.
- On merged `main` (`d9cffb6`, fast-forward from `473629f`; run in the owner's Terminal panel, the sandbox cannot write the main checkout): `uv run pytest -q` → 1678 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 191 files already formatted; `mypy src/atlas` → Success (84 files); 1M-row upsert → 0.73 s PASSED; `make check` → exit 0; `git branch --merged main` lists `claude/phase-one-checkpoint-28fcd7`; checkout clean before and after; all 11 commands exit 0 · raw: [stage-06 artifacts/checkpoint-2026-11-02-merged-main-check.txt](../stage-06-storage/artifacts/checkpoint-2026-11-02-merged-main-check.txt) (script: same name, `.sh`)

**Result:** ALL CRITERIA VERIFIED

#### Phase 0 checkpoint follow-up (Tier 3) — 2026-11-02
Owner-requested follow-up (see worklog, 2026-11-02): audit event ids come from the installed id source (reproducible under `SeededIds`), and several processes can append to one audit file safely. Re-run in this chat on the final working tree (base commit `e458f2f`):
- Brief's Verify `uv run pytest tests/unit/core -q` → 788 passed, exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier3-verify.txt](artifacts/checkpoint-2026-11-02-tier3-verify.txt)
- The 4 new tests failed on `e458f2f`'s code (2 import errors, 2 assertion failures: stale tail hash, broken chain) and PASS now · raw: [artifacts/checkpoint-2026-11-02-tier3-red-before-fix.txt](artifacts/checkpoint-2026-11-02-tier3-red-before-fix.txt)
- `uv run pytest -q` → 2108 passed, 0 failed, 11 deselected (live/llm/integration), exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier3-pytest.txt](artifacts/checkpoint-2026-11-02-tier3-pytest.txt)
- `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 205 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 88 source files · raw: [artifacts/checkpoint-2026-11-02-tier3-lint-type.txt](artifacts/checkpoint-2026-11-02-tier3-lint-type.txt)
- `make check` → lint, format check, mypy clean; 2108 passed; exit 0 · raw: [artifacts/checkpoint-2026-11-02-tier3-make-check.txt](artifacts/checkpoint-2026-11-02-tier3-make-check.txt)
- Machine load during these runs: 11.68 → 6.43 (`uptime`) · raw: [artifacts/checkpoint-2026-11-02-tier3-load.txt](artifacts/checkpoint-2026-11-02-tier3-load.txt)
- The acceptance criterion still holds (every audit event chained and verifiable; logs carry the run_id): `test_record_chains_events`, the `test_tamper_*` tests, `test_concurrent_threads_keep_chain_valid` and the two new cross-process tests pass; `test_event_ids_and_times_follow_the_installed_id_source` checks each run's own `run_id`.
- On merged `main` (`d846205`, fast-forward from `e458f2f`; run in the owner's Terminal panel, the sandbox cannot write the main checkout; this run also covers a docstring-only wording fix to `audit.py` made after the working-tree runs): `uv run pytest -q` → 2108 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 205 files already formatted; `mypy src/atlas` → Success (88 files); the three briefs' Verify commands → 788 passed (03), 275 passed + mypy core Success (04), 136 passed (06); 1M-row upsert → 1.12 s PASSED; `make check` → exit 0 (2108 passed); `git branch --merged main` lists `claude/phase-one-checkpoint-28fcd7`; checkout clean before and after; all 14 commands exit 0 · raw: [stage-03 artifacts/checkpoint-2026-11-02-tier3-merged-main-check.txt](artifacts/checkpoint-2026-11-02-tier3-merged-main-check.txt) (script: same name, `.sh`)

**Result:** ALL CRITERIA VERIFIED

#### Phase 2 checkpoint follow-up (Tier 4: a library's helper thread logs into its run) — 2026-11-14
- Commands run (this chat): `make check` → exit 0 (ruff clean, 393 files formatted, mypy clean on 157 files, 4241 passed in parallel and 3 timing tests alone); `uv run pytest -q` → 4244 passed, 71 deselected; integration on the main lake → 55 passed, 1 skipped (the flag-gated 2018–2024 calibration) · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-final-checks.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-final-checks.txt); red first: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-red.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-red.txt); mutants: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-mutate.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-mutate.txt)
- `tests/unit/core/test_logging.py` and `test_run_context.py` pass; the research CLI test passes with an empty `MPLCONFIGDIR`.
- Acceptance criteria unchanged and still evidenced.

**Result:** ALL CRITERIA VERIFIED
