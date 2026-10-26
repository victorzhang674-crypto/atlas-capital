# Stage 39 — Systematic & Stat-Arb pods
**Phase:** 5 · **Size:** M · **Depends on:** 22, 23, 38

## Goal
Two fully deterministic pods plugged into the framework, giving uncorrelated return streams with no LLM cost.

## In scope
- `SystematicFactorPod`: multi-factor alpha (Stage 22) → views on the top/bottom N (default 40/40), sector-neutral intent, exp_return from Grinold calibration, confidence from the trailing IC t-stat (capped), weekly rebalance, turnover-aware hold band (do not trade if the rank change is small)
- `StatArbPod`: active pair signals (Stage 23) → dollar-neutral per-pair target weights (`target_weights` provided directly rather than views), daily rebalance, pair stop logic
- Both expose `diagnostics()` (signal stats) for reporting

## Files
`src/atlas/agents/pods/{systematic.py,statarb.py}`, configs, tests

## Acceptance criteria
- Deterministic outputs (same input → same proposal); hold band reduces turnover in tests; stat-arb proposals are dollar-neutral per pair.

## Verify
`uv run pytest tests/unit/agents/pods -q`
