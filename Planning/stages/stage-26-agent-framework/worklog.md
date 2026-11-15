# Stage 26 — Agent framework, prompts & tool registry · Work log

Append entries in order. Record failures and dead ends as honestly as successes.

## 2026-11-14 — Gate check and set-up
- Did: read Project.md, PROGRESS.md (current stage 26, NOT STARTED), the brief, spec §3, §6, §7, §11, §12, the Stage 24/25 handoffs and backlog items routed to Stage 26. Stage 25 is DONE and its `verification.md` says ALL CRITERIA VERIFIED.
- Ran: `UV_CACHE_DIR=$TMPDIR/uv-cache-s26 uv sync --group dev` (new worktree) → ok.
- Ran (gate): `uv run pytest -q` → **4391 passed, 71 deselected in 514 s, exit 0** · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
- Copied `_template/` (plan, worklog, verification) into the stage folder; wrote [plan.md](plan.md).

## 2026-11-14 — Live provider probes (before choosing the tool-loop design)
- Why: the Stage 24 backlog says Stage 26's loop "must check what each provider needs" (reasoning replay between tool rounds).
- Ran: `artifacts/probe-deepseek-tool-reasoning.py` with `deepseek-v4-pro` (thinking enabled) and `deepseek-flash` (disabled) → round 2 answers HTTP 200 **without** `reasoning_content` sent back, and with it · raw: [probe-deepseek-tool-reasoning.txt](artifacts/probe-deepseek-tool-reasoning.txt)
- Ran: `artifacts/probe-deepseek-json-tools-wrapup.py` (both tiers) → `response_format: json_object` plus `tools` still gives a tool call; a wrap-up request without `tools` after a tool history → HTTP 200 with valid JSON · raw: [probe-deepseek-json-tools-wrapup.txt](artifacts/probe-deepseek-json-tools-wrapup.txt)
- First attempt of the probe loop failed (`IndexError` on `sys.argv`): zsh does not split `$m` in a `for` loop; re-ran with explicit arguments.
- Cost: 6 small calls, well under 0.01 USD (outside the gateway, so not in a ledger). The API key was read from the main checkout's `.env` into the environment only and never printed (the scripts replace it with `***` in any error text).
- Result → plan D1: no gateway change needed for DeepSeek; the Anthropic provider is not probed (no key) → Backlog.

## 2026-11-14 — Task 1: dependency, config, audit kind
- Tests first: `tests/unit/agents/test_agents_foundations.py` (5 tests) → collection error `cannot import name 'AgentsConfig'` (red for the right reason). The first run failed earlier on the agents `conftest.py` importing `atlas.agents.context` at module level; made that import lazy so each task's tests fail for their own reason.
- Did: `uv add 'jinja2>=3.1'` (jinja2 3.1.6, markupsafe 3.0.4); `AgentsConfig` (`max_tool_rounds` 4, `evidence_repair_retries` 1, `low_quality_confidence_cap` 0.3) on `AtlasConfig.agents` and in `config/fund.yaml` with comments; `EventType.AGENT_REPORT`.
- `tests/unit/core/test_audit.py::test_seeded_event_types` failed: it pins the set of event kinds. Added `AGENT_REPORT` to the expected set (Stage 03's handoff: "later stages add members (Stage 26 `AGENT_REPORT` …)"); the assertion is not weakened.
- Ran: `uv run pytest tests/unit/agents/test_agents_foundations.py tests/unit/core/test_config.py tests/unit/core/test_audit.py tests/unit/test_dependency_floors.py -q` → 363 passed.

## 2026-11-14 — Task 2: prompt templates and version extraction
- Tests first: `tests/unit/agents/test_agents_prompting.py` (22 tests) → collection error `No module named 'atlas.agents.prompting'`.
- Did: `src/atlas/agents/prompting.py` (`PromptRegistry`, `PromptTemplate`, `RenderedPrompt`, `PROMPTS_DIR`, `VERSION_RE`); a loader that requires `{# version: N #}` on the first line, records N and removes the line before Jinja2 parses the source (so a partial adds no blank line; `trim_blocks` would also eat the newline after `{% include %}`, so it is not used); `StrictUndefined`; `system` / `user` blocks rendered separately; digest over every referenced template (dynamic includes refused). Partials `_date_rules.j2`, `_output_rules.j2`, `_data_block.j2` (each `version: 1`) and package `__init__.py` files (the layout test needs one per folder). `evidence.py` got the tag and line helpers the templates use.
- One test changed before implementing: the stray-blank-line test first expected `x\nA\ny` from `x\n{% include %}y`; with the version line stripped and Jinja2's default trailing-newline removal, the natural template is `x\n{% include %}\ny`. The test now uses that form (the behaviour it protects, no blank line from the version comment, is unchanged).
- Ran: `uv run pytest tests/unit/agents/test_agents_prompting.py -q` → 22 passed.

## 2026-11-14 — Task 3: context, tool registry, DataView tools, leak test
- Tests first: `test_agents_context.py`, `test_agents_tools.py`, `test_agents_tools_pit.py`, plus `fundamentals_detail` in `tests/unit/data/test_view_pit.py` (its public-API list, both leak suites, the blocked-symbol suite and a new `test_fundamentals_detail_matches_fundamentals`) → collection errors (modules missing).
- Did: `AgentContext` (`as_of`/`day` from the view, `run_id` from the run context, frozen `inputs`); `ToolRegistry`/`Tool`/`ToolOutcome` and the built-in tools `news`, `market_news`, `fundamentals`, `price_history`; `DataView.fundamentals_detail` (cached, guarded; the tool needs the period end and availability of each metric).
- Failures and fixes: (1) `test_the_leak_test_can_see_a_leak` assumed `news:alpaca-10` is inside the default news lookback at 2024-03-18; it is not, the test now checks `news:alpaca-12` only. (2) `test_future_rows_never_leak[fundamentals_detail]` differed in `accn` only: the test world numbers accession numbers with one process-wide counter, so the dirty lake's extra filings shift them. Not a leak (values, periods and availability are equal); the leak-suite entry drops the `accn` column, with a comment.
- The agents `conftest.py` imported `World` from `conftest`, which resolves to another folder's `conftest` when several test folders run together (seen in Task 8). Moved `World`, `AGENT_PROMPTS` and `build_agent_world` to `tests/unit/agents/agent_world.py`.
- Refactor: the registry's look-ahead check now reuses `atlas.data.view.guard_evidence` (one rule in one place).
- Ran: `uv run pytest tests/unit/agents tests/unit/data/test_view_pit.py -q` → 213 passed.

## 2026-11-14 — Task 4: evidence validator
- Tests first: `test_agents_evidence.py` (11 tests) → `cannot import name 'CitationReport'`.
- Did: `EvidenceLedger`, `CitationReport`, `check_citations` (claims = nested models with `evidence_ids`; citations = `evidence_ids`, string `evidence` lists, inline `[E:id]` tags), `cap_confidence`.
- Ran: → 11 passed.

## 2026-11-14 — Task 5: BaseAgent and the toy echo analyst
- Tests first: `test_agents_base.py` (6 tests), `tests/unit/agents/prompts/echo_analyst.j2`, `agent_helpers.py` (`EchoAnalyst`, `ToolAnalyst`, `report`, `StepClock`) → `No module named 'atlas.agents.base'`.
- Did: `base.py` (`BaseAgent`, `AgentOutput`; one call only at this point), `trace.py` (`AgentTrace`, `ToolCallRecord`, in-memory `TraceStore`).
- Ran: → 6 passed, incl. `test_the_echo_analyst_runs_fully_offline` and `test_prompt_version_reaches_the_llm_cache_key` (a second run is a cache hit, 1 provider call).

## 2026-11-14 — Task 6: tool loop, max rounds, wrap-up
- Tests first: `test_agents_tool_loop.py` (6 tests) → 6 failed (no loop yet). One test's final answer was changed before implementing so that Task 7's repair turn would not need an unscripted reply (it cited an id no tool had returned; it now cites nothing and checks the error records instead).
- Did: `_tool_loop` (rounds up to `max_tool_rounds`, tool messages in data blocks, wrap-up turn without tools, `LLMError` when the wrap-up or an agent without tools gets tool calls).
- Ran: `uv run pytest tests/unit/agents -q` → 90 passed.

## 2026-11-14 — Task 7: citations inside the run
- Tests first: `test_agents_citations.py` (8 tests) → 7 failed, 1 passed (the valid-answer case).
- Did: repair turns (`evidence_repair`), then `low_quality` with `cap_confidence`; trace status `low_quality`.
- Ran: → 98 passed.

## 2026-11-14 — Task 8: trace persistence and AGENT_REPORT
- Tests first: `tests/unit/core/storage/test_storage_agent_runs.py`, `test_agents_trace.py`, `agent_runs` added to the reserved names in `test_storage_schema.py` → import errors.
- Did: native `agent_runs` table and `Database.insert_agent_runs`; `insert_llm_usage` and `insert_agent_runs` now share `Database._append` (same checks and messages); `trace_row` / `trace_from_row`; `TraceStore(database)`; `AGENT_REPORT` events from `BaseAgent` with an `AuditLog`.
- Failures and fixes: my storage test expected `None` for a null `error`, DuckDB→pandas gives NaN (test fixed with `pd.isna`); `test_storage_db.py::test_views_are_not_persisted` pins the list of native tables: `agent_runs` added to it.
- Ran: `uv run pytest tests/unit/agents tests/unit/core/storage tests/unit/llm -q` → 600 passed, 2 deselected.

## 2026-11-14 — Task 9: public API, docs, checks
- Tests first: `test_agents_package.py` → 2 failed (no `__all__`, no docs). A README link check was dropped: the README lists no docs at all and the brief does not ask for one.
- Did: `atlas.agents.__all__` (20 names), [docs/agents.md](../../../docs/agents.md).
- Lint/type round: ruff (8 long lines or style findings in tests, one long docstring line in `db.py`), mypy (4: Jinja2 `get_source` return type, `trace_from_row` key type, `float()` of an `itertuples` cell → rows via `to_dict`, a redundant `cast`) → all fixed; `ruff check .` clean, `mypy src/atlas` clean.
- Found while fixing: a malformed symbol from the model (`"a b!"`) raised a plain `ValueError` in `normalize_symbol` and would crash the run. Test first (`test_a_malformed_symbol_is_an_error_text`; the first version passed only because its string exceeded the 12-character limit, so it was shortened → 3 failed), then the tool argument models normalise the symbol with an `AfterValidator` → an error text for the model.
- Coverage of `src/atlas/agents`: first 99% (9 lines); `test_agents_edges.py` covers the default hooks, a repair turn asking for tools, an answer without parsed output, mappings in outputs, a template syntax error and a partial included twice → **100%** (654 statements) · `uv run pytest tests/unit/agents tests/unit/core/storage/test_storage_agent_runs.py --cov=src/atlas/agents` → 132 passed.

## 2026-11-14 — First full verification
- Ran (one after another, in the background): `make check` → **exit 2**: `ruff format --check` wanted to reformat `docs/agents.md` (ruff formats Python code blocks in `docs/*.md`; the example had aligned comments) · raw: [artifacts/make-check-run1.txt](artifacts/make-check-run1.txt). `uv run pytest -q` → 4529 passed, 71 deselected, exit 0 ([pytest-run1.txt](artifacts/pytest-run1.txt)); Verify `uv run pytest tests/unit/agents -q` → 119 passed ([verify-run1.txt](artifacts/verify-run1.txt)).
- Fix: `uv run ruff format docs/agents.md` → 429 files formatted.

## 2026-11-14 — Audit (self-review against the brief, spec and Project.md; no reviewer subagent)
- Checked each In-scope bullet and the acceptance criterion against code and tests (see verification.md); the hard rules (no `datetime.now()` outside the clock: trace times come from an injected `Clock`; data only through `DataView`; no network in unit tests); fail-safe paths (every `AtlasError` is traced as `failed` and re-raised); look-ahead (tool output and `gather` output both pass `guard_evidence`, so even a tool that calls `view.advance()` cannot hand later evidence to the model).
- **A1 (defect, fixed test-first):** `agent_run_id` came from `new_ulid()`, which follows the installed id source; a backtest installs `SeededIds`, so a same-seed re-run on one lake minted the same id and `agent_runs` rejected the row (`DataError`), crashing the agent run. Test `test_agents_audit.py::test_a1_seeded_re_runs_do_not_collide_in_agent_runs` → failed with `Duplicate key "agent_run_id: 01HQX1K8404TFF59TDWH9EDD1R"`; fix: a random `ULID()` (record id, like the gateway's `call_id`) → passed; agents suite 120 passed.
- Not changed (recorded): ids of the brief's example `[E:fund:AAPL:2024Q1:Revenue]` are written `fund:AAPL:<period end>:<metric>` (the lake knows period ends, not fiscal-quarter labels); `cap_confidence` caps a top-level `confidence` only (MacroOutlook has none: it is still flagged `low_quality`); a ledger id seen twice with different text raises `ValueError` (a bug signal, not traced as `failed`).

## 2026-11-14 — Commit and merge
- Re-ran on the final tree (after A1): `make check` exit 0; `uv run pytest -q` → 4530 passed, 71 deselected; Verify → 120 passed; coverage 100%. Secret scan: no match.
- Reviewed `git diff --cached --stat` (60 files: stage folder, `src/atlas/agents`, storage, config, view, tests, docs, `uv.lock`; no `.env`, lake data or caches). Committed **60a2eda** `stage-26: Agent framework, prompts & tool registry` (pre-commit hooks passed); `git status --short` clean.
- Merged into `main` in the owner's Terminal panel with [merged-main-check.sh](artifacts/merged-main-check.sh): fast-forward 8ca85e0 → 60a2eda, trees equal; merged result `uv run pytest -q` 4530 passed, `make check` exit 0; `git branch --merged main` lists the branch · raw: [merged-main-check.txt](artifacts/merged-main-check.txt).
- Records commit follows on the branch, then `main` is fast-forwarded to it. Not pushed: waiting for the owner.

## 2026-11-14 — Push and CI
- Owner: "Yes, push". `git push origin main claude/stage-26-planning-execution-3bbe8a` from the sandbox → `main` 8ca85e0..67906e2, new branch.
- CI on 67906e2 read from the public API: `main` run 43670775190 success, branch run 43670775194 success · raw: [ci-runs-67906e2.json](artifacts/ci-runs-67906e2.json). This records commit is fast-forwarded into `main` and pushed too.

## 2026-11-14 — Post-DONE full audit (owner: "can you help me to fully audit this stage and make sure everything is being fully completed?")
- Scope: every module of `src/atlas/agents` re-read line by line against the brief, spec §3 and §6–§7 (the output contracts later agents will use) and Project.md; the stage has never run on a real model, so a live run was added; a mutation run tests the tests. Report: [artifacts/audit2-report.md](artifacts/audit2-report.md).
- Tests first: `tests/unit/agents/test_agents_audit.py` (A2–A4, A6–A8) → 7 failed, 2 passed (A1 from the first audit; the "cannot cite" control). Fixes:
  - **A2** `PromptRegistry` used Jinja2's default `auto_reload=True`: a template edited on disk while a process runs would render new text under the version and digest recorded at load. Now `auto_reload=False`.
  - **A3** an output that can cite but cites nothing at all (an `AnalystReport` with no signals, an empty `evidence` list) passed the check. It is now an uncited claim at `<root>` (`can_cite` looks at the schema). Two tests that pinned the old behaviour were updated: `test_a_bad_call_is_answered_with_an_error_and_the_loop_goes_on` (now `low_quality`, with a scripted repair reply).
  - **A4** `cap_confidence` capped `confidence` only; `InvestmentThesis` (Stage 36) has `conviction`. Both top-level fields are capped now.
  - **A6** only `AtlasError`s were traced as `failed`; a bug (`ValueError` in `gather`) left no trace. Every exception is traced (`<Type>: <message>`) and re-raised.
  - **A7** an agent with an `AuditLog` but no run context spent LLM money and then failed in `AuditLog.record`. It now refuses before any call.
  - **A8** `price_history` of a blocked symbol said `(no data)` (DataView silently drops it) while `news` said blocked. Now an error text naming the block; `test_blocked_symbol_is_an_error_text_or_no_data` updated; unknown symbols still give `(no data)` (new test).
- **A5** live run (gap: the framework had only run on FakeLLM): `test_agents_live_deepseek.py` (`llm` marker, test template `tool_analyst.j2`), both tiers. Run 1 → 2 passed, both models called `news` and `price_history`, cited only shown ids, status `ok` · raw: [audit2-live-deepseek-run1.txt](artifacts/audit2-live-deepseek-run1.txt).
- **A9** found in that live output: on a 60-session window the tool reported a raw high of 79.41 next to a close of 36.85 (AAA's 2:1 split inside the window). Test `test_a9_price_range_is_split_adjusted` → failed; the range is now in adjusted closes → passed. `price_history` → **version 2** (A8 and A9 change its output; the version is in the LLM cache key, so old recordings miss); the pinned versions string in `test_agents_trace.py` updated.
- Live run 2 on the final code → 2 passed (quick 0.000398 USD, deep 0.003113 USD), both `ok` · raw: [audit2-live-deepseek.txt](artifacts/audit2-live-deepseek.txt). Key value occurs in no file.
- `docs/agents.md` updated (uncited outputs, `conviction`, every exception traced, audit needs a run context, templates loaded once).
- Ran: `ruff check .` clean, `ruff format --check .` clean, `mypy src/atlas` clean; `uv run pytest tests/unit/agents tests/unit/core/storage/test_storage_agent_runs.py --cov=src/atlas/agents` → 143 passed, 2 deselected (live), coverage 100% (678 statements).
- CI on the earlier records commit 15e65a0: `main` run 43672058665 success, branch run 43672059005 success · raw: [ci-runs-15e65a0.json](artifacts/ci-runs-15e65a0.json).
- Mutation testing (mutmut 3.8.1, scratch copy, [setup](artifacts/audit2-mutmut-setup.cfg)): the first attempt never mutated (baseline failed: `docs/` was not copied, so `test_docs_explain_how_to_build_an_agent` failed) → `docs/` added to `also_copy`. Run 1: 925 / 1117 killed, 184 survived, 8 timeouts (machine load). Reviewed every survivor; the real gaps are pinned by `tests/unit/agents/test_agents_audit_mutation.py` (16 tests; one first failed on my own wrong tool index and was corrected). Simplified `tools._instant` (a branch no input reaches). Run 2: 958 / 1109. Four more tests (repair-text id cap, error-outcome `call`, a citing field inside an optional, every known fundamentals metric: a `continue`→`break` mutant showed a missing metric could end the loop unnoticed); a targeted re-run killed 7 of those 8 mutants; the last is equivalent. Final **965 / 1109**, the 144 survivors equivalent · raw: [summary](artifacts/audit2-mutation-summary.txt), [run 1](artifacts/audit2-mutation-survivors-run1.txt), [run 2](artifacts/audit2-mutation-survivors-run2.txt).
- After all fixes: `ruff check .` clean, `ruff format --check .` 431 files formatted, `mypy src/atlas` clean, agents + storage tests 163 passed with 100% coverage (676 statements).
- Committed **bbc4535** `stage-26: post-DONE full audit (A2-A9, live run, mutation gaps)` after reviewing the staged diff (27 files, no secrets or lake data). Merged into `main` (fast-forward 15e65a0 → bbc4535, trees equal); merged result `uv run pytest -q` 4560 passed, `make check` exit 0 · raw: [audit2-merged-main-check.txt](artifacts/audit2-merged-main-check.txt). This records commit is fast-forwarded into `main` and both branches are pushed.
