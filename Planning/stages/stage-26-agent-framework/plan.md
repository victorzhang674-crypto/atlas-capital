# Stage 26 — Agent framework, prompts & tool registry · Plan

- **Brief:** [brief.md](brief.md) · **Spec:** [00-design-spec.md](../../00-design-spec.md) §6 (agent roster), §3 principle 6 (fail safe), §3 principles 2–3 (point in time, replay), §7, §12
- **Status:** DONE 2026-11-14 (see [verification.md](verification.md))
- **Gate check:** Stage 25 is DONE (its `verification.md`: ALL CRITERIA VERIFIED); `uv run pytest -q` → 4391 passed, 71 deselected, exit 0 (2026-11-14) · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
- **Execution:** written and executed in one go, inline TDD, no subagents (kick-off: "please plan, implement, execute and audit stage 26"; the owner's standing preference: plan.md first, then execute, no review pause). `worklog.md` is the ledger.

**Goal:** one consistent way to build an agent: a role, a versioned prompt, point-in-time tools, a structured output, an evidence trail and a persisted trace.

**Architecture:** `atlas.agents` holds `AgentContext` (a `DataView`, its `as_of`, the `run_id`, an optional memory handle, extra inputs), a Jinja2 prompt registry (`prompting.py`; templates in `agents/prompts/`, shared partials in `agents/prompts/_partials/`), a tool registry whose tools read only the context's `DataView` and return `Evidence` (rendered as `[E:<id>] …` lines inside a data block), an evidence validator, the trace store (`agent_runs` in DuckDB or in memory) and `BaseAgent[OutT]`, whose `run(ctx)` renders the prompt, runs the tool loop through `LLMGateway`, checks citations (one repair turn, else `low_quality` with confidence capped), persists the trace and records an `AGENT_REPORT` audit event.

## Global constraints
- No real network or LLM call in unit tests (FakeLLM only). No `datetime.now()`: trace times come from an injected `Clock` (default `LiveClock`).
- Agent and tool code reads data only through the context's `DataView`.
- Nothing hard-coded: round limit, repair retries and the low-quality confidence cap live in `config/fund.yaml` (`agents.*`).
- Fail safe: an `LLMError` (budget, provider, invalid output, a model that keeps calling tools after the wrap-up) is recorded in the trace as `failed` and re-raised: no output, no new risk. A tool that returns evidence published after `as_of` raises `LookAheadError` (never swallowed).

## Collisions with the overall plan
None found (roadmap row 26: M, depends on 13 and 25; spec §6 and §12 `agents/ base, prompts/, tools, …` match the brief). Readings and decisions, also logged in PROGRESS.md › Decisions log:

- **D1 Provider facts, probed live on 2026-11-14** (DeepSeek, both tiers; ≈ 0.002 USD; scripts and output in `artifacts/probe-*`): (a) a tool round in thinking mode is accepted **without** sending `reasoning_content` back, so the Stage 24 backlog item "reasoning replay in tool loops" needs no gateway change for DeepSeek; (b) `response_format: json_object` together with `tools` still yields tool calls, so every round may carry the output schema; (c) a wrap-up request **without** `tools` after a tool history is accepted. The optional Anthropic provider was not probed (no key) → Backlog.
- **D2 Templates:** one Jinja2 file per agent with `{% block system %}` and `{% block user %}`; its first line must be `{# version: N #}` (partials too; a template without it is refused with `ConfigError`). `StrictUndefined` (a missing variable fails). Globals: `data_block` (Stage 24 hygiene) and `evidence_lines`. Partials: `_date_rules.j2` ("today is {{ as_of }}; you know nothing after it; use only provided data"), `_output_rules.j2` (one JSON object, cite `[E:id]` ids exactly as given, every claim cites), `_data_block.j2` (pre-fetched evidence inside a data block). Agent templates of Phase 4 live in `src/atlas/agents/prompts/`; the toy agent's template lives in the tests (the registry takes extra search folders first).
- **D3 Prompt identity:** `PromptTemplate.version` (N) and `digest` = SHA-256 of the template's source and of every template it includes or imports (recursively). Every gateway call carries `metadata["prompt_version"] = "<template>@v<N>|<tool>@v<M>,…"` (tools sorted), so a template or tool version bump changes the Stage 25 cache key (closes the Stage 25 backlog item "Stage 26 must set `prompt_version`").
- **D4 Tools:** `Tool(name, description, args model, version, fn(ctx, args) -> Sequence[Evidence])`. The registry validates arguments with the Pydantic args model, checks each returned `Evidence` against `ctx.as_of` (`LookAheadError` otherwise, on top of DataView's own guards) and renders them as `[E:<id>] <text>` lines in `data_block(…, source="tool:<name>")`. An unknown tool, invalid arguments or a `DataError` (blocked symbol) become an error text for the model, logged and kept in the trace; any other exception propagates. Built-in tools wrap DataView methods: `news`, `market_news`, `fundamentals` (new `DataView.fundamentals_detail`, needed for the period end and availability of each metric: evidence id `fund:<SYM>:<period end>:<metric>`) and `price_history` (evidence id `px:<SYM>:<last session>`).
- **D5 Tool loop:** at most `agents.max_tool_rounds` (default 4) model turns may ask for tools; each round is one gateway call with tools and the output schema; all calls of a round run in order. If the model still asks for tools after the last round, a **wrap-up turn** (user message "no more tools, answer now", no tools offered) follows; a wrap-up answer without a valid output raises `LLMError`. Step labels for FakeLLM and the ledger: `round-1…N`, `wrap_up`, `evidence_repair`.
- **D6 Evidence validator:** a *claim* is any nested model with an `evidence_ids` field: it must cite at least one id. Cited ids are those fields plus any `evidence` string-sequence field and any inline `[E:<id>]` tag in a string. *Known* ids are the evidence pre-fetched by the agent plus everything tools returned in this run. Unknown ids or uncited claims → one repair turn (`agents.evidence_repair_retries`, default 1) showing the problems; still invalid → `AgentOutput.low_quality = True` and an output `confidence` field capped at `agents.low_quality_confidence_cap` (0.3).
- **D7 Trace:** native DuckDB table `agent_runs` (reserved name) with one row per `run()`: ids, agent, as_of, cutoff, times, status (`ok` / `low_quality` / `failed`), prompt name/version/digest, inputs digest (SHA-256 of the canonical JSON of the agent, as_of, inputs and rendered messages), tool calls (JSON), evidence ids, output JSON, citation report, usage, cost, LLM calls, error. `TraceStore(database=None)` keeps rows in memory without a lake (like `CostLedger`).
- **D8 Audit:** `EventType.AGENT_REPORT`; `BaseAgent` records one when given an `AuditLog` (payload: agent run id, agent, status, prompt version, output digest, cost). `AuditLog.record` needs a run context, as for every event.
- **D9 Context:** `AgentContext.as_of` is the view's `as_of` (they cannot disagree); `run_id` defaults to the current run context's; `memory` is an opaque optional handle for Stage 27.
- **D10 Dependency:** `jinja2` becomes a runtime dependency (the brief names Jinja2; spec §11 lists the main choices, not every library).

## File map
| File | Responsibility |
|---|---|
| `pyproject.toml`, `uv.lock` | + `jinja2` |
| `src/atlas/core/config.py`, `config/fund.yaml` | `AgentsConfig` (`agents.max_tool_rounds`, `evidence_repair_retries`, `low_quality_confidence_cap`) |
| `src/atlas/core/audit.py` | `EventType.AGENT_REPORT` |
| `src/atlas/core/storage/db.py`, `schema.py` | native `agent_runs` table, `insert_agent_runs`, reserved name |
| `src/atlas/data/view.py` | `DataView.fundamentals_detail(symbol)` |
| `src/atlas/agents/context.py` | `AgentContext` |
| `src/atlas/agents/prompting.py`, `prompts/__init__.py`, `prompts/_partials/{_date_rules,_output_rules,_data_block}.j2` | `PromptRegistry`, `PromptTemplate`, `RenderedPrompt` |
| `src/atlas/agents/evidence.py` | `evidence_tag`, `evidence_lines`, `EvidenceLedger`, `CitationReport`, `check_citations`, `cap_confidence` |
| `src/atlas/agents/tools.py` | `Tool`, `ToolRegistry`, `ToolOutcome`, built-in DataView tools, `default_tools()` |
| `src/atlas/agents/trace.py` | `ToolCallRecord`, `AgentTrace`, `TraceStore` |
| `src/atlas/agents/base.py` | `BaseAgent[OutT]`, `AgentOutput[OutT]` |
| `tests/unit/agents/…` | tests per task; `prompts/echo_analyst.j2` (toy agent) |
| `docs/agents.md` | how to build an agent |

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

Brief task mapping: brief 1 (echo analyst) → Task 5; brief 2 (templates) → Task 2; brief 3 (tool loop) → Task 6; brief 4 (evidence validator) → Tasks 4 and 7; brief 5 (trace) → Task 8. Tasks 1 and 3 build what they need.

### Task 1 — Foundations: dependency, config, audit kind
- Files: `pyproject.toml`, `uv.lock`, `config.py`, `fund.yaml`, `audit.py` · test `tests/unit/core/test_config.py`, `tests/unit/core/test_audit.py`, `tests/unit/agents/test_agents_package.py`
- [x] Failing tests: `test_agents_config_defaults`, `test_agents_config_validation`, `test_agent_report_event_type`, `test_jinja2_is_a_runtime_dependency`
- [x] Run → FAIL (no `agents` section, no member) · implement · run → PASS; config yaml key/comment tests still pass

### Task 2 — Prompt templates and version extraction
- Files: `prompting.py`, `prompts/__init__.py`, `prompts/_partials/*.j2` · test `tests/unit/agents/test_prompting.py`
- [x] Failing tests: version parsed from `{# version: N #}`; missing / malformed version → `ConfigError`; partials without a version refused; `system` and `user` blocks rendered; missing variable → `ConfigError` (StrictUndefined); date rules show `as_of`; data block escapes `</data>`; digest changes when a partial changes; extra search folder wins; unknown template → `ConfigError`
- [x] Run → FAIL · implement · run → PASS

### Task 3 — Context, evidence formatting, tool registry, DataView tools, leak tests
- Files: `context.py`, `evidence.py` (tags and ledger), `tools.py`, `view.py` · test `test_context.py`, `test_tools.py`, `test_tools_pit.py`, `tests/unit/data/test_view_pit.py` (fundamentals_detail)
- [x] Failing tests: context `as_of` from the view and run id from the run context; `evidence_tag`; tool spec from the args model; argument validation error and unknown tool → error text, not an exception; `DataError` → error text; other exceptions propagate; a tool returning future evidence → `LookAheadError`; each built-in tool on the Stage 13 world; **leak test**: on the dirty world (rows known only later) every tool returns exactly what it returns on the clean world, and no future id (`news:alpaca-10…13`, the 2024-03-04 10-K, bars after the cutoff) appears; `fundamentals_detail` guarded and equal to `fundamentals`
- [x] Run → FAIL · implement · run → PASS

### Task 4 — Evidence validator
- Files: `evidence.py` · test `test_evidence.py`
- [x] Failing tests: valid (all cited ids known), invalid (unknown id in `evidence_ids`, in an `evidence` list, in an inline `[E:…]` tag), missing (a claim with no ids), nested claims with paths, outputs without claims, `cap_confidence` caps and keeps lower values, outputs without `confidence`
- [x] Run → FAIL · implement · run → PASS

### Task 5 — BaseAgent end to end: the toy echo analyst (brief task 1)
- Files: `base.py`, `trace.py` (in-memory part) · test `test_base_agent.py`, `tests/unit/agents/prompts/echo_analyst.j2`
- [x] Failing tests: the echo analyst (pre-fetches the news of a symbol, answers an `AnalystReport` citing it) runs fully offline with FakeLLM; output parsed; evidence used; cost and usage summed; `prompt_version` metadata on every call; agent/step labels; subclass validation (missing name, schema, template)
- [x] Run → FAIL · implement · run → PASS

### Task 6 — Tool loop with max rounds and wrap-up (brief task 3)
- Files: `base.py` · test `test_tool_loop.py`
- [x] Failing tests: one tool round then an answer; several calls in one round; tool outputs reach the next request as `tool` messages in data blocks; a model that keeps calling tools stops after `max_tool_rounds` and gets the wrap-up turn without tools; a wrap-up that still asks for tools → `LLMError`; an error text for a bad call keeps the loop going
- [x] Run → FAIL · implement · run → PASS

### Task 7 — Citation check inside the agent: repair, then low quality
- Files: `base.py` · test `test_agent_citations.py`
- [x] Failing tests: an unknown id → repair turn → fixed output is accepted (not low quality); still unknown after the repair → `low_quality`, confidence ≤ 0.3; an uncited claim is caught the same way; evidence returned by tools counts as known; an id from a future article is unknown (acceptance: uncited claims are caught)
- [x] Run → FAIL · implement · run → PASS

### Task 8 — Trace persistence and the AGENT_REPORT event (brief task 5)
- Files: `trace.py`, `db.py`, `schema.py`, `base.py` · test `test_trace.py`, `tests/unit/core/test_storage_agent_runs.py`
- [x] Failing tests: `agent_runs` created, reserved, insert / read back, duplicate id rejected, read-only refused; `TraceStore` in memory and on DuckDB give the same rows; a run writes one row with prompt version, inputs digest, tool calls, output and cost; inputs digest stable and sensitive to inputs; a failing run is stored as `failed` and the error re-raised; `AGENT_REPORT` recorded in the run's audit log with a valid chain
- [x] Run → FAIL · implement · run → PASS

### Task 9 — Public API, docs, verification
- Files: `agents/__init__.py`, `docs/agents.md`, `tests/unit/agents/test_agents_package.py`
- [x] Public API test; docs link check (`test_repo_docs.py`)
- [x] `make check`, `uv run pytest -q`, Verify `uv run pytest tests/unit/agents -q`, coverage of `src/atlas/agents`; record in `verification.md`
- [x] Audit (self-review against the brief, spec and Project.md), fix findings test-first
- [x] Commit `stage-26: Agent framework, prompts & tool registry`, merge into `main`, re-verify the merged result
