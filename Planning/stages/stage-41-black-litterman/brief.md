# Stage 41 — Views → Black-Litterman posterior
**Phase:** 5 · **Size:** M · **Depends on:** 19, 38 · **Spec refs:** §1.3, §10

## Goal
Turn heterogeneous pod views (LLM convictions, quant scores) into one coherent set of expected returns, weighted by how much each source has earned trust.

## In scope
- Prior: equilibrium returns `π = δ Σ w_mkt` (cap-weighted universe, δ from the target Sharpe) **or** zero-alpha prior for the market-neutral book (configurable; default zero-alpha + small momentum tilt)
- Views: absolute views per ticker from each pod; confidence → Ω via the Idzorek method (confidence% → view variance); trust weight per source agent/pod (Stage 54 later; default 1.0) scales the confidence
- Conflicting views on the same ticker from different pods are kept as separate rows (BL blends them)
- Posterior `μ_BL, Σ_BL`; guardrails: clip |μ| ≤ 50% annualised; shrink views with horizon < 5 days
- Outputs `ExpectedReturns{as_of, mu (annual), source_contributions}` for transparency (which pod drove each μ)

## Files
`src/atlas/portfolio/{views.py,black_litterman.py}`, tests

## Tasks
1. BL maths against a textbook example (He & Litterman numbers).
2. Idzorek confidence mapping (100% confidence → posterior equals the view).
3. Multi-source conflicting views test.
4. Trust weight scaling + contributions breakdown.

## Acceptance criteria
- Matches the reference example within 1e-6; contributions sum correctly.

## Verify
`uv run pytest tests/unit/portfolio/test_black_litterman.py -q`
