# Stage 14 — Data sync CLI & bootstrap backfill · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** DONE (2026-11-01; see [verification.md](verification.md))
- **Gate check:** Stage 13 is DONE (its [verification.md](../stage-13-data-quality-dataview/verification.md) says **ALL CRITERIA VERIFIED**); `uv run pytest -q` → 1569 passed, 11 deselected, exit 0 (2026-11-01) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution:** written and executed in one go, inline TDD, no subagents (owner's words: "According to the planning markdown files, please help me to generate a fully broken-down stage 14 plan then implement the plans. Aim for full completion for this stage."). The stage `worklog.md` is the ledger; one stage commit (Rule 2A).

**Goal:** one command backfills and incrementally updates the whole lake (`atlas data sync` / `atlas data update`), one shows its health (`atlas data status`), one runs the quality report (`atlas data quality`). This completes **M1**.

**Architecture:** `atlas.data.sync` is the orchestrator. A sync is an ordered list of *steps* (universe → prices → fundamentals → macro → news → events → sentiment → quality). Each step names the keys it covers (symbols, series ids or one pseudo-key), its provider (whose thread pool runs it) and how it fetches a chunk of keys over a date window; every step delegates to the Stage 07–13 ingest function. A JSON checkpoint `<lake>/cache/sync_state.json` records, per step and key, the covered date window; a re-run plans only what is not covered, so a crashed backfill resumes and `update` is incremental. `atlas.cli` is a Typer app (`atlas` entry point) whose `data` group renders progress bars and tables with Rich and writes `DATA_SYNC` audit events.

**Tech stack:** Typer 0.27 + Rich 15 (new runtime deps), `concurrent.futures.ThreadPoolExecutor`, the Stage 06 `Lake` (+ in-memory DuckDB for status aggregates), Stage 03 `run_context` / `AuditLog`, the Stage 07–13 ingest functions.

## Collisions with the overall plan
None found (roadmap row 14 and milestone M1, spec §3, §10–§12 (`cli/` = "typer app", "CLI / UI: Typer + Rich"), protocol §4–§5 checked). Interpretations, also logged in `PROGRESS.md › Decisions log`:

1. **Datasets.** The brief's `--datasets prices,fundamentals,macro,news,events` is extended with `universe` (the Stage 08 refresh: the sync needs the S&P 500 membership to know which symbols to backfill), `sentiment` (Stage 12: lexicon, news scoring, social posts when enabled) and `quality` (Stage 13: persist the day's report; Stage 13 Backlog). `events` covers the Stage 12 wiring list: actual earnings (EDGAR), scheduled earnings/ex-dividend snapshot (Yahoo), the macro calendar (FRED) and earnings reactions (from prices). The default is all eight, in that order.
2. **Symbols.** Without `--symbols`: every S&P 500 member at any time in `[from, to]` (survivorship-free, from `index_membership`) plus the tickers of every ticker-list universe (`macro_etfs`). EDGAR steps (fundamentals, earnings) take the equities only; the scheduled-events snapshot takes the members on the run's end date only (a delisted name has no scheduled dates), and a chunk Yahoo fails is retried one symbol at a time. `--symbols` replaces the list for every symbol step.
3. **Checkpoint = coverage.** `sync_state.json` holds, per step and key, the covered window `[from, to]`, `synced_at` and `status` (`ok` / `empty`). This is the brief's "checkpoint per dataset/symbol chunk": it is written (atomically) after every finished chunk. Windowed steps (prices, news, news scoring, social) fetch only what is not covered (`[to + 1 − update_overlap_days, end]`, or the whole window if the request starts before `from`); snapshot steps (universe, fundamentals, macro, earnings, scheduled events, macro calendar, reactions, quality) refetch a key whose snapshot is older than the run's end date.
4. **`update`** = a sync from the earliest covered `from` in the state (default `data.sync.default_start`) to today: coverage turns it into an incremental fetch from each key's last covered date; new index members are backfilled in full. Without a state file it refuses ("run `atlas data sync` first").
5. **Concurrency.** One thread pool per provider (`yahoo`, `sec`, `fred`, `alpaca`, `wikipedia`, `local`), sized by `data.sync.workers`; chunks of a step run on its provider's pool. Rate limits hold across threads because `Throttle.wait` becomes thread-safe (a lock spaces request starts by `min_interval`; Stage 07 code, one small change). Lake writes are serialised by a lock (`Lake` supports one writer at a time). `yahoo` defaults to 1 worker: `yfinance.download` keeps module-level state and is not thread-safe.
6. **Empty vs failed keys.** A key with no data at the vendor (prices "no price data returned", fundamentals/earnings "no CIK" or "no mapped facts") is `empty`: covered, never retried by `sync`, shown in the summary. A vendor error leaves the key uncovered, so the next run retries it. An exception other than `DataError` / `ConfigError` (a bug, Ctrl-C) stops the run at once; finished chunks stay checkpointed.
7. **A step that cannot run** (missing API key: `ConfigError`; the source page fails: `DataError`) is reported as an error and the sync goes on with the other steps (steps that need it fall back to what the lake holds); `atlas data sync` then exits 1.
8. **Status coverage** (acceptance criterion): current members = `Universe.members(today)`; "≥ 8 years of prices" = first and last stored bar at least `data.sync.status_min_price_years` × 365.25 days apart; "fundamentals" = at least one stored fact; "macro" = every catalog series has observations. `status` also shows rows, keys, first/last date per table, last sync per dataset and the latest persisted quality report (issues by severity, blocked symbols).
9. **News scope.** News is fetched for the symbol list (ETFs included, so SPY/QQQ-tagged market news is covered); untagged market-wide news is not backfilled (Alpaca's untagged stream is the whole feed). Raw vendor payload archiving (Stage 07 Backlog) stays in the Backlog.

## Design decisions

- **D1 Config.** `cfg.data.sync` (`SyncConfig`): `default_start` 2015-01-01; `state_file` `cache/sync_state.json` (relative to the lake root); `update_overlap_days` 5; `empty_retry_days` 30 (added during Task 9: a windowed `empty` key, e.g. a delisted name, is re-checked for new days only that often); `news_window_days` 90 (news sub-window per checkpoint); `sentiment_window_days` 365; `status_min_price_years` 8.0; `universe` `sp500` (the index universe synced); `workers` (`SyncWorkers`: yahoo 1, sec 4, fred 2, alpaca 2, wikipedia 1, local 1); `chunk_size` (`SyncChunks`: prices 50, fundamentals 20, macro 2, news 50, earnings 20, upcoming 50, social 20). YAML in `config/fund.yaml › data.sync` with comments.
- **D2 State file.** `{"format": 1, "steps": {step: {key: {"from", "to", "synced_at", "status"}}}, "runs": [...]}` (dates ISO, `synced_at` UTC ISO from the clock). `SyncState.load(path)`: missing → empty; unreadable / wrong format → `DataError` (never silently restart a backfill). `save()` uses the Stage 06 `atomic_write`. Thread-safe (`record_chunk` under a lock). The last 50 runs are kept (command, run id, start/end, ok).
- **D3 Planning.** `plan_window(coverage, start, end, overlap)` → the window still to fetch for one key (or `None`); `plan_snapshot(coverage, end)` → whether to refetch. `group_chunks(windows, size)` groups keys with the same window into chunks of `size`, in key order.
- **D4 Steps.** `StepSpec(name, dataset, provider, mode: "window" | "snapshot", window_days | None, fetch)`; `fetch(keys, start, end) -> ChunkOutcome(rows, ok, empty, failed: {key: reason})`. A windowed chunk is cut into sub-windows of `window_days`, run in order inside one worker and checkpointed after each sub-window; a key that fails stops there (an `empty` one goes on: a later window may have data). Dependencies run in order (prices before reactions, news before scoring).
- **D5 Orchestrator.** `run_sync(lake, config, sources, *, start=None, end=None, datasets=None, symbols=None, command="sync", progress=None, audit=None) -> SyncReport`. `end` defaults to the clock's New York date; price windows end at the latest session whose bar is published (no partial bars). `SyncReport.steps: tuple[StepReport, ...]` with `planned`, `skipped` (already covered), `ok`, `empty`, `failed` (+ reasons), `rows`, `chunks`, `seconds`, `error`. One `DATA_SYNC` audit event per step (payload = the step report). `progress` gets `start_step(step, total)`, `advance(step, n)`, `end_step(report)`.
- **D6 Sources.** `SyncSources` holds lazy factories (`price_provider`, `edgar`, `cik_map`, `fred`, `calendar_fred`, `news_provider`, `upcoming_provider`, `social_providers`, `universe_pages`, `lexicon`); `SyncSources.live(cfg, lake)` builds the Stage 07–12 clients (keys only from `cfg.secrets`). A factory is called only when its step runs. Clients with `close()` are closed at the end.
- **D7 Status.** `lake_status(lake, cfg, *, as_of=None) -> LakeStatus(as_of, tables, members, coverage, quality_day, quality, blocked, last_run, note)` (the state is read from the lake; as built): aggregates with an in-memory DuckDB over the Parquet files (no `atlas.duckdb` written, safe while a sync runs); coverage per D8 above.
- **D8 CLI.** `atlas [--profile dev] [--config-dir config] data sync [--from D] [--to D] [--datasets a,b] [--symbols A,B]`, `data update [--datasets] [--symbols]`, `data status`, `data quality [--as-of D] [--persist] [--limit N]`. Each command loads the config, opens the lake (`cfg.data.lake_path`, relative to the working directory) and runs under `use_clock(LiveClock())`. The writing commands (`sync`, `update`, `quality --persist`) also open a top-level `run_context`, log JSON to `<lake>/runs/<run_id>/<command>.log` (console: warnings) and write `RUN_STARTED` / `DATA_SYNC` / `RUN_FINISHED` to the run's audit log; `status` and `quality` without `--persist` only read (as built). Exit codes: 0 ok, 1 a step errored, 2 bad arguments (Typer).

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

Commands below are run as `UV_CACHE_DIR=$TMPDIR/uv-cache-s14 UV_NO_SYNC=1 uv run …` (sandbox). Fakes live in `tests/unit/data/sync_helpers.py`.

### Task 1 — Dependencies and `SyncConfig`
- Files: modify `pyproject.toml` (typer, rich; `[project.scripts] atlas = "atlas.cli.app:main"`), `src/atlas/core/config.py`, `config/fund.yaml` · test `tests/unit/core/test_config.py`
- [x] `uv add "typer>=0.12" "rich>=13.7"` (done at set-up: typer 0.27.3, rich 15.0.0 locked)
- [x] Write failing tests `test_sync_defaults`, `test_sync_validation` (workers ≥ 1, chunk sizes ≥ 1, window days ≥ 1, overlap ≥ 0, years > 0)
- [x] Run `uv run pytest tests/unit/core/test_config.py -q -k sync` → FAIL (`DataConfig` has no `sync`)
- [x] Add `SyncWorkers`, `SyncChunks`, `SyncConfig`, `DataConfig.sync`; YAML block with a comment per key
- [x] Run → PASS; `uv run pytest tests/unit/core/test_config.py -q` → PASS (YAML/comment coverage tests included)

### Task 2 — Thread-safe `Throttle`
- Files: modify `src/atlas/data/providers/base.py` · test `tests/unit/data/test_price_base.py`
- [x] Write failing test `test_throttle_spaces_requests_across_threads` (8 threads × 3 `wait()` with `time.sleep`, 0.02 s: consecutive return times ≥ 0.02 s − ε apart) and `test_throttle_first_call_does_not_wait`
- [x] Run → FAIL (threads pass the throttle together)
- [x] Hold a lock across the pause in `wait()`
- [x] Run → PASS; `uv run pytest tests/unit/data -q -k "price"` → PASS

### Task 3 — Sync state and planning
- Files: create `src/atlas/data/sync.py` · test `tests/unit/data/test_sync_state.py`
- [x] Tests: `test_state_missing_file_is_empty`, `test_state_round_trip`, `test_state_corrupt_file_raises`, `test_state_wrong_format_raises`, `test_state_save_is_atomic` (a failing write leaves the old file), `test_record_chunk_merges_contiguous_windows`, `test_record_chunk_thread_safe`, `test_runs_are_capped`, `test_plan_window_cases` (no coverage, covered, partial with overlap, request starts earlier, overlap never before start), `test_plan_snapshot_cases`, `test_group_chunks_by_window_and_size`, `test_min_from`
- [x] Run `uv run pytest tests/unit/data/test_sync_state.py -q` → FAIL (no module)
- [x] Implement `Coverage`, `SyncState`, `plan_window`, `plan_snapshot`, `group_chunks`
- [x] Run → PASS

### Task 4 — Orchestrator core (generic steps)
- Files: modify `src/atlas/data/sync.py` · test `tests/unit/data/test_sync_engine.py`
- [x] Tests with pure fake steps: `test_runs_all_chunks_and_checkpoints`, `test_resume_after_crash_fetches_only_the_rest` (a fetch raising `RuntimeError` on chunk 3; second run fetches chunks 3.. only; lake equals a clean run's), `test_failed_keys_are_retried_next_run`, `test_empty_keys_are_not_retried`, `test_windowed_subwindows_checkpoint_in_order`, `test_failed_key_stops_its_later_subwindows`, `test_chunks_run_concurrently_on_provider_pool` (workers 3 → >1 chunk in flight; same lake as workers 1), `test_lake_writes_are_serialised`, `test_progress_callbacks`, `test_audit_event_per_step`, `test_step_error_reported_and_others_run` (`ConfigError` in a factory)
- [x] Run → FAIL
- [x] Implement `StepSpec`, `ChunkOutcome`, `StepReport`, `SyncReport`, `run_steps`, the locked lake, progress protocol, audit payload
- [x] Run → PASS

### Task 5 — Dataset steps and `run_sync` / `run_update`
- Files: modify `src/atlas/data/sync.py` · create `tests/unit/data/sync_helpers.py` · test `tests/unit/data/test_sync_datasets.py`
- [x] Fake world: tiny Wikipedia pages (3 members, one removed in the window, one added), `FakePriceProvider` bars for them + SPY/QQQ, EDGAR via respx over the recorded AAPL/MSFT fixtures, FRED via respx over the CPIAUCSL/DGS10 fixtures (two-series catalog), a fake calendar FRED, `FakeNewsProvider`, a fake upcoming provider, the test lexicon
- [x] Tests: `test_sync_all_datasets_fills_every_table`, `test_symbols_are_members_in_window_plus_etfs`, `test_explicit_symbols_replace_the_list`, `test_equity_only_steps_skip_etfs`, `test_second_sync_skips_everything`, `test_resume_after_price_crash` (provider raises `KeyboardInterrupt` on the 2nd batch; re-run fetches only the rest; lake equals a clean run), `test_update_fetches_only_new_days` (+ overlap), `test_update_backfills_new_member`, `test_update_without_state_refuses`, `test_price_end_capped_at_published_bar`, `test_missing_key_step_errors_others_run`, `test_news_scoring_follows_news_backfill`, `test_quality_report_persisted`, `test_unknown_dataset_rejected`, `test_reference_refreshed_after_prices` (instrument trade dates filled)
- [x] Run → FAIL
- [x] Implement `SyncSources` (+ `live`), symbol resolution, the step adapters, `run_sync`, `run_update`, `DATASETS`
- [x] Run → PASS; `uv run pytest tests/unit/data -q` → PASS

### Task 6 — Status and quality summaries
- Files: modify `src/atlas/data/sync.py` · test `tests/unit/data/test_sync_status.py`
- [x] Tests (synthetic lake with known answers): `test_table_summary_rows_keys_dates`, `test_last_sync_per_dataset`, `test_price_coverage_counts_eight_years` (members with 9, 8, 7.9 years and none → 2/4), `test_fundamentals_and_macro_coverage`, `test_quality_summary_latest_report`, `test_status_on_empty_lake`, `test_status_writes_nothing`
- [x] Run → FAIL
- [x] Implement `lake_status`, `LakeStatus`, `CoverageLine`
- [x] Run → PASS

### Task 7 — CLI (`atlas data …`)
- Files: create `src/atlas/cli/app.py`, `src/atlas/cli/data_cmd.py`, modify `src/atlas/cli/__init__.py` · test `tests/unit/cli/test_data_cmd.py` (+ `tests/unit/cli/conftest.py` putting `tests/unit/data` on `sys.path` for the shared fakes)
- [x] Tests (`typer.testing.CliRunner`, fake sources patched into `data_cmd.build_sources`, lake via `ATLAS__DATA__LAKE_PATH`): `test_help_lists_commands`, `test_sync_runs_and_prints_summary`, `test_sync_writes_audit_events` (RUN_STARTED, one DATA_SYNC per step, RUN_FINISHED; chain verifies), `test_sync_bad_dataset_exit_2`, `test_sync_bad_date_exit_2`, `test_sync_step_error_exit_1`, `test_update_incremental`, `test_update_without_state_exit_1`, `test_status_prints_tables_and_coverage`, `test_quality_as_of_prints_issues`, `test_quality_persist`, `test_entry_point_declared` (pyproject script → `atlas.cli.app:main`)
- [x] Run → FAIL
- [x] Implement the Typer app, Rich progress (`RichProgress` implementing the progress protocol), summary/status/quality tables
- [x] Run → PASS; `uv sync` once so `uv run atlas --help` works → prints the `data` group

### Task 8 — `docs/data.md`
- Files: create `docs/data.md` · test `tests/unit/test_repo_docs.py`
- [x] Failing test `test_data_doc_sections` (sources, licences/ToS, limitations, how to refresh; mentions every dataset name and `sync_state.json`)
- [x] Write the doc (sources and licences/ToS notes per vendor, limitations with a link to `docs/data-limitations.md`, refresh/resume how-to, the state file)
- [x] Run → PASS

### Task 9 — Live backfill (Rule 6: run by the AI, in the background)
- [x] Copy the root `.env` (done at set-up) and the LM lexicon (`data/reference/lm_lexicon.csv`) into the worktree
- [x] Smoke run: `uv run atlas data sync --from 2024-01-01 --symbols AAPL,MSFT,SPY` on a scratch lake (`ATLAS__DATA__LAKE_PATH`) → every step ok; fix what breaks (test first)
- [x] Full backfill in the background: `uv run atlas data sync --from 2015-01-01` (lake `data/`), log to `artifacts/backfill-sync.log`; monitor to completion; resume after any interruption with the same command
- [x] `uv run atlas data status` → `artifacts/status.txt`; record timings per step in the worklog and handoff note
- [x] Check the acceptance numbers: prices ≥ 8 years for ≥ 95% of current members, fundamentals ≥ 90%, all macro series

### Task 10 — Full verification, records, commit, merge
- [x] `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/atlas`, `make check` → `artifacts/`
- [x] Brief's Verify: `uv run atlas data sync --from 2015-01-01 && uv run atlas data status` (resumes on the backfilled lake) → `artifacts/verify.txt`
- [x] Docstrings with units on new public API; no network in unit tests (`-p no:cacheprovider` with the network blocked by the sandbox); new config keys documented
- [x] `verification.md` (ALL CRITERIA VERIFIED only if every criterion has evidence), `PROGRESS.md` (status, Decisions log, Backlog, handoff note), tick the box
- [x] Review the staged diff, commit `stage-14: Data sync CLI & bootstrap backfill`, ff-merge into `main`, re-run `uv run pytest -q` and `make check` on merged `main`, record hashes
