# Stage 17 — Signal research toolkit (IC, quantiles, decay) · Work log

Append entries in order. Record failures and dead ends as honestly as successes.

## 2026-11-04 — Rule 0, gate check, plan
- Did: read `Project.md`, `CLAUDE.md`, `Planning/PROGRESS.md` (Stage 16 handoff), the brief, roadmap row 17, spec §3/§11/§12, the Stage 15 (`atlas.quant.features`) and Stage 16 (`atlas.quant.factors`) code, `DataView`, the CLI. Stated "Project.md read. Current stage: 17 — Signal research toolkit (IC, quantiles, decay) (NOT STARTED)."
- Gate check: Stage 16 `DONE` in `PROGRESS.md`; its `verification.md` says **ALL CRITERIA VERIFIED**. `uv sync --group dev` in the new worktree (fresh `UV_CACHE_DIR=$TMPDIR/uv-cache-s17`).
- Ran: `uv run pytest -q` → 2322 passed, 14 deselected, exit 0 (174 s) · raw: `artifacts/gate-check-pytest.txt`. (A first attempt piped through `tail`, which hid the summary line under duckdb's nanobind leak messages at exit and reported `tail`'s exit code; re-run with the whole output saved and the real exit code.)
- Probe (real lake, read only, `$TMPDIR` script): the first `DataView` + `universe("sp500")` at 2018-03-01 took 70 s, a `compute_features(momentum_12_1)` over 503 members 1.0 s, each later date via `advance` ~16 s (the day's quality report dominates); `returns` of 507 names × 1,000 sessions 10.7 s. → monthly evaluation dates by default (plan interpretation 4).
- Probe: pandas 3.1 keeps a frame's `attrs` through `*`, `-`, `rank`, `shift`, `iloc`, `reindex`, numpy ufuncs, `stack`, frame + frame; only rebuilding from `.to_numpy()` drops it → the label guard (plan interpretation 3).
- Copied `Planning/stages/_template/` (worklog, verification) into the stage folder; wrote `plan.md` (interpretations 1–13, D1–D8, Tasks 1–11). Collisions with the overall plan: none.

## 2026-11-04 — Task 1: `quant.research` config
- Red: `test_quant_research_defaults`, `test_quant_research_validation` (15 cases), `test_quant_research_backtest_validation` (6), `test_quant_research_min_names_covers_quantiles` → `ImportError: cannot import name 'ResearchBacktestConfig'`.
- Green: `ResearchBacktestConfig`, `ResearchConfig` (+ `_ascending_positive`, relative `out_dir` check, `min_names ≥ n_quantiles`), `QuantConfig.research`; YAML block `quant.research` in `config/fund.yaml` with a comment per key.
- Ran: `uv run pytest tests/unit/core/test_config.py -q` → 241 passed (incl. every key in YAML and commented).
- Files: `src/atlas/core/config.py`, `config/fund.yaml`, `tests/unit/core/test_config.py`.

## 2026-11-04 — Task 2: matplotlib dependency
- Red: `test_matplotlib_is_a_runtime_dependency` → `AssertionError: no 'matplotlib>=' requirement`.
- Green: `uv add "matplotlib>=3.9"` (PyPI through `allowed_domains`) installed matplotlib 3.11.2 (+ contourpy, cycler, fonttools, kiwisolver, pillow, pyparsing); floor raised to the installed `>=3.11`, `uv lock`; the dependency comment names Stage 17.
- Ran: `uv run pytest tests/unit/test_dependency_floors.py tests/unit/test_toolchain.py -q` → 7 passed; `import matplotlib; matplotlib.use("Agg")` → 3.11.2.
- Problem: in the sandbox `~/.matplotlib` is not writable (matplotlib falls back to a temp cache and warns); commands here set `MPLCONFIGDIR=$TMPDIR/mpl`. Not an issue in CI.
- Files: `pyproject.toml`, `uv.lock`, `tests/unit/test_dependency_floors.py`.

## 2026-11-04 — Task 3: label tag and look-ahead guard
- Red: `test_tag_label_marks_a_copy`, `test_guard_rejects_labels_and_derived_frames` (10 derivations), `test_guard_rejects_label_objects`, `test_guard_accepts_plain_frames`, `test_composite_rejects_labels` → `ImportError: cannot import name 'LABEL_ATTR'`.
- Problem / fix: my helper `_frame()` shadowed an existing `_frame(stamps, col)` in `test_pit.py` (TypeError in older tests) → renamed `_label_frame()`.
- Green: `atlas.core.pit` gains `LABEL_ATTR`, `tag_label`, `is_label`, `guard_not_label` (+ docstring paragraph); Stage 16 `composite` calls `guard_not_label(factors, "factor frame")` (earlier-stage change, one line + docstring).
- Ran: `uv run pytest tests/unit/core tests/unit/quant/factors -q` → 967 passed; `mypy` on both files → no issues.
- Files: `src/atlas/core/pit.py`, `src/atlas/quant/factors/composite.py`, `tests/unit/core/test_pit.py`, `tests/unit/quant/factors/test_factor_composite.py`.

## 2026-11-04 — Task 4: forward-return labels (brief task 1)
- Test world: `tests/unit/quant/research/research_world.py` (`build_research_world`: N names × S sessions to 2024-02-29 from returns with a planted signal — per decision day, the next session's cross-sectional returns have Pearson correlation exactly `rho` with the signal when `exact=True`; `SP500` membership with one late joiner; instruments with sectors, every fourth name none; options `drop` (remove bars) and `stale` (freeze the last 6 closes)).
- Red: 11 tests in `test_research_labels.py` → `ModuleNotFoundError: No module named 'atlas.quant.research'`.
- Green: `atlas.quant.research.labels` (`check_horizons`, `compound_forward`, `ForwardReturns`, `forward_returns`) + package `__init__`.
- Problem / fix: `test_forward_returns_blocked_symbol_is_reported` first failed (`() == ('S003',)`): my test asked for labels ending at session 41, whose label view's 21-session quality window does not reach the frozen closes (sessions 74–79). The code was right; the test now labels a date whose view (after session 78) sees them, and checks `known_at`.
- Ran: `uv run pytest tests/unit/quant/research/test_research_labels.py -q` → 11 passed.
- Files: `src/atlas/quant/research/{__init__,labels}.py`, `tests/unit/quant/research/{research_world,test_research_labels}.py`.

## 2026-11-04 — Task 5: evaluation dates and the point-in-time signal panel
- Red: 8 tests in `test_research_signals.py` → `ImportError: cannot import name 'SignalPanel'`. A `conftest.py` in `tests/unit/quant/research/` puts the Stage 16 factor world on `sys.path` (as `tests/unit/cli/conftest.py` does for the data world).
- Test fix before green: `test_signal_panel_ignores_future_rows` first scaled the future bars by a constant 1.37, which leaves every price ratio (and so `reversal_5d`) unchanged on later dates too, so its "later dates do see them" half could not fail; the scale now varies by session.
- Green: `atlas.quant.research.signals` (`evaluation_dates`, `signal_kind`, `SignalPanel`, `signal_panel`); `labels.check_dates` made public (shared). Refactor: `signal_panel` takes `calendar=`; ruff UP047/RUF007 fixes (PEP 695 type parameter in `tag_label`, `itertools.pairwise`).
- Ran: `uv run pytest tests/unit/quant/research/ tests/unit/core/test_pit.py -q` → 64 passed; `ruff check src tests` → All checks passed; `mypy src/atlas` → no issues in 103 files.
- Files: `src/atlas/quant/research/{signals,labels,__init__}.py`, `src/atlas/core/pit.py`, `tests/unit/quant/research/{conftest,test_research_signals}.py`.

## 2026-11-04 — Task 6: information coefficient and decay (brief task 2)
- Red: 15 tests in `test_research_ic.py` → `ImportError: cannot import name 'ICSummary'`.
- Green: `atlas.quant.research.ic` (`ic_series` (Pearson / Spearman of average ranks over the jointly valid names), `newey_west_tstat`, `nw_lags`, `ICSummary`, `summarize_ic`, `ic_by_group`, `ic_by_regime`, `ic_decay`, `rank_autocorrelation`).
- Problem / fix: `test_newey_west_known_values` failed on a constant series `[0.1, 0.1, 0.1]`: its float mean is not exactly 0.1, so the "variance" was ~1e-34 and the t-stat 9.7e15. `newey_west_tstat` now returns NaN for a constant series (the per-date IC already checked for a constant cross-section with max > min, not the variance, for the same reason).
- Acceptance numbers (seeded): planted Pearson IC 0.05, 500 names × 400 dates → mean Pearson IC 0.05123, Spearman 0.05021 (theory for a bivariate normal 0.04775), both within ±0.01; through the lake (200 names × 160 sessions, exact per-date Pearson 0.05) → every date's Pearson IC = 0.05 within 1e-9, Spearman within ±0.01.
- Ran: `uv run pytest tests/unit/quant/research/test_research_ic.py -q` → 15 passed; ruff, mypy clean.
- Files: `src/atlas/quant/research/{ic,__init__}.py`, `tests/unit/quant/research/test_research_ic.py`.

## 2026-11-04 — Task 7: quantile analysis (brief task 3)
- Red: 8 tests in `test_research_quantiles.py` → `ImportError: cannot import name 'QuantileAnalysis'`. Test fix before green: the tests' date grid was `pd.bdate_range` (it includes NYSE holidays), which `nw_lags` rightly refuses; the grid is now the XNYS sessions.
- Green: `atlas.quant.research.quantiles` (`quantile_buckets` (average ranks, signal only), `quantile_returns`, `monotonicity`, `held_rows`, `daily_quantile_returns`, `QuantileAnalysis`, `quantile_analysis`).
- Bug found while writing (before the run): the first `daily_quantile_returns` forward-filled the bucket frame per cell, so a name that left the cross-section kept its old bucket until the end. Replaced by `held_rows` (each session takes the whole row of the latest evaluation date strictly before it). Added the regression case (N000 drops out on the second date) to `test_daily_quantile_returns_hold_membership` and checked it: with the per-cell `ffill` put back → 1 failed, 7 passed; restored → 8 passed.
- Ran: `uv run pytest tests/unit/quant/research/test_research_quantiles.py -q` → 8 passed; ruff, mypy clean.
- Files: `src/atlas/quant/research/{quantiles,__init__}.py`, `tests/unit/quant/research/test_research_quantiles.py`.

## 2026-11-04 — Task 8: vectorised L/S backtest with costs (brief task 4)
- Red: 10 tests in `test_research_backtest.py` → `ImportError: cannot import name 'BacktestResult'`.
- Green: `atlas.quant.research.vector_backtest` (`rebalance_dates`, `long_short_weights`, `BacktestResult`, `vector_backtest`).
- Problem / fix: `test_rebalance_frequency` failed (`[1, 4, 7] == [1, 4]`): my test's 130 sessions from 2021-01-04 run into July, so three quarters are right; the expectation now says so (and asserts the last month is July).
- Refactor: mypy flagged `signal.loc[d]` (could be a frame on a duplicated date) → rows by position (`iloc`).
- Ran: `uv run pytest tests/unit/quant/research/ -q` → 52 passed; ruff check/format, mypy (106 files) clean.
- Files: `src/atlas/quant/research/{vector_backtest,__init__}.py`, `tests/unit/quant/research/test_research_backtest.py`.

## 2026-11-04 — Task 9: tear sheet (brief task 5, part 1)
- Tests written first: 5 in `test_research_tearsheet.py` (summary sections and values equal to the module results, strict JSON, regimes, save → JSON/CSV/PNG, `research_signal` end to end on the research world with `reversal_5d`, label guard).
- **Process deviation:** I did not run these tests red before writing `tearsheet.py` (the red state was a certain `ImportError`, but it was not observed in this chat). Recorded here; the tests then passed on their first run, so their discriminating power is checked by the mutation checks at verification instead.
- Green: `atlas.quant.research.tearsheet` (`FIGURES`, `HEADLINE_METHOD`, `SignalTearSheet` (`summary`, `to_dict`, `figures`, `save`), `headline_horizon`, `build_tearsheet`, `research_signal`); `ic.session_step` factored out of `nw_lags` (used for the headline horizon); `METHODS` typed as `tuple[Method, ...]`.
- Looked at the figures of a weekly `reversal_5d` sheet on an 80-name research world (`$TMPDIR/s17sheet/out/*.png`): quantile bars per horizon, IC decay and rank autocorrelation render; legend text tidied (`h = 1 sessions`).
- Ran: `uv run pytest tests/unit/quant/research/ -q` → 57 passed; ruff check/format, mypy (107 files) clean.
- Files: `src/atlas/quant/research/{tearsheet,ic,__init__}.py`, `tests/unit/quant/research/test_research_tearsheet.py`.

## 2026-11-04 — Task 10: CLI `atlas research signal` (brief task 5, part 2)
- `src/atlas/cli/research_cmd.py` was drafted before its tests; the tests were then run red against the unwired command: 10 tests in `tests/unit/cli/test_research_cmd.py` → `No such command 'research'` (exit 2). (Same process deviation as Task 9, milder: the red run happened, but after the command module existed.)
- Green: `research_app` added to `atlas.cli.app` as `research`. `parse_bound` (YYYY → 1 Jan / 31 Dec, or YYYY-MM-DD), validation before any data read (unknown name, bad frequency, `--from` after `--to`, no session → exit 2), a forward-only view factory (`DataView` once, then `advance`), Rich progress over the dates, `AtlasError` → exit 1, summary table + backtest line + output path. `tests/unit/cli/conftest.py` also puts the research world on `sys.path`.
- Test fix: the `--out` test named a feature that does not exist (`momentum_1m`); now `rsi_14`.
- Manual check: an empty lake → `Research failed: DataError: no index_membership rows for SP500; …` (exit 1).
- Refactor: `cast(Frequency, …)` instead of a `type: ignore`.
- Ran: `uv run pytest tests/unit/cli -q` → 30 passed; ruff, mypy (108 files) clean.
- Files: `src/atlas/cli/{research_cmd,app}.py`, `tests/unit/cli/{test_research_cmd,conftest}.py`.

## 2026-11-04 — Task 11: real-lake run (started), mutation checks, audit fixes
- Started the real-lake run in the background (read only; `ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data`, `--out` into this folder's `artifacts/`, because the main checkout is not writable from the sandbox).
- Mutation checks (`artifacts/mutate.py`, 27 mutants of the label maths, guard and timing, IC/Newey-West, quantiles, backtest, tear sheet), run 1 → **24/27 caught**, 3 survived (`artifacts/mutation-checks-run1.txt`): all three were test gaps, not code bugs:
  1. *constant cross-section not excluded*: `test_ic_constant_cross_section_is_nan` used a constant 1.0, whose float mean is exact, so the max > min check was never needed; now 0.1 (mean not exact).
  2. *hit rate counts zero*: no test IC was exactly 0; `test_summarize_ic_fields` now has a 0.0 (hit rate 2/4).
  3. *NaN written into the JSON*: the tear-sheet test summary had no NaN; new `test_unrealised_horizon_is_null_in_json` (a 200-session horizon: every statistic NaN → null in strict JSON, also after `save`).
- Audit fix (in scope, found while the jobs ran): the tear sheet's backtest held the last book until the end of the longest label window (up to 63 sessions after the last evaluation date, i.e. past `--to`). `build_tearsheet` now cuts the daily returns at `backtest_end` = last date + one evaluation period (the median spacing); `summary["backtest_end"]`; `test_build_tearsheet_summary_sections` checks it (its backtest reference first failed against the old full window, as intended, and was updated to the cut window).
- Ran: `uv run pytest tests/unit/quant/research tests/unit/cli/test_research_cmd.py -q` → 68 passed; ruff check/format, mypy clean. Mutation run 2 started.
- The first real-lake run began before this fix, so it is kept as a diagnostic run; the evidence run is repeated on the final code.
- Mutation run 2 → **26/27** (`artifacts/mutation-checks-run2.txt`): *constant cross-section not excluded* still survived. Cause: the test used the default Spearman method, where a constant row has exactly equal ranks (NaN without the check); only Pearson is fooled by the rounded mean. `test_ic_constant_cross_section_is_nan` now checks both methods; with the mutant applied by hand → 1 failed, 14 passed; restored → 15 passed. Mutation run 3 started.
- Housekeeping: an empty `artifacts/.claude/.cc-writes/` folder appeared (created by the session tooling while my shell's working directory was `artifacts/`); removed.
- Mutation run 3 → **27/27 caught**, exit 0 (`artifacts/mutation-checks.txt`).
- The first (pre-fix) real-lake run had printed nothing after ~35 minutes (Rich progress is transient when output is not a terminal, and it shared the CPU with the mutation runs); stopped it (its output was never written) and started the evidence run on the final code alone: `artifacts/real-lake-cli.txt`.
- **Real-lake evidence run** (final code, read only): `ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data uv run atlas research signal --name momentum_12_1 --from 2016 --to 2021 --out …/artifacts/tearsheet-momentum_12_1` → **exit 0** in 2,284 s (38 min; one PIT view per monthly date dominates), 72 monthly dates 2016-01-04..2021-12-01, 542 names over time (PIT membership), labels read at 2022-03-04, no name blocked, 208 names without a sector left out of the by-sector table, 9 files (`tearsheet.json`, 3 CSVs, 5 PNGs) · raw: `artifacts/real-lake-cli.txt`.
  - Result (a finding, not a pass/fail item): `momentum_12_1` had a small **negative** IC on S&P 500 names in 2016–2021: Spearman −0.019 / −0.038 / −0.040 / −0.041 at 1/5/21/63 sessions (21-session NW t −1.57, hit rate 0.39); quintile means fall monotonically (monotonicity −1.00 at 5, 21, 63); the equal-weight monthly L/S lost 6.9% a year (Sharpe −0.29, max drawdown 46.8%, annual turnover 13.0, 5 bps). Rank autocorrelation 0.89 at one month and −0.04 at twelve, as a 12-month lookback should give.
  - Looked at `ic.png` and `backtest.png`: the losses sit in 2016 (momentum crash) and late 2020 (the November rotation); the IC chart's rolling mean is visible.
  - **Independent cross-check** (`artifacts/real_lake_crosscheck.py` → `real-lake-crosscheck.txt`): momentum rebuilt from `DataView.prices` pivoted by hand, the forward return from a later view, Spearman as pandas' Pearson of pandas ranks → equals the tear sheet's `h21_spearman` on 2017-03-01 (−0.088484), 2019-06-03 (−0.105929), 2021-02-01 (−0.393462); max difference 1.4e-17. (First attempt used pandas' `method="spearman"`, which needs scipy; not installed.)

## 2026-11-04 — Verification and records
- Ran (raw in `artifacts/`): brief's Verify `uv run pytest tests/unit/quant/research -q` → 58 passed; research + CLI tests `-W error` → 68 passed; coverage `atlas.quant.research` + `atlas.cli.research_cmd` 98%; `ruff check .` → All checks passed!; `ruff format --check .` → 248 files already formatted; `mypy src/atlas` → no issues in 108 files; `uv run pytest -q` → 2428 passed, 14 deselected, exit 0; `make check` → exit 0 (2428 passed).
- Acceptance values (`artifacts/planted-ic-values.txt`): synthetic planted 0.05 → Pearson 0.05123, Spearman 0.05021; through the lake → Pearson 0.05 on every date (max error 1e-15), Spearman 0.04469 (error 0.0053).
- Self-audit against brief and plan: every In-scope item and both criteria evidenced (`verification.md`); plan "As built" note added; five out-of-scope gaps written to `PROGRESS.md › Backlog` (Stage 17).
- Wrote `verification.md` (ALL CRITERIA VERIFIED) and the `PROGRESS.md` handoff note; Stage 17 set DONE; current stage → 18.
