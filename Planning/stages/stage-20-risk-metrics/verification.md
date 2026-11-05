# Stage 20 — Risk & performance metrics library · Verification evidence

Every output below comes from commands run in this stage's chat (Project.md Rule 1), on the final code. Raw output is in `artifacts/`. Commands ran as `UV_CACHE_DIR=$TMPDIR/uv-cache-s20 UV_NO_SYNC=1 MPLCONFIGDIR=$TMPDIR/mpl uv run …` (sandbox).

#### Verification evidence — Stage 20 — 2026-11-04
- Commands run (this chat):
  - `make check` (= `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/atlas`, `uv run pytest -q`) → exit 0: All checks passed! · 296 files already formatted · Success: no issues found in 127 source files · **2801 passed, 19 deselected** (live/llm/integration) · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
  - `uv run pytest tests/unit/quant/metrics -q` (brief's Verify) → **127 passed** · raw: [artifacts/verify-brief.txt](artifacts/verify-brief.txt)
  - `uv run pytest tests/unit/quant/metrics -q -W error` → 127 passed · raw: [artifacts/werror.txt](artifacts/werror.txt)
  - coverage of `atlas.quant.metrics` → **99%** (performance, relative, contributions, liquidity, exposure, `__init__` 100%; var 99%; `_common` 98%) · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
  - mutation checks `artifacts/mutate.py` → **72/72 caught on the final code**. First run 71/72: survivor "rolling beta ignores the risk-free rate" (a constant rate cancels in cov/var; test gap) → `test_time_varying_risk_free_changes_beta` added → 2/2 on rerun → full rerun 72/72 · raw: [artifacts/mutation-checks.txt](artifacts/mutation-checks.txt), [artifacts/mutation-checks-rerun.txt](artifacts/mutation-checks-rerun.txt), [artifacts/mutation-checks-final.txt](artifacts/mutation-checks-final.txt)
  - Real lake (read only), `artifacts/real_lake_check.py`, decision day 2025-01-02 → exit 0, all invariants held: SPY 2020–2024 Sharpe 0.746, max drawdown 33.7% (COVID 2020), 2022 episode 24.5% / 489 sessions; plain-pandas cross-checks equal; long/short book VaR95 0.49–0.51%, CVaR97.5 0.71–0.75% (every method); Monte Carlo within 0.11% of normal; contributions sum to the vol; beta 0.2575 = beta-adjusted net; liquidity 96/98/100% liquid in 1/3/5 days · raw: [artifacts/real-lake-check.txt](artifacts/real-lake-check.txt)
  - Reference values: empyrical-reloaded 0.7.2 in an isolated environment (not a project dependency) → `tests/fixtures/metrics/empyrical_reference.json` (30 values) · generator: [artifacts/empyrical_reference.py](artifacts/empyrical_reference.py)
  - On merged `main` (`c844465`, fast-forward of `bfd5290` via the owner's Terminal panel): `uv run pytest -q` → 2801 passed, 19 deselected; brief's Verify → 127 passed; `make check` → exit 0; `git branch --merged main` lists the stage branch; pushed `main` and `claude/stage-20-completion-30d1cd` · raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt)
  - GitHub Actions CI on `c844465`: success on `main` (41949981068) and the branch (41949980641) · raw: [artifacts/ci-runs.txt](artifacts/ci-runs.txt)
  - Gate (stage start): `uv run pytest -q` → 2658 passed, exit 0 · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
- Acceptance criteria:
  - [x] **Every metric documented with units** — evidence: `test_every_module_states_units` (each module docstring has a `Units:` section), `test_every_public_callable_documents_its_units` (each of the 69 public names' docstring states its units; it caught 6 missing ones, fixed), config `MetricsConfig` docstring + a YAML comment per key (`test_every_yaml_key_has_a_comment`)
  - [x] **Every metric tested** — evidence: `test_every_public_name_is_tested` (all 69 public names exercised by tests); 127 tests in `tests/unit/quant/metrics`: hand examples (stdlib `statistics`, `scipy.stats`, `scipy.integrate.quad`), **empyrical cross-checks** (30 reference values at rel ≤ 1e-9: annual return/vol, Sharpe ± rf, Sortino ± required return, downside risk, Calmar, Omega ± threshold, max drawdown, tail ratio, skew, kurtosis, alpha/beta ± rf, up/down capture, excess Sharpe, historical VaR 5%/1%, CVaR 5%, rolling Sharpe/vol/max drawdown/beta), **hypothesis** `test_component_contributions_sum_to_total_vol`, `test_cvar_is_never_below_var` (every series method), `test_monte_carlo_cvar_is_never_below_var`, drawdown, exposure and homogeneity properties; mutation checks 72/72
  - [x] **Single source of truth (later stages import from here only)** — evidence: `atlas.quant.metrics.__all__` (`test_public_api_is_exported`); Stage 17 backtest summary now computed by the library (`test_research_backtest_summary_uses_the_library`, `test_summary_drawdown_counts_the_first_session`); `test_no_other_module_defines_a_library_metric` and `test_no_other_module_computes_a_running_peak_drawdown` scan `src/atlas`; `risk_contributions(w, RiskModel)` delegates to Stage 19 (`test_matches_stage19_portfolio_risk`); VaR/CVaR confidences, ADV participation and window are read from `limits` (one source). Scope boundary (Decisions log): Stage 15 features `max_dd_252d` / rolling vol are per-name signal inputs, not fund numbers
- Brief scope items, each implemented and tested: returns-based (`performance.py`), benchmark-relative (`relative.py`), VaR/CVaR historical / normal / Cornish-Fisher / Monte Carlo, portfolio (weights + returns or cov) and P&L series, horizon scaling (`var.py`), marginal / component / percent contributions (`contributions.py`), days-to-liquidate and % liquid in 1/3/5 days (`liquidity.py`), gross / net / long / short / beta-adjusted net / sector nets (`exposure.py`)
- Waivers: none

**Result:** ALL CRITERIA VERIFIED

#### Post-DONE audit follow-up — Stage 20 — 2026-11-05
Owner: "can you audit the important ones for the not being fully audited things?" (after "is everything for this stage being audited?"). Details in the worklog.
- Commands run (this chat, final code):
  - `make check` → exit 0: All checks passed! · 298 files already formatted · Success: no issues found in 127 source files · **2831 passed, 23 deselected** · raw: [artifacts/make-check-followup.txt](artifacts/make-check-followup.txt)
  - `uv run pytest tests/unit/quant/metrics -q` (brief's Verify) → **157 passed**; `-W error` → 157 passed · raw: [artifacts/verify-brief-followup.txt](artifacts/verify-brief-followup.txt), [artifacts/werror-followup.txt](artifacts/werror-followup.txt)
  - coverage of `atlas.quant.metrics` → **100%** (764 statements) · raw: [artifacts/coverage-followup.txt](artifacts/coverage-followup.txt)
  - mutation checks (78 mutants incl. 6 new constant-series ones) → **78/78 caught** (first run 77/78; flat-book test added) · raw: [artifacts/mutation-checks-followup.txt](artifacts/mutation-checks-followup.txt), [artifacts/mutation-checks-followup-rerun.txt](artifacts/mutation-checks-followup-rerun.txt), [artifacts/mutation-checks-followup-final.txt](artifacts/mutation-checks-followup-final.txt)
  - `ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data uv run pytest tests/integration/test_metrics_real_lake.py -m integration -v` → **4 passed** (without the lake: 4 skipped) · raw: [artifacts/integration-real-lake.txt](artifacts/integration-real-lake.txt)
  - Stage 17 real-lake tear sheet re-check → library summary = recorded numbers (diff 0) · raw: [artifacts/stage17-recheck.txt](artifacts/stage17-recheck.txt)
  - CI on the records commit `c54c69f`: success on `main` (41960545116) and the branch (41960545285) · raw: [artifacts/ci-runs-records.txt](artifacts/ci-runs-records.txt)
  - On merged `main` (`cffd7f7`, fast-forward of `c54c69f`): `uv run pytest -q` → 2831 passed; brief's Verify → 157 passed; `make check` → exit 0; pushed · raw: [artifacts/merged-main-check-followup.txt](artifacts/merged-main-check-followup.txt)
  - GitHub Actions CI on `cffd7f7`: success on `main` (41964994717) and the branch (41964994262) · raw: [artifacts/ci-runs-followup.txt](artifacts/ci-runs-followup.txt)
- Bug found and fixed: constant series with rounding noise gave huge Sharpe / IR (~1e16), a finite beta on a constant benchmark and noise moments → now NaN / 0 via `is_constant` / `sample_std` (tests listed in the worklog).
- Independent re-derivations: `test_metrics_independent.py` (Cornish-Fisher by stratified simulation, contributions by the covariance form, liquidity by a liquidation simulation, exposures by loops, normal CVaR by scipy).
- Not done (owner's call): an independent reviewer; a generated (exhaustive) mutation run.
- Acceptance criteria: unchanged and still met (evidence above supersedes the counts of the first block).

**Result:** ALL CRITERIA VERIFIED

#### Completion: generated mutation run — Stage 20 — 2026-11-05
Owner: "can you complete this stage?" (details in the worklog).
- Commands run (this chat, final code):
  - mutmut 3.8.1 (throwaway overlay, scratch copy) on `src/atlas/quant/metrics/` with `tests/unit/quant/metrics/` → **2,678 mutants: 2,468 killed, 5 timeouts, 205 survived**; first run 2,201 / 476 survived. All 205 classified: 129 cosmetic, 76 equivalent (14 counted classes, each with its reason) · [artifacts/mutmut-triage.md](artifacts/mutmut-triage.md), [artifacts/mutmut-survivors.txt](artifacts/mutmut-survivors.txt), [artifacts/mutmut-results.txt](artifacts/mutmut-results.txt), [artifacts/mutmut-runs.txt](artifacts/mutmut-runs.txt)
  - **Bug found and fixed:** Monte Carlo `portfolio_var` ignored a by-symbol `mean` (`test_portfolio_var_passes_every_argument`, fails on the old code)
  - hand-picked mutation script → **78/78** · raw: [artifacts/mutation-checks-completion.txt](artifacts/mutation-checks-completion.txt)
  - `make check` → exit 0: All checks passed! · 299 files already formatted · Success: no issues found in 127 source files · **2871 passed, 23 deselected** · raw: [artifacts/make-check-completion.txt](artifacts/make-check-completion.txt)
  - brief's Verify → **197 passed**; `-W error` → 197 passed; coverage of `atlas.quant.metrics` **100%** · raw: [artifacts/verify-brief-completion.txt](artifacts/verify-brief-completion.txt), [artifacts/werror-completion.txt](artifacts/werror-completion.txt), [artifacts/coverage-completion.txt](artifacts/coverage-completion.txt)
  - real-lake integration test → 4 passed · raw: [artifacts/integration-real-lake-completion.txt](artifacts/integration-real-lake-completion.txt)
- Not done (owner's call): an independent reviewer (needs a separate agent).
- Acceptance criteria: unchanged and still met.

**Result:** ALL CRITERIA VERIFIED

