# Stage 18 — Covariance & volatility forecasting · Work log

Append entries in order. Record failures and dead ends as honestly as successes. Commands run as `UV_CACHE_DIR=$TMPDIR/uv-cache-s18 UV_NO_SYNC=1 MPLCONFIGDIR=$TMPDIR/mpl uv run …` (sandbox), shortened below to `uv run …`.

## 2026-11-04 10:50 — Rule 0, reading, gate check
- Did: read `Project.md`, `Planning/PROGRESS.md` (Stage 17 handoff note, Backlog), the brief, roadmap row 18, spec §3/§9/§11/§12/§15, the Stage 15–17 quant code (`atlas.quant.features`, `factors`, `research`) and `atlas.data.view.DataView.returns`. Rule 0: "Project.md read. Current stage: 18 — Covariance & volatility forecasting (NOT STARTED)."
- Ran: `uv sync --group dev` in the new worktree (PyPI allowed) → ok; `uv run pytest -q` → 2430 passed, 14 deselected, exit 0 (159 s) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt). Stage 17 `verification.md` → **ALL CRITERIA VERIFIED**. Gate passed.
- Next: copy the template, write `plan.md`.

## 2026-11-04 11:10 — stage folder and plan
- Did: copied `Planning/stages/_template/{worklog,verification}.md`; wrote [plan.md](plan.md) (10 interpretations, D1–D5, 13 tasks); `PROGRESS.md`: stage IN PROGRESS, three Decisions log rows. Checked PyPI: `arch` 8.0.0 (numpy < 3, pandas ≥ 1.4, scipy, statsmodels).
- Next: Task 1.

## 2026-11-04 11:20 — Task 1: `quant.risk_model` config
- Did: tests `test_quant_risk_model_defaults`, `test_quant_risk_model_validation` (12 cases), `test_quant_risk_model_blend_weights_sum_to_one` in `tests/unit/core/test_config.py`.
- Ran: `uv run pytest tests/unit/core/test_config.py -q -k risk_model` → red (ImportError: no `BlendWeights`).
- Implemented `BlendWeights`, `VolatilityConfig`, `RiskModelConfig`, `QuantConfig.risk_model` in `src/atlas/core/config.py`; YAML block `quant.risk_model` in `config/fund.yaml`.
- Ran: `uv run pytest tests/unit/core/test_config.py -q` → 1 failed (`test_every_yaml_key_has_a_comment`: the `volatility:` key had no comment) → added a comment line → 255 passed.

## 2026-11-04 11:25 — Task 2: `arch` dependency
- Did: test `test_arch_is_a_runtime_dependency` in `tests/unit/test_dependency_floors.py`.
- Ran: `uv run pytest tests/unit/test_dependency_floors.py -q` → 1 failed (no `arch>=` floor), 3 passed.
- Implemented: `uv add "arch>=8.0"` (PyPI via `allowed_domains`) → arch 8.0.0, scipy 1.18.1, statsmodels 0.15.0, patsy, formulaic, narwhals, interface-meta, wrapt; `pyproject.toml` dependency comment updated. `arch` ships `py.typed`.
- Ran: `uv lock --check` → "Resolved 95 packages"; the test file → 4 passed.

## 2026-11-04 11:35 — Task 3: PSD repair and condition number (brief task 3)
- Did: `tests/unit/quant/risk_model/test_risk_psd.py` (8 tests, 11 cases incl. Higham's 2002 published 4×4 example and a Higham 1988 optimality check against 200 random PSD candidates).
- Ran: → red (collection error: no `atlas.quant.risk_model`).
- Implemented `src/atlas/quant/risk_model/{__init__,psd}.py`: `is_psd`, `condition_number` (`inf` when λmin ≤ n·eps·|λmax|), `nearest_psd` (eigenvalue clipping; `keep_diagonal=True` → nearest correlation rescaled), `nearest_correlation` (Higham 2002 with Dykstra's correction; returns the PSD iterate rescaled to a unit diagonal), `floor_condition`.
- Ran: → 11 passed on the first run.

## 2026-11-04 11:50 — Task 4: `CovResult`, input checks, history and missing-data policy
- Did: `tests/unit/quant/risk_model/risk_world.py` (known Σ generators, MVN returns, standard errors, GARCH simulator) and the Task 4 tests in `test_risk_covariance.py` (8 tests, 14 cases).
- Ran: → red (no `covariance` module).
- Implemented `src/atlas/quant/risk_model/covariance.py`: `corr_from_cov`, `cov_from_corr`, `CovResult` (frozen, read-only `matrix`, `cov`/`vol`/`corr` fresh frames, read-only `dropped`), `_validate`, `_overlap_drops`, `CovarianceEstimator` (history/constant/overlap/complete policy, `_finalise`: symmetrise → `nearest_psd(keep_diagonal=True)` → `floor_condition`), `SampleCovariance` (pandas-style pairwise moments).
- Ran: → 4 failed, 10 passed. Fixes: (1) the overlap rule multiplied a **boolean** mask (`bool @ bool` is a logical OR of ANDs, so every pair "had" 1 common session and every name was dropped) → cast to float; (2) the read-only test used chained assignment (`res.cov.iloc[0, 0] = …`), which pandas 3 refuses (`ChainedAssignmentError`) → the test now edits the frames a caller holds. → 14 passed.

## 2026-11-04 12:05 — Task 5: sample and EWMA against a known Σ (brief task 1)
- Did: Task 5 tests in `test_risk_covariance.py` (7 tests). `ewma_lambda` moved forward from Task 8: `EWMACovariance` needs it, so `volatility.py` was created here with only that function (its tests are `test_ewma_half_life_equals_lambda` here).
- Ran: → red (ImportError: no `EWMACovariance`, no `atlas.quant.risk_model.volatility`).
- Implemented `ewma_lambda` (`volatility.py`) and `EWMACovariance` (row-position weights `λ^k`, pairwise normalisation `(M·w)'M`).
- Ran: → 2 failed, 19 passed. Both were test-design errors, not code bugs: (1) the holed EWMA example (`Y = [1, −1, 2]`, middle return missing) is itself **not PSD** pairwise (var X 6.43, var Y 3.4, cov 5.0 → det < 0), so the repair and the condition floor rightly changed it; the example now uses `Y = [1, −1, 0.5]` and asserts `adjustments == ()`; (2) "EWMA error < half the sample error" after a 4× variance shift held on average but not on one path (λ = 0.94 is ~32 effective sessions). Measured over 20 paths: EWMA nearer on 20/20, mean error ratio 0.53, worst 0.92 → the test asserts every path nearer and mean ratio < 0.75. → 21 passed.
- Note: the sample tests (`test_sample_recovers_known_sigma`, `test_sample_matches_pandas_pairwise`) passed on arrival, because `SampleCovariance` was completed in Task 4; they are covered by the mutation checks (Task 12).

## 2026-11-04 12:25 — Task 6: Ledoit-Wolf (constant correlation) and OAS (brief task 2)
- Did: `tests/unit/quant/risk_model/test_risk_shrinkage.py` (7 tests): a loop-based literal Ledoit-Wolf (2004) implementation as the reference, OAS eq. 23 on a 6×3 example, comparisons with the sample estimator, unit-interval and long-history checks.
- Ran: → red (ImportError).
- Implemented `_moments`, `LedoitWolfCovariance` (masked matrix form of π̂, ϑ, ρ̂, γ̂; `T` = mean pair count), `OASCovariance`.
- Ran: → 4 failed: T = 60 < the default `min_history` 63 → the estimators refused (`DataError`, correct); fixed the test helper (`min_history=20`). → 3 failed: the comparison claims. The literal cross-check passed at 1e-12, so the code matches the paper; the claims were too strong. Measured (20 paths, Frobenius error vs sample): N = 50, T = 60: LW 0.51 (constant-correlation truth, 20/20 wins), 0.94 (factor truth, 15/20); OAS 0.94 and 0.94 (12–13/20). N = 100, T = 50: LW 0.47 (20/20) and 0.87 (18/20); OAS 0.91 (14/20) and 0.87 (17/20). The gain grows with N/T; OAS's scaled-identity target ignores the 1–3% spread of vols, so on a constant-correlation truth it helps little. Tests re-based on N = 100, T = 50: LW mean ratio < 0.6 / 0.95 with ≥ 18 / 15 wins; OAS on a spherical truth (its own target) < 0.5 with 20 wins and on the factor truth < 0.95 with ≥ 14 wins. → 9 passed.

## 2026-11-04 12:40 — Task 7: repair and condition check inside the estimators; factory; all outputs PSD
- Did: Task 7 tests in `test_risk_covariance.py`: a pairwise pattern whose raw estimate has a negative eigenvalue (asserted), the N > T floor, the factory, and a hypothesis property test (60 random frames: 1–7 names, 8–90 sessions, 0–40% holes, near-collinear columns; every method).
- Ran: → red (ImportError: no `covariance_estimator`, `METHODS` import).
- Implemented `covariance_estimator(method=None, *, config=None, **kwargs)` (default `quant.risk_model.default_method`).
- Ran: → 25 passed. The repair/floor tests passed on arrival: `_finalise` was written in Task 4 (its mutation checks are in Task 12).

## 2026-11-04 12:55 — Task 8: annualisation, EWMA and realised vol
- Did: `tests/unit/quant/risk_model/test_risk_volatility.py` (8 tests, 12 cases): helpers keep the type (float/ndarray/Series/DataFrame), the EWMA recursion by hand (λ = 0.5, missing return decays), the path's no-look-ahead (truncation and rewriting the future), EWMA vol = `EWMACovariance` diagonal, realised window, `DataError` below `min_history` and on zero variance, input checks.
- Ran: → red (ImportError).
- Implemented in `volatility.py`: `annualise_vol`, `annualise_variance`, `deannualise_vol` (P = `quant.features.sessions_per_year`), `VolForecast` (frozen, read-only `params`, `vol`, `annual_vol`), `_series`/`_checked`/`_forecast` (validation, `min_history`, zero variance → `DataError`: a zero vol would make vol-target sizing divide by zero, so no forecast is the fail-safe answer), `ewma_variance_path`, `ewma_vol`, `realised_vol`.
- Ran: `uv run pytest tests/unit/quant/risk_model -q` → 57 passed (first run).

## 2026-11-04 13:10 — Task 9: GARCH(1,1) via `arch` with EWMA fallback (brief task 4)
- Did: `tests/unit/quant/risk_model/test_risk_garch.py` (7 tests, 13 cases): parameter recovery on a seeded simulated path (ω 0.02 %², α 0.08, β 0.90, T 6000), the forecast against the one-step recursion computed by hand from an independent `arch` fit, fallbacks (non-convergence, optimiser raising RuntimeError / LinAlgError / ValueError, five invalid parameter sets, short history with the fit never called), NaN handling (the fit receives the 997 clean returns in percent). Warnings checked with `structlog.testing.capture_logs`.
- Ran: → red (ImportError: no `garch_vol`).
- Implemented `_fit_garch` (lazy `arch` import: covariance users do not pay for scipy/statsmodels) and `garch_vol` (fallback returns the EWMA forecast with `method="ewma"` and the reason in `fallback`; parameters reported in daily fractions; forecast from `arch`'s `forecast(horizon=1)`).
- Ran: → 13 passed (first run). Fitted on the test path: ω 0.0176 %² (true 0.02), α 0.0802 (0.08), β 0.9060 (0.90), persistence 0.9861.

## 2026-11-04 13:20 — Task 10: realised-vol blend and the forecast entry points
- Did: Task 10 tests in `test_risk_volatility.py` (5 tests): the blend by hand from the three components, the carried GARCH fallback (short series → GARCH component = EWMA), dispatch, per-column forecasts (a short column is left out and logged), `annual_vol`.
- Ran: → red (ImportError: no `blend_vol`, `forecast_vol`, `forecast_vols`).
- Implemented `blend_vol` (a zero-weight component is not computed), `VOL_METHODS`, `forecast_vol`, `forecast_vols` (`DataError` per name → left out, `vol_forecast_skipped` warning).
- Ran: `uv run pytest tests/unit/quant/risk_model -q` → 75 passed.

## 2026-11-04 13:30 — Task 11: package surface and look-ahead
- Did: `test_package_exports`, `test_package_import_does_not_load_arch` (subprocess: `arch` not in `sys.modules` after importing the package), `test_estimate_uses_only_the_given_rows` (every method: rewriting the rows after the cut changes nothing; a reversed frame is refused; EWMA moves > 1000× more for a shock on the last row than on the first).
- Ran: → 1 failed (`__all__` incomplete), 2 passed on arrival (the lazy import and the ordering check were already in place).
- Implemented the package `__init__` (28 exports, module docstring).
- Ran: → 78 passed. Then lint/type clean-up: `ruff check .` → 54 errors: line lengths (formatter), UP047 (the repo uses PEP 695 type parameters, e.g. `atlas.core.pit.tag_label[T: …]`: converted the `TypeVar`s) and RUF002/RUF003 (confusable α, σ, ρ, γ, ×, − in docstrings/comments: spelled out; λ, Σ are not flagged); mypy → 12 redundant casts (removed with the `TypeVar`s). → ruff clean, mypy "no issues found in 112 source files", 337 passed (risk_model + config + dependency floors).

## 2026-11-04 13:45 — review before verification (two in-scope gaps fixed, test-first)
- Read `covariance.py` end to end. Found: (1) under `missing="complete"` the constant check ran on each name's own history, so a name that moves only before the common sessions start would reach the estimator with zero variance (and break `corr`/the PSD repair); (2) EWMA weights for sessions ~1,100+ before the end underflow to 0 at λ = 0.5, giving 0/0 = NaN and an opaque `ValueError` from the PSD check.
- Tests `test_complete_policy_drops_names_constant_on_common_sessions`, `test_non_finite_estimate_raises_data_error` → red (dropped `{}`; `ValueError: matrix has NaN`).
- Fixed: the complete policy loops (drop names constant on the common sessions with reason `constant`, recompute the rows); a non-finite raw estimate raises `DataError` (no covariance); the EWMA division runs under `np.errstate` so the expected 0/0 does not warn (else the `-W error` run fails); the numeric-dtype check simplified.
- Ran: `uv run pytest tests/unit/quant/risk_model -q -W error` → 80 passed; ruff, mypy clean.

## 2026-11-04 14:00 — Task 12: mutation checks (run 1) → two test gaps closed
- Did: `artifacts/mutate.py` (38 mutants: EWMA weights/order/normalisation, half-life, the EWMA path, ddof and demeaning, the Ledoit-Wolf π/ϑ/ρ/r̄/T/clamp terms and `S` normalisation, OAS 2/p terms, target and cap, Higham's Dykstra correction, clipping floor, `keep_diagonal`, condition floor, the estimator's repair/floor steps, `is_psd` tolerance, history/overlap/tie-break/constant rules, index order, GARCH validity/convergence/short-history/percent/ω units, blend weights, realised window and mean, annualisation, zero variance).
- Ran: → **36/38 caught** · raw: [artifacts/mutation-checks-run1.txt](artifacts/mutation-checks-run1.txt). Survivors: (1) "LW intensity not clamped below": no test had a negative κ; (2) "OAS 2/p terms dropped": the 6×3 example's ρ was clamped to 1, so the formula was never really checked.
- Fixed (tests): `test_ledoit_wolf_negative_intensity_is_clamped_to_zero` (a 3-name, 9-session one-factor sample with κ/T = −5.78, found by searching 20,000 seeds; the result must be `S`); the OAS example replaced by 40 seeded sessions with an interior ρ = 0.224 (0.279 without the 2/p terms), asserted `0 < ρ < 1`. Both pass on the real code.
- Ran: mutation checks again → **38/38 caught**.

## 2026-11-04 14:10 — Task 12: real-lake check → repair order fixed for the shrinkage estimators
- Did: `artifacts/real_lake_check.py` (read only, main lake): one `DataView` at decision day 2022-01-03, the 504 S&P 500 members' last 252 returns, all four estimators + Ledoit-Wolf `complete`, an independent pandas cross-check of the sample variances, vol forecasts (1,000 sessions) for AAPL, MSFT, JPM, XOM, SPY.
- Ran (first): every estimator's output PSD, symmetric, condition number ≤ 1e5, look-ahead ok (last return 2021-12-31 < 2022-01-03); 6 names dropped (5 `history`, 1 `overlap`). Two findings: (1) the `complete` policy raised `DataError: only 60 complete sessions, fewer than min_history (63)` — the designed fail-safe (a few late-listed names leave 60 common sessions; `pairwise` is the default for this reason); the script now reports it instead of stopping; (2) **Ledoit-Wolf and OAS also needed the PSD repair and then the condition floor** (cond pinned at 1e5): the pairwise `S` has negative eigenvalues, shrinkage did not cure them, and repairing the shrunk matrix to the nearest correlation matrix lands on the PSD boundary (a zero eigenvalue), which the floor then props up — losing the conditioning shrinkage is for.
- Fixed (test-first): `test_shrinkage_repairs_the_pairwise_sample_before_shrinking` (LW and OAS on the broken pairwise frame: adjustments must be only `nearest_psd`, cond < 1e5; the sample estimator still needs both) → red (both had `condition_floor`). `_moments` now repairs a non-PSD pairwise `S` (`nearest_psd(keep_diagonal=True)`) **before** shrinking; `_estimate` returns `(cov, shrinkage, repaired)` and `_finalise` records the repair; Ledoit-Wolf's π̂ and ϑ stay moments about the raw pairwise `S` (they are identities around it). → 83 passed with `-W error`. Mutation script: patterns updated, two mutants added (repair after shrinking; repair not recorded).
- Ran (again): Ledoit-Wolf δ 0.1333, min eigenvalue 1.03e-5, **cond 4.50e3, no floor**; OAS ρ 0.0464, **cond 2.85e3, no floor**; sample and EWMA (N = 498 > T = 252, singular) repaired and floored at 1e5; median annual vol 0.25–0.26, mean correlation 0.23–0.25 (EWMA 0.35: late-2021 Omicron weeks); each estimate 0.3–1.0 s; sample variances vs pandas max |diff| 3.9e-7 within the floor bound 4.8e-7. Vol forecasts: GARCH converged for all five (persistence 0.94–0.99, no fallback); SPY annual vol EWMA 15.3%, GARCH 12.3%, realised 16.8%, blend 14.5%; realised vols match numpy · raw: [artifacts/real-lake-check.txt](artifacts/real-lake-check.txt) (structlog warnings in `real-lake-check.stderr.txt`).

## 2026-11-04 14:35 — Task 12: stage verification
- Ran (all in this chat, raw output in `artifacts/`): brief's Verify `uv run pytest tests/unit/quant/risk_model -q` → 83 passed; same with `-W error` → 83 passed; coverage of `atlas.quant.risk_model` → 98%; mutation checks → 40/40 caught; `uv run pytest -q` → 2528 passed, 14 deselected; `ruff check .` → All checks passed!; `ruff format --check .` → 258 files already formatted; `mypy src/atlas` → no issues in 112 files; `make check` → exit 0.
- Wrote [verification.md](verification.md): **ALL CRITERIA VERIFIED**.

## 2026-11-04 14:40 — Task 13: self-audit against brief and plan
- In scope, item by item: estimators sample / EWMA (λ 0.94, half-life) / Ledoit-Wolf constant correlation / OAS ✓; minimum history and missing data (pairwise → nearest PSD; complete) ✓; `nearest_psd` (Higham) and condition check ✓; vol forecasts EWMA, GARCH(1,1) via `arch` with EWMA fallback, realised blend ✓; annualisation √252 and corr from cov ✓; `CovarianceEstimator.estimate(returns_df) -> CovResult{cov, vol, corr, method, n_obs}` ✓. Out of scope (factor covariance, Stage 19) untouched. Brief tasks 1–4 each have tests (verification.md). No CLI or long-running entry point in the brief → no `run_context` needed (warnings carry a caller's `run_id`).
- Plan deviations recorded in plan.md "As built". In-scope gaps fixed during the stage: complete-policy constants, non-finite EWMA, repair order for the shrinkage estimators, two test gaps from the mutation run.
- Backlog (PROGRESS.md › Backlog, Stage 18): a DataView convenience; multi-step GARCH; the pairwise approximation in LW/OAS; the `complete` policy's all-or-nothing window; N > T sample/EWMA always floored; GARCH cost per name.
- Code files created: `src/atlas/quant/risk_model/{__init__,psd,covariance,volatility}.py`; tests `tests/unit/quant/risk_model/{risk_world,test_risk_psd,test_risk_covariance,test_risk_shrinkage,test_risk_volatility,test_risk_garch}.py`. Changed: `src/atlas/core/config.py`, `config/fund.yaml`, `pyproject.toml`, `uv.lock`, `tests/unit/core/test_config.py`, `tests/unit/test_dependency_floors.py`.
- Measured a GARCH fit: 0.007 s per 1,000-session series (10 seeded paths, after a warm-up fit); an earlier estimate of ~0.3 s in the draft backlog note was wrong and was corrected before the commit.

## 2026-11-04 15:00 — merged into `main`, pushed
- Ran in the owner's Terminal panel (Rule 6 exception: the sandbox cannot write the main checkout or reach GitHub) `zsh artifacts/merged-main-check.sh` → `git merge --ff-only` 8023120 → **69baf9f** (exit 0); on merged `main`: `uv lock --check` ok, `uv run pytest -q` → 2528 passed, 14 deselected; brief's Verify → 83 passed; `make check` → exit 0; `git branch --merged main` lists the branch; `git push origin main claude/stage-18-covariance-vol-0d3e46` → `8023120..69baf9f main -> main`, new branch; `git status -sb` → `## main...origin/main`. CI queued: main 41872886155, branch 41872885674 · raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt)

## 2026-11-04 15:05 — post-DONE audit (owner: "can you help me to audit this stage: check for completion, mistakes and errors, and remaining gaps? after auditing, help me to fix them.")
- Completion: every In-scope item, brief task 1–4 and both acceptance criteria re-checked against code and tests (verification.md) — complete. Config cost checked: `AtlasConfig()` 0.2 ms, so resolving it per call is not a problem (no change).
- Mistakes found (A–D, fixed test-first) and gaps noted (E–G):
  - (A) `CovResult` was a frozen dataclass with an array field: `a == b` raised `ValueError` (ambiguous truth value) and `hash(a)` raised `TypeError`. → `eq=False` (identity equality, hashable); test `test_cov_results_compare_by_identity_and_hash` red (ValueError) → green.
  - (B) Under `missing="complete"` the dropped sessions were removed, so EWMA weights skipped their decay slots (inconsistent with `pairwise` and with the "weights follow session positions" rule): 10.43 instead of 12.45 in the hand example. → the sessions are masked (NaN) instead; sample/LW/OAS are unaffected (all-NaN rows add nothing). Test `test_ewma_complete_policy_keeps_decay_positions` red → green. Masking needed a writable copy of the input (pandas 3 copy-on-write hands out read-only views, `ValueError: assignment destination is read-only`): `to_numpy(copy=True)`; guard test `test_estimate_never_writes_to_the_callers_frame` (added after the fix, passes).
  - (C) An estimated variance of exactly 0 (a name non-constant overall but with only zero returns where EWMA weight remains) passed through and the **condition floor invented a variance** for it — a guess against spec §3.6. → `DataError`. Test `test_zero_estimated_variance_raises_data_error` red (did not raise) → green.
  - (D) GARCH with valid parameters but a non-finite or non-positive one-step forecast raised `DataError` instead of falling back to EWMA. → fallback "invalid forecast". Test `test_garch_falls_back_on_an_invalid_forecast` (NaN, 0, −1) red → green. Docstring now also says a missing last session makes the GARCH forecast one step past the last return.
  - (E) Numerical constants (`is_psd` tol 1e-12, Higham `max_iter` 200 / tol 1e-9) are function defaults, not `config/*.yaml` keys: algorithm tolerances, not business thresholds (Decisions log). (F) `VolForecast` is unhashable (its `params` mapping), equality works — noted. (G) the gaps already in the Backlog stand.
- Ran: `uv run pytest tests/unit/quant/risk_model -q -W error` → 90 passed; ruff, mypy clean. Mutation script: four `AUDIT` mutants added.
