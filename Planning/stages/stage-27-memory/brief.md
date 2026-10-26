# Stage 27 — Memory system (episodic + lessons, point-in-time)
**Phase:** 3 · **Size:** M · **Depends on:** 26 · **Spec refs:** §10

## Goal
Agents remember past situations, decisions and lessons, without seeing the future: memories created after `as_of`, or whose outcomes settle after it, are invisible.

## In scope
- Stores: `episodes(id, agent, symbol?, created_at, situation_text, decision_json, outcome_json?, settled_at?)`, `lessons(id, agent|"global", created_at, text, tags, source_episode_ids, quality_score)`
- Vector index: ChromaDB (local persistent) with sentence-transformers embeddings (small local model, e.g. all-MiniLM-L6-v2), or a pluggable `Embedder`; metadata filter `created_at <= as_of`
- Retrieval API: `recall(agent, query_text, as_of, k=5, symbol=None) -> list[Memory]`; outcome fields are visible only if `settled_at <= as_of`
- Lesson curation: dedupe near-duplicates (cosine > 0.92), cap per agent, decay of quality over time
- Memory rendering partial for prompts (compact, 2–4 lines each)

## Out of scope
Writing lessons (Stage 54); scoring (Stage 53).

## Files
`src/atlas/agents/memory/{store.py,embedder.py,retrieval.py,render.py}`, tests

## Tasks
1. Store CRUD with DuckDB.
2. Embedder interface + a fake deterministic embedder for tests.
3. PIT retrieval tests (future memories hidden; outcomes hidden until settled).
4. Dedupe + caps.

## Acceptance criteria
- The PIT memory tests pass; retrieval returns relevant items on a synthetic corpus.

## Verify
`uv run pytest tests/unit/agents/memory -q`
