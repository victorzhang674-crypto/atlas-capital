# Stage 17 — Signal research toolkit (IC, quantiles, decay) · Verification evidence

Filled in only with output from commands run in this stage's chat (Project.md Rule 1). Raw outputs are in `artifacts/`.

#### Verification evidence — Stage 17 — 2026-11-04
- Commands run (this chat; sandbox form `UV_CACHE_DIR=$TMPDIR/uv-cache-s17 UV_NO_SYNC=1 MPLCONFIGDIR=$TMPDIR/mpl uv run …`):
  - `uv run pytest -q` → 2428 passed, 0 failed, 14 deselected (live/llm/integration), exit 0 · raw: [artifacts/pytest.txt](artifacts/pytest.txt)
  - `uv run ruff check .` → All checks passed!, exit 0 · raw: [artifacts/ruff-check.txt](artifacts/ruff-check.txt)
  - `uv run ruff format --check .` → 248 files already formatted, exit 0 · raw: [artifacts/ruff-format.txt](artifacts/ruff-format.txt)
  - `uv run mypy src/atlas` → Success: no issues found in 108 source files, exit 0 · raw: [artifacts/mypy.txt](artifacts/mypy.txt)
  - `uv run pytest tests/unit/quant/research -q` (brief's Verify) → 58 passed, exit 0 · raw: [artifacts/verify-research.txt](artifacts/verify-research.txt)
  - `make check` → exit 0 (ruff check, format check, mypy 108 files, pytest 2428 passed, 14 deselected) · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
  - Research + CLI tests with `-W error` → 68 passed, exit 0 · raw: [artifacts/verify-research-werror.txt](artifacts/verify-research-werror.txt)
  - Coverage of `atlas.quant.research` + `atlas.cli.research_cmd` → 98% (804 statements, 20 missed: defensive branches) · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
  - Mutation checks (`artifacts/mutate.py`, 27 deliberate bugs in the label maths, look-ahead guard and timing, IC / Newey-West, quantiles, backtest, tear sheet) → **27/27 caught**, exit 0 · raw: [artifacts/mutation-checks.txt](artifacts/mutation-checks.txt) (runs 1 and 2, 24/27 and 26/27, found three test gaps, closed: [run1](artifacts/mutation-checks-run1.txt), [run2](artifacts/mutation-checks-run2.txt))
  - Planted-IC values behind the acceptance tests → [artifacts/planted-ic-values.txt](artifacts/planted-ic-values.txt)
  - Real lake (read only): `ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data uv run atlas research signal --name momentum_12_1 --from 2016 --to 2021 --out Planning/stages/stage-17-signal-research/artifacts/tearsheet-momentum_12_1` → exit 0 in 2,284 s; 72 monthly dates 2016-01-04..2021-12-01; 9 files · raw: [artifacts/real-lake-cli.txt](artifacts/real-lake-cli.txt), tear sheet: [artifacts/tearsheet-momentum_12_1/](artifacts/tearsheet-momentum_12_1/)
  - Independent real-lake cross-check (`artifacts/real_lake_crosscheck.py`) → the tear sheet's 21-session Spearman IC equals a hand-built recomputation on 3 dates, max difference 1.4e-17 · raw: [artifacts/real-lake-crosscheck.txt](artifacts/real-lake-crosscheck.txt)
- Acceptance criteria:
  - [x] On synthetic data with a planted signal, the toolkit recovers the IC within ±0.01 — evidence: `test_planted_ic_recovered_within_tolerance` (500 names × 400 dates, planted 0.05 → mean Pearson IC 0.05123, Spearman 0.05021); `test_planted_ic_end_to_end_through_the_lake` (returns written to a lake with an exact per-date Pearson IC of 0.05, read back through `DataView` by `forward_returns` → every date's Pearson IC 0.05 within 1e-15, mean Spearman 0.04469, |error| 0.0053); both in the brief's Verify run (58 passed); the label timing is pinned by `test_forward_returns_align_t_to_t_plus_h` and `test_forward_returns_skip_the_decision_day`, and the guard by `test_labels_are_tagged_and_guarded` and five `LEAK` mutants caught
  - [x] The CLI produces a tear sheet for a real factor — evidence: the real-lake `atlas research signal --name momentum_12_1 --from 2016 --to 2021` run above (exit 0; `tearsheet.json`, `ic_series.csv`, `quantile_returns.csv`, `backtest_returns.csv`, `ic.png`, `quantiles.png`, `cumulative_spread.png`, `decay.png`, `backtest.png`; figures inspected), its independent cross-check, and the CLI tests (`test_research_signal_writes_tearsheet_to_lake_runs` writes to the default `<lake>/runs/research/<name>/`, `--out`, exit codes 1/2)
- Brief In-scope items (not criteria, checked one by one): forward returns + guard (`labels.py`, `core.pit.guard_not_label`); Pearson/Spearman IC per date, mean, Newey-West t, IR, hit rate, by sector, by regime (`ic.py`); 5 buckets, mean return per bucket, monotonicity, long-short spread, cumulative spread chart data (`quantiles.py`); IC across horizons + rank autocorrelation (`ic.py`); vectorised L/S backtest, equal or signal weights, rebalance frequency, cost bps, daily returns + Sharpe/turnover (`vector_backtest.py`); `SignalTearSheet` dict + figures (`tearsheet.py`); CLI (`cli/research_cmd.py`). See the [worklog](worklog.md).
- Result on the real factor (a finding, not a criterion): `momentum_12_1` on S&P 500 members 2016–2021 has a small negative IC (21-session Spearman −0.040, NW t −1.57); the monthly equal-weight L/S lost 6.9% a year (Sharpe −0.29).
- Waivers: none

**Result:** ALL CRITERIA VERIFIED
