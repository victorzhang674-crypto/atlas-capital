# Stage 28 — LangGraph orchestration skeleton
**Phase:** 3 · **Size:** M · **Depends on:** 26

## Goal
The graph plumbing for the fund: typed state, a subgraph per research name, parallel fan-out, checkpointing and resume, proven with stub agents.

## In scope
- `FundState` (TypedDict/Pydantic) with reducers: `as_of, regime, macro_outlook, research_queue, reports{symbol:{analyst:report}}, theses, proposals, allocation, target, risk_decision, compliance, orders, errors[]`
- `ResearchSubgraph(symbol)`: analysts fan out in parallel → join → debate → director (stub nodes for now)
- Fan-out over the research queue with the `Send` API, plus a concurrency limit
- SQLite checkpointer (`data/runs/<run_id>/checkpoints.sqlite`); resume a failed run from its last checkpoint
- Error policy: a failing node records into `errors` and the symbol is dropped from the day (fail safe); it never crashes the whole graph
- Graph visualisation export (mermaid) to docs

## Out of scope
Real agents; the full Fund Day (Stage 55).

## Files
`src/atlas/orchestration/{state.py,research_graph.py,checkpoint.py,errors.py}`, tests

## Tasks
1. State + reducers (parallel writes merge correctly).
2. Research subgraph with stubs; fan-out across 5 symbols.
3. Checkpoint + resume test (kill mid-run, resume, same final state).
4. Error isolation test.

## Acceptance criteria
- Parallel stub graph over N symbols completes; resume works; one failing symbol does not affect the others.

## Verify
`uv run pytest tests/unit/orchestration -q`
