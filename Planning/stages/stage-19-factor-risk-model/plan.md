# Stage 19 — Factor risk model · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** DONE (2026-11-04; see [verification.md](verification.md))
- **Gate check:** Stage 18 is DONE (its [verification.md](../stage-18-covariance-vol/verification.md) says **ALL CRITERIA VERIFIED**); `uv run pytest -q` → 2535 passed, 0 failed, 14 deselected (live/llm/integration), exit 0 (2026-11-04, fresh worktree)
- **Execution:** written and executed in one go, inline TDD, no subagents (owner's standing preference: "write a fully broken-down `plan.md` first, then execute it in one go, task by task, test-first, with no review pause"). The stage `worklog.md` is the ledger; one stage commit plus a records-only follow-up (Rule 2A / 6A).

**Goal:** a Barra-style fundamental factor risk model. Daily cross-sectional constrained WLS turns returns and exposures into factor returns and residuals; an EWMA + Newey-West factor covariance and a shrunk EWMA specific variance give `Σ = X F Xᵀ + D` for any as-of. Portfolio analytics (risk decomposition, exposures, marginal contributions) and ETF time-series betas sit on top. It underpins Stage 20 (risk metrics), 42 (optimiser), 46 (stress) and 53 (attribution).

**Architecture:** `atlas.quant.risk_model` gains
- `regression.py` — `sector_constraint`, `constrained_wls`, `RegressionResult` (pure numpy: one cross-section in, factor returns + residuals + R² out).
- `specific.py` — `ewma_specific_variance`, `shrink_to_sector_median`, `specific_variance` (EWMA of squared residuals, Bayesian shrinkage to the sector median, floor).
- `factor_model.py` — `FactorSet`, `standardize_styles`, `design_matrix`, `CrossSection`, `FactorHistory`, `fit_factor_history`, `factor_covariance` (EWMA + Newey-West), `RiskModel`, `build_risk_model`.
- `analytics.py` — `PortfolioRisk`, `portfolio_risk`, `factor_exposures`, `marginal_contribution`, `bias_statistic`.
- `etf_mapping.py` — `EtfExposures`, `etf_exposures` (252-session time-series betas), `extend_model`.
- Supporting modules beyond the brief's list (the brief's persistence and real-data items need a home): `store.py` (`RiskModelStore`: lake tables `factor_returns`, `risk_model_snapshots`), `loader.py` (`load_cross_sections`, `style_inputs`: `DataView` + Stage 15/16 → cross-sections and as-of exposures).

**Tech stack:** numpy / pandas, Stage 18's `nearest_psd` / `floor_condition`, Stage 15 `PricePanel` features (`momentum_12_1`, `beta_252d`, `amihud_21d`), Stage 16 `factor_panel` + `style_composites`, Pydantic v2 config. No new dependency.

**As built / audit follow-up (2026-11-04).** Extra config keys: `anchor_months` (fundamental refresh every k month-ends; evidence run uses 3 because `factor_panel` costs ~2 min per anchor on 500 names), `max_stale_sessions` (a model needs factor returns within 5 sessions of its as-of), `etf.universe`. Extra functions: `etf_exposures_from_view`, `build_and_store`, `build_factor_history`, `build_model_from_view`. `RiskModel.as_of` (and the store key) is the **data date** (last session included); rows are available from `bar_available_at(data date)`, i.e. from the next session's decision (interpretation 4/12 corrected during Task 8 — the first version leaked a day). Interpretation 5 corrected: with √cap weights the market factor only approximates the cap-weighted return (exact with cap weights). `factor_covariance` also requires `min_history` observations per factor; `fit_factor_history(on_short="skip")` also skips rank-deficient days. The Task 9 "end to end on the synthetic lake" test is not possible (the Stage 16 world has 12 names < 20 factors); the loader→regression path runs in the real-lake integration test. See worklog for every deviation.

## Collisions with the overall plan
None with the roadmap (row 19: size L, depends on 16, 18; Phase 2 / M2) or the spec (§3 principles 2 and 6, §9 risk framework, §12 `quant/risk_model/`). Interpretations and design choices, also logged in `PROGRESS.md › Decisions log`:

1. **Factors (K = 20).** `market` (intercept), the 11 GICS sectors (`quant.risk_model.factor_model.sectors`, names as in the instruments table), and 8 styles: `momentum`, `value`, `quality`, `size`, `beta` (the brief's "low-vol (beta)": exposure = beta, so a *low*-vol portfolio has negative exposure), `growth`, `leverage`, `liquidity`. Orientation: raw, never flipped — momentum high = past winner, value high = cheap, quality high = good, size high = large (ln cap), beta high = high beta, growth high = fast, leverage high = indebted, liquidity high = illiquid (Amihud).
2. **Style sources.** `momentum` = Stage 15 `momentum_12_1`, `beta` = `beta_252d`, `liquidity` = `amihud_21d` (daily, from prices). `value`, `quality`, `growth`, `leverage`, `size` = Stage 16 `style_composites` of the cleaned exposures at the latest month-end (held until the next; fundamentals move quarterly). The core (`standardize_styles`) takes a frame of raw style values, so the numerical code is independent of the sources.
3. **Standardisation (Barra).** Each style is winsorised at `style_winsor_lower/upper`, then shifted to cap-weighted mean 0 and scaled to equal-weighted std 1 over the names in the cross-section. A missing style value becomes 0 (neutral) and is counted; a name with more than `max_missing_styles` missing styles is dropped (`exposures`). A name without a known sector is dropped (`sector`). Dropped names are reported with the reason, never guessed (spec §3.6).
4. **Timing.** The regression of session `t` uses exposures and caps known at the close of the previous session and the returns of `t` (no look-ahead). The model at an as-of `D` uses the factor returns up to `D` and the exposures at `D`'s close, i.e. it forecasts session `D+1`.
5. **Regression.** WLS with weights `√cap` (the brief), design = market + sector dummies + styles, subject to `Σ_s cap_s · f_s = 0` (cap-weighted sector returns sum to zero, so `f_market` is the cap-weighted market return net of styles). Solved exactly by the null-space method: `f = R g` with `R` the (K × K−1) basis of the constraint, WLS on `X R` (lstsq), no penalty. A sector with no names on the day is left out of the design and gets NaN; fewer than `min_cross_section` names → `DataError`. Residuals are `r − X f` (all names of the cross-section).
6. **Factor covariance.** Zero-mean (as Stage 18). Weights `w_t ∝ 0.5^((T−1−t)/h)` with `h = factor_half_life` (90), lag-`l` autocovariance `Γ_l = Σ_{t≥l} w_t f_t f_{t−l}ᵀ / Σ_{t≥l} w_t`, Newey-West `F = Γ_0 + Σ_{l=1..L} (1 − l/(L+1)) (Γ_l + Γ_lᵀ)` with `L = newey_west_lags` (2). NaN entries (an empty sector on a day) are skipped pairwise (numerator and weight sums over the days both factors exist). Result symmetrised, repaired with `nearest_psd(keep_diagonal=True)` if indefinite, eigenvalue-floored at `λmax / max_condition` (Stage 18 helpers); each step recorded in `RiskModel.adjustments`. Needs at least `min_history` days of factor returns, else `DataError`.
7. **Specific variance.** EWMA (half-life `specific_half_life`, 90) of squared residuals over the days the name has a residual; a name needs `min_history` (63) residuals or it is dropped (`history`). Bayesian shrinkage of the vol toward the sector median `σ̄_S` (Barra USE4 form): `v = q·g / (δ_S + q·g)` with `g = |σ_n − σ̄_S|`, `δ_S` the std (ddof 0) of the sector's vols, `q = shrinkage_q`; `σ_n' = v σ̄_S + (1 − v) σ_n` (`v = 0` when the denominator is 0). Floor at `specific_floor_vol` (daily). Output variance (daily²).
8. **`RiskModel`.** Frozen dataclass of read-only arrays (the Stage 17/18 lesson): `as_of`, `symbols`, `factors`, `exposures` (N × K), `factor_cov` (K × K), `specific_var` (N), `dropped` (name → reason), `adjustments`, `n_days`. Properties return fresh frames. `cov(symbols=None) = X F Xᵀ + D` (daily return², N × N; positive definite because `D > 0`). `vol()` = daily vols. Annualise with `annualise_vol` (Stage 18).
9. **Analytics.** `portfolio_risk(model, w)`: `w` a Series of NAV fractions (shorts negative); nonzero weight on a name not in the model → `DataError` (fail safe: no risk without a risk estimate). Returns `PortfolioRisk`: `total_var`, `factor_var = xᵀFx`, `specific_var = Σ w²D`, `total_vol`, `factor_vol`, `specific_vol`, `by_factor` (contribution to variance, `x_k (F x)_k`, sums to `factor_var`), `exposures` (`x = Xᵀw`), `mctr` (marginal contribution to risk `(Σw)_n / σ_p`), `contribution` (`w_n · mctr_n`, sums to `σ_p`). All daily; `annual_vol(periods)`.
10. **Bias statistic.** `bias_statistic(realised, predicted_vol)` = `sqrt(mean((r_t / σ_t)²))` over the sessions with both (≈ 1 for a calibrated model; > 1 under-predicted risk). `n < 20` → `DataError`.
11. **ETFs.** `etf_exposures(etf_returns, factor_returns, window=252)`: OLS of each ETF's last `window` daily returns on the factor returns (demeaned, intercept dropped) with a small ridge (`etf.ridge`, relative to `tr(FᵀF)/K`) because the sector constraint makes the factor returns near-collinear; betas are its exposures, residual variance (dof-corrected) its specific variance, R² and n reported. An ETF with fewer than `etf.min_obs` observations is dropped. `extend_model(model, etfs)` appends them as extra rows (the factor covariance is unchanged).
12. **Persistence.** `RiskModelStore(lake, config)`: `factor_returns` (date, factor, value, r2, n_names, config_hash, available_at) one row per day × factor; `risk_model_snapshots` (date, component ∈ {exposure, factor_cov, specific_var}, row, column, value, config_hash, available_at), written on monthly rebalance dates (`monthly_rebalance_dates`). `available_at = AsOf.for_session(day).cutoff − 1 µs` (as `FactorStore`); reads are PIT-filtered with `assert_no_lookahead` and filtered by `config_hash` (a hash of `quant.risk_model.factor_model` and the factor-library hash).
13. **Real data (brief task 6).** `load_cross_sections(view_factory …)` builds the daily cross-sections for a window: month-end `factor_panel` (point-in-time universe and fundamentals), technical styles from one vectorised `PricePanel` over the window (shifted one session), caps = month-end cap × adjusted-price ratio. The 2018–2024 build is an `integration` test reading the real lake read-only (`ATLAS__DATA__LAKE_PATH`); it logs the bias statistics (cap-weighted market, random long-short, single names, each style) to `artifacts/`.
14. **Acceptance timing.** "Build time for one date with 500 names < 2 s" = `build_risk_model` (standardise, factor cov, specific variance, shrinkage, PSD repair) from an already fitted `FactorHistory`; the per-day regression time is measured and logged too.

## Design decisions

- **D1 Config** `cfg.quant.risk_model.factor_model` (`FactorModelConfig`): `sectors` (11 GICS names) · `factor_half_life` 90 (≥ 1) · `newey_west_lags` 2 (≥ 0) · `specific_half_life` 90 (≥ 1) · `min_cross_section` 100 (≥ 10) · `style_winsor_lower` 0.01 / `style_winsor_upper` 0.99 (ordered) · `max_missing_styles` 2 (≥ 0) · `shrinkage_q` 1.0 (≥ 0) · `specific_floor_vol` 0.002 (> 0, daily) · `etf`: `window` 252 (≥ 30), `min_obs` 126 (≥ 30, ≤ window), `ridge` 1e-3 (≥ 0). YAML block in `config/fund.yaml`, a comment per key. `min_history` and `max_condition` come from `quant.risk_model`.
- **D2 Errors.** Bad input (not a frame/series, non-finite, misaligned, unsorted) → `ValueError`; not enough data → `atlas.core.errors.DataError`.
- **D3 Test world** (`tests/unit/quant/risk_model/factor_risk_world.py`, seeded): N names in the 11 sectors with known style exposures (standardised), slowly varying caps, true factor returns from a known factor covariance projected onto the cap constraint each day, heterogeneous specific vols; returns `r_t = X f_t + ε_t`.

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

Commands run as `UV_CACHE_DIR=$TMPDIR/uv-cache-s19 UV_NO_SYNC=1 MPLCONFIGDIR=$TMPDIR/mpl uv run …`. Tests in `tests/unit/quant/risk_model/` with unique basenames (`test_factor_risk_*.py`).

### Task 0 — config (supports all tasks)
- Files: `src/atlas/core/config.py`, `config/fund.yaml` · test `tests/unit/core/test_config.py`
- [x] Failing tests `test_quant_factor_model_defaults`, `test_quant_factor_model_validation`
- [x] Run → FAIL · add `EtfConfig`, `FactorModelConfig`, `RiskModelConfig.factor_model`, YAML · run `tests/unit/core/test_config.py -q` → PASS

### Task 1 — synthetic world generator (brief task 1)
- Files: create `tests/unit/quant/risk_model/factor_risk_world.py` · test `test_factor_risk_world.py`
- [x] Failing tests: `test_world_shapes_and_determinism` (same seed → identical frames), `test_world_returns_follow_the_model` (returns − X f = ε exactly), `test_world_factors_satisfy_the_sector_constraint` (Σ cap_s f_s = 0 each day), `test_world_style_exposures_standardised` (cap-weighted mean 0, std 1)
- [x] Run → FAIL · implement · run → PASS

### Task 2 — constrained WLS (brief task 2)
- Files: create `regression.py` · test `test_factor_risk_regression.py`
- [x] Failing tests: `test_sector_constraint_holds` (Σ cap_s f_s = 0 to 1e-12), `test_noise_free_returns_recovered_exactly` (ε = 0 → f exact, residuals 0, R² = 1), `test_matches_dense_kkt_solution` (the constrained WLS normal equations solved as a KKT system), `test_wls_weights_matter` (heteroskedastic noise: √cap weights beat OLS error over seeds), `test_empty_sector_gets_nan`, `test_validation` (shape, NaN, too few names → `DataError`/`ValueError`), `test_synthetic_recovery_correlation` (T = 500, N = 600: corr(f̂, f) > 0.95 for every factor)
- [x] Run → FAIL · implement · run → PASS

### Task 3 — cross-section prep, factor history, factor covariance (brief task 3a)
- Files: `factor_model.py` (`FactorSet`, `standardize_styles`, `design_matrix`, `CrossSection`, `FactorHistory`, `fit_factor_history`, `factor_covariance`) · test `test_factor_risk_model.py`
- [x] Failing tests: `test_standardize_styles` (cap-mean 0, std 1, winsor, missing → 0 counted, > max_missing dropped), `test_unknown_sector_dropped`, `test_design_matrix_columns`, `test_fit_factor_history_shapes` (T × K factor returns, T × N residuals with NaN for absent names, r2, n_names), `test_factor_cov_known_example` (hand-computed EWMA + NW with L = 1 on 4 rows), `test_factor_cov_half_life_weights`, `test_newey_west_raises_long_run_variance_for_ar1` (AR(1) φ = 0.5: NW(L = 5) closer to the long-run variance than L = 0), `test_factor_cov_psd_and_conditioned` (hypothesis: random factor frames with NaNs → symmetric, PD, cond ≤ max), `test_factor_cov_nan_pairwise`, `test_factor_cov_needs_min_history`
- [x] Run → FAIL · implement · run → PASS

### Task 4 — specific risk (brief task 3b)
- Files: `specific.py` · test `test_factor_risk_specific.py`
- [x] Failing tests: `test_ewma_specific_variance_known` (hand example), `test_ewma_skips_missing_residuals`, `test_short_history_dropped`, `test_shrinkage_formula_known` (hand-computed v and σ'), `test_shrinkage_identity_at_the_median`, `test_shrinkage_pulls_outliers_towards_the_median`, `test_floor_applied`, `test_specific_recovers_true_vol_on_synthetic` (median relative error < 10%)
- [x] Run → FAIL · implement · run → PASS

### Task 5 — `RiskModel` and `build_risk_model` (brief tasks 3c, 6 timing)
- Files: `factor_model.py` · test `test_factor_risk_model.py`
- [x] Failing tests: `test_cov_equals_xfxt_plus_d`, `test_cov_is_positive_definite`, `test_risk_model_is_read_only`, `test_cov_subset`, `test_build_reports_dropped_names`, `test_calibration_bias_statistic_near_one` (synthetic: model at day 750, 250 out-of-sample days; mean bias of 20 random portfolios in [0.9, 1.1]; thresholds set from measured behaviour, recorded), `test_build_time_500_names_under_2s`, `test_model_is_pit_no_lookahead` (changing a return after `as_of` does not change the model)
- [x] Run → FAIL · implement · run → PASS

### Task 6 — portfolio analytics (brief task 4)
- Files: `analytics.py` · test `test_factor_risk_analytics.py`
- [x] Failing tests: `test_decomposition_sums_to_total` (factor_var + specific_var = total_var = wᵀΣw; by_factor sums to factor_var; contribution sums to total_vol; hypothesis over random w), `test_factor_exposures_are_x_transpose_w`, `test_mctr_is_the_gradient` (finite difference), `test_zero_weights_on_unknown_names_ok_nonzero_raises`, `test_single_factor_portfolio_known_value` (hand example), `test_bias_statistic_known` and `test_bias_statistic_validation`, `test_annual_vol`
- [x] Run → FAIL · implement · run → PASS

### Task 7 — ETF betas (brief task 5)
- Files: `etf_mapping.py` · test `test_factor_risk_etf.py`
- [x] Failing tests: `test_etf_betas_recover_planted_exposures` (ETF = known mix of factors + noise; betas within 0.05), `test_etf_specific_variance`, `test_etf_uses_only_the_window`, `test_short_etf_dropped`, `test_extend_model_adds_rows` (cov includes ETFs; stock block unchanged), `test_sector_etf_loads_on_its_sector` (an ETF made of one sector's names)
- [x] Run → FAIL · implement · run → PASS

### Task 8 — persistence
- Files: `store.py` · test `test_factor_risk_store.py`
- [x] Failing tests: `test_factor_returns_roundtrip`, `test_snapshot_roundtrip_equals_model`, `test_read_is_point_in_time` (a row written for day D is invisible at an earlier as-of), `test_other_config_hash_not_read`, `test_write_replaces_a_day`, `test_snapshot_dates_monthly`
- [x] Run → FAIL · implement (tables registered in `DEFAULT_REGISTRY`) · run → PASS

### Task 9 — loader and real-data build (brief task 6)
- Files: `loader.py` · tests `test_factor_risk_loader.py` (synthetic lake from `tests/unit/quant/factors/factor_world.py`), `tests/integration/test_risk_model_real_lake.py` (`@pytest.mark.integration`)
- [x] Failing tests: lag test as `test_exposures_never_see_the_session_being_regressed`, `test_loader_uses_only_dataview`; `test_loader_end_to_end_on_the_synthetic_lake` not possible (12 names < 20 factors) → covered by the real-lake integration test (worklog)
- [x] Run → FAIL · implement · run → PASS
- [x] Real lake 2018–2024 (read only): fit, build model at month-ends, write bias statistics to `artifacts/real-lake-bias.txt`; integration test asserts model validity (PD, decomposition adds up) and loose bounds on the bias

### Task 10 — package exports, docs, full verification
- [x] `__init__.py` exports and docstring · `make check` · brief's Verify · `-W error` · coverage of the new modules · mutation checks (`artifacts/mutate.py`, as Stage 18) · timing run · save raw output to `artifacts/`
- [x] `verification.md`, `PROGRESS.md` (checkbox, handoff, Backlog, Decisions log), commit `stage-19: Factor risk model`, merge into `main` and re-run checks (Rule 6A)
