# Stage 24 — LLM gateway, model tiers & cost budget · Plan

- **Brief:** [brief.md](brief.md) · **Spec:** [00-design-spec.md](../../00-design-spec.md) §3.3, §3.4, §3.6, §11, A2, A8
- **Status:** DONE 2026-11-10 (see [verification.md](verification.md))
- **Gate check:** Stage 23 is DONE (its `verification.md`: ALL CRITERIA VERIFIED); `uv run pytest -q` → 3807 passed, 29 deselected, exit 0 (2026-11-09) · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
- **Execution:** written and executed in one go, inline TDD, no subagents (kick-off: "plan, execute and audit Stage 24 according to the existing plans"; the owner's standing preference: plan.md first, then execute, no review pause). `worklog.md` is the ledger.

**Goal:** one provider-agnostic `LLMGateway.complete(...)` that every agent calls: tiered models, schema-validated output with one repair retry, retries with backoff, every call costed and logged, hard budgets.

**Architecture:** the gateway owns everything that must be identical across providers (data-policy system text, budget check, retry loop, schema validation and repair, costing, the ledger row). A provider only turns one `ProviderRequest` into one `ProviderResponse` or raises `LLMTransientError` / `LLMError`. Costs come from a price table in config, evaluated at the wall-clock instant of the call; rows go to an in-memory ledger and, when a lake is given, to the native DuckDB table `llm_usage`.

**Tech:** Pydantic v2, httpx (OpenAI-compatible provider; respx in tests), `anthropic` SDK 2.x (optional provider, dependency group `llm`), DuckDB, structlog.

## Global constraints
- No real network or LLM call in unit tests (FakeLLM, respx, a fake SDK client). The one live test is `@pytest.mark.llm`.
- Never `datetime.now()` outside `atlas.core.clock`: billing time comes from an injected `Clock` (default `LiveClock()`); latency from an injected `timer` (default `time.perf_counter`).
- Nothing hard-coded: model ids, base URL, prices, budgets, timeouts and retry counts live in `config/fund.yaml` and `LLMConfig`.
- Never log, print or store an API key; error messages carry status codes and truncated bodies only.
- Fail safe: a call that cannot be costed, validated or afforded raises (`ConfigError` / `LLMError` / `BudgetExceeded`); it never returns a guess.

## Prices checked for this stage (2026-11-09)
- DeepSeek, read from api-docs.deepseek.com/quick_start/pricing in the browser (USD per 1M tokens; off-peak / peak):
  `deepseek-flash` cache-hit input 0.003 / 0.006, cache-miss input 0.15 / 0.30, output 0.60 / 1.20;
  `deepseek-v4-pro` cache-hit 0.022 / 0.044, cache-miss 0.66 / 1.32, output 1.98 / 3.96.
  Peak = 01:00–04:00 and 06:00–10:00 UTC, Monday–Friday, excluding Chinese public holidays. Base URL `https://api.deepseek.com`. Unchanged model ids.
- Claude, from the `claude-api` skill (cached 2026-11-03): `claude-opus-5-5` input 4.00, output 20.00, cache read 0.20, 5-minute cache write 1.25× = 5.00; `claude-haiku-5-5` input 0.10, output 0.50 for prompts up to 100K tokens (0.50 / 2.50 above), cache read ~0.1× = 0.01, cache write 1.25× = 0.125.
- API facts used: DeepSeek `usage` has `prompt_tokens` = `prompt_cache_hit_tokens` + `prompt_cache_miss_tokens`, `completion_tokens`, `completion_tokens_details.reasoning_tokens`; `response_format` is `text` or `json_object` only; forced `tool_choice` is a 400 in thinking mode (the default). Claude Opus 5.5 / Haiku 5.5 reject forced `tool_choice` and sampling parameters (SDK 2.7 `messages.create` has no `temperature`); structured output is `output_config.format` with `anthropic.transform_schema(Model)`.

## Collisions with the overall plan
None found (roadmap row 24: M, depends on 03; spec §11 and A2 already name DeepSeek as default since 2026-10-30). Readings and decisions, also logged in PROGRESS.md › Decisions log:

- **D1 Structured output = JSON schema, not forced tool use.** The brief allows "tool-use / JSON schema"; forced tool use is rejected by Claude 5.5 models and by DeepSeek in thinking mode. OpenAI-compatible: `response_format: json_object` plus the schema in the system text. Anthropic: `output_config.format` json_schema. The gateway validates with Pydantic in both cases.
- **D2 `BudgetExceeded` and `LLMTransientError` subclass `LLMError`** in `atlas.core.errors`. `LLMError`'s docstring already covers "exceeded its budget"; a caller that fails safe on `LLMError` then also holds on a budget stop. The six top-level kinds stay siblings.
- **D3 Billing time is wall-clock UTC**, not the simulated clock: the vendor bills at real time, and DeepSeek's peak windows are real UTC hours. The daily budget is the UTC calendar day of the wall clock. Chinese public holidays are not modelled: a weekday in a peak window is always priced at peak (cost is over-, never under-estimated).
- **D4 Hard budgets with a reservation.** Before each provider call the guard refuses the call if `spent + in-flight reservations + estimate > cap`, where estimate = `max_tokens` × output rate + `ceil(prompt chars / budgets.estimate_chars_per_token)` × input rate. A call with `max_tokens` above `per_agent_call_max_tokens` raises `BudgetExceeded`; `max_tokens=None` uses the cap. Calls without a run context share one per-run bucket (`run_id` NULL).
- **D5 `llm_usage` is a native DuckDB table** in `atlas.duckdb` (like `audit_events`), one row per billed provider response (a repair retry is a second row with `attempt = 2`). Without a lake the ledger is in memory, so agents are testable with FakeLLM alone.
- **D6 Config shape:** `llm.provider`, `llm.base_url`, `llm.tiers` (DeepSeek ids, default provider), `llm.anthropic_tiers` (Claude ids, used when `provider: anthropic`), `llm.thinking` per tier (`enabled | disabled | omit`; the OpenAI-compatible provider sends `thinking: {type}` unless `omit`; default quick `disabled`, deep `enabled`), `llm.request_timeout_seconds` 120, `llm.retry` (`max_retries` 3, `backoff_seconds` 1.0, `max_backoff_seconds` 30.0), `llm.prices.<model>` (`input`, `cached_input`, `cache_write`, `output`, `windows[]` with `days`, `start`, `end` UTC and the same four rates), `llm.budgets.estimate_chars_per_token` 3.0.
- **D7 `temperature` is a hint**: sent by the OpenAI-compatible provider, dropped by the Anthropic one (the API rejects it). Default 0.0.
- **D8 Tools are pass-through**: `ToolSpec` in, `ToolCall`s out, `tool` / assistant-with-tool-calls messages translated per provider. The tool loop and the replay of provider reasoning between tool rounds are Stage 26 (Backlog).
- **D9 `anthropic` goes in the `llm` dependency group**, included by `dev` so CI installs it; the provider imports it lazily and raises `ConfigError` with the install hint when missing. SDK retries are set to 0: the gateway owns retries.
- **D10 Prompt-injection hygiene** lives in a new `atlas/llm/hygiene.py` (not in the brief's file list): `data_block()` and `DATA_POLICY`; the gateway adds the policy to every call's system text.
- **Not built (Backlog):** Claude refusal fallbacks (`fallbacks` beta; a refusal raises `LLMError`), Haiku 5.5's higher price above 100K prompt tokens, streaming, async.

## File map
| File | Responsibility |
|---|---|
| `src/atlas/core/errors.py` | + `BudgetExceeded(LLMError)` (`scope`, `limit`, `spent`), `LLMTransientError(LLMError)` (`retry_after`) |
| `src/atlas/core/config.py`, `config/fund.yaml`, `.env.example` | D6 keys, DeepSeek defaults, `Secrets.deepseek_api_key` |
| `src/atlas/core/storage/db.py`, `schema.py` | native `llm_usage` table, `Database.insert_llm_usage(rows)`, reserved name |
| `src/atlas/llm/schemas.py` | `Tier`, `Message`, `ToolSpec`, `ToolCall`, `Usage`, `LLMResult`, `ProviderRequest`, `ProviderResponse`, `LLMProvider` protocol |
| `src/atlas/llm/hygiene.py` | `data_block(text, *, source)`, `DATA_POLICY`, `with_data_policy(messages)` |
| `src/atlas/llm/cost.py` | `rates_at(price, ts)`, `cost_usd(usage, price, ts)`, `UsageRecord`, `CostLedger` |
| `src/atlas/llm/budget.py` | `BudgetGuard` (token cap, reservations, daily and per-run caps) |
| `src/atlas/llm/gateway.py` | `LLMGateway`, `build_gateway(config, *, lake=None)` |
| `src/atlas/llm/providers/{fake,openai_compat,anthropic}.py` | `FakeLLM` (+ `FakeReply`), `OpenAICompatibleProvider`, `AnthropicProvider` |
| `tests/unit/llm/…`, `tests/conftest.py` (`fake_llm` fixture) | tests mirror the modules |

## Review focus (failure modes the brief implies but does not list; each has a test in its task)
1. A model wraps its JSON in a markdown fence → accepted without spending a repair call (Task 2).
2. A provider answer without `usage` → `LLMError`; no uncosted success (Task 3).
3. A model id with no price row → `ConfigError` before any provider call (Task 4).
4. Data text that contains the closing delimiter cannot end the data block early (Task 1).
5. Parallel calls cannot jointly overshoot a budget: reservations are counted (Task 4).

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

### Task 0 — Config, secrets, errors
- Files: modify `src/atlas/core/config.py`, `src/atlas/core/errors.py`, `config/fund.yaml`, `.env.example`, `pyproject.toml` · test `tests/unit/core/test_config.py`, `tests/unit/core/test_errors.py`
- [x] Failing tests: defaults (`provider == "openai_compatible"`, `base_url`, tiers `deepseek-v4-pro` / `deepseek-flash`, `anthropic_tiers`, `secrets.deepseek_api_key is None`); `LLMConfig.model_for(tier)` per provider; every tier id of both providers has a price row in defaults and in `fund.yaml`; `ModelPrice` validation (negative rate, `start >= end`, unknown day → `ConfigError` through `load_config`; the planned missing-`output` case was dropped, see Addendum); `DEEPSEEK_API_KEY` read from env and `.env`; `BudgetExceeded` / `LLMTransientError` are `LLMError`s and keep their attributes
- [x] Run `uv run pytest tests/unit/core/test_config.py tests/unit/core/test_errors.py -q` → FAIL
- [x] Implement; add `anthropic>=2.7` to group `llm`, `{include-group = "llm"}` to `dev`; `uv lock` + sync
- [x] Same command → PASS

### Task 1 — FakeLLM + gateway contract (brief task 1)
- Files: create `schemas.py`, `hygiene.py`, `providers/__init__.py`, `providers/fake.py`, `gateway.py` · test `tests/unit/llm/test_schemas.py`, `test_hygiene.py`, `test_fake.py`, `test_gateway.py`; `tests/conftest.py`
- Interfaces: `LLMGateway(provider, config, *, ledger=None, clock=None, sleep=time.sleep, timer=time.perf_counter)`; `complete(messages, *, tier="quick", schema=None, tools=None, temperature=0.0, max_tokens=None, metadata=None) -> LLMResult`; `FakeLLM(script=None, *, default=None)`, `.add(agent, step, *replies)`, `.calls`; keys `(agent, step)` → `(agent, None)` → default; a list is consumed in order and exhaustion raises `LLMError`; a reply is `str | BaseModel | Mapping | Exception | FakeReply`
- [x] Failing tests: result fields (`content`, `parsed is None`, `usage`, `cost_usd`, `model` = tier id, `latency_ms` from the timer, `cache_hit is False`); tier → model; `metadata` agent/step reach the provider; unknown tier and empty messages rejected; FakeLLM keying, order, exhaustion, scripted exception, recorded calls; `data_block` wraps and neutralises embedded delimiters (review focus 4); the provider always receives `DATA_POLICY` in the system text, once; frozen models
- [x] Run `uv run pytest tests/unit/llm -q` → FAIL, implement, → PASS

### Task 2 — Structured output + repair retry (brief task 2)
- Files: modify `gateway.py` · test `tests/unit/llm/test_structured.py`
- [x] Failing tests: valid JSON → `parsed` is the model instance; fenced JSON accepted (review focus 1); invalid then valid → 2 provider calls, the second request carries the first answer and the validation error, `parsed` set, usage and cost are the sum; invalid twice → `LLMError` naming the schema, both calls in the ledger; a reply with tool calls and a schema is returned unvalidated; the provider request carries the schema
- [x] FAIL → implement → PASS

### Task 3 — Providers + retries (brief task 3)
- Files: create `providers/openai_compat.py`, `providers/anthropic.py`; modify `gateway.py` (retry loop, `build_gateway`) · test `tests/unit/llm/test_retry.py`, `test_openai_compat.py`, `test_anthropic.py`, `test_live_deepseek.py`
- [x] Failing tests, retry: transient twice then success → 3 calls, sleeps 1 s, 2 s; `retry_after` wins when longer; backoff capped; exhausted → `LLMError` chained to the last transient; a plain `LLMError` is not retried
- [x] Failing tests, OpenAI-compatible (respx): URL `{base_url}/chat/completions`, bearer header, body (model, messages, `max_tokens`, `temperature`, `stream: false`, `thinking`, `response_format` + schema text when a schema is given, `tools`); usage mapping incl. cache hit / miss and reasoning tokens, OpenAI-style `prompt_tokens_details.cached_tokens`; tool calls parsed; 429 / 500 / timeout → `LLMTransientError` (`Retry-After` read); 400 / 401 → `LLMError` without the key in the message; missing `usage` or `choices` → `LLMError` (review focus 2)
- [x] Failing tests, Anthropic (fake client object): system text joined into `system`, messages translated incl. tool use / tool result, no `temperature`, `output_config.format` from `transform_schema`, `max_retries=0`, usage mapping (`cache_read_input_tokens`, `cache_creation_input_tokens`), text blocks joined and thinking blocks skipped, `refusal` → `LLMError`, SDK rate-limit / 5xx / timeout / connection errors → `LLMTransientError`, other status errors → `LLMError`
- [x] `build_gateway`: provider from config; missing `DEEPSEEK_API_KEY` → `ConfigError` naming the variable
- [x] FAIL → implement → PASS
- [x] Live: `uv run pytest tests/unit/llm/test_live_deepseek.py -m llm -q` (quick tier, a tiny schema, `max_tokens` 200) → PASS with real usage and a positive cost; output saved to `artifacts/`

### Task 4 — Cost ledger + budgets (brief task 4)
- Files: create `cost.py`, `budget.py`; modify `gateway.py` · test `tests/unit/llm/test_cost.py`, `test_budget.py`
- [x] Failing tests, cost: hand-computed costs for both DeepSeek models off-peak and peak; window edges (`01:00` in, `04:00` out, `05:00` off-peak, Saturday off-peak); cached input falls back to the input rate when unpriced; cache-write tokens; a naive timestamp is rejected; unknown model → `ConfigError` before the provider is called (review focus 3)
- [x] Failing tests, budget: `max_tokens` above the cap → `BudgetExceeded(scope="per_agent_call_max_tokens")`, no provider call; default `max_tokens` = cap; daily cap reached → `BudgetExceeded("daily_usd")`, no provider call; the next UTC day is allowed again; per-run cap by `run_id` (two run contexts are independent); the estimate alone can refuse a call; reservations of in-flight calls count and are released on success and on failure (review focus 5); ledger totals per run / day / agent
- [x] FAIL → implement → PASS

### Task 5 — Usage persistence (brief task 5)
- Files: modify `src/atlas/core/storage/db.py`, `schema.py`, `cost.py`, `gateway.py` · test `tests/unit/core/storage/test_db_llm_usage.py`, `tests/unit/llm/test_ledger.py`
- [x] Failing tests: `llm_usage` exists in a new database and is a reserved table name; `insert_llm_usage` round-trips every column, rejects a read-only database and a duplicate `call_id`; a `CostLedger(database)` survives reopening the database (budget sees yesterday's and today's spend from disk); a gateway built with `lake=` writes one row per provider response with `run_id`, agent, step, tier, model, tokens, cost, attempt, status
- [x] FAIL → implement → PASS

### Task 6 — Exports, audit, full verification
- [x] `atlas.llm` exports; docstrings state units (USD, USD per 1M tokens, tokens, milliseconds, UTC)
- [x] Self-audit of the diff against this plan, the brief and the Definition of Done; mutation check over `src/atlas/llm` and fixes test-first; findings in `worklog.md`
- [x] `make check`, the brief's Verify command, raw output in `artifacts/`; `verification.md`; PROGRESS.md; commit; merge; merged-main checks

## Addendum (audit, 2026-11-09)
- A1: test files are named `tests/unit/llm/test_llm_*.py` and `tests/unit/core/storage/test_storage_llm_usage.py` (unique basenames), not the shorter names in the task lists.
- A2: Task 0's "price without `output`" case is not testable through `load_config` (an override merges into the default price row) and was dropped; `ModelPrice` still requires `output`.
- A3: the retry log, the `llm.call` log and the `llm.budget_exceeded` log were added to the gateway (brief acceptance: "costed and logged"); `build_provider(config)` is public next to `build_gateway`.
- A4: `cost.py` also holds `Rates` and `UsageRecord`; `CostLedger` totals are `spent_on(day)`, `spent_in_run(run_id)`, `spent_by_agent(agent, day=None)`.
- A5: mutation check: 1135 / 1272 killed before the audit tests, 1213 / 1272 after; the 59 survivors are message-text, SQL-case or equivalent mutants (`worklog.md`).
