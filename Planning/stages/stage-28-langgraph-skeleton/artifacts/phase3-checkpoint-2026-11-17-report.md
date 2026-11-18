# Phase 3 checkpoint (Stages 24–28): report — 2026-11-17

Scope: are Stages 24–28 complete against their briefs, what gaps remain, and what is worth
improving before Phase 4 (Stages 29–36) builds eight LLM agents on this infrastructure. Kept
in the Stage 28 folder because Stage 28 completes Phase 3. Fixes are recorded in the folder
of the stage whose code they change (Rule 2A), as in the Phase 0, 1 and 2 checkpoints.

Base: branch `claude/phase-3-checkpoint-stages-24-28-e324d2` at `3900868` (= `main` =
`origin/main`; Stage 28 is `DONE`, Stage 29 `NOT STARTED`).

## 1. Checks re-run in this chat

| Check | Result |
|---|---|
| Gate: `uv run pytest -q` (serial) | exit 0: **4921 passed**, 74 deselected, 9 min 31 s |
| `make check` | exit 0: `ruff check .` All checks passed · `ruff format --check .` 468 files already formatted · `mypy src/atlas` no issues in 178 source files · pytest 4918 passed in parallel (6 min 07 s) + 3 timing tests alone |
| The four Verify commands of Stages 24–28 | all exit 0: `tests/unit/llm` 305 passed, 2 deselected (Stages 24 and 25) · `tests/unit/agents` 308 passed, 3 deselected (26, with the memory tests) · `tests/unit/agents/memory` 158 passed, 1 deselected (27) · `tests/unit/orchestration` 189 passed (28) |
| `uv run pytest -m integration -q -rs` (offline; no lake in this worktree) | exit 0: **41 passed, 15 skipped** in 3 min 12 s (the 15 skips are the real-lake tests: this worktree has no lake; the offline Phase 0 and Phase 2 runs pass) |
| Live DeepSeek, both tiers (`-m llm`, about 0.003 USD) | **4 passed**: the gateway's structured call (Stage 24) and a tool-using agent with citations (Stage 26) on `deepseek-flash` and `deepseek-v4-pro`; costs equal the published rates |
| DeepSeek's pricing page, read 2026-11-17 | equal to `config/fund.yaml`: both models, off-peak and peak rates, peak hours 01:00–04:00 and 06:00–10:00 UTC on weekdays; model ids unchanged |
| Acceptance evidence | all 34 test names quoted in the five evidence blocks still exist; no `skip` / `xfail` in `tests/unit/{llm,agents,orchestration}` except the two live tests without a key |
| Probes on the unchanged code | the view cache under four threads: `KeyError` (finding 2); two identically built memories: different prompt text (finding 6); `build_gateway` in `replay_only` without a key: `ConfigError` (finding 3) |

Raw output: `phase3-checkpoint-2026-11-17-*.txt` in this folder; the commands are in
`phase3-checkpoint-2026-11-17-checks.sh`.

## 2. Stage by stage

Every stage is `DONE` with `ALL CRITERIA VERIFIED` in its `verification.md`, evidence in
`PROGRESS.md`, and no waivers. Every test named as acceptance evidence still exists (34 names
checked) and passed today. No `skip` / `xfail` marker hides a criterion (the only skips are
the two live tests without a key).

| Stage | Acceptance criteria | Still holds | Open in records |
|---|---|---|---|
| 24 LLM gateway | every call costed and logged; budgets enforced; agents testable with FakeLLM only | yes (part of 306; live: both tiers answered, costs match the published rates) | Anthropic provider never run live (no key): refusals, long-prompt price tier, tool loops; budgets provisional; no streaming / async |
| 25 Record/replay cache | same scripted agent twice in `read_write` = 1 provider call; `replay_only` miss fails loudly | yes (part of 306) | none (four backlog items closed 2026-11-14) |
| 26 Agent framework | toy agent fully offline; uncited claims caught; tools cannot see beyond `as_of` | yes (150 + memory; live tool agent passed on both tiers today) | Anthropic tool loops not probed |
| 27 Memory | PIT memory tests pass; retrieval returns relevant items on a synthetic corpus | yes (158) | `min_similarity` tuned on 11 synthetic episodes; lesson writes expected in time order |
| 28 LangGraph skeleton | parallel stub graph over N symbols completes; resume works; one failing symbol does not affect the others | yes (189) | `LookAheadError` in `errors` should fail a backtest day (55 / 56); no per-node timeout |

**Conclusion: no stage needs to reopen.** Every criterion is met by the code, and each stage
was audited at least once after `DONE`. What the per-stage audits could not see is what
happens when the five stages meet, and what Phase 4 will ask of them on its first day. The
gaps below are all of that kind.

### What Phase 3 promises, checked

"A provider-agnostic gateway, a replay cache, one way to build an agent, a point-in-time
memory, and the graph plumbing, proven with stub agents." Phase 4 (Stages 29–36) then builds
eight LLM agents on it, each with "a recorded live fixture" that "runs offline from the cache".

| Capability | Works alone (stage tests) | Works together (before this checkpoint) |
|---|---|---|
| A real `BaseAgent` in an analyst slot of the research graph | each half tested | **never run**: the graph was only ever run with stub functions; no test builds an agent inside it |
| Agents on the graph's worker threads read the day's `DataView` | view single-threaded by design (Stage 13) | **fails under load**: the view's cache raises `KeyError` when threads evict each other's entries; the graph turns that into a dropped symbol |
| Replay with zero API calls (spec §3 principle 3) | yes, with a provider object in hand | **needs the API key**: `build_gateway` refuses to build without `DEEPSEEK_API_KEY` even in `replay_only`, where no call can happen |
| Recorded live fixtures (Stages 29–36) | `atlas llm cache export` writes a file | **no way back in**: nothing reads an export; a recording cannot be committed and replayed elsewhere |
| FakeLLM tests of agents in the fan-out | scripts keyed by agent and step | **cannot tell symbols apart**: five symbols ask the same agent and step at once, in no fixed order |
| Cost, traces, audit, checkpoints under one `run_id` | each tested | works (probe and the new end-to-end test) |

## 3. Findings of this checkpoint (new)

1. **The Phase 3 chain had never been run as one.** Stage 28 proved the graph with stub
   functions; Stages 24–27 proved the gateway, cache, agents and memory one agent at a time on
   one thread. No test, in any stage, put a `BaseAgent` into the research graph. A probe did
   (three agents × four symbols on four threads, one DuckDB for ledger, cache, traces): it
   works, a second run replays with zero provider calls and identical reports and theses.
   The findings below are what the probe and the code reading turned up around it.
2. **`DataView` is not safe to share between threads, and the graph shares it.** The view's
   cache (`_LRU`, Stage 13) checks a key and then touches it in separate steps. With four
   threads and a full cache a reader's key is evicted between the two steps: `KeyError`
   (reproduced: 3 errors in 800,000 reads on a two-slot cache; `…-probe-lru-race.txt`). Inside
   the graph the error policy turns it into a dropped symbol with an error nobody can
   explain. Two threads that miss the same key also both compute it: on a new as-of the
   block list (one quality report, about a second on the main lake) would run once per
   thread. The lazily built readers and `Universe`'s membership cache have the same
   check-then-use shape.
3. **A replay needs an API key.** `build_gateway` calls `build_provider`, which raises
   `ConfigError` without `DEEPSEEK_API_KEY`, also in `replay_only`, where a miss raises
   `CacheMiss` before any provider is asked. Stage 36's acceptance command
   (`atlas research AAPL --date 2024-05-01 --replay` "works offline") and every replay in CI
   would fail at start-up for a key they can never use.
4. **Recordings cannot leave the lake they were made in.** `atlas llm cache export` writes
   JSON lines; nothing reads them back (no `import`, no loader). Stages 29, 30, 31, 33 and 36
   each require a recorded live run kept as a fixture and replayed offline. Without a way
   back in, each stage would invent its own.
5. **FakeLLM cannot answer per symbol.** Its scripts are keyed by agent and step. In the
   fan-out the same agent and step are asked for every symbol at once and the order depends
   on thread timing, so a scripted list hands AAA's report to BBB (the graph then drops the
   symbol: wrong ticker). Stages 35 and 36 must test agents inside the graph with FakeLLM.
6. **Memory ids are random and are shown to the model.** `record_episode` and `add_lesson`
   mint `epi_…` / `les_…` ids with `ULID()` directly (not through the seedable id source of
   Phase 0), and `render_memory` prints the id in the prompt. Two backtest runs that build
   the same memories therefore send different prompts: the second run cannot replay the
   first (`CacheMiss`, or it pays again in `read_write`). Separately, nothing keeps one
   backtest run's memories apart from the next run's on the same lake: run 2 would recall
   run 1's lessons (their `created_at` is simulated time, so they pass the point-in-time
   filter). No effect yet: nothing writes memories until Phase 4 / Stage 54. Shown by
   `…-probe-memory-ids.txt`.
7. **Integrity failures look like ordinary failures in the graph.** A `CacheMiss` in
   `replay_only` (the replay is no longer valid) and a `LookAheadError` are recorded in
   `errors` and the symbol is dropped, like a vendor timeout. That is fail safe, but a
   replayed backtest that silently researches fewer names is not "identical results". The
   Backlog has this for `LookAheadError` (55 / 56); `CacheMiss` is the same case and Stage 36's
   `--replay` meets it first.
8. **A no-evidence run pays for a repair that cannot succeed.** When an agent's run showed
   no evidence at all (a symbol with no news), an output that cites nothing is sent back for
   one repair turn that lists "(none)" as the ids to cite. The answer cannot improve; the
   call is paid (two calls instead of one per such name in the probe). Stage 31's brief makes
   the zero-article path a first-class case.
9. **A vendor outage takes hours to fail safe.** One call that times out costs
   `request_timeout_seconds` × (`max_retries` + 1) + backoff = 120 × 4 + 7 = 487 s. Each agent
   call of the day does that again: 30 names × 5 analysts on 4 threads is about 5 hours before
   the last symbol is dropped. Nothing remembers that the vendor is down.
10. **Planning: two things Phase 4 briefs assume that no brief provides.**
    (a) Briefs 29, 31 and 33 put tools in `src/atlas/agents/tools/<name>_tools.py`; Stage 26
    built `src/atlas/agents/tools.py` (a module, as its own brief says). Both cannot exist.
    (b) A recorded fixture has two halves: the model's answers (finding 4) and the data the
    tools read. The cache key covers the tool results, so a replay needs the same lake rows;
    CI has no lake. No brief says where a fixture's data comes from.
    (c) Stage 27 stores episodes and Stage 54 writes lessons from them, but no brief says
    which agent records an episode.
11. **Four validators, no hook.** Stages 32 (sentiment within ±0.3 of the baseline), 33
    (risk appetite under stress), 34 (numbers equal the tool numbers) and 36 (p10 ≤ p50 ≤ p90,
    conviction vs debate) each need a check of the output with a repair turn. `BaseAgent`
    has that loop for citations only, in a private method.
12. **Checked and fine.** DeepSeek's published prices and peak hours equal `config/fund.yaml`
    (read 2026-11-17). Prompt-injection hygiene reaches every call (data policy in the system
    text, tool results and pre-fetched evidence in `<data>` blocks). `atlas --help` imports
    none of langgraph, chromadb, onnxruntime, jinja2, openai or anthropic (1.3 s).
    One run's cost rows, agent traces, audit events and checkpoints carry one `run_id`.

## 4. Gaps and improvements, in tiers

### Tier 1 — real gaps (a defect when the stages meet; what Phase 4 needs on its first day; the untested chain)
1. **(13, 08) One `DataView` is safe on the graph's threads.** The view cache computes a key
   once however many threads ask, never fails under eviction, and never makes different keys
   wait for each other; readers are built once; `Universe`'s membership cache is read under
   a lock. Same values as before (every public view method compared on four threads against
   a single reader).
2. **(24) FakeLLM replies may be functions of the request**, so a test answers each symbol
   from its own prompt whatever the thread order.
3. **(24, 25) A replay needs no API key.** In `replay_only`, `build_gateway` stands an
   `OfflineProvider` in for a missing key: the configured provider's name and endpoint (the
   cache keys match), and a `complete` that refuses. Every other mode still needs the key.
4. **(25) Recordings travel as files.** `write_export` / `read_export` / `LLMCache.load` and
   `atlas llm cache import FILE [--store] [--replace]`. A line whose key is not the SHA-256
   of its request is refused, and a file with one bad line stores nothing.
5. **(28 folder; all Phase 3 packages) Offline end-to-end test**
   `tests/integration/test_phase3_run.py`: two real agents (pre-fetched evidence + memory;
   tool loop) in the research graph over four symbols on four threads, one DuckDB for
   ledger, cache, traces and memory, an audit log per run; interrupted and resumed; replayed
   with no provider; replayed after a prompt input changed. So CI covers the chain.
6. **Records.** Backlog items this tier closes or changes; the report; notes for Stage 29.

### Tier 2 — correctness and integrity hardening (each changes behaviour or a brief: waits for the owner's word)
1. **(27) Memory and replay** (finding 6). Decide before memories are written in a backtest
   (Stage 54 at the latest): ids from the seedable id source or derived from the content, and
   a way to keep one backtest run's memories from the next (a memory scope per run, or a
   fresh memory per backtest). Smallest safe step now: mint the ids through `new_id`, so a
   seeded run reproduces them.
2. **(28) Integrity errors are told apart** (finding 7): `integrity_errors(errors)` (kinds
   `LookAheadError`, `CacheMiss`) next to `failed_symbols`, for `atlas research --replay`
   (36) and the backtest (55 / 56) to fail on. The graph's policy itself (record, drop the
   symbol, go on) stays as the brief wrote it.
3. **(26) No repair turn when the run showed no evidence** (finding 8): the output goes
   straight to `low_quality`. One paid call saved per no-data name.
4. **(26) An output-check hook** (finding 11): `BaseAgent.check_output(ctx, output) -> problems`,
   joined to the existing repair turn and the `low_quality` rule.
5. **(26 → 29) `atlas.agents.tools` becomes a package** (finding 10a): `tools/registry.py`,
   `tools/dataview_tools.py`, same public names, so Stage 29 adds
   `tools/fundamental_tools.py` as its brief says.
6. **(24) Fail fast while the vendor is down** (finding 9): after N consecutive transient
   failures the gateway refuses new calls for a cool-down (`LLMError`, no wait), so a dead
   vendor costs minutes, not hours.
7. **(planning) Brief notes for the owner's approval** (finding 10b, 10c): where a recorded
   fixture's data comes from (a small lake slice committed with the fixture, built by a
   helper), and which agent records an episode (suggested: the Research Director, one per
   thesis; Stage 53 settles it).

### Tier 3 — coverage and performance
1. (25, 14) `atlas data slice --symbols … --as-of … --out DIR`: the data half of a recorded
   fixture (Tier 2 item 7), if the owner picks that route.
2. (24) `atlas llm usage [--run] [--day]`: spend per day, run, agent and model from
   `llm_usage` (today only the cache hit rate is shown).
3. (27) Re-check `min_similarity` on the first real agent situations (Backlog; Stage 29 on).
4. (01) Unit suite time: 4,921 tests, 571 s serial and 367 s parallel (+ 34 s timing tests);
   the protocol's "under 60 s until Phase 8" has not held since Phase 2. Phase 3's share is
   small (about 35 s: llm 5 s, agents 15 s, orchestration 14 s); no action for Phase 3.
5. (24) The optional Anthropic provider has never answered a live call (no key): refusals,
   the long-prompt price tier and tool loops stay unproven until a key exists.

### Tier 4 — housekeeping
1. (24) `docs/llm.md`: the gateway, tiers, budgets, prices, providers and FakeLLM in one
   place (today the cache, the agents and the graph have a page; the gateway has none).
2. (28) A per-node timeout (`add_node(timeout=…)`, Backlog) once real agents run.
3. (24) Budgets (`per_run_usd`, `per_agent_call_max_tokens`) revisited with the first real
   prompts (Backlog; Stage 29).

### Deferred by design (owned by a later stage; no action now)
Wiring the real agents into the graph (35 / 36); writing lessons (54) and scoring (53); the
Fund Day graph (55); treating integrity errors as a failed backtest day (55 / 56); streaming
and async calls; Claude refusal fallbacks; process-level coordination of two recording
backtests on one lake (56).

## 5. Status
- Tier 1 (2026-11-17): items 1–6 done, started without a further question following the
  Phase 0, 1 and 2 precedent (the owner's request: "Please help me to start with the
  implementation"). Test-first: 9 tests failed and 3 files did not collect on the unchanged
  code ([red](phase3-checkpoint-2026-11-17-tier1-red.txt)); all pass after the change.
  - Item 1: `DataView`'s cache computes a key once, waits instead of recomputing, never
    fails under eviction; readers are built once; `Universe`'s membership cache is locked.
    Every public view method on four threads equals a single reader.
  - Items 2–4: FakeLLM functions; `OfflineProvider` for keyless `replay_only`; recordings as
    files (`write_export`, `read_export`, `LLMCache.load`, `atlas llm cache import`), refused
    line by line when a key is not the hash of its request.
  - Item 5: `tests/integration/test_phase3_run.py`, 7 tests: two real agents in the graph
    over four symbols on four threads (one blocked, one duplicate), memory with a lesson from
    after the decision day that is never shown, ledger / cache / traces / memory in one
    DuckDB, audit log per run; interrupted and resumed; replayed with no provider (identical
    results, every call a free hit); one recording deleted → only that symbol dropped.
  - Item 6: Backlog items for Tiers 2–4; worklogs and verification in Stages 08, 13, 24, 25
    and 28.
  - After Tier 1: `uv run pytest -q` 4967 passed; `make check` exit 0 (4964 + 3 timing);
    Verify 1084 / 339 / 308 / 158 / 189 passed; integration 48 passed, 15 skipped; coverage
    of every changed module 100% for the new lines.
- Push (2026-11-17, owner: "Yes, push"): CI on 4679e24 passed on `main` and failed on the
  branch: Stage 28's kill-resume test killed its worker after a director logged "done" but
  before LangGraph saved that director's write, so the resumed run researched that symbol
  again. A race in the test, not the code; the test now waits for the saved writes (red
  reproduced on purpose, then green; full suite 4967 passed, `make check` exit 0). Fix
  538702a merged into `main` and pushed: CI success on `main` and the branch.
- Tier 2 (2026-11-17, owner: "Start Tier 2"): items 1–6 done test-first (19 failed and 2
  files did not collect on the code before, [red](phase3-checkpoint-2026-11-17-tier2-red.txt));
  item 7 drafted for the owner's approval, no brief edited
  ([brief notes](phase3-checkpoint-2026-11-17-tier2-brief-notes.md)).
  - Item 1: memory ids through `new_id` (same seed → same ids and prompt text). The other
    half (a backtest run's own memory) is note C, for Stage 56.
  - Item 2: `NodeError.integrity` and `integrity_errors(errors)`; the graph's policy unchanged.
  - Items 3, 4: no repair turn with no evidence shown; `BaseAgent.check_output` shares the
    repair turn and the `low_quality` rule; the citation-only repair text is unchanged.
  - Item 5: `atlas.agents.tools` is a package (`registry.py`, `dataview_tools.py`), same names.
  - Item 6: `llm.retry.outage_failures` (8) / `outage_cooldown_seconds` (300).
  - After Tier 2: `uv run pytest -q` 5008 passed; `make check` exit 0; Verify 350 / 327 /
    162 / 200 passed; integration 48 passed, 15 skipped; changed modules 100% covered.
  - Merged into `main` (798a99e) and pushed: CI success on `main` and the branch.
- Item 7 (2026-11-17, owner: "Approve all notes"): the notes are applied to briefs 29, 30,
  31, 33, 35, 36, 53, 54 and 56. Tier 2 is complete.
- Tier 3 (2026-11-18, owner: "Start Tier 3"):
  - Item 2 done test-first: `atlas llm usage [--run] [--day] [--by day|run|agent|model]` over
    `CostLedger.usage_summary` (Stage 24). Running it read-only on the main lake found a
    Stage 25 defect: a database made before Stages 24 and 25 has no `llm_usage` / `llm_cache`
    table, and the read-only `atlas llm` commands raised a traceback; they now exit 1 naming
    the missing table.
  - Item 1 is Stage 29 task 4 since the approved notes (Rule 4: not built early). Item 3 waits
    for real agent situations (Stage 29 on). Item 4: no action. Item 5 needs
    `ANTHROPIC_API_KEY` in `.env` (absent).
  - After Tier 3: `uv run pytest -q` 5033 passed; `make check` exit 0; `tests/unit/llm` 363
    passed; integration 48 passed, 15 skipped; `cost.py` and `llm_cmd.py` 100% covered.
- Tier 4 (2026-11-18, owner: "Start Tier 4"):
  - Item 1 done test-first: `docs/llm.md` (tiers and providers, one call step by step,
    budgets, prices and the ledger, retries and outages, untrusted text, testing without a
    vendor, configuration). `test_llm_doc_covers_the_gateway` names every `llm` config key
    and priced model, so the page cannot fall behind the config silently.
  - Item 2 investigated, not built: LangGraph 1.2.22 refuses `add_node(timeout=…)` on a
    synchronous node ([probe](phase3-checkpoint-2026-11-18-tier4-node-timeout-probe.txt)),
    and every research node is synchronous. A node timeout needs async nodes or a limit
    inside the agent: a Stage 55 design choice (Backlog corrected). Item 3 waits for real
    prompts (Stage 29) and the first LLM backtest (Stage 56).
  - After Tier 4: `uv run pytest -q` 5034 passed; `make check` exit 0 (after formatting the
    page's example); docs test 4 passed; integration 48 passed, 15 skipped.
