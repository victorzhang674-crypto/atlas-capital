# Stage 42 — Portfolio optimizer (cvxpy)
**Phase:** 5 · **Size:** L · **Depends on:** 41 · **Spec refs:** §9

## Goal
A constrained optimiser that turns expected returns + the risk model into target weights that satisfy every portfolio constraint **by construction** (the risk engine is then a second line of defence).

## In scope
- Objective: maximise `μᵀw − λ·wᵀΣw − κ·‖w − w₀‖₁·cost − γ·‖w‖₁·borrow` (risk aversion λ calibrated to hit the target vol)
- Two-level allocation: (1) per-pod sub-portfolio optimisation within the pod's risk budget, (2) aggregate, then fund-level constraints with a final "netting" optimisation, minimising the distance to the pod-sum subject to fund constraints
- Constraints from `limits.yaml`: gross, net range, beta range, single name long/short caps, sector net caps, factor exposure caps (from Stage 19), ADV liquidity caps (position ≤ x days of ADV), turnover cap, no shorts where `is_shortable=False`, min position size (drop dust < 0.25%)
- Vol targeting: scale to `target_vol × regime.risk_scalar` (ex-ante via the risk model)
- Solver: Clarabel/OSQP with fallback; infeasibility handling: relax in a documented priority order (turnover → factor caps → … never the hard risk caps), else keep the current book and raise an alert
- Output `TargetPortfolio` + `OptimizerReport{status, binding_constraints, ex_ante_vol, beta, relaxations}`

## Files
`src/atlas/portfolio/{optimizer.py,constraints.py,vol_target.py}`, tests

## Tasks
1. Single-level MVO with basic constraints; verify against the closed form when unconstrained.
2. Each constraint family test-first (a constructed case where it must bind).
3. Two-level pod → fund netting.
4. Vol targeting accuracy (ex-ante vol within ±0.5% of target).
5. Infeasibility relaxation order tests.
6. Performance: 500 names solve in under 3 s.

## Acceptance criteria
- Every constraint is honoured on random problems (hypothesis); infeasibility is handled safely.

## Verify
`uv run pytest tests/unit/portfolio/test_optimizer.py -q`
