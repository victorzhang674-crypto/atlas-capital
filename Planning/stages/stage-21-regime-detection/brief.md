# Stage 21 — Regime detection
**Phase:** 2 · **Size:** M · **Depends on:** 10, 15

## Goal
A probabilistic market-regime read that pods, the CIO and risk can condition on, which helps cut risk when the environment turns hostile.

## In scope
- Volatility regime: SPY realised vol + VIX percentile → LOW / NORMAL / HIGH / CRISIS
- Trend regime: SPY vs 200d MA, 12-1 momentum sign, breadth (% of universe above the 200d MA)
- Statistical regime: Gaussian HMM (hmmlearn, 2–3 states) on [SPY returns, vol, HY OAS change, curve slope], fit on an expanding window only (refit monthly, PIT), states ordered by vol for stable labels
- Macro quadrant: growth (INDPRO/PAYEMS/claims trend) × inflation (core CPI 3m annualised vs 12m) → Goldilocks / Reflation / Stagflation / Deflation
- Credit/liquidity stress flag: HY OAS z-score > 2 or NFCI > 0
- `RegimeState{as_of, vol_regime, trend_regime, hmm_probs, macro_quadrant, stress_flag, risk_scalar∈[0.25,1.0]}`; `risk_scalar` is a documented deterministic mapping used by vol-targeting
- Persist the daily regime history

## Out of scope
LLM interpretation (Stage 33).

## Files
`src/atlas/quant/regime/{vol_trend.py,hmm.py,macro_quadrant.py,state.py}`, tests

## Tasks
1. Rule-based regimes with synthetic series.
2. HMM on synthetic two-state data recovers states (accuracy > 85%); label-stability test across refits.
3. Expanding-window PIT test (fit at t uses data ≤ t).
4. Quadrant and stress flag; risk_scalar mapping table tests.
5. Real-data sanity check (integration): March 2020 → CRISIS/stress.

## Acceptance criteria
- PIT-safe, stable labels; the 2020 and 2022 episodes are flagged on real data.

## Verify
`uv run pytest tests/unit/quant/regime -q`
