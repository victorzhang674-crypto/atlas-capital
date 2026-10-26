# Stage 22 — Systematic alpha models
**Phase:** 2 · **Size:** M · **Depends on:** 17, 19

## Goal
Deterministic alpha signals with calibrated expected returns: a cross-sectional multi-factor equity model and time-series trend for ETFs.

## In scope
- `AlphaModel` protocol: `predict(view) -> DataFrame[symbol: score, exp_return_ann, confidence]`
- Multi-factor equity alpha: composite of momentum 12-1, value, quality, low-vol, short-term reversal (weights from config; optional IC-weighted on a trailing 36m window, PIT); sector-neutral; score → expected return via a Grinold rule `α = IC × σ_resid × z`
- Time-series momentum (ETFs): blended 1/3/6/12-month trend signals scaled by inverse vol → `exp_return` and direction
- Earnings drift signal (PEAD) from `earnings_reactions` (Stage 12): post-event abnormal return direction, decays over 60 days
- Evaluation via Stage 17 tooling on 2016–2021 (in-sample only; hold-out stays untouched), with results recorded in the handoff note

## Out of scope
ML models (backlog: gradient boosting on features with purged CV).

## Files
`src/atlas/quant/alpha/{base.py,multifactor.py,tsmom.py,pead.py,calibration.py}`, tests

## Tasks
1. Protocol + Grinold calibration with unit tests.
2. Multi-factor model; synthetic planted-alpha test.
3. TSMOM on synthetic trending series.
4. PEAD signal decay test.
5. Research run on real in-sample data; record IC/Sharpe.

## Acceptance criteria
- Models return calibrated expected returns (annualised, in fractions); the in-sample IC of the composite is positive with a t-stat recorded.

## Verify
`uv run pytest tests/unit/quant/alpha -q`
