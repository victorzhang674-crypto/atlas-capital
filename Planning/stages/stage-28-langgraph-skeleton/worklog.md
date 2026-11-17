# Stage 28 — LangGraph orchestration skeleton · Work log

Append entries in order. Record failures and dead ends as honestly as successes.

## 2026-11-16 — Start: Rule 0, gate check, probes, plan
- Did: read Project.md, PROGRESS.md (Stage 28 `NOT STARTED`, Stage 27 `DONE`), the brief, roadmap row 28, spec §2/§3/§5/§6/§7/§11/§12, the Stage 26/27 handoff notes; `uv sync --group dev` in this fresh worktree (`UV_CACHE_DIR=$TMPDIR/uv-cache-s28`).
- Ran (gate): `uv run pytest -q` → 4730 passed, 74 deselected in 386 s, exit 0 · raw: artifacts/gate-pytest.txt. Stage 27 `verification.md`: **ALL CRITERIA VERIFIED**. Gate passed; stage folder created from the template.
- Probes (scratch env, `uv run --no-project --with langgraph --with langgraph-checkpoint-sqlite`): langgraph 1.2.22, langgraph-checkpoint 4.3.2, langgraph-checkpoint-sqlite 3.1.3. Facts in plan D1 (a)–(j); scripts saved as artifacts/probe-langgraph-fanout-resume.py, probe-langgraph-concurrency.py, probe-langgraph-serde.py. Two plan corrections came from them before any code: state sequences are lists (a checkpoint turns top-level tuples into lists) and a type left off the serializer allow-list comes back as a plain dict (logged, not raised).
- Wrote plan.md (D1–D10, Tasks 1–9).

## 2026-11-16 — Task 1: foundations
- Red: `test_orchestration_foundations.py` → collection error (`OrchestrationConfig` missing).
- Did: `uv add "langgraph>=1.2" "langgraph-checkpoint-sqlite>=3.1"`; lock change purely additive (18 packages added, none removed or changed: langgraph 1.2.22, langgraph-checkpoint 4.3.2, langgraph-checkpoint-sqlite 3.1.3, langchain-core 1.8.1, langsmith 0.14.17, ormsgpack, xxhash, sqlite-vec, aiosqlite, ...). `OrchestrationConfig(max_concurrency=4)` as section `orchestration` of `AtlasConfig`, `config/fund.yaml`, env allow-list `_SECTIONS`; `OrchestrationError` in `atlas.core.errors` (added to the sibling list of `tests/unit/core/test_errors.py`).
- Ran: `uv run pytest tests/unit/orchestration tests/unit/core/test_errors.py -q` → 30 passed; config/memory foundations tests → 362 passed.

## 2026-11-16 — Task 2: state and reducers (brief task 1)
- Red: `test_orchestration_state.py` → collection error (no `atlas.orchestration.state`).
- Did: `state.py` (`FundState`, `ResearchInput`, `ResearchOutput`, `ResearchState`, `merge_keyed`, `merge_reports`, `append_errors`); the `NodeError` record in `errors.py` (the state's `errors` type; its policy is Task 3). Test helpers `graph_world.py` (`report`, `thesis`, `node_error`).
- Problems / fixes: the helper's thesis id used `ths_`; the prefix is `th_` (`IdPrefix.THESIS`) → fixed in the helper. Removed one weak test of my own (`append_errors is not operator.add`) before it ran green: it proved nothing.
- Ran: `uv run pytest tests/unit/orchestration -q` → 43 passed (incl. hypothesis order-independence, 6 parallel graph writers, conflicting parallel writes → `OrchestrationError`, two writes to `research_queue` → `InvalidUpdateError`).

## 2026-11-16 — Task 3: error policy
- Red: `test_orchestration_errors.py` → collection error (`guarded` missing).
- Did: `guarded(node, work, limiter=None)` (records `Exception`s as `NodeError`, logs `orchestration_node_failed` at error level with the traceback, re-raises `GraphBubbleUp`; the limiter is held only during the work), `failed_symbols`, `MAX_MESSAGE_CHARS` (500, cut ends in `...`).
- Ran: `uv run pytest tests/unit/orchestration -q` → 62 passed.

## 2026-11-16 — Task 4: stub team and research subgraph (brief task 2, part 1)
- Red: `test_orchestration_stubs.py`, `test_orchestration_subgraph.py` → collection errors (no `research_graph` / `stubs`).
- Did: `research_graph.py` (`ResearchTask`, `ResearchTeam` with name checks and a read-only roster, `AnalystFn` / `DebateFn` / `DirectorFn`, `RESERVED_NODES`, `build_research_subgraph`: analysts from `START` in parallel → `join` → `debate` → `director` → `publish`, routers to `END` on error, output checks raising `DataError`); `stubs.py` (`ANALYSTS` = spec §6's five, `stub_analyst`, `stub_debate`, `stub_director`, `stub_team`, BLAKE2b-based so equal across processes). `graph_world.Spy` (call log, hooks, output replacement, peak calls / symbols in flight), `raiser`, `barrier_hook`.
- Problems / fixes: first green run had 1 failure: the director saw the debate turns in a different order than a direct stub call. Cause: LangGraph merges the parallel analyst writes in its own order (alphabetical by node here), so `analyst_reports` reached the debate in that order. Prompts built from it would then depend on LangGraph internals (spec §3.3 determinism; Stage 25 cache keys). Fix: `_in_roster_order` — the debate, the director and `publish` see the reports in the team's roster order; new test `test_agents_and_the_fund_state_see_reports_in_roster_order` (non-alphabetical roster). Added after the fix; the failing test above was its red.
- Ran: `uv run pytest tests/unit/orchestration -q` → 97 passed.

## 2026-11-16 — Task 5: fan-out with `Send` and the concurrency limit (brief task 2, part 2)
- Red: `test_orchestration_fanout.py` → collection error (no `build_research_graph`).
- Did: `_prepare` (checks `as_of` is a date, not a datetime; validates symbols; de-duplicates keeping order; raises `OrchestrationError` on malformed input), `_fan_out` (`Send("research", {symbol, as_of})` per symbol, `collect` when empty), `_collect` (logs `orchestration_research_done`: queued / researched / dropped), `ResearchGraph` (`builder`, `max_concurrency`, `compile`, `config`, `invoke`), `build_research_graph(team, max_concurrency=)` with one `BoundedSemaphore` shared by every agent node.
- Ran: `uv run pytest tests/unit/orchestration -q` → 114 passed (5 symbols equal to each symbol alone; duplicates once; empty / missing queue; peak calls and symbols ≤ N for N = 1, 2, 3; N = 1 strictly serial; a two-symbol barrier proves symbols run in parallel).

## 2026-11-16 — Task 6: checkpoint and resume (brief task 3)
- Red: `test_orchestration_checkpoint.py` → collection error (no `checkpoint`, no `state_types`).
- Did: `state.state_types()` (every model and enum reachable from `FundState` / `ResearchState`), `checkpoint.py` (`CHECKPOINT_FILE`, `checkpoint_path` with the ULID check, `serializer()` with the allow-list, `sqlite_checkpointer`, `run_graph`, `resume_graph`, `durability="sync"`, `thread_id = run_id`). Test helpers: `graph_world.full_fund_state` / `full_research_state` (every contract type), `kill_worker.py` (a checkpointed run whose CCC news analyst blocks; every call logged to a file).
- Ran: `uv run pytest tests/unit/orchestration/test_orchestration_checkpoint.py -q` → 17 passed on the first run. The kill test starts the worker process, waits until AAA/BBB/DDD/EEE finished and CCC's news analyst is blocked, sends `SIGKILL` (return code -9), resumes in the test process: final state equals an uninterrupted run, only CCC is researched again and none of CCC's analysts that had finished re-run. Ctrl-C (`KeyboardInterrupt` from CCC's news analyst) the same; a finished run's resume calls no agent. Flakiness check: the three resume tests 10 times in a row → 10 × 3 passed.

## 2026-11-16 — Task 7: error isolation (brief task 4)
- Wrote `test_orchestration_isolation.py` (29 cases: each of the 7 agent nodes × RuntimeError / LookAheadError / BudgetExceeded on one symbol; bad outputs; a failed analyst stops its symbol before the debate while its sibling analysts finish; several analysts of one symbol; several symbols; every symbol failing; a late failure while the others finish; errors surviving a Ctrl-C + resume).
- Ran: `uv run pytest tests/unit/orchestration/test_orchestration_isolation.py -q` → 29 passed at once (the behaviour came with Tasks 3–5, as the plan allowed). To show the tests are not vacuous, ran them against a scratch copy of `src` with two deliberate defects (`PYTHONPATH=$TMPDIR/s28/mut/src`): routers that never drop a symbol → 29 failed; `guarded` catching only `RuntimeError` → 17 failed, 12 passed (the RuntimeError cases). No code change needed.

## 2026-11-16 — Task 8: mermaid export, docs, public API
- Red: `test_orchestration_package.py` → 4 failed, 1 passed (no exports, no `mermaid()`, no docs file).
- Did: `ResearchGraph.mermaid()` (`get_graph(xray=1).draw_mermaid()`), `atlas.orchestration.__all__`, `docs/orchestration.md` (state, subgraph and fan-out, concurrency, error policy, checkpoints and resume, the diagram pasted from the export of `stub_team()`).
- Ran: `uv run pytest tests/unit/orchestration tests/unit/test_package_layout.py tests/unit/test_repo_docs.py -q` → 187 passed.

## 2026-11-16 — Task 9 (part 1): lint, types, coverage
- `ruff check .` → 5 findings (long lines, an en dash in two docstrings, import order) → fixed; `ruff format` on `src/atlas/orchestration`, `src/atlas/core`, `tests/unit/orchestration`, `tests/unit/core` only (never `Planning/`).
- `mypy src/atlas` → 7 errors: LangGraph's node protocol is `__call__(self, state: S)`, which a `Callable[[S], ...]` (positional-only) does not satisfy, and `invoke` returns `dict[str, Any]`. Fixed with a `NodeFn[S]` protocol for `guarded` / `_publisher` and `cast(FundState, ...)` on the invoke results. → Success: no issues found in 178 source files.
- Coverage: `uv run pytest tests/unit/orchestration --cov=src/atlas/orchestration` → 100% (370 statements).

## 2026-11-16 — Task 9 (part 2): audit (A1–A4), test-first
- Probes (`$TMPDIR/s28/probe6.py`, `probe7.py`): with `LANGSMITH_TRACING=true` in the environment, a plain graph run made langsmith try to upload the run (209 KB multipart body, `Failed to multipart ingest runs`); wrapping the invoke in `tracing_context(enabled=False)` → 0 connection attempts in a fresh process. Fund-level `theses` / `reports` follow the queue order in 5 runs with random finishing order. Config `metadata` is stored in checkpoint metadata.
- Red: `test_orchestration_audit.py` → 6 failed, 3 passed (A1 ×3, A2, A3 ×2 failed; A4 passed: it pins behaviour, no defect).
- Did: A1 `invoke_untraced` used by `ResearchGraph.invoke`, `run_graph`, `resume_graph`; A2 `ResearchGraph.fingerprint` (cached SHA-256 of the mermaid export) sent as run metadata `atlas_graph`, `resume_graph` compares it with the latest checkpoint's; A3 `ResearchGraph.check_inputs` called by `run_graph` before the checkpointer opens.
- Problems / fixes: (1) a first edit script for `checkpoint.py` did not apply (ruff format had re-wrapped the text it matched); re-applied after reading the file. (2) The first full run after the fix passed (174) but the sandbox logged refused connections to api.smith.langchain.com:443. Cause: my first A1 tests set `LANGSMITH_TRACING` through `monkeypatch.setenv`; langsmith caches what it read from the environment, so later tests that invoke raw LangGraph graphs (state and subgraph tests) traced after the env was restored. Not a production leak (every Atlas entry point is untraced) but a test that pollutes the process. Rewrote the in-process A1 tests to turn tracing on with `tracing_context(enabled=True)`; the environment case runs only in a subprocess. Checked red against a scratch copy where `invoke_untraced` does not disable tracing → the 2 in-process A1 tests fail. Re-run: 174 passed, no refused connections. (3) `-W default` showed a `ResourceWarning` (the kill test's worker `stderr` pipe never closed) → stderr goes to `worker.err` in `tmp_path`, closed in `finally`.
- Ran: `uv run pytest tests/unit/orchestration -q -W default` → 174 passed, no warnings, no outbound connection attempts.
- Checked, no change: provider error texts already redact API keys (`openai_compat.py` replaces the key with `***`), so `NodeError.message` cannot carry one; LangGraph writes its own wall-clock `ts` into checkpoint rows (library metadata, never read by Atlas logic).
- Docs: `docs/orchestration.md` sections on no tracing, the fingerprint and the input check; `GRAPH_METADATA_KEY`, `invoke_untraced` exported.

## 2026-11-16 — Task 9 (part 3): mutation run and its follow-up (A5)
- Scratch copy `$TMPDIR/s28/mutcopy` (rsync without `data/`, `.venv`, `.git`, `Planning/`), mutmut 3.8.1 overlay (`PYTHONPATH=<copy>/mutants/src`, `--max-children 4`); config saved as artifacts/mutmut-setup.cfg.
- First attempt failed before any mutant ran: the stats phase crashed in the kill test's child process (it inherits the mutated code through `PYTHONPATH` but runs in `tmp_path`, where mutmut cannot find its config: "Could not figure out where the code to mutate is"). Excluded the two subprocess tests from mutation runs (`-k "not killed_process and not no_connection_is_attempted"`); they run in the normal suite.
- Run: 801 mutants → 680 killed, 121 survived. Reviewed every non-stub survivor's diff (artifacts/mutmut-survivors-first-run-diffs.txt). Gaps → A5 tests (11): durability and run config of checkpointed runs, the concurrency limit of in-memory runs, the limiter held by analyst / debate / director, a 500-char message kept whole, run / resume / collect log fields, bad-output messages naming agent and type, `invoke_untraced` passing its arguments. They passed on the real code (no defect, only unpinned behaviour). Re-ran the surviving mutants with them → 722, then 723 / 801 killed after the `invoke_untraced` test (my first A5 spy sat in front of `invoke_untraced`, so its kwargs mutant still survived).
- Left: 61 stub constants, 17 equivalent mutants (listed in the audit report). Raw: artifacts/mutmut-results.txt.
- Ran: `uv run pytest tests/unit/orchestration -q` → 185 passed; coverage 100% (384 statements).

## 2026-11-16 — Task 9 (part 4): full verification
- First pass: `uv run pytest -q` → 4917 passed, 74 deselected, exit 0 (406 s). `make check` → ruff clean, 468 files formatted, mypy clean, then `make test` **failed**: 9 errors "Different tests were collected between gw0 and gwN" (raw kept as artifacts/make-check-first-run-xdist-collection-error.txt). Cause: `test_a_run_id_must_be_a_ulid` built two parameters from `str(ULID())`, random in each xdist worker, so the test ids differed. Fix: fixed strings (a lower-case and a path-suffixed copy of one fixed ULID). `pytest tests/unit/orchestration -n 4 --dist loadfile` → 185 passed.
- Re-ran Verify, the serial suite and `make check` on the final code (results in verification.md).
- Final run: `uv run pytest tests/unit/orchestration -q` → 185 passed; `uv run pytest -q` → 4917 passed, 74 deselected, exit 0; `make check` → exit 0 (ruff clean, 468 files formatted, mypy clean, 4914 + 3 timing passed). No outbound connection logged. Secret scan of the new files and artifacts against the main checkout's `.env` values (not printed): 0 hits.
- Records: verification.md (ALL CRITERIA VERIFIED), audit-report.md, PROGRESS.md (status, checklist, Decisions log, Backlog, handoff).

## 2026-11-16 — Code files created or changed by this stage
- Created: `src/atlas/orchestration/{state,errors,research_graph,checkpoint,stubs}.py`, `docs/orchestration.md`, `tests/unit/orchestration/{graph_world,kill_worker}.py`, `tests/unit/orchestration/test_orchestration_{foundations,state,errors,stubs,subgraph,fanout,checkpoint,isolation,package,audit}.py`
- Changed: `src/atlas/orchestration/__init__.py`, `src/atlas/core/config.py` (`OrchestrationConfig`, env section), `src/atlas/core/errors.py` (`OrchestrationError`), `config/fund.yaml`, `pyproject.toml`, `uv.lock`, `tests/unit/core/test_errors.py`

## 2026-11-16 — Commit and merge into `main` (Rule 6A)
- Reviewed the staged diff (44 files, only the stage's code, tests, docs, config, lock and records; no `data/`, `.env`, caches, sqlite files); pre-commit hooks passed. Commit **e1a2d20** `stage-28: LangGraph orchestration skeleton`; `git status --short` clean.
- Merge: the main checkout is not writable from the sandbox, so `artifacts/merged-main-check.sh` ran in the owner's Terminal panel (nothing else was running there): fast-forward 09f76eb → e1a2d20, trees equal (e752c0d), `uv sync --group dev` (adds langgraph to main's venv), `uv run pytest -q` → 4917 passed, 74 deselected, exit 0; `make check` → exit 0; `git branch --merged main` lists the stage branch. Raw: artifacts/merged-main-check.txt.
- This records commit follows; `main` is fast-forwarded to it too. Push waits for the owner's go-ahead (the remote may be public).
- Records commit bfe9850 fast-forwarded `main` e1a2d20 → bfe9850 in the owner's Terminal panel with the full merged-result checks again: trees equal (e94870b), `uv run pytest -q` → 4917 passed, 74 deselected, exit 0; `make check` → exit 0; branch listed as merged.

## 2026-11-16 — Push and CI
- Owner: "Yes, push". `git push origin main claude/stage-28-planning-audit-957ff6` from the sandbox (`allowed_domains: github.com`) → `main` 09f76eb..bfe9850, stage branch new on origin.
- CI read from the public API (`actions/runs?head_sha=bfe9850…`): `main` run 44002675127 completed success; branch run 44002675035 completed success. Raw: artifacts/ci-runs-bfe9850.json. (My wait loop's exit test used `set --` word splitting, which zsh does not do, so it ran its full 30 polls; the result is unaffected.)
- This push-and-CI records commit goes to `main` the same way (fast-forward in the owner's Terminal panel, merged-result checks) and is pushed; its own CI is reported in the chat.
- CI on the push-and-CI records commit 6a2f9b4: `main` run 44007526003 success, branch run 44007526021 success (reported in the chat; recorded here by the re-audit below).

## 2026-11-16 — Re-audit (owner: "please help me to audit again and make sure the completion of this stage")
- Fresh evidence on 6a2f9b4 before any change: Verify → 185 passed; `uv run pytest -q` → 4917 passed, 74 deselected, exit 0; `make check` → exit 0. Git: `main` = `origin/main` = branch = 6a2f9b4, main checkout clean, CI success on both refs (GitHub API).
- Re-read the brief, protocol §2–§6 and Project.md; reviewed the code again; checked LangGraph's `SqliteSaver` (cursor under its own lock: thread-safe).
- R1 (defect): checkpoints are filed under the `run_id` argument, logs and agent traces under the active run context's id, and nothing checked they agree. Red: `test_r1_run_graph_refuses_another_runs_id`, `test_r1_resume_graph_refuses_another_runs_id` failed (2 failed, 2 passed). Fix: `checkpoint._check_run_context` in `run_graph` and `resume_graph` (before anything is written). Green: 189 passed.
- R2: units in the `stub_analyst` / `stub_director` docstrings (protocol §3). R3: the `IN PROGRESS` step after the gate check was skipped (recorded; Stage 29 should write it). R4: documented that a LangGraph upgrade changes the fingerprint (resume of older runs refused) and that runs of one `ResearchGraph` share its semaphore.
- Ran: ruff clean, format clean, mypy clean, coverage 100% (390 statements); full results in artifacts/re-audit-report.md and artifacts/reaudit-*.txt.
- Code changed: `src/atlas/orchestration/checkpoint.py`, `stubs.py` (docstrings), `research_graph.py` (docstrings), `docs/orchestration.md`, `tests/unit/orchestration/test_orchestration_audit.py`.
