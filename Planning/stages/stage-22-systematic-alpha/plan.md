# Stage 22 — Systematic alpha models · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** DONE (2026-11-07): the pre-registered composite's in-sample IC was negative (−0.0062, t −0.42); owner decision "Re-specify in-sample" → weights refitted in-sample (quality 0.62, reversal 0.38, others 0), composite IC +0.0256 (t +1.91) — see [verification.md](verification.md)
- **Gate check:** Stage 21 is DONE (its [verification.md](../stage-21-regime-detection/verification.md) says **ALL CRITERIA VERIFIED**); `uv run pytest -q` → 3605 passed, 29 deselected, exit 0 (2026-11-07, fresh worktree; raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt))
- **Execution:** written and executed in one go, inline TDD, no subagents (kick-off: "plan, execute, audit and fully complete it"; the owner's standing preference: plan.md first, then execute, no review pause). `worklog.md` is the ledger. Owner decision of 2026-11-05 respected: the Stage 21 `risk_scalar` table is not touched.

**Goal:** deterministic alpha signals with calibrated expected returns (annualised fractions): a cross-sectional multi-factor equity model, time-series momentum for ETFs and a post-earnings-drift signal, all behind one `AlphaModel` protocol that reads data only through `DataView`, and an in-sample evaluation (2016–2021) with the Stage 17 toolkit.

**Architecture:** new package `atlas.quant.alpha` (the brief's five files):
- `base.py` — `ALPHA_COLUMNS`, the `AlphaModel` protocol, `check_alpha_frame` (the output contract), `alpha_frame` (builder), `direction`.
- `calibration.py` — Grinold calibration `grinold_alpha`, `horizon_scale`; IC bookkeeping for IC weighting and calibration: `ic_history`, `known_ic`, `trailing_ic` (PIT: an IC dated `d` with horizon `h` is used only once its label is known), `ic_weights`, `calibration_slope`, `specific_vol` (Stage 19 `RiskModel` → annual specific vol).
- `multifactor.py` — `COMPONENTS`, `clean_component`, `component_frame(view, symbols)`, `combine` (pure: components → z → expected return), `MultiFactorModel`.
- `tsmom.py` — `trend_blend` (pure), `TSMOMModel`.
- `pead.py` — `pead_decay`, `pead_signal` (pure), `PEADModel`.

**Tech stack:** numpy / pandas; Stage 15 features through `compute_features`; Stage 16 `factor_panel`, `style_composites`, `winsorize` / `neutralize` / `zscore`, `composite`; Stage 17 `forward_returns`, `ic_series`, `summarize_ic`, `nw_lags`, `build_tearsheet`, `vector_backtest` (research run); Stage 19 `RiskModel` (optional residual-vol source); Stage 20 `atlas.quant.metrics` (Sharpe in the research run). No new dependency.

## Collisions with the overall plan
None with the roadmap (row 22: size M, depends on 17, 19; Phase 2 / M2; consumers 34, 37, 39) or the spec (§3 quant "alpha", §8 Systematic pod "multi-factor + TSMOM", Event-Driven pod "PEAD", §12 `quant/ … alpha/`, A7 in-sample end 2021-12-31). The brief's file list is kept; the research run is a stage artifact script (`artifacts/research_run.py`), as Stages 19–21 did with their real-lake checks, because the brief lists no research module.

Interpretations and design choices (also logged in `PROGRESS.md › Decisions log`):

1. **Output contract.** `predict(view) -> DataFrame`: index `symbol` (unique, sorted), exactly the float64 columns `score`, `exp_return_ann`, `confidence`; every value finite; `confidence ∈ [0, 1]`. A name without a prediction is **absent** (never a NaN row, never a guessed value). `score`: higher = more attractive, units per model (documented). `exp_return_ann`: the expected **annualised simple return, as a fraction** (0.03 = 3% a year) — the residual (sector/market-relative) return for the cross-sectional models, the total return of the asset for TSMOM. `direction(frame)` = sign of `exp_return_ann` (−1 / 0 / +1). Every model has a `name` and a `horizon` (sessions the forecast is calibrated for). Timing is Stage 17's: a prediction for decision day `t` uses data through `t − 1`.
2. **Grinold calibration.** `α_h = IC_h × σ_h × z` is the expected residual return over the IC's horizon `h` (sessions) for a cross-sectional z-score `z` (mean 0, population std 1). With the annualised residual vol `σ = σ_h × √(P/h)` (P = `quant.features.sessions_per_year`, 252) and `α_ann = α_h × P/h`: **`exp_return_ann = IC_h × σ_ann × z × √(P/h)`**. The brief's `α = IC × σ_resid × z` is this formula at the IC's own horizon; the √(P/h) term converts it to the annualised units the contract requires (recorded). The calibration test plants returns `r = ρ σ z + √(1−ρ²) σ ε`: the OLS slope of realised returns on `α` is ≈ 1.
3. **Multi-factor components** (each a cross-sectional score of the members of `quant.alpha.multifactor.universe` at `t`): momentum = Stage 15 `momentum_12_1` (+); value = Stage 16 style composite `value` (+); quality = style composite `quality` (+); low-vol = Stage 15 `vol_63d` (−); short-term reversal = Stage 15 `ret_21d` (−; the 1-month reversal that 12-1 momentum skips). Sources and signs live in config. Each component: winsorise at `quant.factors.winsor_lower/upper`, neutralise by sector (demean within GICS sector; a name without a sector against the whole cross-section, Stage 16 rule), z-score. A component with fewer than `min_names` valid values is dropped for that date.
4. **Composite.** Weights from config, **pre-registered before any real-data run: equal, 0.2 each** (the neutral prior; no tuning on the evaluation window). `combine`: Stage 16 `composite` (a missing component's weight is left out), then sector-neutralised and z-scored again → `z`. A name needs component coverage ≥ `min_coverage` (0.6 of the absolute weight) or it gets no prediction. Optional **IC weighting** (`weighting: ic`): weight_k ∝ max(trailing mean IC_k, 0) over the ICs **known** at `t` (dated `d` with `offset(d, h) < t`, i.e. the label close is before the decision) within the last `ic_window_months` (36), needing `ic_min_obs` (12); otherwise (or all ≤ 0) the config weights (logged). The IC history (dates × components, plus `composite`) is an input of the model (`ic_history=`); `ic_history(...)` builds it from component panels and Stage 17 labels.
5. **Expected return and confidence (multi-factor).** `IC` = the trailing known composite IC (same PIT rule, floored at 0: a noisy negative estimate never flips the book; fail safe) when an IC history with ≥ `ic_min_obs` dates is given, else the prior `ic_prior` (0.03 at `horizon` 21 sessions). `σ_ann` = Stage 15 `idio_vol_252d` (residual vs `fund.benchmark`, annualised), or an explicit `resid_vol` Series (e.g. `specific_vol(RiskModel)`, Stage 19); a name without a residual vol gets no prediction. `confidence = coverage × reliability`: coverage = share of absolute weight with a component value; reliability = `clip(t_IC / t_full, 0, 1)` from the trailing composite IC (NW t-stat), else `prior_confidence` (0.5).
6. **TSMOM** (universe `macro_etfs`): for each lookback L in `lookbacks` (21, 63, 126, 252 sessions ≈ 1/3/6/12 months), the sign of the Stage 15 total return `ret_Ld` (0 when exactly flat); blend `b` = weighted mean of the available signs (equal weights; renormalised; needs `min_lookbacks` = 2) ∈ [−1, 1]. Vol `σ` = Stage 15 `vol_63d` (annualised). `score = b × vol_target / σ` (the inverse-vol-scaled position, NAV fraction per asset at `vol_target` = 0.10; capped at ±`max_position` 2.0); `exp_return_ann = sharpe_prior × σ × b` (an asset held along a full trend earns `sharpe_prior` (0.3) × σ a year; a zero blend expects 0); `confidence = |b|` (agreement of the lookbacks). Direction = sign(b).
7. **PEAD** (universe sp500 members): the latest earnings reaction known at `t` (`DataView.earnings_reactions(symbol, 1)`; Stage 12 `car` = window abnormal return vs the benchmark). Age `k` = sessions after the reaction window end up to `t − 1` (k = 0 at the first decision that knows it). `decay = max(0, 1 − k / decay_sessions)` (linear, `decay_sessions` 60); `k ≥ 60` → no prediction. `score = sign(car) × decay`; `exp_return_ann = drift_ann × score` (`drift_ann` 0.06: the annualised abnormal drift right after an event); `confidence = decay × min(|car| / car_full, 1)` (`car_full` 0.05). A name without a known reaction gets no prediction; a reaction with `car` = 0 has score 0.
8. **Research run (Task 5, in-sample only).** Decision days = first session of each month from 2016-01-01 whose longest label (63 sessions) ends on or before `backtest.in_sample_end` (2021-12-31): **no 2022 bar is read** (the label view is at the session after the last label close). Signals through `model.predict(view)` at each decision day on views made with `DataView.advance` (shared cache); labels by Stage 17 `forward_returns`; composite evaluated with Stage 17 `build_tearsheet` (IC Spearman/Pearson, NW t, quantiles, decay, L/S backtest Sharpe) saved under `artifacts/`; plus the components' ICs, the PIT IC-weighted variant, PEAD's IC, TSMOM's time-series hit rate / Sharpe, and the realised calibration slope of each model. Real lake read only (`ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data`).
9. **Acceptance — "in-sample IC of the composite is positive".** Defined before the run: the **fixed-weight composite** (config weights as pre-registered in 4) has a positive mean Spearman IC at the headline horizon (21 sessions, monthly dates), with its Newey-West t-stat recorded. If it is not positive the result is reported as is and the stage is `BLOCKED` for the owner's decision (weights are not tuned to pass). **Outcome (2026-11-07):** IC −0.0062 (t −0.42) → `BLOCKED`; owner chose "Re-specify in-sample": fixed weights ∝ max(full-sample component IC_h21, 0) (quality 0.62, reversal 0.38, momentum / value / low-vol 0), declared an in-sample fit before re-scoring, one candidate; the criterion is then checked on that composite.
10. **Config** `cfg.quant.alpha` (`AlphaConfig` with `multifactor`, `tsmom`, `pead`; YAML `config/fund.yaml › quant.alpha`), every threshold above.

## Design decisions
- **D1 Errors.** Bad arguments / config → `ValueError` (pydantic `ValidationError` for config); a malformed model output → `ValueError` from `check_alpha_frame`; no data for a name → the name is absent; a universe too small for a cross-section (< `min_names`) → empty frame (logged), never a guess.
- **D2 Types.** The protocol is a `typing.Protocol` (`runtime_checkable`); models are frozen dataclasses holding config; outputs are DataFrames (the brief's contract), validated by `check_alpha_frame`.
- **D3 PIT.** Models read only through the `DataView` they get; the IC history passed to the IC-weighted model is filtered by `known_ic` against the view's day; labels never reach a model (`guard_not_label` on every frame input).
- **D4 Logging.** structlog `get_logger(__name__)`: names dropped for coverage / residual vol, IC-weight fallbacks.

## Re-specification after the failed criterion (owner decision 2026-11-07)

**Owner's decision:** "Re-specify in-sample" (answer in this chat), relayed by the parent session as "Re-specify and re-run" (chosen over waiving the criterion and over a bug audit first).

**Chosen after seeing the in-sample ICs — said plainly.** The pre-registered equal-weight composite (interpretation 4) failed interpretation 9 (IC −0.0062, t −0.42). The new weights were picked **after** the 2016–2021 component ICs were seen, from those same ICs, so the composite's in-sample IC below is selected on the data it is measured on and **overstates the real edge**. It is not evidence of out-of-sample skill; that test belongs to Stage 58 (walk-forward, 2022+ hold-out) — Backlog.

**Specification.** Fixed weight_k ∝ max(component k's full-sample mean Spearman IC at 21 sessions, 0), rounded to 2 decimals: quality 0.62, reversal 0.38, momentum 0, value 0, low-vol 0. **Reason:** it is the model's own IC-weighting rule (interpretation 4) with hindsight over the whole window — the simplest rule that follows from the measured ICs, with no free parameter to search; a component with a negative IC gets no weight rather than a flipped sign (no short-momentum / short-quality bets invented from one sample). All five components stay in config (the brief's composite of the five named factors with weights from config; weight 0 leaves one out of the composite and of the coverage). Everything else (cleaning, coverage 0.6, Grinold, confidence) unchanged.

**Order of events (honest record).** The rule and weights were written in `worklog.md` before any re-scored result was seen; the re-score from run 1's cached panels came next; this `plan.md` section was written after that re-score, with the parent's instructions, and before the fresh run 3. The sign / label check (below) ran after the re-score (the pre-decision audit had checked signs and timing by reasoning only).

**Before accepting: signs and label alignment** ([artifacts/sign_label_check.py](artifacts/sign_label_check.py), real lake, 2018-06-01): feature components vs raw features — momentum +0.95, low-vol −0.85, reversal −0.92 (expected signs); the decision view's last bar is t−1; `ret_21d` = close(t−1)/close(t−22)−1 exactly; the h21 label = close(t+21)/close(t)−1 (max diff 1e−15); the composite IC recomputed independently equals the evaluation's row. No bug. (Value / quality signs rest on Stage 16's tested style composites.)

**Every variant scored** (composite, Spearman IC at 21 sessions, NW t, 2016–2021 monthly):
| Variant | Weights | IC | t | Status |
|---|---|---|---|---|
| V1 pre-registered | equal 0.2 × 5 | −0.0062 | −0.42 | failed interpretation 9 |
| V2 in-sample fit | quality 0.62, reversal 0.38 | +0.0256 | +1.91 | adopted (in-sample) |
| PIT IC-weighted (`weighting: ic`), run 1 | trailing known ICs, fallback V1 | −0.024 | −1.30 | diagnostic only |
| PIT IC-weighted, run 2 | trailing known ICs, fallback V2 | −0.017 | −0.96 | diagnostic only |

No other weighting or component set was scored. Had V2 not been positive, the plan was to report back rather than search further.

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

Commands run as `UV_CACHE_DIR=$TMPDIR/uv-cache-s22 UV_NO_SYNC=1 LOKY_MAX_CPU_COUNT=4 MPLCONFIGDIR=$TMPDIR/mpl uv run …`. Tests in `tests/unit/quant/alpha/` with unique basenames `test_alpha_*.py`.

### Task 0 — Config
- Files: modify `src/atlas/core/config.py` (`AlphaMultiFactorConfig`, `AlphaComponentConfig`, `AlphaTSMOMConfig`, `AlphaPEADConfig`, `AlphaConfig`; `QuantConfig.alpha`), `config/fund.yaml` · test `tests/unit/quant/alpha/test_alpha_config.py`
- [x] Failing tests: defaults equal the YAML; validators (weights finite and not all zero, horizon ≥ 1, lookbacks ascending, fractions in range, known component kinds)
- [x] Implement → PASS

### Task 1 — Protocol + Grinold calibration
- Files: `src/atlas/quant/alpha/{__init__.py,base.py,calibration.py}` · tests `test_alpha_base.py`, `test_alpha_calibration.py`
- [x] Failing tests: `check_alpha_frame` accepts the contract and rejects NaN / extra or missing columns / duplicate symbols / confidence outside [0, 1]; `direction`; a class with `name`, `horizon`, `predict` is an `AlphaModel`; `grinold_alpha` known values and the √(P/h) annualisation; calibration slope ≈ 1 on planted returns (seeded); `known_ic` drops ICs whose label is not known at `t` (boundary day); `trailing_ic` window / min obs; `ic_weights` (proportional to positive ICs, fallback); `calibration_slope`; `specific_vol` from a Stage 19 `RiskModel`
- [x] Implement → PASS

### Task 2 — Multi-factor model; synthetic planted-alpha test
- Files: `src/atlas/quant/alpha/multifactor.py` · tests `test_alpha_multifactor.py`, world `tests/unit/quant/alpha/alpha_world.py`
- [x] Failing tests: `clean_component` is sector-neutral (each sector sums to 0) and z-scored; `combine` on planted components recovers the planted composite (IC with the planted return ≈ ρ, calibration slope ≈ 1), renormalises over missing components, drops low coverage, sector-neutral output; IC weighting uses only known ICs (a future IC that would flip the weights is ignored); `MultiFactorModel.predict(view)` on a lake world (prices + fundamentals, Stage 16 factor world plus a benchmark) satisfies the contract, equals the pure pipeline, and a later-dated row in the lake changes nothing (PIT)
- [x] Implement → PASS

### Task 3 — TSMOM on synthetic trending series
- Files: `src/atlas/quant/alpha/tsmom.py` · test `test_alpha_tsmom.py`
- [x] Failing tests: `trend_blend` known values (all up → +1, mixed → fraction, missing lookbacks renormalised / too few → absent); on a lake world with an up-trending, a down-trending and a flat ETF, `TSMOMModel.predict` gives long / short / weak, `score = b × vol_target / σ` (higher vol → smaller position), `exp_return_ann = sharpe_prior × σ × b`, contract holds
- [x] Implement → PASS

### Task 4 — PEAD signal decay
- Files: `src/atlas/quant/alpha/pead.py` · test `test_alpha_pead.py`
- [x] Failing tests: `pead_decay` = 1 at k = 0, 0.5 at k = 30, 0 at k ≥ 60; on a lake world with earnings reactions, the score decays linearly with the decision day, the sign follows `car`, a reaction not yet known (window not closed) is ignored, the name drops out after 60 sessions, a newer reaction replaces the older one
- [x] Implement → PASS

### Task 5 — Research run on real in-sample data; record IC / Sharpe
- Files: `Planning/stages/stage-22-systematic-alpha/artifacts/research_run.py` (+ outputs under `artifacts/research/`)
- [x] Run in the background on the real lake (read only) over the in-sample decision days of interpretation 8; save the raw log and the tear sheet
- [x] Record composite IC (mean, NW t), quintile spread, L/S Sharpe, components' ICs, IC-weighted variant, PEAD IC, TSMOM Sharpe, calibration slopes; check interpretation 9

### Task 6 — Package exports, audit, full verification
- [x] `atlas.quant.alpha.__init__` exports; package test (every export importable, no `datetime.now`, no network)
- [x] Self-audit of the diff (PIT, units, errors, config), fixes test-first
- [x] `make check` + `uv run pytest tests/unit/quant/alpha -q`; `verification.md`, PROGRESS (BLOCKED note)
- [x] Owner decision on the failed IC criterion: "Re-specify in-sample" (2026-11-07)
- [x] Test first (`test_defaults` new weights: red; `test_combine_leaves_out_zero_weight_components`), config weights in `config.py` + `fund.yaml`; `research_run.py` re-scores from cache under the current config
- [x] Re-score from the cached panels (IC +0.0256, t +1.91); `make check`, Verify, mutation check re-run
- [x] Fresh full research run 3 with the new config (identical to the re-score); sign / label check; DONE records
- [x] Merge into `main` (fc15255), re-verify on the merged result (pytest 3673 passed; make check exit 0), push, CI green (runs 42507797643, 42507797402); hashes recorded
