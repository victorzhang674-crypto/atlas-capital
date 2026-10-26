# Stage 32 — Sentiment analyst agent
**Phase:** 4 · **Size:** S · **Depends on:** 12, 28

## Goal
Read crowd and news sentiment, its trend and its extremes (crowding and contrarian signals), anchored on the numeric lexicon baseline.

## In scope
- Tools: `get_sentiment_series` (daily decayed lexicon score, 90d, percentile), `get_social_posts` (if enabled), `get_short_interest_proxy` (backlog stub returns "unavailable")
- Prompt: level vs trend, divergence from price, extremes (contrarian risk), quality of the sources
- Output `AnalystReport`; the LLM's sentiment score must stay within ±0.3 of the numeric baseline unless it justifies the difference with cited evidence (validator)

## Files
`src/atlas/agents/research/sentiment.py`, prompt, tests

## Acceptance criteria
- The validator enforces anchoring; tests pass offline.

## Verify
`uv run pytest tests/unit/agents/research/test_sentiment.py -q`
