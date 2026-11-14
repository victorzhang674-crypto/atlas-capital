# Stage 26 — Agent framework, prompts & tool registry · Verification evidence

Output of commands run in this stage's chat (Project.md Rule 1). Raw output is under `artifacts/`.

#### Verification evidence — Stage 26 — 2026-11-14
- Commands run (this chat, on the final tree, after the audit fix):
  - Gate: `uv run pytest -q` → 4391 passed, 71 deselected, exit 0 · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
  - `make check` → **exit 0** · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
    - `uv run ruff check .` → All checks passed!
    - `uv run ruff format --check .` → 429 files already formatted
    - `uv run mypy src/atlas` → Success: no issues found in 167 source files
    - pytest (parallel) → 4527 passed; timing budgets alone → 3 passed
  - `uv run pytest -q` (serial) → **4530 passed, 71 deselected**, exit 0 (deselected: `live` / `llm` / `integration` only) · raw: [artifacts/pytest.txt](artifacts/pytest.txt)
  - Brief's Verify `uv run pytest tests/unit/agents -q` → **120 passed**, exit 0 · raw: [artifacts/verify-pytest-unit-agents.txt](artifacts/verify-pytest-unit-agents.txt)
  - Coverage of `src/atlas/agents` → **100%** (654 statements) · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
  - Acceptance tests, verbose → 54 passed · raw: [artifacts/acceptance-tests.txt](artifacts/acceptance-tests.txt)
  - Earlier run, before the audit fix: `make check` exit 2 (`docs/agents.md` not ruff-formatted), fixed; recorded in [worklog.md](worklog.md) · raw: [make-check-run1.txt](artifacts/make-check-run1.txt)
  - Live provider probes (DeepSeek, both tiers, outside the unit suite) · raw: [probe-deepseek-tool-reasoning.txt](artifacts/probe-deepseek-tool-reasoning.txt), [probe-deepseek-json-tools-wrapup.txt](artifacts/probe-deepseek-json-tools-wrapup.txt)
  - Secret scan: the `DEEPSEEK_API_KEY` value of the main checkout's `.env` occurs in no worktree file (`grep -rlF`) → no match
- Acceptance criteria (brief: "The toy agent runs fully offline with FakeLLM; uncited claims are caught; tools cannot see beyond `as_of` (leak test via DataView)"):
  - [x] The toy agent runs fully offline with FakeLLM — evidence: `test_agents_base.py::test_the_echo_analyst_runs_fully_offline` (FakeLLM only, no network; output parsed, evidence, cost, trace), and the whole agents suite uses FakeLLM
  - [x] Uncited claims are caught — evidence: `test_agents_citations.py::test_an_uncited_claim_is_caught` (`uncited_claims == ("signals[1]",)`, repair turn, `low_quality`, confidence capped), `test_still_unknown_after_the_repair_is_low_quality`, `test_a_future_article_is_unknown`; `test_agents_evidence.py::test_missing_citation_is_an_uncited_claim`
  - [x] Tools cannot see beyond `as_of` (leak test via DataView) — evidence: `test_agents_tools_pit.py::test_tools_cannot_see_beyond_as_of` (9 cases: on the lake with later rows every tool returns exactly what it returns on the clean lake, no future id), `test_the_leak_test_can_see_a_leak` (the same calls differ at a later as-of), `test_agents_tools.py::test_a_tool_returning_future_evidence_raises_lookahead`, `test_agents_base.py::test_gathered_evidence_must_be_known_at_as_of`, `test_view_pit.py::test_future_rows_never_leak[fundamentals_detail]` and `test_leak_is_caught[fundamentals_detail]`
- Brief In-scope items (each checked against code and tests):
  - [x] `BaseAgent[OutT]` with `name, tier, prompt_template, output_schema, tools`; `run(ctx) -> AgentOutput[OutT]` (output + evidence + trace + cost) — `src/atlas/agents/base.py`; `test_agents_base.py`
  - [x] `AgentContext`: `as_of`, `DataView`, `run_id`, memory handle (optional), extra inputs — `context.py`; `test_agents_context.py`
  - [x] Jinja2 templates with `{# version: N #}`; partials `_data_block.j2`, `_output_rules.j2`, `_date_rules.j2` — `prompting.py`, `prompts/_partials/`; `test_agents_prompting.py` (22 tests)
  - [x] Tool registry over DataView methods with evidence-tagged text (`[E:news:alpaca-2] …`, `[E:fund:AAA:2023-12-31:revenue_ttm] …`); tool loop with `max_tool_rounds` and a wrap-up turn — `tools.py`, `base.py`; `test_agents_tools.py`, `test_agents_tool_loop.py::test_a_model_that_keeps_calling_tools_gets_a_wrap_up_turn`
  - [x] Evidence validator: unknown ids → repair retry → else `low_quality`, confidence capped at 0.3 — `evidence.py`, `base.py`; `test_agents_evidence.py`, `test_agents_citations.py`
  - [x] Trace persisted in `agent_runs` (inputs digest, prompt version, tool calls, output, cost) — `trace.py`, `db.py`; `test_agents_trace.py`, `test_storage_agent_runs.py`
  - [x] `AGENT_REPORT` audit events — `test_agents_trace.py::test_agent_report_is_audited`, `test_a_failed_run_is_audited_too`
- Audit (self-review, recorded in [worklog.md](worklog.md#2026-11-14--audit-self-review-against-the-brief-spec-and-projectmd-no-reviewer-subagent)): A1 fixed test-first (`agent_run_id` collided under seeded ids on a re-run) — `test_agents_audit.py::test_a1_seeded_re_runs_do_not_collide_in_agent_runs`
- Deviations from the brief: evidence ids for fundamentals use the period end (`fund:AAA:2023-12-31:revenue_ttm`) rather than a quarter label; extra files `prompting.py` and `DataView.fundamentals_detail`; `docs/agents.md`
- Waivers: none

**Result:** ALL CRITERIA VERIFIED
