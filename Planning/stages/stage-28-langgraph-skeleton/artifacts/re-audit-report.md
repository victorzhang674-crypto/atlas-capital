# Stage 28 — LangGraph orchestration skeleton · Re-audit report

- **Date:** 2026-11-16 · **Asked for:** "please help me to audit again and make sure the completion of this stage"
- **Auditor:** a new pass in the same chat as the stage (self-review; no reviewer subagent), done after the stage was marked DONE, merged and pushed at `6a2f9b4`. It re-checked everything from scratch instead of trusting the first audit ([audit-report.md](audit-report.md)).
- **Checked against:** the brief, roadmap row 28, design spec §2/§3/§5–§7/§11/§12, `02-stage-protocol.md` §2–§6 (definition of done, conventions, handoff template), Project.md Rules 0–6A.

## Verdict

**COMPLETE.** All three acceptance criteria and every in-scope item still hold on fresh runs. The re-audit found one real defect (R1: a checkpointed run could be filed under one run id while its logs and agent traces carried another), fixed test-first, plus two definition-of-done gaps (R2 units in docstrings, R3 the `IN PROGRESS` status step) and two undocumented behaviours (R4). Nothing is left open.

## Fresh evidence on the committed stage (`6a2f9b4`, before any re-audit change)

- `uv run pytest tests/unit/orchestration -q` (brief's Verify) → 185 passed, exit 0
- `uv run pytest -q` → 4917 passed, 74 deselected, exit 0
- `make check` → exit 0 (ruff clean, 468 files formatted, mypy clean, 4914 + 3 timing passed)
- Git: `main` = `origin/main` = branch = `6a2f9b4`; the main checkout is clean; `git branch --merged main` lists the branch. CI on `6a2f9b4`: `main` run 44007526003 success, branch run 44007526021 success (read from the GitHub API in this pass).

## Acceptance criteria, re-checked

| Criterion | Evidence (re-run in this pass) |
|---|---|
| Parallel stub graph over N symbols completes | `test_five_symbols_fan_out_and_complete`, `test_the_limit_caps_agent_calls_and_symbols_in_flight[1-3]`, barrier tests `test_symbols_are_researched_in_parallel`, `test_the_analysts_run_in_parallel` |
| Resume works | `test_a_killed_process_resumes_from_its_last_checkpoint` (real `SIGKILL`), `test_ctrl_c_mid_run_then_resume_gives_the_same_final_state`, `test_resuming_a_finished_run_reruns_nothing`, `test_errors_survive_a_resume` |
| One failing symbol does not affect the others | `test_one_failing_node_drops_only_its_symbol` (7 nodes × 3 error kinds), `test_failures_in_several_symbols`, `test_every_symbol_failing_still_completes_the_run`, `test_a_failing_symbol_does_not_hold_up_the_others` |

In-scope items: `FundState` with the 13 brief keys and reducers; research subgraph (analysts parallel → join → debate → director, stubs); `Send` fan-out with `orchestration.max_concurrency`; SQLite checkpoints at `<lake>/runs/<run_id>/checkpoints.sqlite`; error policy; mermaid diagram in `docs/orchestration.md` (test keeps it equal to the export). Out of scope respected: no real agents, no Fund Day.

## Findings

| # | Severity | Finding | Resolution | Evidence |
|---|---|---|---|---|
| R1 | Medium (audit trail) | `run_graph` / `resume_graph` file checkpoints under their `run_id` argument, while logs (`atlas.core.logging`) and agent traces (`AgentTrace.run_id`) take the id from the active run context. Nothing checked that the two agree: called under the run context of run X with `run_id=Y`, one run's records would be split across two ids. | Both refuse (`OrchestrationError`, before anything is written) when a run context of another run is active; docstrings and docs say to call them inside the run's `run_context`. A nested scope of the same run (`stage=`, `as_of=`) is fine. | Red first: `test_r1_run_graph_refuses_another_runs_id`, `test_r1_resume_graph_refuses_another_runs_id` failed; then pass. `test_r1_agents_run_inside_the_callers_run_context` turns the first audit's probe ("the run context reaches the agent threads") into a test; `test_r1_a_nested_scope_of_the_same_run_is_fine` |
| R2 | Low (definition of done: "docstrings state units") | `stub_analyst` and `stub_director` produce numbers (strength, confidence, return quantiles, horizon, conviction) without stating units. | Docstrings now state them: fractions in [0, 1), simple returns over 20 trading days (not annualised), conviction = mean confidence. | `stubs.py` |
| R3 | Info (process, protocol §2.1) | The protocol asks to set the stage to `IN PROGRESS` in `PROGRESS.md` after the gate check. Stage 28 went from `NOT STARTED` straight to `DONE` in its stage commit; the status was never written. Earlier stages also committed only the final status (git history shows `IN PROGRESS` only in Stages 01 and 26), so no reader saw a wrong state, but the step was skipped. | Recorded here and in the worklog; cannot be applied after the fact. From Stage 29 on: write `IN PROGRESS` right after the gate check. | — |
| R4 | Info (behaviour not documented) | (a) The graph fingerprint is computed from LangGraph's mermaid text, so a LangGraph upgrade that changes the text also refuses runs started before it (fail safe, but surprising). (b) The agent-call semaphore belongs to a built `ResearchGraph`; two runs of one graph at the same time share its limit. | Both documented (`ResearchGraph` docstrings, `docs/orchestration.md`). | — |

## Re-checked, no change needed

- **LangGraph's SQLite saver and threads:** every cursor is taken under the saver's own `threading.Lock` (`langgraph/checkpoint/sqlite/__init__.py`, `cursor()`), so parallel tasks writing through one connection are safe.
- **Error policy edges:** unguarded nodes (`join`, `publish`, routers, `prepare`, `collect`) do no agent work; `publish` runs only when the router saw no error, so `thesis` exists; a conflicting write still raises (a bug, by design).
- **Symbols:** `prepare` validates with the `Symbol` type, so de-duplication happens after normalisation; reports must carry the normalised ticker.
- **Recursion limits:** the subgraph takes 5 steps, the fund graph 3; LangGraph's default limit (25) is far away.
- **Network:** no outbound attempt in any run of this pass (no `smith.langchain` line in the raw outputs; the sandbox logged none).
- **Records:** the stage folder has `brief.md`, `plan.md` (no unticked box), `worklog.md` (lists every code file), `verification.md` (**ALL CRITERIA VERIFIED**), `artifacts/`; PROGRESS.md has the ticked checkbox, current stage 29, the Decisions log row, backlog notes and a handoff note following protocol §6 with the evidence block; config key `orchestration.max_concurrency` is in `config/fund.yaml` with a comment and in `OrchestrationConfig` with a default.

## After the fixes

Raw outputs: [reaudit-verify-orchestration.txt](reaudit-verify-orchestration.txt), [reaudit-pytest.txt](reaudit-pytest.txt), [reaudit-make-check.txt](reaudit-make-check.txt); coverage of `src/atlas/orchestration` 100% (390 statements). Results: Verify `uv run pytest tests/unit/orchestration -q` → 189 passed, exit 0 · `uv run pytest -q` → 4921 passed, 74 deselected, exit 0 · `make check` → exit 0 (All checks passed!, 468 files already formatted, mypy: no issues in 178 source files, 4918 + 3 timing passed). Merged into `main` (fast-forward 6a2f9b4 → f2126e7, owner's Terminal panel): `uv run pytest -q` → 4921 passed, `make check` → exit 0, trees equal · raw: [reaudit-merged-main-check.txt](reaudit-merged-main-check.txt).
