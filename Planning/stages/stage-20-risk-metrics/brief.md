# Stage 20 — Risk & performance metrics library
**Phase:** 2 · **Size:** M · **Depends on:** 18

## Goal
One audited library for every risk and performance number the fund reports or limits on.

## In scope
- Returns-based: annualised return/vol, Sharpe, Sortino, Calmar, Omega, max drawdown + duration, ulcer index, skew/kurtosis, hit rate, profit factor, tail ratio, rolling versions
- Benchmark-relative: beta, alpha (CAPM), tracking error, information ratio, up/down capture, correlation
- VaR/CVaR: historical, parametric (normal and Cornish-Fisher), Monte Carlo from a covariance; for a portfolio (weights + returns/cov) and a P&L series; horizon scaling
- Risk contributions: marginal, component, percent (Euler) from a covariance
- Liquidity metrics: days-to-liquidate at x% ADV, % of positions liquid in 1/3/5 days
- Exposure metrics: gross, net, long/short, beta-adjusted net, sector nets

## Out of scope
Attribution (Stage 53); report formatting.

## Files
`src/atlas/quant/metrics/{performance.py,relative.py,var.py,contributions.py,liquidity.py,exposure.py}`, tests

## Tasks
Test-first each metric against known values (cross-check against `empyrical`-style formulas on fixed series); hypothesis tests (component contributions sum to total vol; CVaR ≥ VaR).

## Acceptance criteria
- Every metric documented with units and tested; there is a single source of truth (later stages import from here only).

## Verify
`uv run pytest tests/unit/quant/metrics -q`
