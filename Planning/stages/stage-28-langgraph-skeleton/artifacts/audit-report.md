# Stage 28 — LangGraph orchestration skeleton · Audit report

- **Date:** 2026-11-16 · **Auditor:** the stage's own chat (self-review, as the owner's standing preference allows; no reviewer subagent)
- **Asked for:** "please plan, execute and audit stage 28"
- **Scope:** `src/atlas/orchestration/` (state, errors, research_graph, checkpoint, stubs), the `orchestration` config section, `OrchestrationError`, `docs/orchestration.md`, the tests in `tests/unit/orchestration/`; checked against the brief, roadmap row 28, design spec §2, §3, §5–§7, §11, §12 and Project.md Rules 1–6A.

## Verdict

**COMPLETE.** Every in-scope item of the brief is built and tested, the three acceptance criteria are met with evidence, and five audit findings were handled (three defects fixed test-first, test gaps from the mutation run closed, one behaviour pinned by a test). No open defect.

## Brief coverage

| Brief item | Where | Evidence |
|---|---|---|
| `FundState` with reducers (the 13 keys) | `state.py` | `test_fund_state_has_the_brief_keys`, reducer and hypothesis tests, `test_parallel_nodes_merge_their_writes_in_a_graph` |
| `ResearchSubgraph(symbol)`: analysts in parallel → join → debate → director (stubs) | `research_graph.build_research_subgraph`, `stubs.py` | `test_orchestration_subgraph.py` (barrier proves parallel analysts; join waits for the slowest) |
| Fan-out with `Send` + concurrency limit | `build_research_graph`, `orchestration.max_concurrency` | `test_orchestration_fanout.py` (peak calls and symbols ≤ N for N = 1, 2, 3; parallel symbols) |
| SQLite checkpointer at `data/runs/<run_id>/checkpoints.sqlite`; resume | `checkpoint.py` | `test_the_checkpoint_lives_in_the_run_folder`, `test_a_killed_process_resumes_from_its_last_checkpoint` (real `SIGKILL`), `test_ctrl_c_mid_run_then_resume_gives_the_same_final_state` |
| Error policy: failing node → `errors`, symbol dropped, never crashes the graph | `errors.py`, routers in `research_graph.py` | `test_orchestration_isolation.py` (29 cases), `test_orchestration_errors.py` |
| Mermaid export to docs | `ResearchGraph.mermaid()`, `docs/orchestration.md` | `test_the_docs_diagram_is_the_current_export` |
| Out of scope: real agents, full Fund Day | — | none built (stubs only; `FundState` fields for later steps are typed but unwritten) |

## Findings

| # | Severity | Finding | Fix | Test |
|---|---|---|---|---|
| A1 | High (privacy, Rule 5) | LangGraph depends on langchain-core and langsmith. With `LANGSMITH_TRACING=true` (or `LANGCHAIN_TRACING_V2`) in the environment, a graph run is uploaded to api.smith.langchain.com: probe 2026-11-16 saw a 209 KB multipart body leave for the endpoint (the state; from Stage 29 on, prompts and model answers too). Atlas never sets the variable, but a developer with a LangSmith account may. | `invoke_untraced` (`tracing_context(enabled=False)`) around every graph invocation: `ResearchGraph.invoke`, `run_graph`, `resume_graph`. | `test_a1_in_memory_runs_never_trace`, `test_a1_checkpointed_and_resumed_runs_never_trace` (red against an unfixed copy: 2 failed), `test_a1_no_connection_is_attempted_with_tracing_in_the_environment` (fresh process, every socket connect recorded: 0) |
| A2 | Medium (correctness) | `resume_graph` accepted any graph: resuming a run with another analyst roster or graph shape would mix two teams' work in one final state. | The graph's structural fingerprint (SHA-256 of the mermaid export) is sent as run metadata `atlas_graph` and stored with every checkpoint; `resume_graph` refuses a mismatch (`OrchestrationError`). The concurrency limit is not part of it, so a resume may use another limit. | `test_a2_resume_refuses_a_differently_built_graph` |
| A3 | Low (operability) | Malformed inputs (no `as_of`, a bad symbol) failed in `prepare` *after* the first checkpoint was written, so the run id could neither be run again (`run_graph` refuses an id with a checkpoint) nor resumed usefully (`prepare` fails again). | `run_graph` calls `ResearchGraph.check_inputs` before it opens the checkpointer. | `test_a3_malformed_inputs_leave_no_checkpoint` |
| A4 | Info (determinism) | Is the fund-level order of `reports` / `theses` stable when symbols finish in another order? Yes: it follows the queue (`Send`) order. Inside a symbol, the order of the analysts' reports did depend on LangGraph's merge order (alphabetical); that was found and fixed during Task 4 (D11: roster order). | — (pinned) | `test_a4_fund_level_order_is_the_queue_order` (3 random finishing orders), `test_agents_and_the_fund_state_see_reports_in_roster_order` |
| A5 | Low (test gaps) | The mutation run showed details no test pinned: `durability="sync"` and the run config on checkpointed runs, the concurrency limit on in-memory runs, the limiter held by every agent kind, the exact 500-char message, log fields, bad-output messages, `invoke_untraced` passing keyword arguments. | Tests only (no code change). | `test_a5_*` (11 cases) |

## Checked, no change needed

- **Rule 5 / no network in unit tests:** after A1, a full `tests/unit/orchestration` run shows no refused connection in the sandbox log. While writing A1, an env-based version of the in-process test polluted later tests (langsmith caches the environment it read); those tests now switch tracing on with `tracing_context(enabled=True)` and the env case runs only in a subprocess (worklog).
- **Secrets:** provider error texts already redact the API key (`openai_compat.py`), so `NodeError.message` (cut at 500 chars) cannot carry one. No key or `.env` value appears in the new code, tests or artifacts.
- **Clock:** no Atlas code in the package reads the time. LangGraph writes its own wall-clock `ts` into checkpoint rows: library metadata, never read by Atlas logic.
- **Point in time:** `ResearchTask` carries only the symbol and the decision day; agents get data through their own `AgentContext` / `DataView` (Stage 26), which the Stage 29+ wrappers build from the task.
- **`LookAheadError` recorded, not raised:** a look-ahead inside one symbol's research drops that symbol (no new risk) and is logged at error level and kept in `errors`. A backtest must treat any `LookAheadError` in `errors` as a failed day (backlog note for Stage 55/56).
- **Threads and the run context:** LangGraph runs nodes on executor threads with a copied context, so `current_run_context()` and structlog's bound fields reach the agents (probe D1 c).
- **Deadlocks:** the semaphore is held only around one agent call, never while a node waits on others; N = 1 runs to completion (test).
- **Checkpoint serializer:** every model and enum reachable from the state schemas is allow-listed (`state_types()`, 29+ types); a round trip of a state holding all of them gives equal values of the same types and no "Blocked deserialization" log.

## Test quality

- Coverage of `src/atlas/orchestration`: **100%** (370 statements, before the audit fixes; re-measured after them in verification.md).
- Deliberate-defect checks: routers that never drop a symbol → 29 / 29 isolation tests fail; `guarded` catching only `RuntimeError` → 17 / 29 fail; `invoke_untraced` without the override → both in-process A1 tests fail.
- Mutation (mutmut 3.8.1, `src/atlas/orchestration`, the two subprocess tests excluded because a child process cannot load mutmut's trampoline config): first run 680 / 801 killed, 121 survived. The non-stub survivors pointed at five untested details (durability and run config not pinned, the limiter on the director not observed, the 500-char boundary of `_cut`, the run / resume / collect log fields, bad-output messages naming the agent and the type, `invoke_untraced` passing its keyword arguments), closed by the A5 tests in `test_orchestration_audit.py`; re-run of those mutants → **723 / 801 killed (90.3%)**. The 78 left: 61 in `stubs.py` (constants of the fake numbers: rounding digits, spreads, texts; any value works for a stub) and 17 equivalent ones (an `add_node` call without the name still works because `guarded` names its function; `xray=2` on a one-level graph; `include_extras` in `state_types`; `cast` targets; the default of `state.get("debate", ())` when the debate always ran; `input_schema=None` on the subgraph, which receives only `Send` payloads; message wording checked by substring). Raw: `mutmut-results.txt`, `mutmut-survivors-first-run-diffs.txt`, config `mutmut-setup.cfg`.
- Flakiness: the three resume tests 10 runs in a row → 30 / 30 passed.

## Backlog notes raised

- (Stage 28 → 55/56) Any `LookAheadError` in `FundState.errors` should fail a backtest day rather than only drop a symbol.
- (Stage 28 → 55) No per-node timeout yet: a hung agent call blocks its slot until the LLM gateway's own timeout (`llm.request_timeout_seconds`, 120 s). LangGraph 1.2 `add_node(timeout=...)` can bound a node if Stage 55 needs it.
- (Stage 28 → 29) Real agents plug in as `ResearchTeam` callables; their wrapper must build the agent's `AgentContext` (with `DataView(as_of=task.as_of)`) from the `ResearchTask`.
