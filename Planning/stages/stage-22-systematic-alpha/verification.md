# Stage 22 — Systematic alpha models · Verification evidence

Filled in only with output from commands actually run in this stage's chat (Project.md Rule 1). Raw outputs are under `artifacts/`.

#### Verification evidence — Stage 22 — 2026-11-07
- Commands run (this chat, worktree `claude/stage-22-systematic-alpha-b15459`):
  - Gate: `uv run pytest -q` → 3605 passed, 29 deselected, exit 0 · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
  - `make check` (= `ruff check .` + `ruff format --check .` + `mypy src/atlas` + `pytest -q`) → All checks passed · 337 files already formatted · Success: no issues found in 141 source files · **3672 passed, 29 deselected**, exit 0 · raw: [artifacts/make-check-worktree.txt](artifacts/make-check-worktree.txt)
  - Brief's Verify: `uv run pytest tests/unit/quant/alpha -q` → **67 passed**, exit 0 · raw: [artifacts/verify-alpha-tests.txt](artifacts/verify-alpha-tests.txt)
  - Mutation check: `python artifacts/mutate.py` → **12/12 mutants killed**, files restored · raw: [artifacts/mutate.txt](artifacts/mutate.txt)
  - Research run (Task 5, real lake read only): `research_run.py ~/Projects/atlas-capital/data <scratch>` → 70 decision days 2016-01-04 .. 2021-10-01, labels' last session 2021-12-31 (no hold-out bar read; label view at 2022-01-03 08:00), signals in 4,555 s, exit 0 · raw: [artifacts/research/research-run.log](artifacts/research/research-run.log), [summary.json](artifacts/research/summary.json), [tear sheet](artifacts/research/tearsheet-multifactor/), [IC history](artifacts/research/ic_history_h21.csv)
- Acceptance criteria (brief):
  - [x] **Models return calibrated expected returns (annualised, in fractions)** — evidence: contract `check_alpha_frame` (finite float `exp_return_ann`, `confidence` ∈ [0, 1], sorted unique `symbol` index; `test_contract_*`); Grinold `IC × σ_ann × z × √(P/h)` (`test_grinold_known_values`, √(P/h) mutant killed) is calibrated by construction: slope of realised on predicted ≈ 1 on planted returns (`test_grinold_is_calibrated_on_planted_returns`, `test_planted_alpha_ic_and_calibration`); every model's output meets the contract (`test_model_is_an_alpha_model_and_meets_contract`, `test_trending_series`, `test_decays_linearly_with_the_decision_day`); TSMOM `exp = 0.3 × σ × blend` and PEAD `exp = 0.06 × score` (`test_inverse_vol_scaling_and_expected_return`, `test_pead_signal_pure`). *Real-data calibration slopes are recorded below; they are negative because the in-sample ICs are, not because of units.*
  - [ ] **The in-sample IC of the composite is positive with a t-stat recorded** — **NOT MET.** Pre-registered (plan interpretation 9): the fixed-weight composite (equal weights 0.2, chosen before any real-data run) at 21 sessions, monthly dates: **mean Spearman IC −0.0062, NW t −0.42** (69 dates, hit rate 52.2%); Pearson −0.0127 (t −0.73). The t-stat is recorded; the sign is not positive. Not tuned (pre-registered rule) → stage `BLOCKED` for the owner's decision.
- In-sample results (all from [summary.json](artifacts/research/summary.json); h = 21 sessions, NW lags 0 since the monthly step is 21 sessions):
  - Composite IC by horizon (Spearman mean, t): h1 −0.0040 (−0.22), h5 −0.0058 (−0.30), h21 −0.0062 (−0.42), h63 −0.0145 (−0.60). Quintile spread h21 −0.35% (t −0.89). L/S quintile backtest (Stage 17, 5 bps): ann. return −3.6%, vol 12.7%, **Sharpe −0.28**, max DD 35.6%. ~429 names scored per date.
  - By vol regime (h1 IC): LOW +0.034 (13 dates), NORMAL +0.031, HIGH −0.067 (t −2.30, 24 dates), CRISIS +0.003 (4). The rolling IC is positive through 2017–2019 and negative in 2016 and Nov 2020 – Jan 2021 (−0.41 on 2020-11-02).
  - Components (h21 IC, t): momentum −0.023 (−1.06), value −0.016 (−0.84), **quality +0.017 (+1.11)**, low-vol −0.006 (−0.27), reversal +0.010 (+0.59).
  - IC-weighted composite (PIT, known ICs, 36 m, ≥ 12 obs; IC weights on 57 dates): −0.024 (t −1.30) on all dates; −0.016 (t −0.76) on the 57 dates vs the fixed composite's +0.007 (t +0.44) on the same dates.
  - Multi-factor calibration (realised 21d × 12, cross-sectionally demeaned, on `exp_return_ann`): slope −0.88 (30,047 pairs); mean |exp_return_ann| 2.3% a year.
  - PEAD: IC h21 −0.016 (t −1.86), ~399 names / date, calibration slope −0.34.
  - TSMOM (macro ETFs, ~29 / date): inverse-vol book monthly mean +0.19%, **Sharpe +0.67** (70 months), hit rate 54.4%; calibration slope −1.54 (2,058 pairs, intercept +0.125: ETF returns' level dominates); cross-sectional IC −0.014 (t −0.39).
- Waivers: none (the failed criterion needs the owner's decision).

**Result:** NOT VERIFIED — `BLOCKED`: the composite's in-sample IC is negative (pre-registered weights). All other evidence above passed.
