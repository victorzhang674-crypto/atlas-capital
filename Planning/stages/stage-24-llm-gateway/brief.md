# Stage 24 — LLM gateway, model tiers & cost budget
**Phase:** 3 LLM infra · **Size:** M · **Depends on:** 03 · **Spec refs:** §3.3, §11, A2, A8

## Goal
A provider-agnostic gateway every agent uses: tiered models, structured output, retries, token and cost accounting, and hard budgets.

## In scope
- `LLMGateway.complete(messages, tier="quick"|"deep", schema: type[BaseModel] | None, tools=None, temperature, max_tokens, metadata) -> LLMResult{content, parsed, usage, cost_usd, model, latency_ms, cache_hit}`
- Providers: `AnthropicProvider` (official SDK; default deep `claude-opus-5-5`, quick `claude-haiku-5-5`, configurable), `OpenAICompatibleProvider` (optional); `FakeLLM` (scripted responses keyed by agent/step, for tests)
- Structured output: tool-use / JSON schema from the Pydantic model; on a validation error, one repair retry that includes the error; then raise `LLMError`
- Retries: exponential backoff on rate-limit/5xx errors; timeout per call
- Cost: per-model price table **in config** (USD per MTok in/out; the stage chat checks current prices with the `claude-api` skill), a `CostLedger` per run/day/agent stored in DuckDB `llm_usage`
- Budgets: `daily_usd`, `per_run_usd`, `per_agent_call_max_tokens`; exceeding a budget raises `BudgetExceeded`, and callers fail safe (no new research → hold existing theses)
- Prompt-injection hygiene: tool/news text is wrapped in delimited "data" blocks; the system prompt says data is not instructions

## Out of scope
Caching/replay (Stage 25); agent abstractions (Stage 26).

## Files
`src/atlas/llm/{gateway.py,providers/anthropic.py,providers/openai_compat.py,providers/fake.py,cost.py,budget.py,schemas.py}`, tests

## Tasks
1. FakeLLM + gateway contract tests.
2. Structured output + repair retry (fake returns invalid JSON first).
3. Anthropic provider with mocked SDK client; one `@pytest.mark.llm` live test.
4. Cost ledger + budgets; budget-exceeded test.
5. Usage persistence.

## Acceptance criteria
- Every call is costed and logged; budgets are enforced; agents can be unit-tested with FakeLLM only.

## Verify
`uv run pytest tests/unit/llm -q`
