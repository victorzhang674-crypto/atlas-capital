# Stage 15 — Technical feature library · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** DONE (2026-11-02; see [verification.md](verification.md))
- **Gate check:** Stage 14 is DONE (its [verification.md](../stage-14-data-cli/verification.md) says **ALL CRITERIA VERIFIED**); `uv run pytest -q` → 1673 passed, 11 deselected, exit 0 (2026-11-02) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution:** written and executed in one go, inline TDD, no subagents (owner's words: "According to the planning markdown files, please help me to generate a fully broken-down stage 15 plan then implement the plans. Aim for full completion for this stage."). The stage `worklog.md` is the ledger; one stage commit (Rule 2A).

**Goal:** a vectorised, tested library of technical features computed from `DataView` (point-in-time), for the quant models (Stages 17–23) and, as a per-symbol `TechnicalSnapshot`, for the Technical Analyst agent (Stage 30).

**Architecture:** `atlas.quant.features.registry` holds the feature registry (`@feature(name, lookback, kind, unit=...)`), the `PricePanel` (wide date × symbol frames of adjusted OHLC, dollar volume, returns, the benchmark's returns) and the compute plumbing: `compute_panel(panel, names)` is pure (no data access); `compute_features(view, symbols, names, history=, layout=)` asks the view for exactly `max lookback + buffer + history − 1` sessions, builds the panel and calls `compute_panel`. `technical.py` registers the 40 features (column-wise numpy/pandas, one time-loop for the recursive smoothers, vectorised across symbols). `snapshot.py` builds `TechnicalSnapshot` (latest values + percentile vs own history + vs universe).

**Tech stack:** pandas 3 / numpy (no new dependency: TA-Lib is not installed and is not added; reference values come from an independent loop-based implementation in the tests, written to fixture CSVs), Pydantic v2 (`AtlasModel`) for the snapshot contract, the Stage 13 `DataView`.

## Collisions with the overall plan
None found (roadmap row 15 (depends on 13), Phase 2 / M2, spec §3 (principles 2, 6, 7), §11 (pandas/numpy), §12 (`quant/ features/`), protocol §4 (vectorise; nothing hard-coded; units in docstrings), §5 (`tiny_universe` fixture, known-answer maths, look-ahead tests)). Interpretations, also logged in `PROGRESS.md › Decisions log`:

1. **Reference values.** The brief allows "hand-computed or TA-Lib-reference values". TA-Lib is not a dependency and needs a C library; the reference is an independent, plain-Python, loop-per-symbol implementation of the same definitions (`tests/unit/quant/features/reference.py`), run once on a short known series to write `tests/fixtures/features/expected.csv` (inputs in `ohlcv.csv`). A test re-runs the reference and checks the CSV is current. Small hand-computed cases (pen-and-paper values) are added per feature group. Recursive smoothers follow TA-Lib's conventions (EMA/Wilder seeded with the SMA of the first `n` values).
2. **`kind="ts"|"xs"`.** `ts` = a symbol's value depends only on its own bars (and the benchmark's); `xs` = it may depend on the requested cross-section. All 40 brief features are `ts` (a test enforces invariance to the symbol set); `xs` plumbing is tested with a test-local feature. Cross-sectional comparison for agents is the snapshot's universe percentile.
3. **Lookback.** A feature's `lookback` = the sessions of bars it needs for its first value (e.g. `ret_21d` 22, `ema_200` 200, `macd_signal` 34); a test checks that the first non-NaN value is exactly at row `lookback − 1` for every feature. "The view loads exactly `lookback + buffer` sessions": `compute_features` asks for `max(lookback) + quant.features.buffer_sessions + history − 1` sessions (test with a spy on `DataView.prices`). Recursive features (EMA, Wilder ATR/RSI, MACD) depend on where the loaded window starts; the buffer is their warm-up (documented).
4. **Units and adjustment.** Prices are adjusted with the view's `adj_close` (actions known at `as_of`; Stage 07 Backlog item honoured): factor `f = adj_close / close` scales open/high/low too. Volume features use raw dollar volume `close × volume` (USD; split-invariant). Returns are simple daily; vols are annualised with `quant.features.sessions_per_year` (252).
5. **Benchmark.** Beta and idiosyncratic vol use `fund.benchmark` (SPY; spec A9). A blocked or missing benchmark gives NaN (fail safe), never a guess.
6. **Missing bars.** Rows are the trading sessions (not the dates present), so a missing bar is NaN. Window statistics need every value in their window (NaN until the gap leaves the window); recursive smoothers keep their state across a missing input and output NaN on that date. Blocked symbols are left out of the result (DataView rule).
7. **Snapshot universe.** "vs universe" = the members of `quant.features.snapshot.universe` (`sp500`) at `as_of` plus the requested symbols; features in USD (MAs, ATR, MACD) are not comparable across symbols, so their universe percentile is `None` (own-history percentile still given).
8. **Out of scope kept out:** robust handling of bad bars older than the quality window (Stage 13 Backlog: "Stage 15 may want") is not in the brief's In scope → stays in Backlog; no persistence of features in the lake (not asked; Stage 16 persists factors).

## Design decisions

- **D1 Config.** New top-level section `quant` (`QuantConfig`) with `features` (`FeaturesConfig`): `buffer_sessions` 63, `sessions_per_year` 252, `snapshot` (`SnapshotConfig`: `history_sessions` 252, `min_history` 63, `universe` `sp500`, `min_universe` 20). YAML block in `config/fund.yaml` with a comment per key; `quant` added to the loader's sections.
- **D2 `DataView.sessions(lookback)`** (Stage 13 change, one public method): the last `lookback` sessions whose bars are known at `as_of`, oldest first (what `prices` uses). Added to the Stage 13 leak suite's `METHODS`.
- **D3 Registry.** `FeatureSpec(name, lookback, kind, unit, description, func, benchmark)` (frozen dataclass); `FeatureRegistry.register/feature/get/specs/names/__contains__/__iter__/__len__`; names are `snake_case`, unique (duplicate → `ValueError`), `lookback ≥ 1`, `kind ∈ {ts, xs}`, unit from `FeatureUnit`. `REGISTRY` is the default; `feature = REGISTRY.feature`. A feature function takes a `PricePanel` and returns a `DataFrame` aligned to it.
- **D4 `PricePanel`.** Frozen dataclass of wide frames (index `DatetimeIndex` `date` of sessions, columns symbols): `open`, `high`, `low`, `close` (adjusted, USD), `dollar_volume` (raw USD), `benchmark_close` (Series or `None`); derived `returns`, `benchmark_returns`, a `memo(key, fn)` cache for shared intermediates (e.g. EMA 12/26). `PricePanel.from_bars(bars, sessions, symbols, benchmark=None)` pivots `DataView.prices` rows.
- **D5 Compute.** `compute_panel(panel, names=None, registry=REGISTRY) -> dict[str, DataFrame]` (float64, aligned). `compute_features(view, symbols, names=None, *, history=1, layout="wide"|"long", registry=REGISTRY)`: `wide` → `dict[name, DataFrame[date × symbol]]` of the last `history` sessions; `long` → one `DataFrame` indexed by `(date, symbol)` with one column per feature. Unknown names → `ValueError` listing them; `history ≥ 1`.
- **D6 Features (40).** Notation: `C,O,H,L` adjusted prices, `r` daily simple return, `m` benchmark return, `DV = close × volume`.
  - Returns: `ret_{1,5,21,63,126,252}d` = `C_t/C_{t−n} − 1` (lookback n+1); `momentum_12_1` = `C_{t−21}/C_{t−252} − 1` (253); `reversal_5d` = `−ret_5d` (6).
  - Volatility: `vol_21d`, `vol_63d` = `std(r, ddof=1)·√252` (22, 64); `downside_vol_63d` = `√mean(min(r,0)²)·√252` (64); `atr_14` Wilder ATR in USD, TR = `max(H−L, |H−C₋₁|, |L−C₋₁|)`, seed = mean of the first 14 TR (15); `atr_pct_14` = `atr_14/C` (15).
  - Oscillators: `rsi_14` Wilder (seeded averages of gains/losses; 100 if no losses, 50 if no moves) (15); `macd` = EMA12 − EMA26 (USD) (26); `macd_signal` = EMA9 of `macd` (34); `macd_hist` = `macd − macd_signal` (34); `bb_pctb_20` = `(C − lower)/(upper − lower)`, bands SMA20 ± 2 population std (NaN if the band is flat) (20).
  - Moving averages: `sma_{20,50,200}`, `ema_{20,50,200}` (USD; EMA α = 2/(n+1), SMA seed) (n); `price_to_sma_n`, `price_to_ema_n` = `C / MA` (ratio) (n).
  - Range: `dist_52w_high` = `C / max(H over 252) − 1` (≤ 0), `dist_52w_low` = `C / min(L over 252) − 1` (≥ 0) (252); `max_dd_252d` = worst peak-to-trough of `C` inside the last 252 sessions (≤ 0) (252).
  - Market: `beta_252d` = `cov(r,m)/var(m)` over 252 returns; `idio_vol_252d` = `√(SSR/(n−2))·√252` of the OLS residuals with intercept (253; `benchmark=True`).
  - Liquidity and gaps: `volume_z_21d` = z-score of `ln DV_t` against the previous 21 sessions (ddof 1) (22); `amihud_21d` = `mean(|r|/DV)·1e6` (return per USD 1M traded) (22); `gap_1d` = `O_t/C_{t−1} − 1` (2); `gap_mean_21d` (22), `gap_std_63d` (ddof 1, daily) (64).
- **D7 Snapshot.** `FeatureReading(name, unit, value, pct_own, pct_universe)` and `TechnicalSnapshot(symbol, as_of, session, history_sessions, universe_size, readings)` (`AtlasModel`; NaN → `None`; percentiles are fractions in (0, 1)); `.reading(name)`, `.value(name)`. `build_snapshots(view, symbols, *, names=None, universe=None, registry=REGISTRY) -> dict[str, TechnicalSnapshot]`: one `compute_features` call over `universe ∪ symbols` with `history = snapshot.history_sessions`; percentile = mid-rank `(#below + ½·#equal)/#valid`; own history needs `min_history` valid values, universe needs `min_universe` and a comparable unit; blocked symbols left out.

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

Commands below are run as `UV_CACHE_DIR=$TMPDIR/uv-cache-s15 UV_NO_SYNC=1 uv run …` (sandbox). Feature tests live in `tests/unit/quant/features/`.

**As built — file names.** The test tree has no `__init__.py` files (pytest's rootdir import mode), so test-module basenames must be unique across `tests/`; the stage's files are prefixed: `test_feature_registry.py` (Task 4), `test_feature_compute.py` (5), `feature_reference.py` + `test_feature_reference.py` (6), `test_technical_features.py` (7–13), `test_feature_lookahead.py` (14), `test_feature_snapshot.py` (15), `test_feature_performance.py` (16). The `tiny_universe` builder is `tests/tiny_universe.py` (importable by every test; the fixture is in `tests/conftest.py`), which also holds `add_future_rows` (used by Tasks 14 and 15). `registry.py` also exposes `normalize_symbols` (used by the snapshot builder).

### Task 1 — `quant.features` config
- Files: modify `src/atlas/core/config.py`, `config/fund.yaml` · test `tests/unit/core/test_config.py`
- [x] Write failing tests `test_quant_features_defaults`, `test_quant_features_validation` (buffer ≥ 0, sessions/year ≥ 1, history ≥ 2, 1 ≤ min_history ≤ history, min_universe ≥ 1)
- [x] Run `uv run pytest tests/unit/core/test_config.py -q -k quant` → FAIL (`AtlasConfig` has no `quant`)
- [x] Add `SnapshotConfig`, `FeaturesConfig`, `QuantConfig`, `AtlasConfig.quant`, `quant` in `_SECTIONS`; YAML block with comments
- [x] Run → PASS; `uv run pytest tests/unit/core/test_config.py -q` → PASS (every key in YAML, every key commented)

### Task 2 — `DataView.sessions`
- Files: modify `src/atlas/data/view.py` · tests `tests/unit/data/test_view_pit.py`
- [x] Write failing tests `test_sessions_window` (last n known sessions; 03-01 bar unknown at 08:00; matches `prices` dates) and `test_sessions_lookback_validation`; add `sessions` to `METHODS`
- [x] Run `uv run pytest tests/unit/data/test_view_pit.py -q -k sessions` → FAIL
- [x] Add the public method (cached like the others)
- [x] Run → PASS; `uv run pytest tests/unit/data/test_view_pit.py -q` → PASS (leak suite covers it)

### Task 3 — `tiny_universe` fixture
- Files: modify `tests/conftest.py` · test `tests/unit/quant/features/test_tiny_universe.py`
- [x] Write failing tests: 5 tickers (`AAA`, `BBB`, `CCC`, `DDD`, `SPY`), 300 sessions to 2024-02-29 readable through `DataView` at 2024-03-01; known properties (stock returns = β·SPY + orthogonal noise with betas 0.5/1.0/1.3/1.8; SPY daily vol 1%); `sp500` membership for the four stocks
- [x] Run → FAIL (no fixture)
- [x] Build `TinyUniverse` (lake, as_of, symbols, betas) in `tests/conftest.py` (OHLC with gaps and ranges, volumes)
- [x] Run → PASS

### Task 4 — Feature registry
- Files: create `src/atlas/quant/features/__init__.py`, `registry.py` · test `tests/unit/quant/features/test_registry.py`
- [x] Tests: `test_feature_decorator_registers_spec`, `test_duplicate_name_rejected`, `test_invalid_spec_rejected` (name, lookback, kind, unit), `test_unknown_name_lists_known`, `test_specs_in_request_order`, `test_description_from_docstring`
- [x] Run → FAIL (no module)
- [x] Implement `FeatureSpec`, `FeatureRegistry`, `REGISTRY`, `feature`
- [x] Run → PASS

### Task 5 — Panel and compute plumbing on `tiny_universe`
- Files: modify `registry.py` · test `tests/unit/quant/features/test_compute.py`
- [x] Tests: `test_panel_from_bars_adjusts_ohlc` (split day: adjusted O/H/L/C continuous; dollar volume raw), `test_panel_missing_bar_is_nan_row`, `test_compute_panel_aligned_float64`, `test_compute_features_wide_and_long` (same values; last `history` sessions; index = sessions ≤ last known), `test_compute_features_loads_lookback_plus_buffer` (spy on `DataView.prices`/`sessions`: `max lookback + buffer + history − 1`), `test_benchmark_loaded_only_when_needed`, `test_blocked_symbol_left_out`, `test_unknown_feature_raises`, `test_history_validation`, `test_xs_feature_sees_cross_section` (test-local registry)
- [x] Run → FAIL
- [x] Implement `PricePanel`, `compute_panel`, `compute_features`
- [x] Run → PASS

### Task 6 — Reference implementation and fixture CSVs
- Files: create `tests/unit/quant/features/reference.py`, `tests/fixtures/features/{ohlcv.csv,expected.csv}` · test `tests/unit/quant/features/test_reference_fixture.py`
- [x] Reference: plain-Python loops per symbol for every D6 definition (no pandas rolling/ewm); `make_ohlcv()` = 300 sessions of AAA (2:1 split mid-series, a dividend), BBB (one missing bar inside the last 60 rows), SPY; `main()` writes both CSVs (expected = the last 60 sessions × {AAA, BBB}, 12 significant digits)
- [x] Test `test_expected_csv_is_current` (re-run reference → equals the CSV within 1e-12) and `test_reference_hand_values` (a few pen-and-paper values)
- [x] Generate the CSVs; run → PASS

### Tasks 7–13 — Features, each group test-first against `expected.csv` (atol/rtol 1e-6) plus hand-computed cases

As built: the tests of all seven groups were written first and failed together (collection error: no features), then `technical.py` was written for all groups and passed on its first run — one red→green cycle for the seven groups rather than one per group (recorded in the worklog). Tolerance used: `rtol 1e-6, atol 1e-9` with identical NaN patterns. Task 17's mutation checks show the tests catch wrong definitions. Feature descriptions live in the functions' docstrings (the registry reads them).
For each group: write the tests in `tests/unit/quant/features/test_technical.py` (parametrised over the group's names: `test_matches_reference[name]`, plus the hand cases) → run `-k <group>` → FAIL (not registered) → implement in `src/atlas/quant/features/technical.py` → PASS.
- [x] **7 Returns & momentum:** `ret_1d … ret_252d`, `momentum_12_1`, `reversal_5d`; hand: `[100, 110, 99]` → `ret_1d` −0.1, `ret_5d` on a known path
- [x] **8 Volatility:** `vol_21d`, `vol_63d`, `downside_vol_63d`, `atr_14`, `atr_pct_14`; hand: alternating ±1% returns → vol = 0.01·√(n/(n−1))·√252; constant range bars → ATR = range
- [x] **9 Oscillators:** `rsi_14`, `macd`, `macd_signal`, `macd_hist`, `bb_pctb_20`; hand: rising-only → RSI 100; flat → RSI 50; constant price → MACD 0, %B NaN
- [x] **10 Moving averages:** `sma_*`, `ema_*`, `price_to_sma_*`, `price_to_ema_*`; hand: linear prices → SMA = mid value; EMA of a constant = constant
- [x] **11 Range & drawdown:** `dist_52w_high`, `dist_52w_low`, `max_dd_252d`; hand: `[100, 120, 90, 130, 117]` → max drawdown −0.25
- [x] **12 Liquidity & gaps:** `volume_z_21d`, `amihud_21d`, `gap_1d`, `gap_mean_21d`, `gap_std_63d`; hand: constant volume then 3× spike → known z; open = prev close → gap 0
- [x] **13 Beta & idiosyncratic vol:** `beta_252d`, `idio_vol_252d`; synthetic: `r = 0.0002 + 1.3·m + ε` with ε exactly orthogonal to m and 1 (std set to 1%) → beta 1.3 (1e-9) and idio vol 0.01·√252 (1e-9); noisy ε → beta within ±0.05; missing benchmark → NaN

### Task 14 — No look-ahead, lookbacks, ts invariance
- Files: test `tests/unit/quant/features/test_no_lookahead.py`
- [x] `test_shift_future_data_changes_nothing[name]` (for every registered feature and three cut dates: replace all bars after the cut with random ones → values ≤ cut identical), `test_first_value_at_lookback[name]`, `test_ts_features_ignore_other_symbols[name]`, `test_view_future_rows_never_leak` (tiny world with later bars and a later split known after `as_of`: `compute_features` equals the same call on a lake without them), `test_all_brief_features_registered` (the 40 names)
- [x] Run → PASS (any failure is a bug in Tasks 5–13: fix test-first). As built: also `test_truncating_the_future_changes_nothing[name]`; the "scrambling had an effect" check runs on the earliest cut only (`momentum_12_1` skips the last 21 sessions, so after the 320 cut its later values legitimately do not move). The three first failures were test bugs (see worklog), none in the features.

### Task 15 — Snapshot builder and percentiles
- Files: create `src/atlas/quant/features/snapshot.py` · test `tests/unit/quant/features/test_snapshot.py`
- [x] Tests: `test_mid_rank_percentile_known_values`, `test_snapshot_values_match_compute_features`, `test_pct_own_known_answer` (a monotonic feature → latest is the max → (n−½)/n), `test_pct_universe_known_answer`, `test_usd_features_have_no_universe_percentile`, `test_short_history_gives_none`, `test_nan_value_gives_none`, `test_snapshot_contract` (immutable, NaN rejected, JSON round trip), `test_blocked_symbol_left_out`, `test_snapshot_no_lookahead` (future rows → same snapshot), `test_default_universe_from_config`
- [x] Run → FAIL
- [x] Implement `FeatureReading`, `TechnicalSnapshot`, `percentile_rank`, `build_snapshots`
- [x] Run → PASS

### Task 16 — Performance: 500 symbols × 2,000 days < 20 s
- Files: test `tests/unit/quant/features/test_performance.py`
- [x] Module fixture writes 500 synthetic symbols + SPY × 2,000 sessions to a tmp lake; `test_all_features_500x2000_under_20s` times `compute_features(view, symbols, history=2000)` (view load included) and asserts < 20 s and full shape
- [x] Run → PASS; record the timing; profile and vectorise further if it is slow. As built: 4.71 s on the first run, 2.52 s in the final run (limit 20 s); no further work needed.

### Task 17 — Verification and records
- [x] `uv run pytest tests/unit/quant/features -q` (brief's Verify); `uv run pytest -q`; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src/atlas`; `make check`; stage tests with `-W error`; coverage of `atlas.quant.features`; docstrings of new public names state units; outputs saved under `artifacts/`. Added: 16 mutation checks (`artifacts/mutate.py`, `artifacts/mutation-checks.txt`); the first run missed one (max drawdown ignoring the window's second bar) → a hand case was added and all 16 are caught.
- [x] `verification.md`, `PROGRESS.md` (status, handoff, Decisions log, Backlog), `worklog.md` complete
- [x] Review the staged diff; commit `stage-15: Technical feature library`; `git status` / `git log` → `c1b8d32`; `main` had moved on (phase-0 checkpoint `d9cffb6`, `4edc709`), merged into the branch as `7dea6a4` (one `PROGRESS.md` conflict, both sides kept; merged tree: `make check` exit 0, 2032 passed)
- [x] Fast-forward `main` (owner's Terminal panel if the sandbox refuses, as in Stages 13–14) and re-run `uv run pytest -q` + `make check` on the merged result; record the hashes → sandbox refused (`Operation not permitted`); `main` `4edc709..7dea6a4` fast-forwarded in the owner's Terminal panel: 2032 passed, `make check` exit 0 ([artifacts/merged-main-check.txt](artifacts/merged-main-check.txt)); then this records follow-up
