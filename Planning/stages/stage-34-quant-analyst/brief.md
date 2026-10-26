# Stage 34 — Quant analyst agent
**Phase:** 4 · **Size:** S · **Depends on:** 19, 22, 28

## Goal
Explain what the quant models say about a name (alpha scores, factor exposures, risk contribution, crowding), so the debate weighs the maths and the narrative together.

## In scope
- Tools: `get_alpha_scores` (multi-factor, PEAD, components), `get_factor_exposures` (risk-model exposures, specific vol), `get_crowding` (momentum crowding proxy: factor-return volatility spike + name in top decile of momentum), `get_pair_relationships` (if in active pairs)
- Output `AnalystReport` with signals: quant_score, factor_risk_notes, crowding_flag
- Deterministic pre-summary: the agent receives a numeric summary and is forbidden from changing the numbers (validator compares them)

## Files
`src/atlas/agents/research/quant.py`, prompt, tests

## Acceptance criteria
- Numbers in the output match the tool numbers exactly; offline tests pass.

## Verify
`uv run pytest tests/unit/agents/research/test_quant.py -q`
