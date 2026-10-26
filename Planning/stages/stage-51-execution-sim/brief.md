# Stage 51 — Execution simulator & transaction-cost models
**Phase:** 7 · **Size:** M · **Depends on:** 50

## Goal
Realistic, conservative fills for backtests: spread, market impact, partial fills, borrow and commissions, plus TCA to measure them.

## In scope
- Fill models: decision at the pre-market cutoff → fill at the **next session open** (default), or VWAP proxy ((O+H+L+C)/4) for normal urgency; urgent orders at the open + an extra half-spread
- Costs: half-spread (from a market-cap bucket table, e.g. 2–10 bps), square-root market impact `k·σ·√(Q/ADV)` (k = 0.1 default), commission (per share, Alpaca-like 0 + regulatory fees for sells), borrow fee for shorts (GC 0.3%/yr, HTB tiers from Stage 48)
- Participation cap → partial fills; unfilled remainder carried to the next day or canceled by TIF
- No fills on halted/no-bar days; gap risk naturally included via open fills
- TCA: implementation shortfall vs decision price and vs arrival (open), by pod and by order size bucket; `tca_report(run)`
- `SimulatedVenue` implementing `ExecutionVenue`

## Files
`src/atlas/execution/{simulator.py,cost_models.py,tca.py}`, tests

## Acceptance criteria
- Cost model unit tests with known inputs; partial-fill tests; the TCA decomposition sums to the total shortfall.

## Verify
`uv run pytest tests/unit/execution -q`
