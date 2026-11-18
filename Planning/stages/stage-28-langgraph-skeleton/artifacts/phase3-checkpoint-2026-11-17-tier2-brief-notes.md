# Phase 3 checkpoint, Tier 2 item 7: proposed brief notes (draft for the owner's approval)

**Status (2026-11-17): approved by the owner ("Approve all notes") and applied as written to
briefs 29, 30, 31, 33, 35, 36, 53, 54 and 56; each added sentence is tagged "Phase 3
checkpoint, Tier 2 item 7; owner approved 2026-11-17".**

Briefs are edited only with the owner's approval (Project.md Rule 2A.1). Nothing below is in
a brief yet. Each note says which brief it would go into and the exact text to add. Approve
them all, approve some, or change the wording.

## A. Where a recorded fixture's data comes from (finding 10b)

A recorded live run has two halves: the model's answers, and the lake rows the agent's tools
read. The LLM cache key includes the tool results, so a replay must read the same rows. CI has
no lake. `atlas llm cache export` / `import` (Tier 1) move the first half. Nothing moves the
second half yet.

**Proposed approach.** Each fixture is a folder `tests/fixtures/recorded/<stage>-<name>/`:
- `llm_cache.jsonl`, written by `atlas llm cache export`;
- `lake/`, a small lake slice with only the rows the run read (the symbols, the as-of day and
  the look-back windows of the tools used). A helper builds the slice; this is Tier 3 item 1,
  `atlas data slice --symbols … --as-of … --out DIR`, and it would become a task of Stage 29;
- `README.md`: the command that recorded the run, its date, model and cost.

The replay test builds its lake from `lake/`, imports `llm_cache.jsonl`, runs with
`llm.cache.mode: replay_only` and no API key, and expects zero provider calls.

**Notes to add:**
- Stage 29, In scope, after "one recorded live run on AAPL 2024-05-01 stored as a fixture for
  later stages": *"The fixture is a folder under `tests/fixtures/recorded/`: the exported cache
  (`atlas llm cache export`) and the lake slice the run read, built by a new
  `atlas data slice --symbols … --as-of … --out DIR`. Its replay test needs no lake and no
  key (Phase 3 checkpoint, Tier 2 item 7)."* Task 4 would gain: *"…and the lake slice
  (`atlas data slice`)."*
- Stages 30, 31, 33 and 36, at their recorded fixture: *"Recorded as Stage 29's fixtures are
  (exported cache + lake slice under `tests/fixtures/recorded/`)."*
- Stage 35, acceptance "runs offline from fixtures": *"(the Stage 29–34 recorded fixtures)"*.

## B. Which agent records an episode (finding 10c)

Stage 27 stores episodes, and Stage 54 writes lessons from settled outcomes. No brief says
who records the episode that a lesson is later written from.

**Proposed:** the Research Director records one episode per thesis, when it publishes the
thesis (situation = the debate summary; decision = the thesis's direction, conviction and
horizon). Stage 53 settles each episode when the thesis's predictions settle.

**Notes to add:**
- Stage 36, In scope: *"After publishing a thesis the Director records one memory episode
  (`AgentMemory.record_episode`, agent `research_director`, situation = the debate summary,
  decision = direction / conviction / horizon / thesis id, `created_at` = the decision
  moment)."*
- Stage 53, In scope, prediction settlement: *"…and settles the thesis's memory episode with
  the realised outcome (`settle_episode`, `settled_at` = the settlement time)."*

## C. Keeping one backtest run's memories from the next (finding 6, second half)

Tier 2 item 1 made memory ids reproducible under a seed (done). Separately, two backtest runs
on one lake share one memory, so run 2 would recall run 1's lessons. Their `created_at` is
simulated time, so they pass the point-in-time filter. Nothing writes memories before Stage
54, so this has no effect yet.

**Proposed:** a backtest run gets a fresh memory of its own (an ephemeral index plus its own
`episodes` / `lessons` rows keyed by `run_id`). Only paper and live runs share the
persistent memory.

**Note to add:**
- Stage 56, In scope: *"Each backtest run uses its own agent memory (episodes and lessons
  scoped to its `run_id`); a backtest never recalls another run's lessons, and paper / live
  runs never see a backtest's."*
- Stage 54, Guardrails: *"Lessons are written to the memory of the run that settled them
  (Stage 56 scopes a backtest's memory to its run)."*
