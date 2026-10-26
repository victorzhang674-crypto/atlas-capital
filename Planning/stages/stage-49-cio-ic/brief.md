# Stage 49 — CIO capital allocation & Investment Committee  → Milestone M4
**Phase:** 6 · **Size:** L · **Depends on:** 38–40, 44 · **Spec refs:** §5 steps 9 & 12

## Goal
Top-level governance: the CIO allocates risk budget across pods, and the Investment Committee approves material changes. This completes **M4**: a risk-approved, compliance-checked fund target for any date.

## In scope
- **CIO allocation (hybrid):** base = inverse-volatility / risk-parity across pods using pod return covariance (simulated history; equal-risk at start) → performance tilt (trailing 6m pod Sharpe and prediction calibration, shrunk toward the base) → drawdown penalty (pods under stop get cut automatically) → LLM CIO agent may tilt within ±20% relative of each budget with a written rationale referencing regime/macro → normalise; monthly cadence plus emergency reallocation on circuit-breaker events
- **Investment Committee:** triggered for new positions > 2% NAV, total turnover > 15% NAV in a day, or any pod budget change > 10%; three voters (deep tier) with distinct lenses (fundamental skeptic, risk-focused, macro), each `vote{approve|reduce|reject, reason}`; rules: majority approve; "reduce" shrinks the item by 50%; any "reject" plus the CRO concern → reject; the decision is recorded
- `decide_fund_target(as_of)` function chaining: pods → BL → optimiser → sizing/hedging → risk engine → CRO → IC → compliance (the orchestration graph comes in Stage 55, but this pure function exists now for testing)
- CLI: `atlas fund target --date D [--replay]` prints the target, risk summary and IC outcome

## Files
`src/atlas/agents/{cio.py,committee.py}`, `src/atlas/portfolio/allocation.py`, `src/atlas/portfolio/decide.py`, `src/atlas/cli/fund_cmd.py`, prompts, tests

## Acceptance criteria
- Allocations sum to 1 and respect the ±20% LLM band; IC triggers and rules are tested; `atlas fund target --replay` runs end to end offline from fixtures.

## Verify
`uv run pytest tests/unit -q && uv run atlas fund target --date 2024-05-01 --replay`
