# Stage 28 — LangGraph orchestration skeleton · Plan

- **Brief:** [brief.md](brief.md) · **Spec:** [00-design-spec.md](../../00-design-spec.md) §2 (kept from the reference: LangGraph orchestration, parallel analysts, checkpoint/resume), §3 principle 6 (fail safe: no new risk), §5 steps 5–7 (research queue → parallel analysts → debate → director), §6 (analyst roster), §7 (contracts), §11 (LangGraph), §12 (`orchestration/`)
- **Status:** DONE 2026-11-16 (see [verification.md](verification.md))
- **Gate check:** Stage 27 is DONE (its `verification.md`: ALL CRITERIA VERIFIED); `uv run pytest -q` → 4730 passed, 74 deselected, exit 0 (2026-11-16) · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
- **Execution:** written and executed in one go, inline TDD, no subagents (kick-off: "please plan, execute and audit stage 28"; the owner's standing preference: plan.md first, then execute, no review pause). `worklog.md` is the ledger.

**Goal:** the graph plumbing of the fund: a typed `FundState` whose parallel writes merge correctly, a research subgraph per queued symbol (analysts in parallel → join → debate → director), fanned out with LangGraph's `Send` under a concurrency limit, checkpointed to SQLite so a killed run resumes where it stopped, and an error policy that drops a failing symbol for the day without touching the others. Proven with deterministic stub agents.

**Architecture:** `atlas.orchestration` has four modules. `state.py` defines `FundState` (the fund-level LangGraph state, a `TypedDict` with reducers) and `ResearchState` (one symbol's subgraph state), plus the pure reducers. `errors.py` is the error policy: the `NodeError` record and `guarded(...)`, the wrapper every working node goes through (an exception becomes a `NodeError` in `errors`, never a crash). `research_graph.py` builds the research subgraph from a `ResearchTeam` (analyst, debate and director callables) and the fund-level fan-out graph (`prepare` → `Send` per symbol → `research` subgraph → `collect`), with one concurrency limit; it also exports the mermaid diagram. `checkpoint.py` runs a graph with a SQLite checkpointer at `<lake>/runs/<run_id>/checkpoints.sqlite` and resumes a failed or killed run from its last checkpoint. `stubs.py` holds the deterministic stub team (real agents from Stage 29 plug into the same callable slots).

## Global constraints
- No network and no LLM: the stubs are pure functions of `(symbol, as_of, analyst)`; outputs are the Stage 04 contracts (`AnalystReport`, `DebateTurn`, `InvestmentThesis`), so a resumed run can be compared with an uninterrupted one value for value.
- No wall clock: nothing in the graph reads time; `NodeError` carries the decision day, not a timestamp.
- Nothing hard-coded: the concurrency limit lives in `config/fund.yaml` (`orchestration.max_concurrency`).
- Fail safe (spec §3.6): an `Exception` in an agent node drops that symbol for the day (no report, no thesis reach the fund state) and is recorded in `errors`; `BaseException`s (Ctrl-C, `SystemExit`, a kill) stop the run, which is then resumable; LangGraph's own control-flow exceptions pass through.

## Collisions with the overall plan
None found (roadmap row 28: M, depends on 26; spec §11 LangGraph and §12 `orchestration/` match the brief). Readings and decisions, also logged in PROGRESS.md › Decisions log:

- **D1 Dependencies:** `langgraph>=1.2` (1.2.22) and `langgraph-checkpoint-sqlite>=3.1` (3.1.3) as runtime dependencies (they bring `langchain-core`, `langgraph-checkpoint`, `langgraph-sdk`, `ormsgpack`, `xxhash`, `sqlite-vec`, ...). Probed 2026-11-16 in a scratch env: (a) a compiled subgraph used as a node accepts `Send` payloads and returns only its `output_schema` keys to the parent; (b) a node with edges from several analysts waits for all of them (join); (c) `ContextVar`s (the run context) reach the node threads; (d) `max_concurrency` in the run config is applied **per graph loop and inherited by subgraphs** (outer 2 → 2 symbols × 2 analysts = 4 calls at once; a subgraph's own `with_config` is overridden by the parent's); (e) after a `KeyboardInterrupt` mid-superstep with `durability="sync"`, `invoke(None, same thread)` re-ran only the one unfinished analyst of the one unfinished symbol; (f) a reopened SQLite file gives the same state; (g) the default serializer warns "Deserializing unregistered type … will be blocked in a future version" for our Pydantic types, so we pass an explicit allow-list; (h) a router returning no `Send` would end the graph, so an empty queue routes to `collect` directly; (i) a checkpoint turns a top-level `tuple` into a `list` (inside Pydantic models tuples are re-validated), so state sequences are lists, or a resumed state would not equal an uninterrupted one; (j) a type outside the allow-list is not refused loudly: it comes back as a plain `dict` with a logged "Blocked deserialization" line. Probe scripts: `artifacts/probe-langgraph-*.py`.
- **D2 State shape:** `FundState` is a `TypedDict(total=False)` (LangGraph's native schema; no per-step Pydantic re-validation) with the brief's keys, typed with the existing contracts: `as_of: date`, `regime: RegimeState`, `macro_outlook: MacroOutlook`, `research_queue: list[Symbol]`, `reports: {symbol: {analyst: AnalystReport}}`, `theses: {symbol: InvestmentThesis}`, `proposals: {pod: PodProposal}`, `allocation: CapitalAllocation`, `target: TargetPortfolio`, `risk_decision: RiskDecision`, `compliance: ComplianceResult`, `orders: list[Order]`, `errors: list[NodeError]`. Fields written by parallel nodes have reducers: `reports` (`merge_reports`, two levels), `theses` and `proposals` (`merge_keyed`), `errors` (`append_errors`). The rest are single-writer (LangGraph's last value): two parallel writes to one of them raise LangGraph's `InvalidUpdateError`, which is the bug we want to see. Reducers never mutate their inputs, are order-independent for disjoint writes, accept an identical re-write (idempotent), and raise `OrchestrationError` for a **conflicting** write (same key, different value): two nodes disagreeing silently is a bug, not a merge.
- **D3 Errors:** new `OrchestrationError(AtlasError)` in `atlas.core.errors` (the fund graph reached an invalid state: conflicting writes, a bad run id, a missing or existing checkpoint). Per-node failures are not raised: they become `NodeError{node, symbol, as_of, kind, message}` records (`kind` = exception class name, `message` cut at 500 chars).
- **D4 Research subgraph:** a `ResearchTeam` holds `analysts: Mapping[name, AnalystFn]`, `debate: DebateFn`, `director: DirectorFn`, where `AnalystFn(task) -> AnalystReport`, `DebateFn(task, reports) -> tuple[DebateTurn, ...]`, `DirectorFn(task, reports, debate) -> InvestmentThesis` and `task = ResearchTask(symbol, as_of)`. Real agents (Stages 29–34) wrap `BaseAgent.run` into these slots. Graph: `START → each analyst (parallel) → join → debate → director → publish → END`; after `join`, `debate` and `director` a router sends a symbol with any error to `END`. Only `publish` writes the symbol's `reports` and `thesis` to the fund state, so a dropped symbol leaves nothing half-written. Outputs are checked inside the guard (type, `ticker`, `as_of`, `analyst` name): a wrong output is a `DataError`, i.e. a dropped symbol. Analyst names: unique, `[a-z][a-z0-9_]*`, not a reserved node name.
- **D5 Fan-out:** fund graph `START → prepare → (Send("research", {symbol, as_of}) per symbol | collect when the queue is empty) → research → collect → END`. `prepare` de-duplicates the queue keeping its order (a symbol researched twice would write conflicting results). `collect` is the join for Stage 55 and logs completed / dropped counts.
- **D6 Concurrency limit:** `orchestration.max_concurrency` (default 4) is one number with two effects: (a) a `BoundedSemaphore` shared by every agent call of one built graph, so at most N agent calls (LLM calls from Stage 29) run at once across all symbols; (b) LangGraph's `max_concurrency` = N in the run config, so at most N symbols are in flight (no idle threads waiting on the semaphore). The semaphore is held only around the agent call itself, never while a node waits for others, so it cannot deadlock.
- **D7 Error policy:** `guarded(node, fn, ...)` catches `Exception` (including `LookAheadError` and budget errors: a dropped symbol is no new risk), logs it at error level with the traceback, and returns `{"errors": [NodeError]}`. It re-raises LangGraph's `GraphBubbleUp` (interrupts and parent commands are control flow, not failures). `BaseException`s propagate: the run fails and can be resumed.
- **D8 Checkpoints:** `checkpoint_path(lake, run_id) = run_dir(lake, run_id) / "checkpoints.sqlite"` (the brief's `data/runs/<run_id>/checkpoints.sqlite` with `lake = cfg.data.lake_path`); `run_id` must be a 26-char ULID (the run-context format; no path tricks). `SqliteSaver` with an explicit serializer allow-list built from the state schemas (every Pydantic model and enum reachable from `FundState` / `ResearchState`), so loading never warns; since a type left off the list would come back as a plain `dict` (probe j), the list is derived from the schemas and a test round-trips every type. LangGraph `thread_id` = `run_id`; `durability="sync"` (a step's checkpoint is on disk before the next step starts; each finished task's writes are saved as it finishes). `run_graph(graph, inputs, run_id=, lake_path=)` refuses a run id that already has a checkpoint (a new run never appends to an old one); `resume_graph(graph, run_id=, lake_path=)` refuses one without; resuming a finished run returns its final state without re-running anything.
- **D9 Mermaid:** `ResearchGraph.mermaid()` = LangGraph's `get_graph(xray=1).draw_mermaid()` for the fund graph with the subgraph expanded; `docs/orchestration.md` carries the diagram of the default stub team (the five analysts of spec §6) and a test keeps it equal to the export.
- **D11 Roster order** (found in Task 4): LangGraph merges the parallel analyst writes in its own order (alphabetical by node name here), so the debate, the director and `publish` see the reports re-keyed in the team's roster order; prompts never depend on LangGraph internals or thread timing (spec §3.3). At the fund level `reports` / `theses` follow the queue order (the `Send` order), whatever finishes first (audit A4 pins it).
- **D12 Audit fixes** (Task 9, `artifacts/audit-report.md`): A1 every entry point runs the graph through `invoke_untraced` (LangSmith `tracing_context(enabled=False)`): with `LANGSMITH_TRACING=true` in the environment langchain-core would upload the whole run to api.smith.langchain.com (probe: 209 KB payload, sandbox refused the connection); A2 checkpoints carry `ResearchGraph.fingerprint` (SHA-256 of the mermaid export) as metadata `atlas_graph`, and `resume_graph` refuses a graph built differently; A3 `run_graph` checks the inputs (`ResearchGraph.check_inputs`) before it writes a checkpoint, so a malformed input never poisons a run id.
- **D10 Files beyond the brief's list:** `stubs.py` (the stub team, in `src` so the docs export and later stages' tests can use it), `docs/orchestration.md`, `config` section `orchestration`.

## File map
| File | Responsibility |
|---|---|
| `pyproject.toml`, `uv.lock` | + `langgraph`, `langgraph-checkpoint-sqlite` |
| `src/atlas/core/errors.py` | `OrchestrationError` |
| `src/atlas/core/config.py`, `config/fund.yaml` | `OrchestrationConfig` (`max_concurrency`), section `orchestration` (+ env allow-list) |
| `src/atlas/orchestration/state.py` | `FundState`, `ResearchState`, `merge_reports`, `merge_keyed`, `append_errors`, `state_types` |
| `src/atlas/orchestration/errors.py` | `NodeError`, `guarded`, `failed_symbols` |
| `src/atlas/orchestration/stubs.py` | `stub_team`, `ANALYSTS`, deterministic stub agents |
| `src/atlas/orchestration/research_graph.py` | `ResearchTask`, `ResearchTeam`, `ResearchGraph` (`fingerprint`, `check_inputs`, `mermaid`), `build_research_subgraph`, `build_research_graph`, `invoke_untraced`, `GRAPH_METADATA_KEY` |
| `src/atlas/orchestration/checkpoint.py` | `checkpoint_path`, `sqlite_checkpointer`, `run_graph`, `resume_graph` |
| `tests/unit/orchestration/…` | tests per task (`graph_world.py`: spy teams; `kill_worker.py`: the process that gets killed; `test_orchestration_audit.py`: A1–A4) |
| `docs/orchestration.md` | how the graph works, the error policy, resume, the diagram |

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

Brief task mapping: brief 1 (state + reducers) → Task 2; brief 2 (subgraph with stubs, fan-out over 5 symbols) → Tasks 4–5; brief 3 (kill, resume, same final state) → Task 6; brief 4 (error isolation) → Task 7. Task 1 is foundations, Task 3 the error policy module, Task 8 the diagram, docs and API.

### Task 1 — Foundations: dependencies, config, error kind
- Files: `pyproject.toml`, `uv.lock`, `config.py`, `fund.yaml`, `core/errors.py` · tests `tests/unit/orchestration/test_orchestration_foundations.py`
- [x] Failing tests: `langgraph` / `langgraph-checkpoint-sqlite` are runtime dependencies with the probed floors; `orchestration.max_concurrency` default 4, YAML equals defaults, validation (`0`, unknown key), env override `ATLAS__ORCHESTRATION__MAX_CONCURRENCY`; `OrchestrationError` is an `AtlasError` sibling
- [x] Run → FAIL · implement · run → PASS

### Task 2 — State and reducers (brief task 1)
- Files: `state.py` · tests `test_orchestration_state.py`
- [x] Failing tests: `merge_reports` merges disjoint symbols and analysts; identical re-write is a no-op; conflicting report → `OrchestrationError`; inputs not mutated; `None`/empty sides; `merge_keyed` same rules; `append_errors` keeps order; **hypothesis**: any split of a set of disjoint writes merged in any order gives the same result; graph level: 6 parallel nodes writing `reports` / `theses` / `errors` merge into one state; two parallel writes to a single-writer field (`target`) raise `InvalidUpdateError`; `FundState` has exactly the brief's keys
- [x] Run → FAIL · implement · run → PASS

### Task 3 — Error policy module
- Files: `errors.py` · tests `test_orchestration_errors.py`
- [x] Failing tests: `NodeError` validation (frozen, kind/message non-empty, message cut at 500); `guarded` returns the function's update on success; an `Exception` becomes `{"errors": [NodeError]}` with node, symbol, as_of, kind, message and is logged at error level; `LookAheadError` recorded too; `KeyboardInterrupt` and LangGraph `GraphInterrupt` propagate; `failed_symbols` lists symbols with errors
- [x] Run → FAIL · implement · run → PASS

### Task 4 — Stub team and research subgraph (brief task 2, part 1)
- Files: `stubs.py`, `research_graph.py` · tests `test_orchestration_stubs.py`, `test_orchestration_subgraph.py`, `graph_world.py`
- [x] Failing tests: stubs deterministic and valid contracts (same inputs → equal outputs, thesis id a valid `ThesisId`); `ResearchTeam` validation (no analysts, bad / duplicate / reserved names); one symbol through the subgraph: every analyst report published, debate sees all reports, director sees reports + debate, thesis published; analysts run in parallel (a barrier that needs all analysts at once passes); join waits for the slowest analyst; wrong outputs (wrong type, wrong ticker, wrong as_of, wrong analyst name) → `DataError` recorded, nothing published
- [x] Run → FAIL · implement · run → PASS

### Task 5 — Fan-out over the queue with `Send` and the concurrency limit (brief task 2, part 2)
- Files: `research_graph.py` · tests `test_orchestration_fanout.py`
- [x] Failing tests: 5 symbols → 5 theses and 5 × 5 reports, equal to running each symbol alone; duplicates in the queue researched once; empty queue → `collect` runs, empty results; with `max_concurrency=N` the peak number of agent calls at once is ≤ N (N = 1, 2, 3) and symbols in flight ≤ N; with N ≥ 2 symbols do run in parallel (barrier across two symbols passes); N = 1 runs strictly one call at a time
- [x] Run → FAIL · implement · run → PASS

### Task 6 — Checkpoint and resume (brief task 3)
- Files: `checkpoint.py` · tests `test_orchestration_checkpoint.py`, `kill_worker.py`
- [x] Failing tests: `checkpoint_path` under `<lake>/runs/<run_id>/checkpoints.sqlite`; bad run ids refused; serializer round trip of a full `FundState` and `ResearchState` (all contract types) with warnings as errors; a type outside the allow-list comes back as a `dict` (documents probe j; the derived list leaves none out); `run_graph` writes the file and returns the final state; `run_graph` on an existing run id → `OrchestrationError`; `resume_graph` without a checkpoint → `OrchestrationError`; **Ctrl-C mid-run** (`KeyboardInterrupt` from one analyst) → resume → final state equal to an uninterrupted run, finished symbols not re-run; **killed process** (`SIGKILL` of a worker process blocked mid-run) → resume in this process → equal final state, only unfinished work re-run; resuming a finished run re-runs nothing and returns the same state
- [x] Run → FAIL · implement · run → PASS

### Task 7 — Error isolation (brief task 4)
- Files: tests `test_orchestration_isolation.py`
- [x] Failing tests (then fixes if any): one symbol's analyst / debate / director raising, or returning a bad output → that symbol has no reports and no thesis, one `NodeError` names it, and every other symbol's results equal a clean run's; failures in several symbols; every symbol failing → empty theses, run completes; a failing symbol never blocks others (its siblings finish even when it fails first); errors survive a checkpoint resume
- [x] Run → FAIL (or PASS where earlier tasks already cover it, recorded) · implement · run → PASS

### Task 8 — Mermaid export, docs and public API
- Files: `research_graph.py`, `__init__.py`, `docs/orchestration.md` · tests `test_orchestration_package.py`
- [x] Failing tests: `mermaid()` shows the fan-out and every subgraph node; `docs/orchestration.md` holds exactly the current export; public API list; every module has a docstring; docs explain the error policy and resume
- [x] Run → FAIL · implement · run → PASS

### Task 9 — Verification, audit and records
- [x] `uv run pytest tests/unit/orchestration -q` (brief Verify), `uv run pytest -q`, `make check` → green; raw output in `artifacts/`
- [x] Coverage of `src/atlas/orchestration` (target 100%); mutation run (mutmut) on `src/atlas/orchestration`; survivors reviewed
- [x] Audit against brief, spec and Project.md (findings A1… fixed test-first) → `artifacts/audit-report.md`
- [x] `verification.md`, PROGRESS.md (status, handoff, Decisions log), commit `stage-28: LangGraph orchestration skeleton`, merge into `main`, merged-result checks, push
