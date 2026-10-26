# Stage 54 — Learning loop: reflection & trust weights
**Phase:** 7 · **Size:** M · **Depends on:** 27, 53 · **Spec refs:** §10

## Goal
Close the loop: agents learn lessons from settled outcomes, and their influence adapts to their demonstrated skill.

## In scope
- Reflection agent (quick tier): for each settled thesis/proposal (and each closed position), write a 2–4 sentence lesson citing the outcome numbers and the original reasoning (following the reference's reflection prompt, extended with per-agent attribution: which analyst's signal was right or wrong)
- Lessons are stored in memory (Stage 27) with tags (sector, regime, setup type) and `created_at = settlement time` (PIT)
- Trust weights: `trust = clip(0.5 + calibration_skill, 0.2, 1.5)` from rolling Brier skill vs a naïve baseline, shrunk toward 1.0 with a small sample (Bayesian, n₀ = 20); feeds the BL confidence (Stage 41) and the pod-performance tilt (Stage 49)
- Weekly "lessons digest" consolidation (dedupe, summarise themes) into global lessons
- Guardrails: lessons can never contain prices/dates after their created_at (validator); max lessons per prompt

## Files
`src/atlas/agents/reflection.py`, `src/atlas/performance/trust.py`, prompt, tests

## Acceptance criteria
- Trust weights converge correctly on synthetic agents (skilled vs random); lessons are PIT-safe; offline tests pass.

## Verify
`uv run pytest tests/unit/agents/test_reflection.py tests/unit/performance -q`
