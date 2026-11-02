# Stage 07 — Price data providers (yfinance, Alpaca) · Verification evidence

Fill this in **only** with output from commands actually run in this stage's final chat (Project.md Rule 1). Save full raw outputs under `artifacts/` and quote the key lines here.

#### Verification evidence — Stage 07 — 2026-10-27
- Commands run (this chat, on the final working tree; base commit `e9dca25`):
  - `uv run pytest -q` → 722 passed, 0 failed, 2 deselected (live), exit 0 (627 before the stage + 95 new) · raw: [artifacts/pytest.txt](artifacts/pytest.txt)
  - `uv run ruff check .` → All checks passed! · raw: [artifacts/ruff-check.txt](artifacts/ruff-check.txt)
  - `uv run ruff format --check .` → 91 files already formatted · raw: [artifacts/ruff-format.txt](artifacts/ruff-format.txt)
  - `uv run mypy src/atlas` → Success: no issues found in 47 source files · raw: [artifacts/mypy.txt](artifacts/mypy.txt)
  - `uv run pytest tests/unit/data -q` (brief's Verify) → 95 passed, 2 deselected, exit 0 · raw: [artifacts/verify-tests-unit-data.txt](artifacts/verify-tests-unit-data.txt); verbose: [artifacts/stage-tests-verbose.txt](artifacts/stage-tests-verbose.txt) (95 PASSED)
  - `uv run pytest tests/unit/data -m live -v -rs` (brief's optional Verify) → `test_yfinance_live_smoke` PASSED against real Yahoo; `test_alpaca_live_smoke` SKIPPED ("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set"); exit 0 · raw: [artifacts/verify-live.txt](artifacts/verify-live.txt)
  - `make check` → ruff, format check, mypy clean; 722 passed; exit 0 · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
  - Acceptance tests with `-W error` → 12 passed, exit 0 · raw: [artifacts/acceptance-tests.txt](artifacts/acceptance-tests.txt)
  - Docstring check (`artifacts/docstrings.py`) → 8 modules, 41 public modules/classes/functions/methods; missing: none · raw: [artifacts/docstrings.txt](artifacts/docstrings.txt)
  - Real-data end-to-end check (`artifacts/live_ingest_check.py`, real Yahoo, throwaway lake): two ingests of AAPL/SPY/SIVB for 2020-08-03 → 2020-09-30 each give `rows=84 action_rows=3 ok=('AAPL', 'SPY') failed=('SIVB',) mismatches=0`; AAPL 2020-08-28 raw close 499.2300 → split-adjusted 124.8075; actions AAPL dividend 0.820 (2020-08-07), AAPL split 4.0 (2020-08-31), SPY dividend 1.339 (2020-09-18) · raw: [artifacts/live-ingest-check.txt](artifacts/live-ingest-check.txt)
  - Gate check (start of stage) → `uv run pytest -q` 627 passed, exit 0 · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
  - First full verification run was **not** green: 670 passed, 52 errors (`ValueError: a different schema is already registered as 'prices_daily'` in the Stage 06 storage tests). Root cause and fix (storage test fixture now uses its own registry; no assertion changed) in the worklog, "Verification run 1" · raw: [artifacts/run1-pytest-failed.txt](artifacts/run1-pytest-failed.txt)
  - Note: full-suite runs end with duckdb's `nanobind: leaked …` lines at interpreter shutdown, after the results; exit code 0, results unaffected (known, Backlog "Stage 06").
- Acceptance criteria (brief.md):
  - [x] **Ingest of the fixtures yields correct split-adjusted series.** Evidence: `test_ingest_fixtures_writes_prices_and_actions` (recorded Yahoo fixtures → 42 AAPL + 42 SPY rows; AAPL 2020-08-28 raw close 499.23; actions AAPL dividend + split, SPY dividend); `test_aapl_split_adjusted_series_is_continuous` (split-adjusted close 124.8075 on 2020-08-28 and 129.04 on 2020-08-31: +3.39% across the 4:1 split, where the raw series falls by more than 70%; split factor 0.25 before, 1.0 from the ex-date); `test_aapl_recomputed_matches_vendor_within_tolerance` (our recomputed factor vs Yahoo's adj_close: no session above 0.5%, max deviation < 0.05%); `test_ingest_fixtures_have_no_mismatches`; `test_tampered_vendor_adj_close_is_recorded` (the check catches all 20 pre-split sessions when the vendor's split is removed). All PASSED (`artifacts/acceptance-tests.txt`, `artifacts/stage-tests-verbose.txt`); same result on real Yahoo data (`artifacts/live-ingest-check.txt`).
  - [x] **Re-running is idempotent.** Evidence: `test_reingest_is_idempotent` (second ingest of the same fixtures: identical `prices_daily` and `corporate_actions` frames, same report, still one year partition); `test_reingest_replaces_changed_values` (upsert: a re-fetch replaces the stored row). PASSED. Real data: run 1 and run 2 reports identical (`artifacts/live-ingest-check.txt`).
  - [x] **A failed symbol never aborts the batch.** Evidence: `test_failed_symbol_never_aborts_batch` (vendor error for one symbol → it fails with its reason, the others are written), `test_batch_error_retries_symbol_by_symbol` (calls `(AAPL, BAD)`, then `(AAPL)`, `(BAD)`), `test_invalid_rows_fail_only_that_symbol` (NaN volume), `test_bar_on_non_session_fails_symbol` (Labor Day bar), `test_actions_error_fails_symbol`, `test_delisted_symbol_is_absent_not_an_error` (SIVB) and, in the ingest test, SIVB → `symbols_failed` with "no price data returned". PASSED.
  - In-scope items, each covered: `PriceProvider` protocol with `fetch_daily`/`fetch_actions` (`test_price_base.py`); `YFinanceProvider` batch download, retry with backoff, rate limiting, empty/delisted symbols (`test_price_yfinance.py`: `test_batches_and_throttles`, `test_retry_then_success`, `test_download_keeps_failing_raises_data_error`, `test_delisted_symbol_is_absent_not_an_error`); `AlpacaPriceProvider` data API v2, IEX feed, adjustment raw/all, optional with keys (`test_price_alpaca.py`, respx; `test_from_config_none_without_keys`); `FallbackPriceProvider` (`test_price_fallback.py`: primary empty → secondary, both fail → `DataError`); curated tables `prices_daily` and `corporate_actions` with `available_at` = session close + 15 min from the Stage 05 calendar (`test_tables_are_registered`, `test_available_at_is_close_plus_lag`, `test_available_at_on_early_close`, `test_action_available_at_is_ex_date_bar`, `test_ingested_prices_respect_point_in_time`); recomputed adjustment factor cross-checked against `adj_close`, mismatches > 0.5% recorded (`test_price_adjustment.py`); recorded fixtures for AAPL (2020 split), SPY and a delisted name (SIVB) under `tests/fixtures/prices/` (README there); caching from the Goal / spec §16 (`test_price_cache.py`).
  - Brief tasks 1–7 → plan Tasks 1–4, 6–8, all done (plan.md ticked); task 7's Alpaca live smoke test exists but could not run here (no keys); the brief makes `-m live` optional and it is not an acceptance criterion.
- Waivers: none

**Result:** ALL CRITERIA VERIFIED

#### Follow-up after the post-DONE audit — 2026-10-27
Owner-approved follow-up (audit items 1, 4, 8, 9 fixed; records for items 2, 5, 6 fixed; see worklog "Audit follow-up"). Re-run in this chat on the final working tree (base commit `59c23f2`):
- `uv run pytest -q` → 727 passed, 0 failed, 2 deselected (live), exit 0 (+5 tests) · raw: [artifacts/followup-pytest.txt](artifacts/followup-pytest.txt)
- `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 91 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 47 source files · raw: [artifacts/followup-lint-type.txt](artifacts/followup-lint-type.txt)
- `uv run pytest tests/unit/data -v` (brief's Verify) → 100 passed, 2 deselected, exit 0 · raw: [artifacts/followup-verify-tests-unit-data.txt](artifacts/followup-verify-tests-unit-data.txt); with `-W error` → 100 passed · raw: [artifacts/followup-data-warnings-as-errors.txt](artifacts/followup-data-warnings-as-errors.txt)
- `uv run pytest tests/unit/data -m live -v -rs` → yfinance PASSED (real Yahoo), Alpaca SKIPPED (no keys) · raw: [artifacts/followup-verify-live.txt](artifacts/followup-verify-live.txt)
- `make check` → exit 0 · raw: [artifacts/followup-make-check.txt](artifacts/followup-make-check.txt)
- Docstrings → 8 modules, 44 public names, missing none · raw: [artifacts/followup-docstrings.txt](artifacts/followup-docstrings.txt)
- Real-data ingest check (real Yahoo, twice) → `rows=84 action_rows=3 ok=('AAPL', 'SPY') failed=('SIVB',) mismatches=0` both runs · raw: [artifacts/followup-live-ingest-check.txt](artifacts/followup-live-ingest-check.txt)
- Acceptance criteria unchanged and still evidenced: every test named above PASSED again in `followup-verify-tests-unit-data.txt`.

**Result (follow-up):** ALL CRITERIA VERIFIED

#### On merged `main` — 2026-10-27
`main` fast-forwarded by the owner from `e9dca25` to `c0e529f` (the sandbox blocks this session from writing to the main checkout; reflog: `main@{0}: merge claude/stage-7-planning-implementation-de524c: Fast-forward`). Checks run read-only against the main checkout: Stage 07 worktree venv (same `uv.lock`), `PYTHONPATH=<main>/src` (confirmed `atlas` imported from the main checkout), no bytecode, caches in `$TMPDIR`; `make check` = its four commands run directly.
- `pytest -q` → 727 passed, 2 deselected, exit 0
- `ruff check .` → All checks passed!; `ruff format --check .` → 91 files already formatted; `mypy src/atlas` → Success: no issues found in 47 source files
- `pytest tests/unit/data -q` (brief's Verify) → 100 passed, 2 deselected
- `pytest tests/unit/data -m live` → yfinance PASSED, Alpaca SKIPPED (no keys)
- `git status --short` empty before and after
- raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt)

#### Phase 1 checkpoint follow-up (Tier 1) — 2026-11-02
Owner request (Phase 1 checkpoint; see worklog, 2026-11-02). Run in this chat on the working tree (base `8056137`):
- Red before the fix: 3 new ingest tests failed · raw: [artifacts/phase1-checkpoint-2026-11-02-red-before-fix.txt](artifacts/phase1-checkpoint-2026-11-02-red-before-fix.txt)
- `uv run pytest -q` → 2120 passed, 0 failed, 14 deselected, exit 0; `ruff check .` → All checks passed!; `ruff format --check .` → 206 files already formatted; `mypy src/atlas` → Success (88 files); `make check` → exit 0 · raw: [artifacts/phase1-checkpoint-2026-11-02-verify.txt](artifacts/phase1-checkpoint-2026-11-02-verify.txt)
- `uv run pytest tests/unit/data -q` (brief's Verify) → 936 passed, 13 deselected, exit 0 (same file)
- `uv run pytest tests/unit/data -m live -v -rs` → 13 passed, 0 skipped: `test_alpaca_live_smoke` PASSED (SKIPPED at DONE), new `test_yfinance_live_actions` / `test_alpaca_live_actions` PASSED; mutant check failed both · raw: same file, [artifacts/phase1-checkpoint-2026-11-02-live-actions-mutation.txt](artifacts/phase1-checkpoint-2026-11-02-live-actions-mutation.txt)
- Built Alpaca fixtures vs real responses → 0 problems after the reverse-split field fix · raw: [artifacts/phase1-checkpoint-2026-11-02-alpaca-shape-check.txt](artifacts/phase1-checkpoint-2026-11-02-alpaca-shape-check.txt)
- Real data: AVB via the sync's Alpaca fallback → 1 518 bars 2020-07-27 → 2026-08-14, 0 mismatches · raw: [artifacts/phase1-checkpoint-2026-11-02-avb-real-data.txt](artifacts/phase1-checkpoint-2026-11-02-avb-real-data.txt)
- Acceptance criteria still evidenced: split-adjusted fixtures (`test_aapl_split_adjusted_series_is_continuous` and the others above), idempotence (`test_reingest_is_idempotent`), a failed symbol never aborts the batch (`test_failed_symbol_never_aborts_batch`, `test_symbol_with_only_incomplete_bars_fails_alone`, `test_bar_on_non_session_fails_symbol`): all PASSED in the brief's Verify.

**Result:** ALL CRITERIA VERIFIED
- On merged `main` (`7ae6924`, fast-forward from `8056137`; owner's Terminal panel): `uv run pytest -q` → 2120 passed; integration 1 passed; ruff, format, mypy clean; brief's Verify → 936 passed; `make check` → exit 0; branch merged; checkout clean; AVB re-synced into the main lake (714 price keys) · raw: [artifacts/phase1-checkpoint-2026-11-02-merged-main-check.txt](artifacts/phase1-checkpoint-2026-11-02-merged-main-check.txt)
