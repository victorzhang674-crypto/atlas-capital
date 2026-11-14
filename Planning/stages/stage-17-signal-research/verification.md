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
  - On merged `main` (`d3337f5`, fast-forward of `379731a` via the owner's Terminal panel): `uv lock --check` ok; `uv run pytest -q` → 2428 passed, 14 deselected; brief's Verify → 58 passed; `make check` → exit 0; `git branch --merged main` lists the stage branch; pushed `main` and `claude/stage-17-signal-research-905219` to `origin` · raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt)
  - GitHub Actions CI on `d3337f5`: run 41850555615 (`main`) ✓ `check` in 9m24s; run 41850555289 (branch) ✓ `check` in 7m17s · raw: [artifacts/ci-runs.txt](artifacts/ci-runs.txt)
- Acceptance criteria:
  - [x] On synthetic data with a planted signal, the toolkit recovers the IC within ±0.01 — evidence: `test_planted_ic_recovered_within_tolerance` (500 names × 400 dates, planted 0.05 → mean Pearson IC 0.05123, Spearman 0.05021); `test_planted_ic_end_to_end_through_the_lake` (returns written to a lake with an exact per-date Pearson IC of 0.05, read back through `DataView` by `forward_returns` → every date's Pearson IC 0.05 within 1e-15, mean Spearman 0.04469, |error| 0.0053); both in the brief's Verify run (58 passed); the label timing is pinned by `test_forward_returns_align_t_to_t_plus_h` and `test_forward_returns_skip_the_decision_day`, and the guard by `test_labels_are_tagged_and_guarded` and five `LEAK` mutants caught
  - [x] The CLI produces a tear sheet for a real factor — evidence: the real-lake `atlas research signal --name momentum_12_1 --from 2016 --to 2021` run above (exit 0; `tearsheet.json`, `ic_series.csv`, `quantile_returns.csv`, `backtest_returns.csv`, `ic.png`, `quantiles.png`, `cumulative_spread.png`, `decay.png`, `backtest.png`; figures inspected), its independent cross-check, and the CLI tests (`test_research_signal_writes_tearsheet_to_lake_runs` writes to the default `<lake>/runs/research/<name>/`, `--out`, exit codes 1/2)
- Brief In-scope items (not criteria, checked one by one): forward returns + guard (`labels.py`, `core.pit.guard_not_label`); Pearson/Spearman IC per date, mean, Newey-West t, IR, hit rate, by sector, by regime (`ic.py`); 5 buckets, mean return per bucket, monotonicity, long-short spread, cumulative spread chart data (`quantiles.py`); IC across horizons + rank autocorrelation (`ic.py`); vectorised L/S backtest, equal or signal weights, rebalance frequency, cost bps, daily returns + Sharpe/turnover (`vector_backtest.py`); `SignalTearSheet` dict + figures (`tearsheet.py`); CLI (`cli/research_cmd.py`). See the [worklog](worklog.md).
- Result on the real factor (a finding, not a criterion): `momentum_12_1` on S&P 500 members 2016–2021 has a small negative IC (21-session Spearman −0.040, NW t −1.57); the monthly equal-weight L/S lost 6.9% a year (Sharpe −0.29).
- Waivers: none

**Result:** ALL CRITERIA VERIFIED

#### Post-DONE audit follow-up — Stage 17 — 2026-11-04
Owner's request after DONE: audit the whole stage; owner's decision: "Fix A–C, note D–H" (see the [worklog](worklog.md) and the Decisions log).
- Fixed: (A) `atlas research signal` runs inside a `run_context` (stage `research.signal`), logs JSON lines with the `run_id` to `research.log` in the output folder, and the tear sheet records `run` — `test_research_signal_writes_tearsheet_to_lake_runs`, `test_tearsheet_records_the_run`; (B) `turnover_proxy` = 1 − lag-1 rank autocorrelation whatever lags are configured — `test_turnover_proxy_uses_lag_one`; (C) `artifacts/merged-main-check-records.txt` committed. Noted: D–H in `PROGRESS.md › Backlog`.
- Commands run (this chat), raw `artifacts/followup-*.txt`:
  - `uv run pytest -q` → 2430 passed, 0 failed, 14 deselected, exit 0
  - `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 248 files already formatted; `uv run mypy src/atlas` → no issues in 108 files
  - brief's Verify `uv run pytest tests/unit/quant/research -q` → 60 passed; research + CLI `-W error` → 70 passed; coverage 98%
  - `make check` → exit 0 (ruff, format, mypy, 2430 passed, 14 deselected)
  - mutation checks → 30/30 caught (3 new mutants for A and B)
- The acceptance criteria above are unchanged and still verified (the planted-IC tests are in the 60 passed).

#### Phase 2 checkpoint follow-up (Tier 2: hold-out guard, audit D, E, F, H) — 2026-11-12
- Red first: every new test fails against the source of `a45c544` · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-red.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-red.txt). Details in [worklog.md](worklog.md).
- `uv run pytest tests/unit/quant/research -q` (brief's Verify) → 79 passed; `tests/unit/cli` → 66 passed · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-verify.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-verify.txt); `make check` → exit 0 (ruff clean, 381 files formatted, mypy clean on 157 files, 4099 passed, 38 deselected) · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-make-check.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-make-check.txt)
- Fixed, each with its tests:
  - hold-out guard — `test_research_holdout.py` (6 tests), `test_research_signal_refuses_a_window_that_reads_the_holdout`, `test_allow_holdout_runs_and_records_the_override` (one `OVERRIDE` event, hash chain verified), `test_allow_holdout_inside_the_sample_records_nothing`, `test_an_override_that_cannot_be_recorded_stops_the_run`, `test_tearsheet_says_whether_its_labels_read_the_holdout`
  - audit E — `test_weights_backtest_raises_when_the_book_loses_everything` (−100% and worse), `test_weights_backtest_runs_just_above_a_total_loss`
  - audit F — `test_daily_is_handed_out_as_a_copy`, `test_label_frames_are_handed_out_as_copies`, `test_forward_returns_keeps_its_own_copy_of_the_frames_it_is_given`
  - audit D — `test_newey_west_keep_gaps_pairs_only_true_neighbours` (hand-computed), `test_ic_by_regime_t_stat_respects_the_gaps`, `test_regimes_option_reads_the_stored_history`, `test_regimes_option_other_kinds_and_errors`, `test_tearsheet_records_where_the_regimes_came_from`
  - audit H — `test_header_counts_the_dates_with_an_ic`, `test_header_is_plain_when_every_date_has_an_ic`
- Main lake (read only): the default-config window `--from 2016 --to 2021` is refused (exit 2); `--allow-holdout` stops with exit 1 where the audit log cannot be written · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-real-lake-guard.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-real-lake-guard.txt). `atlas research signal --name momentum_12_1 --from 2016 --to 2021-10-01 --regimes vol` (scratch output) → exit 0 in 182 s: 70 monthly dates, 69 with an IC (header: "69 dates with an IC (70 requested)"), labels close on 2021-12-31 (`reads_holdout` false); IC by `vol_regime` at 21 sessions from the stored `regime_history`: CRISIS −0.170 (t −1.14, 4 dates), HIGH −0.076 (t −2.11, 24), LOW −0.001 (t −0.02, 13), NORMAL +0.005 (t +0.11, 28); every date has a stored regime. At 21 sessions on monthly dates the Newey-West lag count is 0, so the gap-aware sum only matters for the 63-session rows here · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-real-lake-regimes.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-real-lake-regimes.txt), [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-real-lake-regimes-summary.json](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-real-lake-regimes-summary.json)
- Acceptance criteria unchanged and still evidenced (the planted-IC, quantile and backtest tests are in the brief's Verify).
- Not done: no mutation run for these changes.

#### Phase 2 checkpoint follow-up (Tier 3 item 4: `--cached-factors`) — 2026-11-13
- Red first (against the source of `697b079`), then green: the tests named in [worklog.md](worklog.md); brief's Verify `uv run pytest tests/unit/quant/research -q` → 81 passed; `tests/unit/cli` → 71 passed; `make check` → exit 0 (ruff clean, 384 files formatted, mypy clean on 157 files, 4121 passed, parallel in 135 s); `uv run pytest -q` (serial) → 4121 passed, 71 deselected in 304 s; `make test-integration` → 41 passed, 15 skipped (no lake) · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-verify.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-verify.txt), [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-make-check.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-make-check.txt), [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-red.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier3-red.txt)
- A weak existing test (`test_signal_panel_fundamental_uses_factor_exposures`, NaN compared with NaN) now checks real values.
- Acceptance criteria unchanged and still evidenced (in the brief's Verify).

#### Phase 2 checkpoint follow-up (Tier 4: t-statistics keep the gaps; CLI options; mutants) — 2026-11-14
- Commands run (this chat): `make check` → exit 0 (ruff clean, 393 files formatted, mypy clean on 157 files, 4241 passed in parallel and 3 timing tests alone); `uv run pytest -q` → 4244 passed, 71 deselected; integration on the main lake → 55 passed, 1 skipped (the flag-gated 2018–2024 calibration) · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-final-checks.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-final-checks.txt); red first: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-red.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-red.txt); mutants: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-mutate.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-mutate.txt)
- `tests/unit/quant/research/`, `tests/unit/cli/test_research_cmd.py` pass.
- Acceptance criteria unchanged and still evidenced.

**Result:** ALL CRITERIA VERIFIED
