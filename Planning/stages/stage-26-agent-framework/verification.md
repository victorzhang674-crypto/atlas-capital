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
- Merge into `main`: fast-forward 8ca85e0 → **60a2eda** (2026-11-14, run in the owner's Terminal panel: the sandbox cannot write the main checkout); trees equal (3de646e). Merged result: `uv run pytest -q` → 4530 passed, 71 deselected, exit 0 · `make check` → exit 0 (ruff clean, 429 files formatted, mypy clean, 4527 passed + 3 timing passed) · `git branch --merged main` lists the stage branch · raw: [merged-main-check.txt](artifacts/merged-main-check.txt), script [merged-main-check.sh](artifacts/merged-main-check.sh). pushed (next line)
- Pushed 2026-11-14 on the owner's go-ahead ("Yes, push"): `main` 8ca85e0..67906e2 and the stage branch. CI (GitHub Actions, `make check` + `make test-integration`) on 67906e2: **`main` run 43670775190 success**, stage-branch run 43670775194 success · raw: [ci-runs-67906e2.json](artifacts/ci-runs-67906e2.json)

**Result:** ALL CRITERIA VERIFIED

## Post-DONE full audit — 2026-11-14
Owner: "can you help me to fully audit this stage and make sure everything is being fully completed?" Report: [artifacts/audit2-report.md](artifacts/audit2-report.md); log: [worklog.md](worklog.md).
- Findings fixed test-first: A2 (prompts reload from disk under the old version), A3 (an output citing nothing passed), A4 (`conviction` not capped), A6 (non-Atlas exceptions untraced), A7 (audit without run context spent money first), A8 (blocked symbol shown as "(no data)" by `price_history`), A9 (raw high/low across a split; `price_history` → v2); A5 live run added; 20 tests for mutation-run gaps.
- Commands run (this chat, final tree):
  - `make check` → **exit 0**: ruff clean, 431 files formatted, mypy clean (167 files), pytest 4557 passed + 3 timing passed · raw: [audit2-make-check.txt](artifacts/audit2-make-check.txt)
  - `uv run pytest -q` → **4560 passed, 73 deselected**, exit 0 · raw: [audit2-pytest.txt](artifacts/audit2-pytest.txt)
  - Verify `uv run pytest tests/unit/agents -q` → **150 passed, 2 deselected** (the live `llm` tests), exit 0 · raw: [audit2-verify-pytest-unit-agents.txt](artifacts/audit2-verify-pytest-unit-agents.txt)
  - Coverage `src/atlas/agents` → 100% (676 statements) · raw: [audit2-coverage.txt](artifacts/audit2-coverage.txt)
  - Live `pytest tests/unit/agents/test_agents_live_deepseek.py -m llm -s` → 2 passed (quick and deep: tool calls, only shown ids cited, status ok; 0.0035 USD) · raw: [audit2-live-deepseek.txt](artifacts/audit2-live-deepseek.txt)
  - Mutation (mutmut 3.8.1, `src/atlas/agents/`) → 925 / 1117 before, **965 / 1109** after; remaining survivors equivalent · raw: [audit2-mutation-summary.txt](artifacts/audit2-mutation-summary.txt)
  - Secret scan: the key value occurs in no file → no match
- Acceptance criteria re-checked: all three still hold (same tests as above, plus `test_a3_*` for uncited outputs and the live run for tools on a real model).
- Waivers: none
- Merge into `main` (audit): fast-forward 15e65a0 → **bbc4535** in the owner's Terminal panel; trees equal (0e57f87). Merged result: `uv run pytest -q` → 4560 passed, 73 deselected, exit 0 · `make check` → exit 0 (4557 passed + 3 timing passed) · `git branch --merged main` lists the branch · raw: [audit2-merged-main-check.txt](artifacts/audit2-merged-main-check.txt). Pushed with this records commit (Rule 6A.7).
