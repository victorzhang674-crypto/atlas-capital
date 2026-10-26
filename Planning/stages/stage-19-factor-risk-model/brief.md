# Stage 19 — Factor risk model
**Phase:** 2 · **Size:** L · **Depends on:** 16, 18

## Goal
A Barra-style fundamental factor risk model: exposures → factor returns → factor covariance + specific risk. It underpins the optimiser, risk limits, stress tests and attribution.

## In scope
- Factors: market (intercept), 11 GICS sectors, styles: momentum, value, quality, size, low-vol (beta), growth, leverage, liquidity, using exposures from Stages 15/16 (z-scored)
- Daily cross-sectional WLS regression (weights √mcap) of returns on exposures → factor returns + residuals; sector constraint (cap-weighted sector returns sum to market) handled by the constrained regression
- Factor covariance: EWMA (half-life 90d) with Newey-West adjustment; specific variance: EWMA of residuals with a Bayesian shrinkage to sector median
- `RiskModel` for an as_of: `exposures (N×K)`, `factor_cov (K×K)`, `specific_var (N)`, `cov() = XFXᵀ + D`
- Portfolio analytics: `portfolio_risk(w)` → total, factor vs specific, by factor; `factor_exposures(w)`; marginal contribution to risk
- Persistence of daily factor returns + model snapshots on rebalance dates
- ETF handling: ETFs get exposures by regressing their returns on factor returns (time-series betas, 252d)

## Out of scope
Statistical (PCA) models (backlog).

## Files
`src/atlas/quant/risk_model/{factor_model.py,regression.py,specific.py,analytics.py,etf_mapping.py}`, tests

## Tasks
1. Synthetic world generator: known exposures, factor returns and specific returns.
2. Constrained WLS; recover factor returns on synthetic data (corr > 0.95).
3. Factor cov + specific risk; PSD and calibration test (predicted vs realised vol bias statistic ≈ 1 on synthetic data).
4. Portfolio analytics; decomposition sums to total.
5. ETF time-series betas.
6. Build on real data for 2018–2024 (integration test, marked) and log the bias statistics.

## Acceptance criteria
- Synthetic recovery and bias tests pass; risk decomposition adds up; build time for one date with 500 names < 2 s.

## Verify
`uv run pytest tests/unit/quant/risk_model -q`
