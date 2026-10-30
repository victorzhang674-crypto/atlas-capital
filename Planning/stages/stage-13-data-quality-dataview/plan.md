# Stage 13 — Data quality checks & DataView facade · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** DONE (2026-10-30; see [verification.md](verification.md))
- **Gate check:** Stage 12 is DONE (its [verification.md](../stage-12-events-sentiment/verification.md) says **ALL CRITERIA VERIFIED**); `uv run pytest -q` → 1396 passed, 11 deselected, exit 0 (2026-10-30) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution:** written and executed in one go, inline TDD, no subagents (owner's words: "according to the planning files, please firstly generate a plan for stage 13 then implement the plan and execute the tasks in the plan."). The stage `worklog.md` is the ledger; one stage commit (Rule 2A).

**Goal:** one door to all data — `DataView(lake, as_of)` — that is point-in-time by construction and refuses symbols whose data failed a CRITICAL quality check.

**Architecture:** `atlas.data.quality` holds frame-level checks (each returns `QualityIssue`s), `quality_report(lake, as_of)` that runs them on the data known at `as_of` over a window of recent sessions, the persisted `data_quality_issues` table and `blocked_symbols`. `atlas.data.view.DataView` is an immutable, `as_of`-bound facade over the Stage 07–12 readers (`Universe`, `Fundamentals`, `Macro`, `News`, `Events`, `Sentiment`, `Social`, prices/actions read here), with a defensive look-ahead guard on every result, the quality block applied to every method, and a shared LRU cache keyed on `(cutoff, lake generation, method, args)`.

**Tech stack:** pandas / numpy, the Stage 06 `Lake`, Stage 05 `AsOf` / `pit_filter` / `assert_no_lookahead`, Pydantic config.

## Collisions with the overall plan
None found (roadmap row 13, spec §3 principles 2 and 6, §11–§12, protocol §4–§5 checked). Interpretations, also logged in `PROGRESS.md › Decisions log`:

1. **Names with a lake.** The brief's `quality_report(as_of) -> DataFrame` and `DataView(as_of)` need the lake: `quality_report(lake, as_of, *, config=None, persist=False, ...)` and `DataView(lake, as_of, config=None, ...)`. The individual checks are frame-level functions (as Stages 09–12).
2. **Which issues block, and for how long.** A report covers the last `data.quality.window_sessions` (21) sessions whose bars are known at `as_of`. A CRITICAL issue in that report blocks its symbol for that `as_of` day. Issues are recomputed from the lake each day, so fixing the data (a re-ingest) lifts the block.
3. **Split mismatch without the vendor's `adj_close`.** Stage 07's cross-check compares our adjustment with the vendor's `adj_close`. In the lake, `adj_close` is the vendor value *at each bar's fetch time*: incremental ingests mix values from different fetches, so a cross-check over stored bars would flag every later dividend (and it is not point in time; Backlog, Stage 07). So the check uses Stage 07's own adjustment (`adjustment_factors` with the actions known at `as_of`) and the prices themselves. A recorded action whose adjusted return on its session is still an outlier (> `outlier_sigma` σ) does not match the prices. An outlier on a session with no action whose gross move is close to a common split ratio is an unrecorded split. An action `adjustment_factors` rejects (a dividend at or above the prior close) is also a mismatch. All three are `split_mismatch`.
4. **Additions beyond the brief's list of checks.** A `vendor_mix` check (WARNING): more than one vendor in a symbol's window. The Stage 07 Backlog item asks Stage 13 for it. `QualityIssue` also carries `available_at` (when the issue became knowable) for the persisted table's look-ahead guard.
5. **`symbol` for macro issues** is the FRED series id (`macro_gap` issues are about a series, not a ticker); a CRITICAL macro issue (if so configured) drops that series from `DataView.macro`.
6. **"Drops CRITICAL-flagged symbols".** Multi-symbol methods leave blocked symbols out. Single-symbol methods (`fundamentals`, `news`, `sentiment`, `instrument`, …) raise `DataError` naming the issues (fail safe: no silent "no data").
7. **"Strategy and agent code needs nothing else".** Besides the brief's list, DataView also exposes the other PIT queries Stages 07–12 built: `market_news`, `sentiment_daily`, `past_events`, `earnings_reactions`, `social`, `quality_issues`, `blocked`. All of them are thin delegations.
8. **Prices are raw plus our PIT adjustment.** `prices()` returns raw OHLCV and `adj_close` = our total-return adjustment (Stage 07 `adjustment_factors`) using only actions known at `as_of`, relative to each symbol's last bar in the window. The vendor's `adj_close` is never exposed. `returns()` are simple daily total returns from that series on the session grid (a missing bar gives NaN for that session and the next).

## Design decisions

- **D1 Config.** `cfg.data.quality` (`QualityConfig`): `window_sessions` 21; `stale_min_sessions` 5 (consecutive unchanged closes, each with volume > 0); `outlier_sigma` 8.0; `outlier_vol_window` 63 trailing returns; `outlier_min_returns` 20; `outlier_min_daily_vol` 0.005 (σ floor, so a quiet name cannot make every move an outlier); `split_ratios` (2, 3, 4, 5, 8, 10, 15, 20, 1.5) and `split_ratio_tolerance` 0.03; `fundamentals_jump_ratio` 10.0; `macro_max_gap_days` / `macro_max_age_days` per inferred frequency (daily 7/10, weekly 14/21, monthly 45/100, quarterly 135/230, annual 400/550); `severity` per check (critical: `stale_price`, `ohlc_inconsistent`, `nonpositive_price`, `split_mismatch`; warning: `missing_sessions`, `return_outlier`, `fundamentals_jump`, `macro_gap`, `vendor_mix`). `cfg.data.view.cache_size` 256. Every key goes in `config/fund.yaml` with a comment.
- **D2 Issue contract.** `QualityIssue` frozen dataclass: `table`, `symbol`, `date` (the bar's session; for fundamentals the New York date the filing became public; for macro the observation after a gap, or the as-of day for a stale series), `check`, `severity` (`Severity` enum: info / warning / critical), `detail`, `available_at`. `issues_frame(issues)` → `ISSUE_COLUMNS` frame.
- **D3 Table.** `data_quality_issues`: `report_day`, `table`, `symbol`, `date`, `check`, `severity`, `detail`, `available_at`. PK `(report_day, table, symbol, check, date)`, partitioned by year of `report_day`. `quality_report(..., persist=True)` replaces that day's rows (read, drop the day, overwrite), so a re-run never leaves stale issues behind. Issues sharing a key are merged (details joined, highest severity).
- **D4 Window and PIT.** `L` = the latest session whose bar is known at `as_of`. The window is the `window_sessions` sessions ending at `L`. Prices read from `L − (window + vol_window + stale_min + 1)` sessions so that σ and stale runs have history. Only rows known at `as_of` are used (`pit_filter`, then `assert_no_lookahead`). Fundamentals are checked for symbols with a filing that became public inside the window. Macro checks use the latest known vintages.
- **D5 Checks.** `missing_sessions`: sessions between a symbol's first and last bar in the window with no bar (one issue per session). `ohlc_inconsistent`: NaN OHLC, `high < max(open, close, low)`, `low > min(open, close, high)`, or negative volume. `nonpositive_price`: any OHLC ≤ 0. `stale_price`: ≥ `stale_min_sessions` consecutive zero close-to-close changes on bars with volume > 0 (issue on the run's last bar). `return_outlier`: |adjusted return| > `outlier_sigma` × σ, where σ is the std of the previous `outlier_vol_window` returns (at least `outlier_min_returns`), floored at `outlier_min_daily_vol`, on a session with no action. `split_mismatch`: see interpretation 3. `fundamentals_jump`: consecutive quarterly values of a mapped concept (flow quarters from `quarterly_values`, stock balances ~a quarter apart) whose absolute ratio exceeds `fundamentals_jump_ratio` either way (both non-zero), one issue per symbol and filing day. `macro_gap`: frequency inferred from the median spacing of known observation dates; consecutive observations further apart than `macro_max_gap_days` (later one inside the window), and a last observation older than `macro_max_age_days` on the as-of day. `vendor_mix`: > 1 vendor in the window.
- **D6 DataView.** `DataView(lake, as_of: date | AsOf, config=None, *, calendar=None, concept_map=None, catalog=None, lexicon=None)`. Readers are built lazily once and shared with advanced views. Immutable (`__slots__`, `__setattr__` raises). `advance(new_as_of)` returns a new view sharing readers and cache; an earlier `as_of` raises `ValueError`. Methods (lookbacks: `prices`/`returns`/`adv` in sessions, `news`/`social` in calendar days, `macro` a `timedelta` or calendar days):
  `prices(symbols, lookback)`, `returns(symbols, lookback)`, `adv(symbols, lookback=None)` (USD and split-adjusted shares; default `limits.adv_window_days`), `fundamentals(symbol)`, `fundamentals_panel(symbols)`, `macro(series, lookback)`, `news(symbol, lookback=None, limit=None)`, `market_news(...)`, `sentiment(symbol)`, `sentiment_daily(symbol, days=None)`, `events(symbols=None, horizon_days=None)`, `past_events(symbol, n=None)`, `earnings_reactions(symbol, n=None)`, `social(symbol, lookback=None, limit=None)`, `universe(name="sp500")`, `tradable(name="sp500")`, `instrument(symbol)`, `quality_issues()`, `blocked`.
- **D7 Guard.** Every result is checked before it is returned: frames with `available_at` → `assert_no_lookahead` on the rows with a known time; `Evidence` → `as_of < cutoff`. A leak raises `LookAheadError` even if a delegate's filter were bypassed.
- **D8 Cache.** A shared `OrderedDict` LRU (`data.view.cache_size` entries) keyed on `(cutoff, lake.generation, method, normalised args)`; hits return copies (DataFrame/Series `copy()`, lists copied), so callers cannot corrupt it. A write through the same `Lake` object changes `generation` and so misses. The quality report and the instruments are cached the same way.

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

Commands below are run as `UV_CACHE_DIR=$TMPDIR/uv-cache UV_NO_SYNC=1 uv run …` (sandbox).

### Task 1 — Config `data.quality` and `data.view`
- Files: modify `src/atlas/core/config.py`, `config/fund.yaml` · test `tests/unit/core/test_config.py`
- [x] Write failing tests: `test_quality_defaults`, `test_quality_validation` (bad severities, ratio ≤ 1, bad split ratios), `test_view_defaults`
- [x] Run `pytest tests/unit/core/test_config.py -q -k "quality or view"` → FAIL (`AttributeError: 'DataConfig' object has no attribute 'quality'`)
- [x] Add `Severity`-valued `QualitySeverities`, `FrequencyDays`, `QualityConfig`, `ViewConfig`; YAML keys with comments
- [x] Run the config tests (including `test_every_config_key_is_written_in_yaml`, `test_every_yaml_key_has_a_comment`) → PASS

### Task 2 — Issue contract and persisted table
- Files: create `src/atlas/data/quality.py` · test `tests/unit/data/test_quality.py`
- [x] Failing tests: `test_issue_table_registered`, `test_issues_frame_columns_and_types`, `test_issue_rejects_naive_available_at`
- [x] Run → FAIL (`ModuleNotFoundError: atlas.data.quality`)
- [x] Implement `Severity`, `CHECKS`, `QualityIssue`, `ISSUE_COLUMNS`, `DATA_QUALITY_ISSUES`, `issues_frame`
- [x] Run → PASS

### Task 3 — Price checks: missing sessions, OHLC, non-positive, stale, vendor mix
- Files: `src/atlas/data/quality.py` · test `tests/unit/data/test_quality.py`
- [x] Failing tests, each with a synthetic failing case and a clean case: `test_missing_sessions_flags_gap`, `test_missing_sessions_ignores_before_first_bar`, `test_ohlc_inconsistent_cases`, `test_nonpositive_price`, `test_stale_price_run`, `test_stale_price_needs_volume`, `test_stale_price_short_run_ok`, `test_vendor_mix`, `test_clean_bars_have_no_issues`
- [x] Run → FAIL (functions missing)
- [x] Implement `check_missing_sessions`, `check_ohlc`, `check_nonpositive_prices`, `check_stale_prices`, `check_vendor_mix` (vectorised per symbol)
- [x] Run → PASS

### Task 4 — Return outliers and split mismatches
- Files: `src/atlas/data/quality.py` · test `tests/unit/data/test_quality.py`
- [x] Failing tests: `test_return_outlier_flagged`, `test_return_outlier_not_on_action_day`, `test_recorded_split_explains_move`, `test_wrong_split_ratio_is_mismatch`, `test_unrecorded_split_is_mismatch`, `test_bad_dividend_is_mismatch`, `test_sigma_floor`, `test_outlier_needs_history`
- [x] Run → FAIL
- [x] Implement `adjusted_returns`, `check_return_outliers` (returns both checks)
- [x] Run → PASS

### Task 5 — Fundamentals jumps
- Files: `src/atlas/data/quality.py` · test `tests/unit/data/test_quality.py`
- [x] Failing tests: `test_fundamentals_jump_flow_quarter`, `test_fundamentals_jump_balance`, `test_fundamentals_no_jump_below_ratio`, `test_fundamentals_jump_zero_previous_skipped`, `test_fundamentals_jump_only_new_filings`
- [x] Run → FAIL
- [x] Implement `check_fundamentals_jumps(facts, as_of, since, concept_map, config, jump_ratio)`
- [x] Run → PASS

### Task 6 — Macro gaps
- Files: `src/atlas/data/quality.py` · test `tests/unit/data/test_quality.py`
- [x] Failing tests: `test_infer_frequency`, `test_macro_internal_gap`, `test_macro_stale_tail`, `test_macro_regular_series_ok`, `test_macro_gap_uses_known_vintages_only`
- [x] Run → FAIL
- [x] Implement `infer_frequency`, `check_macro_gaps`
- [x] Run → PASS

### Task 7 — `quality_report`, persistence and the CRITICAL block
- Files: `src/atlas/data/quality.py` · test `tests/unit/data/test_quality.py`
- [x] Failing tests: `test_report_finds_each_table`, `test_report_window_limits_issues`, `test_report_ignores_unknown_rows` (a zero bar dated the as-of day is not seen at 08:00), `test_report_severity_from_config`, `test_blocked_symbols_only_critical`, `test_persist_replaces_day`, `test_persist_empty_report_clears_day`, `test_read_issues_guard`, `test_report_empty_lake`, `test_merge_duplicate_keys`
- [x] Run → FAIL
- [x] Implement `latest_known_session`, `quality_report`, `blocked_symbols`, `read_quality_issues`, `_persist`
- [x] Run → PASS

### Task 8 — DataView core: construction, immutability, advance, cache, guard
- Files: create `src/atlas/data/view.py` · test `tests/unit/data/test_view_pit.py`, helpers `tests/unit/data/view_helpers.py`
- [x] Failing tests: `test_view_as_of_from_date_and_asof`, `test_view_rejects_datetime`, `test_view_is_immutable`, `test_advance_returns_new_view`, `test_advance_backwards_raises`
- [x] Run → FAIL (`ModuleNotFoundError: atlas.data.view`)
- [x] Implement `DataView` skeleton, `_LRU`, `_guard_frame`, `_guard_evidence`
- [x] Run → PASS

### Task 9 — Price methods: prices, returns, adv
- Files: `src/atlas/data/view.py` · test `tests/unit/data/test_view_pit.py`
- [x] Failing tests: `test_prices_window_and_columns`, `test_prices_adjusted_with_known_split_only`, `test_returns_known_answer`, `test_returns_missing_bar_is_nan`, `test_adv_known_answer`, `test_adv_shares_split_adjusted`, `test_lookback_validation`
- [x] Run → FAIL
- [x] Implement the three methods
- [x] Run → PASS

### Task 10 — Delegated methods
- Files: `src/atlas/data/view.py` · test `tests/unit/data/test_view_pit.py`
- [x] Failing tests: `test_fundamentals_matches_reader`, `test_fundamentals_panel_matches_reader`, `test_macro_matches_reader`, `test_news_matches_reader`, `test_market_news`, `test_sentiment_matches_reader`, `test_events_matches_reader`, `test_past_events_and_reactions`, `test_social_matches_reader`, `test_universe_and_tradable`, `test_instrument`, `test_quality_issues_and_blocked`
- [x] Run → FAIL
- [x] Implement the delegations and the block (multi-symbol: absent; single-symbol: `DataError`)
- [x] Run → PASS

### Task 11 — Leak suite (brief task 4) and blocked-symbol suite
- Files: `tests/unit/data/test_view_pit.py`, `tests/unit/data/view_helpers.py`
- [x] World builder: two lakes from one function — `clean` (known data only) and `dirty` (plus rows known only after the cutoff in **every** table: a bar on the as-of day and later, a zero bar on the as-of day, a future split, a late restating filing and a new quarter, a later macro vintage and observation, articles and posts after the cutoff, an 8-K and a reaction after the cutoff, a macro event announced later, an index addition after the day)
- [x] Parametrised over every DataView method: `test_future_rows_never_leak[method]` (dirty == clean at the as-of; every timestamp before the cutoff), `test_injected_rows_are_visible_later[method]` (the same rows change the answer once known, so the suite is not vacuous), `test_guard_raises_if_filter_bypassed[method]` (a delegate patched to leak → `LookAheadError`), `test_blocked_symbol_absent[method]`
- [x] Run → PASS (any failure is a bug in view/quality, fixed test-first)

### Task 12 — Caching and immutability (brief task 5)
- Files: `tests/unit/data/test_view_pit.py`
- [x] Tests: `test_cache_hit_reads_lake_once`, `test_cache_keyed_by_args_and_as_of`, `test_cache_returns_copies`, `test_cache_lru_eviction`, `test_cache_misses_after_write`, `test_advanced_view_shares_cache`
- [x] Run → PASS (red first where behaviour is new)

### Task 13 — Docs, docstrings and verification records
- Files: `docs/data-limitations.md › Data quality and DataView (Stage 13)`, `Planning/PROGRESS.md`, stage `worklog.md`, `verification.md`, `artifacts/`
- [x] Docstring check on the new modules (none missing)
- [x] Full verification (`pytest -q`, ruff, format, mypy, brief's Verify, `-v -W error`, coverage, no-network run, `make check`); acceptance criteria one by one
- [x ] Records, commit `stage-13: Data quality checks & DataView facade`, merge into `main`, merged-main checks, records-only follow-up commit

## Addendum — post-DONE audit (2026-10-30)

Owner's request: "can you help me to audit this stage by comparing the work that you have currently done with the plan you have made?", then "Yes, fix and record". The audit found every task, design decision, brief criterion and Definition-of-Done item met, plus four findings:

- **A1 Two per-row Python loops** (protocol §4: vectorise; no Python loops over tickers × dates) in `src/atlas/data/quality.py`: `_in_window` filters bars with `Series.map(lambda d: d >= since)`, and `check_fundamentals_jumps` finds the facts known since the window start with a list comprehension of `market_date(...)`. Fix: a vectorised comparison of the `date` column, and of `available_at` with 00:00 New York of `since` (the same set: `market_date(t) >= since` ⇔ `t >= since 00:00 New York`). Behaviour is unchanged, so the existing tests are the guard (`test_missing_sessions_ignores_before_first_bar_and_outside_window`, `test_vendor_mix`, `test_ohlc_inconsistent_cases`, `test_fundamentals_jump_only_new_filings`, the whole leak suite); time the two before and after on 500 symbols.
- **A2 Unlogged test rename:** the plan's `test_missing_sessions_ignores_before_first_bar` (Task 3) was written as `test_missing_sessions_ignores_before_first_bar_and_outside_window` (it also covers a gap before the window). Record it.
- **A3 Stale plan text:** interpretation 3 and D5 give the first split-mismatch rule. As executed (Task 4 ruling): on an action session, a `split_mismatch` needs the adjusted return to be an outlier **and** the session to have a split or the raw return not to be an outlier; a real move on an ex-dividend day is not reported. This addendum is the amendment.
- **A4 Tests beyond the plan's lists** (additions, none replacing a planned test): `test_severity_order`, `test_latest_known_session`, `test_adv_default_window`, `test_returns_include_dividends`, `test_symbols_must_be_a_sequence`, `test_methods_cover_the_public_api`, `test_every_method_has_a_guard_test`, `test_every_method_has_a_block_test`, `test_blocked_symbol_raises`, `test_blocked_symbol_present_without_the_block`, `test_macro_drops_blocked_series`, `test_view_is_read_only`, `test_limitations_doc_has_stage_13_section` (and the renamed test of A2). Record them.

### Task A — fix and record
- [x ] Time `_in_window` and the new-facts filter on 500 symbols before the change
- [x ] Vectorise both; run `tests/unit/data/test_quality.py` and `tests/unit/data/test_view_pit.py` → PASS; time again
- [x ] Full verification (`pytest -q`, ruff, format, mypy, brief's Verify, `make check`) → raw output in `artifacts/audit-*.txt`
- [x ] Worklog, Decisions log, verification.md and PROGRESS.md entries; commit `stage-13: post-DONE audit fixes`; fast-forward `main`; merged-main checks; records-only commit `stage-13: audit merged-main records`; fast-forward `main`
