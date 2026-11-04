# Stage 14 — Data sync CLI & bootstrap backfill · Verification evidence

Every command below was run by the AI in this stage's chat (Project.md Rule 1, Rule 6). Commands use `UV_CACHE_DIR=$TMPDIR/uv-cache-s14 UV_NO_SYNC=1` (sandbox). Raw outputs are in `artifacts/`.

#### Verification evidence — Stage 14 — 2026-11-01
- Commands run (this chat):
  - `uv run pytest -q` → 1673 passed, 0 failed, 11 deselected (live), exit 0 · raw: [artifacts/pytest.txt](artifacts/pytest.txt)
  - `uv run ruff check .` → All checks passed!, exit 0 · raw: [artifacts/ruff-check.txt](artifacts/ruff-check.txt)
  - `uv run ruff format --check .` → 190 files already formatted, exit 0 · raw: [artifacts/ruff-format.txt](artifacts/ruff-format.txt)
  - `uv run mypy src/atlas` → Success: no issues found in 84 source files, exit 0 · raw: [artifacts/mypy.txt](artifacts/mypy.txt)
  - `make check` → lint, format check, mypy, 1673 passed, exit 0 · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
  - Brief's Verify `uv run atlas data sync --from 2015-01-01 && uv run atlas data status` (on the backfilled lake, 2026-11-02 00:48 UTC = 2026-11-01 New York) → exit 0; every covered key skipped (resume), the one failed key retried (AVB, see below); status as in the criteria below · raw: [artifacts/verify.txt](artifacts/verify.txt), [artifacts/status.txt](artifacts/status.txt)
  - Stage tests strict: `uv run pytest tests/unit/data/test_sync_{state,engine,datasets,status}.py tests/unit/cli -v -W error` → 93 passed, exit 0 · raw: [artifacts/stage-tests-verbose.txt](artifacts/stage-tests-verbose.txt). No network in unit tests: EDGAR is answered by respx from the recorded fixtures, every other source is an in-process fake (`tests/unit/data/sync_helpers.py`); the runs had no allowed network domains.
  - Coverage of the new code: `atlas.data.sync` 96%, `atlas.cli` 91–94%, total 95% · raw: [artifacts/coverage-stage14.txt](artifacts/coverage-stage14.txt)
  - Docstrings: 111 public module-level functions, classes and their public methods in the new/changed modules checked, 0 without a docstring · raw: [artifacts/docstrings-check.txt](artifacts/docstrings-check.txt)
  - Live smoke run (scratch lake, AAPL/MSFT/SPY from 2024-01-01) → exit 0, every step ok · raw: [artifacts/smoke-sync.txt](artifacts/smoke-sync.txt); news probe (50 members × 2024 Q1: 3 519 articles in 57 s) · raw: [artifacts/news-probe.txt](artifacts/news-probe.txt)
  - **Live backfill (Task 3), run by the AI in the background:** (1) `uv run atlas data sync --from 2015-01-01 --datasets universe,prices,fundamentals,macro,events,sentiment,quality` → exit 0, 18:52 → 20:31 UTC (1 h 39 min) · raw: [artifacts/backfill-sync.log](artifacts/backfill-sync.log); (2) `ATLAS__DATA__SYNC__WORKERS__ALPACA=3 uv run atlas data sync --from 2015-01-01 --datasets news,sentiment,quality` → exit 0, 20:33 → 22:49 UTC (2 h 16 min; news 777/777 symbols, 1 171 897 rows fetched, 845 313 unique articles; 845 313 scored) · raw: [artifacts/backfill-news.log](artifacts/backfill-news.log). Step timings: universe 7 s, prices 63 min (777 symbols: 713 ok, 63 empty = delisted at Yahoo, 1 failed), fundamentals 9 min (747: 685 ok, 62 no CIK), macro 61 s (22 series, 523 749 vintage rows), earnings 2 min, upcoming 20 min, macro calendar 6 s, reactions 2.4 min (29 911), news 2 h 16 min, scoring 7 s, quality 27 s (88 issues). Lake: 2.8 GB before news.
  - On merged `main` (`1154d89`, fast-forward from `cef1f8e`, run in the owner's Terminal panel after the sandbox refused it; tree `5ca3294` = branch tree): `uv run pytest -q` → 1673 passed, exit 0; `make check` → exit 0 · raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt)
  - Gate check at the start: `uv run pytest -q` → 1569 passed, 11 deselected, exit 0 · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- Acceptance criteria (brief):
  - [x] `atlas data status` shows ≥ 8 years of prices for ≥ 95% of current members — **as approved by the owner (2026-11-01): counted over the current members that have traded for at least 8 years: 475/475 = 100%.** Raw figure over all 503 current members: 475/503 = 94.4%. Each of the other 28 first traded under its ticker less than 8 years before 2026-11-01 (ABNB, APP, CARR, CEG, COIN, CRWD, DASH, DDOG, DOW, EXE, FDXF, FOX, FOXA, GEHC, GEV, HONA, HOOD, KVUE, MRNA 7.90 y, OTIS, PLTR, Q, RDDT, SNDK, SOLV, UBER, VLTO, VYLR) and has bars from its first trading day to 2026-10-30; none is a data gap. Owner's answer to the question in chat: "Approve listed-8y reading (Recommended)". Evidence: [artifacts/status.txt](artifacts/status.txt) (prices 475/503), [artifacts/price-coverage-misses.txt](artifacts/price-coverage-misses.txt) (first/last bar and years of every miss); logged in the Decisions log
  - [x] fundamentals for ≥ 90% of current members — 502/503 = 99.8% (VYLR, listed 2026-10-01, has filed nothing yet) · [artifacts/status.txt](artifacts/status.txt)
  - [x] all macro series — 22/22 catalog series with observations (523 749 vintage rows) · [artifacts/status.txt](artifacts/status.txt)
  - Also shown (M1 "query 8+ years of … news"): news 503/503 current members with at least one tagged article, 845 313 articles 2015-01-01 → 2026-11-01; events 503/503
- Brief tasks and in-scope items, checked one by one:
  - [x] 1. Sync orchestrator with fake providers; resume-after-crash test — `test_resume_after_crash_fetches_only_the_rest` (engine: crash on chunk 3, re-run fetches only the rest, lake equals a clean run's), `test_resume_after_price_crash` (dataset level: `KeyboardInterrupt` in the 2nd price batch, re-run fetches the remaining 4 symbols, `prices_daily` equals a clean run's), `test_windowed_subwindows_checkpoint_in_order`; live: the Verify re-run skipped every covered key
  - [x] 2. CLI commands + CliRunner tests — 18 tests in `tests/unit/cli/test_data_cmd.py` (`sync`, `update`, `status`, `quality --as-of`, `--persist`, bad arguments exit 2, step error exit 1, interrupt recorded, no-terminal width); `uv run atlas --help` / `atlas data --help` list the commands
  - [x] 3. Live run by the AI in the background, monitored to completion, logs in `artifacts/`, timings above and in the handoff note
  - [x] Typer app `atlas` (entry point `[project.scripts] atlas = "atlas.cli.app:main"`, `test_entry_point_declared`) with `data sync --from … [--datasets …] [--symbols …]`, `update`, `status` (rows, date ranges, last sync, open quality issues: `test_status_prints_tables_and_coverage`, `test_table_summary_rows_keys_dates`, `test_last_sync_per_dataset`, `test_quality_summary_latest_report`), `quality --as-of DATE` (`test_quality_as_of_prints_issues`)
  - [x] Resumable backfill, checkpoint per dataset/symbol chunk in `data/cache/sync_state.json` — `test_state_file_written_under_lake`, `test_runs_all_chunks_and_checkpoints`, state tests (25)
  - [x] Progress bars (Rich), summary table, `DATA_SYNC` audit events — `test_progress_callbacks`, `test_sync_runs_and_prints_summary`, `test_audit_event_per_step`, `test_sync_writes_audit_events` (RUN_STARTED, one DATA_SYNC per step, RUN_FINISHED; chain verifies)
  - [x] Concurrency: thread pool per provider respecting rate limits — `test_chunks_run_concurrently_on_provider_pool`, `test_lake_writes_are_serialised`, `test_workers_see_clock_and_run_context`, `test_throttle_spaces_requests_across_threads`
  - [x] `docs/data.md` (sources, licences/ToS notes, limitations, how to refresh) — `test_data_doc_sections`
- Definition of Done extras: new config keys `data.sync.*` in `config/fund.yaml` with comments and in `AtlasConfig` with defaults (`test_sync_defaults`, `test_sync_validation`, `test_sync_workers_and_chunks_positive`, `test_sync_empty_retry_days`, `test_every_config_key_is_written_in_yaml`, `test_every_yaml_key_has_a_comment`)
- Known and recorded (not criteria): AVB (a former member) has no prices because one Yahoo bar has a null `open` and Stage 07's ingest skips the whole symbol (Backlog, Stage 14); the quality report for 2026-11-02 has 2 critical issues (WBD blocked by DataView)
- Waivers: none (the price criterion is counted under the owner-approved reading above, recorded in the Decisions log)

**Result:** ALL CRITERIA VERIFIED

#### Phase 0 checkpoint follow-up (Tier 1) — 2026-11-02
Owner-requested follow-up (see worklog, 2026-11-02): every writing `atlas data` run now verifies its audit log and mirrors it into the lake's DuckDB `audit_events`; a failed mirror exits 1. Re-run in this chat on the final working tree (base commit `473629f`):
- `uv run pytest tests/unit/cli tests/unit/test_dependency_floors.py -v` → 21 passed, exit 0, including `test_sync_mirrors_audit_log_into_duckdb`, `test_audit_mirror_failure_exit_1` and `test_sync_interrupt_records_crash` (all three FAILED before the change) · raw: [artifacts/checkpoint-2026-11-02-verify.txt](artifacts/checkpoint-2026-11-02-verify.txt)
- `uv run pytest -q` → 1678 passed, 0 failed, 11 deselected (live/llm/integration), exit 0 · raw: [artifacts/checkpoint-2026-11-02-pytest.txt](artifacts/checkpoint-2026-11-02-pytest.txt)
- `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 191 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 84 source files · raw: [artifacts/checkpoint-2026-11-02-lint-type.txt](artifacts/checkpoint-2026-11-02-lint-type.txt)
- `make check` → lint, format check, mypy clean; 1678 passed; exit 0 · raw: [artifacts/checkpoint-2026-11-02-make-check.txt](artifacts/checkpoint-2026-11-02-make-check.txt)
- Machine load during these runs: 5.07 → 5.33 (`uptime`) · raw: [artifacts/checkpoint-2026-11-02-load.txt](artifacts/checkpoint-2026-11-02-load.txt)
- The stage's acceptance criteria are unaffected (no data was re-synced); the CLI tests for `sync`, `update`, `status` and `quality` all pass.
- On merged `main` (`d9cffb6`, fast-forward from `473629f`; run in the owner's Terminal panel, the sandbox cannot write the main checkout): `uv run pytest -q` → 1678 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 191 files already formatted; `mypy src/atlas` → Success (84 files); 1M-row upsert → 0.73 s PASSED; `make check` → exit 0; `git branch --merged main` lists `claude/phase-one-checkpoint-28fcd7`; checkout clean before and after; all 11 commands exit 0 · raw: [stage-06 artifacts/checkpoint-2026-11-02-merged-main-check.txt](../stage-06-storage/artifacts/checkpoint-2026-11-02-merged-main-check.txt) (script: same name, `.sh`)

**Result:** ALL CRITERIA VERIFIED

#### Phase 0 checkpoint follow-up (audit backfill) — 2026-11-02
Owner-requested follow-up (see worklog, 2026-11-02): the 3 runs logged before the Tier 1 mirror existed are now in DuckDB `audit_events`. No code changed, so the live Verify was not re-run.
- One-time mirror in the main checkout: 3 chains valid; 30 events inserted (12, 5, 13); a second run inserted 0; checkout clean · raw: [artifacts/checkpoint-2026-11-02-mirror-old-audit-logs.txt](artifacts/checkpoint-2026-11-02-mirror-old-audit-logs.txt) (script: same name, `.py`)
- `uv run pytest -q` → 2117 passed, 0 failed, 12 deselected, exit 0; `make check` → exit 0 · raw: [artifacts/checkpoint-2026-11-02-followup-verify.txt](artifacts/checkpoint-2026-11-02-followup-verify.txt)
- On merged `main` (`26eb29e`, fast-forward from `ccf61cb`; run in the owner's Terminal panel): `uv run pytest -q` → 2117 passed; Stage 04's Verify → 276 passed, mypy core Success (23 files); `make check` → exit 0; `git branch --merged main` lists `claude/phase-one-checkpoint-28fcd7`; checkout clean before and after · raw: [stage-04 artifacts/checkpoint-2026-11-02-followup-merged-main-check.txt](../stage-04-domain-model/artifacts/checkpoint-2026-11-02-followup-merged-main-check.txt)

**Result:** ALL CRITERIA VERIFIED

#### Phase 0 checkpoint follow-up (CI help test) — 2026-11-02
- `test_help_lists_commands` failed under `GITHUB_ACTIONS=true` before the fix (CI run 2 and locally) and passes now; full suite 2118 passed under `GITHUB_ACTIONS=true` and `FORCE_COLOR=1`; `make check` exit 0 · raw: [artifacts/checkpoint-2026-11-02-ci-help-test.txt](artifacts/checkpoint-2026-11-02-ci-help-test.txt)
- CI run 3 `41505703051` on `f514eb2`: success · raw: [stage-01 artifacts/checkpoint-2026-11-02-ci-run-3-passed.txt](../stage-01-project-scaffolding/artifacts/checkpoint-2026-11-02-ci-run-3-passed.txt)

**Result:** ALL CRITERIA VERIFIED

#### Phase 1 checkpoint (Stages 07–14, M1) — 2026-11-02
Owner request (see worklog, 2026-11-02). Re-run in this chat on `8056137`, before any change:
- `uv run pytest -q` → 2118 passed, 0 failed, 12 deselected, exit 0 · raw: [artifacts/phase1-checkpoint-2026-11-02-pytest.txt](artifacts/phase1-checkpoint-2026-11-02-pytest.txt)
- `uv run pytest tests/unit/data -m live -v -rs` → 11 passed, 0 skipped · raw: [artifacts/phase1-checkpoint-2026-11-02-live.txt](artifacts/phase1-checkpoint-2026-11-02-live.txt)
- `atlas data status` on the main lake (read-only) → exit 0; prices 475/503 raw = 475/475 members listed ≥ 8 years (owner-approved reading), fundamentals 502/503 = 99.8%, macro 22/22 · raw: [artifacts/phase1-checkpoint-2026-11-02-status.txt](artifacts/phase1-checkpoint-2026-11-02-status.txt)
- Acceptance criteria still met (the live backfill was not re-run: nothing it covers changed). Report: [artifacts/phase1-checkpoint-2026-11-02-report.md](artifacts/phase1-checkpoint-2026-11-02-report.md)

**Result:** ALL CRITERIA VERIFIED

#### Phase 1 checkpoint follow-up (Tier 2) — 2026-11-02
- Red before the fix and `atlas data status` on the main lake (read-only): `prices (listed >= 8y)` 475/475 · raw: [artifacts/phase1-checkpoint-2026-11-02-tier2-status.txt](artifacts/phase1-checkpoint-2026-11-02-tier2-status.txt)
- `uv run pytest tests/unit/cli -q` → 20 passed; full suite 2131 passed; `make check` exit 0 · raw: [stage-07 artifacts/phase1-checkpoint-2026-11-02-tier2-verify.txt](../stage-07-price-data/artifacts/phase1-checkpoint-2026-11-02-tier2-verify.txt)
- Acceptance criteria (M1 coverage) still met and now shown directly by the brief's Verify command.

**Result:** ALL CRITERIA VERIFIED

#### Phase 1 checkpoint follow-up (Tier 4) — 2026-11-03
- Raw archive (red first; real-data check) · raw: [artifacts/phase1-checkpoint-2026-11-03-tier4-archive.txt](artifacts/phase1-checkpoint-2026-11-03-tier4-archive.txt)
- `sync` package split: AST comparison 77 definitions, 0 changed · raw: [artifacts/phase1-checkpoint-2026-11-03-tier4-sync-split.txt](artifacts/phase1-checkpoint-2026-11-03-tier4-sync-split.txt)
- `make check` → exit 0 (2160 passed); integration 1 passed; CLI tests 20 passed; `-m live` 12 passed, 1 failed (LM lexicon source: "Google Drive - Quota exceeded", external) · raw: [artifacts/phase1-checkpoint-2026-11-03-tier4-verify.txt](artifacts/phase1-checkpoint-2026-11-03-tier4-verify.txt)
- Acceptance criteria (M1 coverage) unaffected: no lake data changed; the status command is the same code (moved).

**Result:** ALL CRITERIA VERIFIED
- On merged `main` (`98ad034`, fast-forward from `e11886f`; owner's Terminal panel): full suite 2160 passed; integration 1 passed; ruff, format, mypy clean; Verify runs passed; `make check` exit 0; `atlas data status` ran on the real lake; checkout clean · raw: [artifacts/phase1-checkpoint-2026-11-03-tier4-merged-main-check.txt](artifacts/phase1-checkpoint-2026-11-03-tier4-merged-main-check.txt)
