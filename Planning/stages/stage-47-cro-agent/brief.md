# Stage 47 — CRO agent & risk memo
**Phase:** 6 · **Size:** S · **Depends on:** 44–46

## Goal
A Chief Risk Officer agent that explains the risk position in plain language, challenges concentrated or crowded bets, and can **only tighten** risk, never loosen it.

## In scope
- Inputs: RiskDecision, MonitorReport, StressReport, factor exposures, regime, top risk contributors, pod P&L
- Output `RiskMemo{summary, key_risks[], concerns_by_position[], recommended_actions[{type: reduce|hedge|exit|watch, target, magnitude ≤ current}], tighten_limits{limit: new_value}}`
- Enforcement: `tighten_limits` are accepted only if stricter than current (validator); `recommended_actions` become optional resize inputs, applied deterministically within bounds (max 25% reduction per position per day) and logged
- Used by IC (Stage 49) and the daily risk report (Stage 63)

## Files
`src/atlas/agents/cro.py`, prompt, tests

## Acceptance criteria
- Any attempt to loosen a limit is rejected by the validator (test); offline FakeLLM tests pass.

## Verify
`uv run pytest tests/unit/agents/test_cro.py -q`
