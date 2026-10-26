# Stage 30 — Technical analyst agent
**Phase:** 4 · **Size:** S · **Depends on:** 15, 28

## Goal
An analyst that interprets precomputed technical features (it never computes them itself) for trend, momentum, volatility, support/resistance and timing.

## In scope
- Tool `get_technical_snapshot` (from Stage 15: values + percentiles + regime context), `get_price_summary` (recent OHLC table, 52w range, key MAs)
- Prompt `technical_analyst.j2`: trend state, momentum, volatility regime, levels, timing/entry considerations, invalidation level
- Output `AnalystReport` + `suggested_stop_pct` and `timing` (now / wait for pullback / avoid) fields in signals

## Files
`src/atlas/agents/research/technical.py`, prompt, tests

## Acceptance criteria
- Schema-valid output with evidence; FakeLLM tests; a recorded live fixture.

## Verify
`uv run pytest tests/unit/agents/research/test_technical.py -q`
