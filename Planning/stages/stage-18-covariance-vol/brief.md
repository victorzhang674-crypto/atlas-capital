# Stage 18 — Covariance & volatility forecasting
**Phase:** 2 · **Size:** M · **Depends on:** 15

## Goal
Well-conditioned covariance matrices and volatility forecasts for sizing, VaR and optimisation.

## In scope
- Estimators: sample, EWMA (RiskMetrics λ=0.94 daily / half-life param), Ledoit-Wolf shrinkage (constant correlation target), OAS; a minimum-history policy and handling of missing data (pairwise → nearest PSD)
- `nearest_psd(matrix)` (Higham) and a condition-number check
- Vol forecasts: EWMA, GARCH(1,1) via `arch` (fallback to EWMA on non-convergence), realised-vol blend
- Annualisation helpers (√252), correlation from covariance
- Interface: `CovarianceEstimator.estimate(returns_df) -> CovResult{cov, vol, corr, method, n_obs}`

## Out of scope
The factor-based covariance (Stage 19).

## Files
`src/atlas/quant/risk_model/{covariance.py,volatility.py,psd.py}`, tests

## Tasks
1. Sample/EWMA with known synthetic multivariate normal (known Σ) → estimate error bounds.
2. Ledoit-Wolf beats sample in Frobenius error when N/T is large (synthetic test).
3. PSD repair test on a broken matrix.
4. GARCH fit on simulated GARCH data recovers parameters approximately; non-convergence fallback.

## Acceptance criteria
- All outputs are PSD; the estimators recover synthetic truth within tolerances.

## Verify
`uv run pytest tests/unit/quant/risk_model -q`
