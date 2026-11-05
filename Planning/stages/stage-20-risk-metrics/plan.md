# Stage 20 — Risk & performance metrics library · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** DONE (2026-11-04; see [verification.md](verification.md))
- **Gate check:** Stage 19 is DONE (its [verification.md](../stage-19-factor-risk-model/verification.md) says **ALL CRITERIA VERIFIED**); `uv run pytest -q` → 2658 passed, 0 failed, 19 deselected (live/llm/integration), exit 0 (2026-11-04, fresh worktree; raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt))
- **Execution:** written and executed in one go, inline TDD, no subagents (owner's kick-off: "Start with stage 20. Please plan, execute and audit the entire stage and make sure the stage is fully completed without any errors being created." and the standing preference: plan.md first, then execute, no review pause). The stage `worklog.md` is the ledger; one stage commit plus a records-only follow-up (Rule 2A / 6A). The audit the owner asked for is Task 10 (self-audit, recorded as such).

**Goal:** one audited library, `atlas.quant.metrics`, for every risk and performance number the fund reports or limits on. Later stages (risk engine 44, circuit breakers 45, tear sheet 57, robustness 58, monitoring 62, reporting 63) import these numbers from here only.

**Architecture:** a new package `atlas.quant.metrics` (pure numpy / pandas, no I/O):
- `_common.py` — input validation (`as_returns`, `align`), periods and risk-free helpers shared by the modules.
- `performance.py` — returns-based metrics, drawdowns, rolling versions, `performance_summary`.
- `relative.py` — benchmark-relative metrics and their rolling versions.
- `var.py` — VaR / CVaR: historical, parametric normal, Cornish-Fisher, Monte Carlo from a covariance; on a P&L / return series and on a portfolio (weights + returns or weights + covariance); horizon scaling.
- `contributions.py` — marginal, component and percent (Euler) risk contributions from a covariance (or a Stage 19 `RiskModel`).
- `liquidity.py` — days-to-liquidate at x% ADV, share of positions (count and gross value) liquid within 1/3/5 days; a `DataView` convenience reading `DataView.adv`.
- `exposure.py` — gross, net, long, short, beta-adjusted net, sector (group) nets.

**Tech stack:** numpy / pandas; `statistics.NormalDist` (stdlib) for normal quantiles; Stage 18 `annualise_vol` for annualisation (one source of `sessions_per_year`); Stage 19 `RiskModel` / `portfolio_risk` (contributions on a factor model). No new runtime dependency. `empyrical-reloaded` 0.7.2 is used **only** offline, in an isolated `uv run --no-project` environment, to produce reference values committed as a test fixture (`tests/fixtures/metrics/empyrical_reference.json`; generator `artifacts/empyrical_reference.py`).

**As built / audit (2026-11-04).** Self-audit fixes: `portfolio_returns` rejects duplicate symbols / sessions and an unsorted index; `tail_ratio` type check; historical CVaR on the sorted sample (tail never empty). Mutation checks 72/72 on the final code (first run 71/72: a constant risk-free rate cancels in beta, so a time-varying-rate test was added). Task 3 had no RED run (its code was written with Task 2); the mutation checks cover it. Real-lake check in `artifacts/real-lake-check.txt`. See the worklog for every deviation.

## Collisions with the overall plan
None with the roadmap (row 20: size M, depends on 18; Phase 2 / M2) or the spec (§9 risk framework, §12 `quant/metrics`, §13 success metrics). The spec layout lists `quant/ … metrics` and the brief lists `src/atlas/quant/metrics/{…}.py`: a package, as the brief says.

Interpretations and design choices (also logged in `PROGRESS.md › Decisions log`):

1. **Inputs and units.** Returns are simple daily returns (fractions), a `pd.Series` indexed by session, ascending. NaN entries are dropped (a session without a return); ±inf, a return below −1 (more than −100%), an unsorted or duplicated index raise `ValueError`. Every output is a float in the units its docstring states (fractions; annualised where the name says so; days / sessions as counts). Annualisation uses `quant.features.sessions_per_year` (252), through Stage 18's `annualise_vol`.
2. **Not enough data → `DataError`**, never a number: an empty series; fewer than 2 returns where a standard deviation is needed; fewer than `quant.metrics.var_min_obs` (63) for VaR/CVaR. **Undefined ratios → NaN** (a zero denominator: Sharpe/IR with zero vol, Calmar with no drawdown, profit factor / Omega with no losses, tail ratio with a zero lower quantile, capture with a zero benchmark return), never ±inf. Limit-type numbers (VaR, CVaR, drawdown, vol, exposures, contributions, liquidity) are never NaN on valid input.
3. **Conventions (empyrical-compatible where empyrical has the metric):**
   - `annualised_return` = CAGR `(∏(1+r))^(P/n) − 1`; `annualised_mean` = `mean · P` (arithmetic, what Stage 17 reports as `ann_return`); `annualised_vol` = `std(ddof=1) · √P`; `cumulative_return` = `∏(1+r) − 1`.
   - risk-free: `risk_free` is a **daily** rate (scalar or a Series aligned on the returns); default from `quant.metrics.risk_free_annual` (0.0) converted geometrically, `(1+R)^(1/P) − 1`.
   - `sharpe_ratio` = `mean(r − rf) / std(r − rf, ddof=1) · √P`; `downside_risk` = `√(mean(min(r − req, 0)²)) · √P` (all sessions in the mean); `sortino_ratio` = `mean(r − req) · P / downside_risk`; `calmar_ratio` = CAGR / max drawdown; `omega_ratio(threshold)` with an **annual** threshold converted geometrically to daily (default `quant.metrics.omega_threshold_annual` 0.0) = `Σ(r−τ)⁺ / Σ(τ−r)⁺`.
   - Drawdowns from a wealth index that **starts at 1 before the first return** (so a first-day loss is a drawdown); `drawdown_series` ≤ 0; `max_drawdown` is a **positive magnitude** (0.2 = −20%, as `limits.drawdown_tiers` and Stage 17); `drawdown_periods` lists every episode (peak, trough, recovery or None, depth, sessions peak→trough, underwater sessions, recovered flag); `max_drawdown_duration` = the longest underwater spell, in sessions (an unrecovered spell counts to the last session); `ulcer_index` = `√(mean(dd_t²))` (fraction).
   - `skewness` / `excess_kurtosis`: population (biased) moments, as `scipy.stats.skew` / `kurtosis` defaults and empyrical; a constant series → NaN.
   - `hit_rate` = share of sessions with `r > 0` (zero counts as not a hit); `profit_factor` = `Σ r⁺ / Σ r⁻`; `tail_ratio` = `|q_{1−t}| / |q_t|` with `t = quant.metrics.tail_quantile` (0.05), numpy linear quantiles (empyrical's `np.percentile`).
   - rolling: `rolling_metric(r, fn, window)` applies any scalar metric on each trailing window of `window` returns (default `quant.metrics.rolling_window`, 63); value at the window's last session, NaN before; fast vectorised `rolling_sharpe`, `rolling_vol`, `rolling_max_drawdown`, `rolling_beta`, `rolling_correlation`, equal to `rolling_metric` of the scalar function (tested).
4. **Benchmark-relative.** Returns and benchmark are aligned on the sessions where both exist (inner join, NaN dropped); fewer than 2 common sessions → `DataError`. `beta` = `cov(r−rf, b−rf) / var(b−rf)` (ddof 1 both); `alpha` (CAPM) = annualised geometrically, `(1 + mean(r_e − β b_e))^P − 1` (empyrical); `tracking_error` = `std(r − b, ddof=1) · √P`; `information_ratio` = `mean(r − b) · P / tracking_error`; `up_capture` / `down_capture` = CAGR of the portfolio over the sessions where the benchmark is `> 0` / `< 0` divided by the benchmark's CAGR over the same sessions (empyrical); `up_down_capture` = up / down; `correlation` = Pearson.
5. **VaR / CVaR.** Reported as **positive losses** in the input's units (fraction of NAV for returns, currency for a P&L series): `VaR_α = −q_{1−α}`; a negative VaR means a gain at that confidence. Defaults: VaR confidence `limits.var_confidence` (0.95), CVaR confidence `limits.cvar_confidence` (0.975) — the limit's own confidence, one source; `method` default `quant.metrics.var_method` (historical); horizon `h` sessions (default 1).
   - `historical`: `q` = numpy linear quantile of the observed returns; CVaR = −mean of the returns `≤ q` (never below VaR at the same confidence). Horizon: `quant.metrics.horizon_method` = `sqrt` (1-day number × √h, the square-root-of-time rule) or `overlapping` (quantile of overlapping compounded h-session returns).
   - `normal`: `μ_h = μh`, `σ_h = σ√h`; `VaR = −(μ_h + z σ_h)`, `CVaR = −μ_h + σ_h φ(z)/(1−α)` with `z = Φ⁻¹(1−α)`; `μ = 0` when `quant.metrics.zero_mean` (default true, as Stage 18's zero-mean forecasts) else the sample mean.
   - `cornish_fisher`: `z_cf = z + (z²−1)S/6 + (z³−3z)K/24 − (2z³−5z)S²/36` (S skew, K excess kurtosis of the sample); `VaR = −(μ_h + z_cf σ_h)`; CVaR = expected loss of the Cornish-Fisher-transformed normal below the VaR quantile, in closed form `−μ_h + σ_h φ(z)/(1−α) · [1 + S z/6 + K(z²−1)/24 − S²(2z²−1)/36]`, floored at the VaR (the expansion is not monotone for extreme S, K; the floor is the PerformanceAnalytics convention and is recorded in the result).
   - `monte_carlo`: from a covariance (daily, names × names) and weights, `n = quant.metrics.mc_simulations` (20,000) multivariate-normal scenarios (mean 0 or a given daily mean vector, scaled to the horizon: `μh`, `Σh`) drawn with an explicit `seed`/`rng` (required), portfolio P&L = scenarios · w, then the historical estimator on the simulated P&L. Factorisation by eigen-decomposition (works for PSD, rank-deficient matrices; negative eigenvalues below −1e-12·λmax raise `ValueError`).
   - Portfolio from returns: `portfolio_returns(weights, returns)` = historical simulation of today's weights on past returns (`returns · w`); a session where a held name has no return is dropped (count reported); a nonzero weight on a name without a returns column → `DataError`.
   - Portfolio from a covariance: `normal` uses `σ_p = √(wᵀΣw)` (and `wᵀμ`), `monte_carlo` as above. A nonzero weight on a name outside the covariance → `DataError`.
   - One result type `VaRResult(var, cvar, var_confidence, cvar_confidence, horizon, method, n_obs, adjustments)`; scalar helpers `value_at_risk(...)`, `conditional_value_at_risk(...)`; `scale_to_horizon(value, h)` = √h scaling.
6. **Risk contributions (Euler).** `risk_contributions(weights, cov)` → `RiskContributions(vol, marginal, component, percent)`: `σ = √(wᵀΣw)`, `marginal = Σw/σ` (∂σ/∂w), `component = w ⊙ marginal` (sums to σ), `percent = component/σ` (sums to 1). Covariance: square, finite, symmetric (rtol 1e-8), PSD (eigenvalues ≥ −1e-10·λmax), daily units. A `RiskModel` (Stage 19) is accepted in place of the covariance and is decomposed through `portfolio_risk` (no N×N matrix). Zero portfolio → vol 0, marginal and component 0, percent NaN (undefined). `annual_vol()` on the result.
7. **Liquidity.** `days_to_liquidate(positions, adv, participation)` = `|position| / (participation · ADV)` (sessions, float), both in the same currency (USD: positions = weight × NAV; ADV = `DataView.adv(...).adv_usd`). Defaults: participation `limits.liquidation_adv_frac` (0.10), horizons `quant.metrics.liquidity_horizons` (1, 3, 5). A held name with no / zero / NaN ADV gets `inf` days (fail safe: never counted liquid) and is listed; a zero position gets 0. `liquidity_profile(...)` → `LiquidityProfile(days, names_liquid {h: share of positions with days ≤ h}, gross_liquid {h: share of gross value that can be sold within h days at the participation rate}, max_days, illiquid)`. `liquidity_from_view(view, positions)` reads the ADV (default window `limits.adv_window_days`).
8. **Exposure.** `exposure_summary(weights, betas=None, sectors=None)` → `ExposureSummary(gross Σ|w|, net Σw, long Σw⁺ ≥ 0, short Σw⁻ ≤ 0 (signed: net = long + short), n_long, n_short, beta_adjusted_net Σ w β or None, sector_net Series or None)`. A nonzero weight without a beta (when betas are given) → `DataError` (no guessed beta). Names without a sector go to the `UNCLASSIFIED` group (`"Unclassified"`; ETFs have no GICS sector) and are listed. `group_net(weights, groups)` / `group_gross` are the generic helpers.
9. **Single source of truth.** Existing duplicate code moves onto the library: Stage 17 `vector_backtest` summary (`ann_return`, `ann_vol`, `sharpe`, `total_return`, `max_drawdown`) is computed by `atlas.quant.metrics`. Its drawdown now starts from the initial NAV of 1 (Stage 17 started from the first session's wealth and so missed a first-session loss, e.g. the first rebalance cost); the Stage 17 test that encoded the old start is corrected and the change logged. The Stage 15 feature `max_drawdown_252d` (a rolling price feature on the close, an agent input, not a fund metric) and Stage 19 `portfolio_risk` (the factor-model decomposition, which `risk_contributions` delegates to) stay where they are. A package test asserts the public API; a repo test asserts no other module outside `atlas.quant.metrics` re-implements a Sharpe ratio or a max drawdown (grep for the formulas).
10. **Config.** New `cfg.quant.metrics` (`MetricsConfig`): `risk_free_annual` 0.0 (≥ −0.05, ≤ 0.5), `rolling_window` 63 (≥ 2), `tail_quantile` 0.05 (0 < t < 0.5), `omega_threshold_annual` 0.0 (> −1), `var_method` historical (historical | normal | cornish_fisher), `var_min_obs` 63 (≥ 10), `horizon_method` sqrt (sqrt | overlapping), `zero_mean` true, `mc_simulations` 20,000 (≥ 1,000), `liquidity_horizons` (1, 3, 5) (ascending positive). Confidence levels, participation and ADV window come from `limits` (no copy).

## Design decisions
- **D1 Errors.** Bad input → `ValueError`; not enough data, a name without an estimate → `atlas.core.errors.DataError`.
- **D2 Frozen results.** `VaRResult`, `RiskContributions`, `LiquidityProfile`, `ExposureSummary`, `DrawdownPeriod` are frozen dataclasses; Series fields are copies (callers cannot change a result through them).
- **D3 Reference fixture.** 504 sessions of seeded Student-t returns (portfolio and benchmark), rounded to 8 decimals, with 30 empyrical reference values; tests also carry hand-computed examples on 5–10 session series and literal-loop re-implementations.

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

Commands run as `UV_CACHE_DIR=$TMPDIR/uv-cache-s20 UV_NO_SYNC=1 MPLCONFIGDIR=$TMPDIR/mpl uv run …`. Tests in `tests/unit/quant/metrics/` with unique basenames `test_metrics_*.py`.

### Task 0 — config
- Files: `src/atlas/core/config.py`, `config/fund.yaml` · test `tests/unit/core/test_config.py`
- [x] Failing tests `test_quant_metrics_defaults`, `test_quant_metrics_validation`
- [x] Run → FAIL · add `MetricsConfig`, `QuantConfig.metrics`, YAML block with a comment per key · run `tests/unit/core/test_config.py -q` → PASS

### Task 1 — package skeleton, validation helpers, reference fixture
- Files: create `src/atlas/quant/metrics/{__init__,_common}.py`, `tests/fixtures/metrics/empyrical_reference.json`, `tests/unit/quant/metrics/{__init__,conftest}.py` · test `test_metrics_common.py`
- [x] Failing tests: `test_as_returns_drops_nan_and_validates` (non-Series, inf, < −1, unsorted, duplicates → `ValueError`), `test_as_returns_empty_raises_data_error`, `test_daily_risk_free_conversion` (geometric), `test_align_inner_join`, `test_reference_fixture_loads`
- [x] Run → FAIL · implement · run → PASS

### Task 2 — performance metrics
- Files: `performance.py` · test `test_metrics_performance.py`
- [x] Failing tests: hand examples for `cumulative_return`, `annualised_return`, `annualised_mean`, `annualised_vol`, `sharpe_ratio` (with scalar and Series risk-free), `downside_risk`, `sortino_ratio`, `calmar_ratio`, `omega_ratio`, `hit_rate`, `profit_factor`, `tail_ratio`, `skewness`, `excess_kurtosis` (vs `scipy.stats`); `test_matches_empyrical_reference` (every returns-based empyrical value, rel 1e-9); drawdowns: `test_drawdown_series_starts_from_initial_capital`, `test_max_drawdown_hand_example`, `test_drawdown_periods_hand_example` (two episodes, one unrecovered), `test_max_drawdown_duration`, `test_ulcer_index_hand_example`; undefined → NaN cases; short input → `DataError`; hypothesis: `0 ≤ max_drawdown ≤ 1`, drawdown series ≤ 0, Sharpe invariant to positive scaling (rf 0), CAGR of a constant return equals it annualised
- [x] Run → FAIL · implement · run → PASS

### Task 3 — rolling versions and summary
- Files: `performance.py` · test `test_metrics_rolling.py`
- [x] Failing tests: `test_rolling_metric_equals_metric_on_each_window` (hypothesis over window and metric), `test_rolling_sharpe_vol_drawdown_match_generic`, `test_rolling_matches_empyrical_last_value`, `test_rolling_window_validation`, `test_performance_summary_keys_and_values` (with and without a benchmark)
- [x] Run → FAIL · implement · run → PASS

### Task 4 — benchmark-relative metrics
- Files: `relative.py` · test `test_metrics_relative.py`
- [x] Failing tests: hand examples (beta, alpha, TE, IR, captures, correlation); `test_matches_empyrical_reference` (beta, alpha with/without rf, up/down capture, up/down ratio; empyrical's non-annualised `excess_sharpe` × √P = IR); `test_alignment_inner_join`; hypothesis: beta of `a·b + c` on `b` = `a`, TE of the benchmark vs itself = 0, `|corr| ≤ 1`; `rolling_beta` / `rolling_correlation` equal the scalar function on each window
- [x] Run → FAIL · implement · run → PASS

### Task 5 — VaR / CVaR
- Files: `var.py` · test `test_metrics_var.py`
- [x] Failing tests: historical vs `np.quantile` and empyrical (`value_at_risk` at 5% / 1%, VaR sign); historical CVaR hand example; normal VaR/CVaR closed form vs `scipy.stats.norm` (z = 1.6449 at 95%; CVaR factor 2.0627); Cornish-Fisher reduces to normal when S = K = 0; CF closed-form CVaR equals numerical integration of the CF quantile (fine grid); CF floor applied and recorded for an extreme (S, K); horizon scaling (`sqrt` √h; `overlapping` = quantile of compounded h-session returns; normal μh / σ√h); portfolio from returns (historical simulation equals the weighted series; missing-return sessions dropped; unknown held name → `DataError`); portfolio from covariance (normal equals `z·√(wᵀΣw)`; Monte Carlo within 2% of normal at 20k draws, deterministic for a seed, needs a seed/rng; non-PSD covariance → `ValueError`); `var_min_obs` → `DataError`; P&L series in currency; hypothesis: **CVaR ≥ VaR** at the same confidence for every method, VaR increases with confidence, VaR of `c·r` = `c·VaR(r)` for c > 0
- [x] Run → FAIL · implement · run → PASS

### Task 6 — risk contributions
- Files: `contributions.py` · test `test_metrics_contributions.py`
- [x] Failing tests: `test_hand_example` (2 assets), hypothesis **`test_component_contributions_sum_to_total_vol`** and percents sum to 1, `test_marginal_is_the_gradient` (finite differences), `test_matches_stage19_portfolio_risk` (`RiskModel` path = covariance path on `model.cov()`), `test_zero_portfolio`, `test_unknown_name_with_weight_raises`, `test_rejects_asymmetric_or_indefinite_cov`, `test_ndarray_and_frame_inputs`
- [x] Run → FAIL · implement · run → PASS

### Task 7 — liquidity metrics
- Files: `liquidity.py` · test `test_metrics_liquidity.py`
- [x] Failing tests: `test_days_to_liquidate_hand_example` (10% ADV), `test_short_positions_use_magnitude`, `test_missing_or_zero_adv_is_infinite_and_listed`, `test_profile_names_and_gross_liquid` (hand example at 1/3/5 days), `test_defaults_from_limits`, `test_liquidity_from_view` (synthetic lake `DataView.adv`), validation
- [x] Run → FAIL · implement · run → PASS

### Task 8 — exposure metrics
- Files: `exposure.py` · test `test_metrics_exposure.py`
- [x] Failing tests: `test_hand_example` (gross, net, long, short, counts), `test_beta_adjusted_net`, `test_missing_beta_raises`, `test_sector_nets_and_unclassified`, `test_group_net_and_gross`, hypothesis `net = long + short`, `gross = long − short`, `|net| ≤ gross`, validation
- [x] Run → FAIL · implement · run → PASS

### Task 9 — single source of truth
- Files: `src/atlas/quant/research/vector_backtest.py`, `tests/unit/quant/research/test_research_backtest.py`, `src/atlas/quant/metrics/__init__.py` · tests `test_metrics_package.py`
- [x] Failing tests: `test_public_api` (every name in `__all__`, every public function's docstring states its units), `test_no_duplicate_metric_formulas` (no module outside `atlas.quant.metrics` computes a Sharpe ratio / drawdown from returns), Stage 17 summary equals the library numbers (drawdown from the initial NAV)
- [x] Run → FAIL · route Stage 17 `_summary` through the library · run `tests/unit/quant -q` → PASS

### Task 10 — audit (owner-requested), real-lake check, verification
- [x] Self-audit of every module against the brief and spec §9 (units, fail-safe, NaN/inf paths, defaults from config) — findings and fixes in the worklog
- [x] Mutation checks (`artifacts/mutate.py`): each mutant of a formula must be caught by the suite
- [x] Coverage of `atlas.quant.metrics` (target ≥ 95%), `-W error` run
- [x] Real-lake check (read only, `ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data`): SPY and an equal-weight S&P 500 sample book through the library (performance, relative to SPY, VaR all methods, contributions from a Ledoit-Wolf covariance, liquidity from `DataView.adv`, exposures) — `artifacts/real_lake_check.py`, output in `artifacts/`
- [x] `make check`, the brief's Verify command, records (`verification.md`, `PROGRESS.md`), commit, merge into `main`, merged-main checks, push, CI
