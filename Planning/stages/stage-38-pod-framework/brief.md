# Stage 38 — Pod framework & Fundamental L/S pod
**Phase:** 5 · **Size:** L · **Depends on:** 36 · **Spec refs:** §8

## Goal
The multi-manager structure: each pod has a mandate, a risk budget, its own sub-book and a PM. Plus the first pod: **Fundamental Long/Short**, driven by LLM theses.

## In scope
- `Pod` base: `name, mandate (universe, long/short allowed, max names, max gross, horizon), risk_budget, rebalance schedule`, `propose(ctx) -> PodProposal`, `book` (positions attributed to the pod), `pnl()`
- Pod registry built from `config/pods/*.yaml`; enabled flags
- Mandate validator: proposals outside the mandate are clipped and the clip is logged
- Fundamental L/S PM agent (deep tier): inputs = active theses (longs and shorts), current pod book, macro outlook (sector tilts), the pod's P&L/drawdown, lessons; output `PodProposal{views[{ticker, exp_return (from thesis p50, horizon-adjusted to annual), confidence (thesis conviction × agent trust), horizon}], exits[], rationale}`
- Deterministic rules on top: exit when a thesis is INVALIDATED; max names; min conviction to open (0.55); pair longs vs shorts within sectors when possible (to reduce beta)
- Pod-level P&L and drawdown tracking hooks (filled by accounting in Stage 52)

## Files
`src/atlas/agents/pods/{base.py,registry.py,mandate.py,fundamental_ls.py}`, `config/pods/fundamental_ls.yaml`, prompt, tests

## Tasks
1. Pod base + registry + mandate validation (pure code).
2. Fundamental PM agent with FakeLLM; deterministic rules tests.
3. Integration: theses from fixture → proposal.

## Acceptance criteria
- Proposals always satisfy the mandate; invalidated theses always exit; offline tests pass.

## Verify
`uv run pytest tests/unit/agents/pods -q`
