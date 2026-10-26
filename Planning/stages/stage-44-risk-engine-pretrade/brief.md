# Stage 44 — Risk engine: pre-trade hard limits
**Phase:** 6 Risk · **Size:** L · **Depends on:** 20, 19, 43 · **Spec refs:** §3.1, §9

## Goal
The deterministic gatekeeper. Every target portfolio passes through it, and it returns APPROVE, RESIZE (with the minimally changed compliant weights) or REJECT. **No LLM can override it**, and overrides by the owner are logged.

## In scope
- Limit checks (all from config): gross, net, beta, single-name caps, sector nets, factor exposures, liquidity (ADV), VaR95/CVaR97.5 (historical, from Stage 20 using the risk-model covariance + 2y history), worst historical stress loss (hook for Stage 46), concentration (top-10 weight, HHI), pod risk budget usage, correlation of new positions with the existing book
- RESIZE algorithm: proportional scale-down of the offending legs first, then an optimiser-based projection onto the feasible set (re-using Stage 42 constraints) minimising the change
- REJECT conditions: data CRITICAL for a name, missing risk-model coverage, a circuit breaker in HALT
- `RiskDecision` with every check (value, limit, utilisation %, pass/fail); `RISK_DECISION` + `LIMIT_BREACH` audit events
- Owner override API: `override(limit, new_value, reason, expires)` (requires a CLI confirmation phrase; audited); LLM agents have no access to this API (enforced by the module boundary and a test)

## Files
`src/atlas/risk/{limits.py,checks.py,engine.py,resize.py,overrides.py}`, tests

## Tasks
1. Each check as a pure function with pass/fail tests.
2. Engine aggregation + decision logic.
3. Resize projection: post-resize passes all checks; minimal change vs naïve scaling.
4. Override flow + audit; an "agents cannot import overrides" test (import-linter contract).

## Acceptance criteria
- On 1,000 random target portfolios, every approved or resized output passes every check (hypothesis); rejects carry reasons.

## Verify
`uv run pytest tests/unit/risk -q`
