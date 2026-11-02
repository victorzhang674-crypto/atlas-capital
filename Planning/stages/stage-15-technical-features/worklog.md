# Stage 15 — Technical feature library · Work log

Append entries in order. Record failures and dead ends as honestly as successes.

## 2026-11-02 — Gate check and plan
- Did: read `Project.md`, `PROGRESS.md`, the brief, roadmap row 15, spec §3/§11/§12, protocol §2–§5, downstream briefs (16, 17, 19–22, 30) for names they expect (`momentum_12_1`); copied the template; wrote `plan.md` (17 tasks, D1–D7, interpretations 1–8).
- Ran: `uv sync --group dev` (fresh `UV_CACHE_DIR=$TMPDIR/uv-cache-s15`) then `uv run pytest -q` → 1673 passed, 11 deselected in 32.24 s · raw: `artifacts/gate-check-pytest.txt`. Stage 14 `verification.md` says ALL CRITERIA VERIFIED.
- Problems / fixes: the first background run piped through `tail -3` and lost the summary line; re-ran with the full output saved.
- Next: Task 1.

## 2026-11-02 — Task 1: `quant.features` config
- Did: tests `test_quant_features_defaults`, `test_quant_features_validation` (6 cases), `test_quant_snapshot_min_history_within_history`; saw them fail at import (no `FeaturesConfig`); added `SnapshotConfig`, `FeaturesConfig`, `QuantConfig`, `AtlasConfig.quant`, `quant` in the loader's `_SECTIONS`; YAML block `quant.features` in `config/fund.yaml` with a comment per key.
- Ran: `uv run pytest tests/unit/core/test_config.py -q` → 191 passed (incl. every-key-in-YAML and every-key-commented).
- Problems / fixes: ruff import order in the test (auto-fixed).

## 2026-11-02 — Task 2: `DataView.sessions`
- Did: tests `test_sessions_window`, `test_sessions_lookback_validation[×4]`; `sessions` added to the leak suite tables (`METHODS`, `LEAKS` = `_leak_bars`, `STATIC`, `NOT_PER_SYMBOL`); saw 7 failures; added the public method (cached; raises `LookAheadError` if the grid ever ends at a bar published after the cutoff, so the guard test bites).
- Ran: `uv run pytest tests/unit/data/test_view_pit.py -q` → 119 passed.
- Problems / fixes: this file took 58 s, then 114 s on a re-run; `--durations` showed only pre-existing Stage 13 tests at the top (`test_view_is_read_only` 13.6 s): the machine was loaded at the time, not a regression from this change (the gate run took 32 s for the whole suite).

## 2026-11-02 — Task 3: `tiny_universe`
- Did: tests in `tests/unit/quant/features/test_tiny_universe.py` (shape, bar consistency, exact betas/vols); red (no module); built `tests/tiny_universe.py` (`TinyUniverse`, `build_tiny_universe`, `bars_from_returns`, `tiny_returns`) and the `tiny_universe` fixture in `tests/conftest.py`: SPY returns with sample std exactly 1%, stocks = alpha + beta·SPY + noise exactly orthogonal to [1, SPY] with std 1.5%, betas 0.5/1.0/1.3/1.8, `SP500` membership for the four stocks.
- Ran: → 3 passed.

## 2026-11-02 — Tasks 4–5: registry and compute plumbing
- Did: `test_feature_registry.py` (13 tests) red → `registry.py` (`FeatureSpec`, `FeatureRegistry`, `REGISTRY`, `feature`, `PricePanel`) → green. `test_feature_compute.py` (14 tests: adjusted OHLC on a split, missing bar/symbol NaN, misaligned frames, memo, float64 + inf→NaN, misaligned result, wide, long, lookback+buffer spy, benchmark only when needed, blocked symbol and blocked benchmark, xs feature, argument errors, unknown symbol) red → `compute_panel`, `compute_features` → green.
- Ran: `uv run pytest tests/unit/quant/features -q` → 30 passed.
- Problems / fixes: pandas 3 labels default to its string dtype; the panel first forced `object` columns and `assert_frame_equal` against `view.returns` failed → use the default dtype like the view.

## 2026-11-02 — Task 6: reference implementation and fixtures
- Did: `tests/unit/quant/features/feature_reference.py` (plain Python loops, no pandas rolling/ewm; inputs AAA with a 2:1 split and a 1.50 dividend, BBB with a missing bar, SPY; our CRSP-style `adj_close` computed in the generator) and `test_feature_reference.py` (CSV current to 1e-10, designed events present, hand values of the building blocks and features). Generated `tests/fixtures/features/{ohlcv.csv,expected.csv}` with `uv run python tests/unit/quant/features/feature_reference.py`.
- Problems / fixes: (1) with 300 sessions the 252-session features had no reference value on the first 12 expected rows → 330 sessions, missing bar moved to index 300 (every feature now has ≥ 60 reference values; BBB's NaN pattern after the gap is tested). (2) A hand test used a benchmark with constant returns: zero variance → beta NaN is correct; the test now checks that NaN and uses a zigzag benchmark for beta = 1.
- Ran: → 4 passed.

## 2026-11-02 — Tasks 7–13: the 40 features
- Did: `test_technical_features.py`: `test_matches_reference[group-name]` × 40 (rtol 1e-6, atol 1e-9, identical NaN pattern, ≥ 60 finite reference values each), hand cases per group, beta 1.3 exact (orthogonal noise: beta and idio vol to 1e-9) and noisy (±0.05 / ±15%), missing benchmark → NaN, `test_feature_specs_match_the_plan` (lookbacks, USD units, benchmark flags). Saw the file fail at collection (no `compute_panel` export, no features), then wrote `technical.py` for all seven groups.
- Deviation (honest record): the plan said one red→green cycle per group; the tests for all groups were written first and failed together, then the whole `technical.py` was written and all 51 passed on its first run. Mutation checks in Task 17 show the reference tests catch wrong definitions.
- Ran: `uv run pytest tests/unit/quant/features -q` → 85 passed; `uv run mypy src/atlas` → clean after two pandas-stubs typing fixes (`cast` for the MultiIndex column selection, `np.log` on the numpy array).

## 2026-11-02 — Task 14: no look-ahead, lookbacks, ts invariance
- Did: `test_feature_lookahead.py`: shift test (every feature × cuts 260/300/320: all later bars scrambled → values ≤ cut identical), truncation test, first value exactly at `lookback − 1`, `ts` invariance to the cross-section, 40 registered, and `test_view_future_rows_never_leak` (later bars, a later CCC split and a late-recorded AAA dividend in the lake: same answer at the as-of, a different one at 2024-03-18).
- Problems / fixes: the first run had 8 failures, all test bugs: (1) the "scrambling had an effect" check failed for `momentum_12_1` at cut 320 — it skips the last 21 sessions, so the 9 later rows legitimately depend only on earlier bars → the check runs on the earliest cut only; (2) the perturbation multiplied the whole dollar-volume frame by 1e6, rows before the cut included → scaled only the scrambled rows; (3) the future-bars helper gave `bars_from_returns` one day fewer than prices → the grid now starts at 02-29 (dropped). Then moved the helper to `tests/tiny_universe.py` as `add_future_rows` (reused by Task 15).
- Ran: → 242 passed.

## 2026-11-02 — Task 15: snapshot
- Did: `test_feature_snapshot.py` (11 tests, test-local features with known percentiles: rising counter, per-symbol constants with a tie, USD level, sparse, NaN last value) red (no module) → `snapshot.py` (`FeatureReading`, `TechnicalSnapshot`, `percentile_rank`, `build_snapshots`); `registry._wanted` made public as `normalize_symbols`.
- Problems / fixes: the universe percentile broadcast `row[:, None]` against `latest[None, :]` and returned 2-D arrays (`TypeError` converting to float) → the cross-section is broadcast to one column per symbol.
- Ran: → 11 passed.

## 2026-11-02 — Task 16: performance
- Did: `test_feature_performance.py`: a module fixture writes 500 symbols + SPY × 2,000 sessions (1,002,000 bars) to a tmp lake; `compute_features(view, symbols, history=2000)` timed with `time.perf_counter` (view load included).
- Ran: → "40 features x 500 symbols x 2000 sessions in 4.71 s (limit 20 s)"; the final run 2.52 s.

## 2026-11-02 — Task 17: verification and records
- Ran (raw output in `artifacts/`): brief's Verify `uv run pytest tests/unit/quant/features -q` → 339 passed; `uv run pytest -q` → 2027 passed, 11 deselected; `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 204 files already formatted; `uv run mypy src/atlas` → no issues in 88 files; `make check` → exit 0; `-W error` → 339 passed; coverage of `atlas.quant.features` → 100%; docstrings → 49 public names, none missing.
- Mutation checks (`artifacts/mutate.py`): 16 deliberate bugs (wrong ddof, wrong EMA/Wilder alpha, the smoother's missing-input rule, Bollinger std, drawdown window, idio ddof, volume z including today, momentum skip, 3 look-ahead mutants, buffer dropped, blocked symbols kept, two snapshot mutants). First run: 15/16 — the drawdown mutant that ignores the window's second bar survived → added hand cases with the trough on the second bar and the peak on the second-last bar; second run 16/16 caught.
- Docstrings: the 20 decorated features carried their description in the decorator; moved into each function's docstring (the registry reads it), so every public function has one.
- Records: `verification.md` (ALL CRITERIA VERIFIED), `plan.md` ticked with as-built notes, `PROGRESS.md` (status, checkbox, Decisions log rows, Backlog "Stage 15", handoff note; the Stage 07 and Stage 13 Backlog items annotated).
- Code and test files of the stage: created `src/atlas/quant/features/{__init__,registry,technical,snapshot}.py`, `tests/tiny_universe.py`, `tests/unit/quant/features/{feature_reference,test_tiny_universe,test_feature_registry,test_feature_compute,test_feature_reference,test_technical_features,test_feature_lookahead,test_feature_snapshot,test_feature_performance}.py`, `tests/fixtures/features/{ohlcv,expected}.csv`; modified `src/atlas/core/config.py`, `config/fund.yaml`, `src/atlas/data/view.py`, `tests/conftest.py`, `tests/unit/core/test_config.py`, `tests/unit/data/test_view_pit.py`.

## 2026-11-02 — Git: commit, merge `main` in, fast-forward `main`
- Commit `c1b8d32` "stage-15: Technical feature library" after reviewing `git diff --cached --stat` (39 files, only the stage's files; no secrets: grep of artifacts and fixtures for the `.env` key names found nothing) → `git status --short` clean.
- `main` had moved on while the stage ran (another session's phase-0 checkpoint: `d9cffb6`, `4edc709`; Stages 02/03/05/06/14 follow-ups, pandas floor in `uv.lock`). Merged `main` into the stage branch (Rule 6A): one conflict in `PROGRESS.md` (header, Decisions log, Backlog, handoff: both sides added) resolved by keeping both, checkpoint entries first; `uv sync --group dev`; on the merged tree the brief's Verify → 339 passed and `make check` → exit 0, 2032 passed (`artifacts/merge-main-into-branch-check.txt`; the first capture of make's exit status used a bash-only variable under zsh, re-run). Merge commit `7dea6a4`.
- Fast-forward of `main`: the sandbox refused it in the main checkout (`error: unable to unlink old 'Planning/PROGRESS.md': Operation not permitted`; the checkout was left at `4edc709`, clean). Rule 6 exception: ran `artifacts/merged-main-check.sh` in the owner's Terminal panel (owner approved the line): `git merge --ff-only` `4edc709..7dea6a4` → `uv run pytest -q` 2032 passed, exit 0; brief's Verify 339 passed; `make check` exit 0; `git branch --merged main` lists `claude/stage-15-plan-implement-faa041`; tree clean (`artifacts/merged-main-check.txt`; the DuckDB "nanobind: leaked …" lines at interpreter exit are pre-existing and also in the gate run).
- Then this records-only follow-up commit (hashes cannot sit in the commit they describe), fast-forwarded the same way.
