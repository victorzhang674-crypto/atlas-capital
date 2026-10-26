# Stage 25 — LLM record/replay cache
**Phase:** 3 · **Size:** S · **Depends on:** 24 · **Spec refs:** §3.3

## Goal
Make LLM-driven backtests reproducible and cheap: identical requests return stored responses, and replay mode forbids new calls.

## In scope
- Cache key = SHA-256 of (provider, model, canonical messages, schema name+version, temperature, tools, prompt template version)
- Store: DuckDB table `llm_cache(key, created_at, model, request_json, response_json, usage)` + optional compressed blobs
- Modes: `off`, `read_write` (default dev/backtest), `replay_only` (a miss raises `CacheMiss`), `record` (always call + store)
- Cache-hit cost = 0, recorded as a hit in the ledger; hit-rate stats
- CLI: `atlas llm cache stats|export|prune --older-than`
- Note in the docs: replay is only valid while prompts and data are unchanged (the template version is part of the key)

## Files
`src/atlas/llm/cache.py`, `src/atlas/cli/llm_cmd.py`, tests

## Tasks
Key canonicalisation tests (dict order is irrelevant; a template version bump changes the key); mode behaviour tests; stats; CLI.

## Acceptance criteria
- Running the same scripted agent twice in `read_write` makes 1 provider call; `replay_only` with a miss fails loudly.

## Verify
`uv run pytest tests/unit/llm -q`
