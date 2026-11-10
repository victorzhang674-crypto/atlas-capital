# Stage 24 — LLM gateway, model tiers & cost budget · Verification evidence

Output of commands run in this stage's chat (Project.md Rule 1). Raw output is under `artifacts/`.

#### Verification evidence — Stage 24 — 2026-11-10
- Commands run (this chat):
  - Gate: `uv run pytest -q` → 3807 passed, 29 deselected, exit 0 · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
  - `make check` → exit 0 · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
    - `uv run ruff check .` → All checks passed!
    - `uv run ruff format --check .` → 374 files already formatted
    - `uv run mypy src/atlas` → Success: no issues found in 154 source files
    - `uv run pytest -q` → **4037 passed, 31 deselected** in 382 s (deselected: `live` / `llm` / `integration` markers only)
  - Brief's Verify `uv run pytest tests/unit/llm -q` → **198 passed, 2 deselected** (the 2 are the `llm`-marked live test), exit 0 · raw: [artifacts/verify-llm.txt](artifacts/verify-llm.txt)
  - Live (brief task 3), `uv run pytest tests/unit/llm/test_llm_live_deepseek.py -m llm -q -s` with the owner's `DEEPSEEK_API_KEY` → **2 passed** (quick `deepseek-flash`, deep `deepseek-v4-pro`; 2026-11-10 03:35 UTC, a peak hour): schema-valid answers, real usage, costs 0.0000759 and 0.00037294 USD, equal to a hand calculation at the peak rates · raw: [artifacts/live-deepseek.txt](artifacts/live-deepseek.txt)
  - Coverage of `src/atlas/llm` by `tests/unit/llm` + the `llm_usage` storage test → 646 statements, 0 missed (100%) · raw: [artifacts/coverage-llm.txt](artifacts/coverage-llm.txt)
  - Mutation check (mutmut 3.8.1, `src/atlas/llm`): 1135 / 1272 killed before the audit tests, **1213 / 1272** after; the 59 survivors are message-text, SQL-keyword-case or equivalent mutants · raw: [artifacts/mutation-summary.txt](artifacts/mutation-summary.txt), [run 1](artifacts/mutation-survivors-run1.txt), [run 2](artifacts/mutation-survivors-run2.txt)
  - Secret scan: the real `DEEPSEEK_API_KEY` value occurs in no file of the worktree (`grep -rlF`, `.venv` and `.git` excluded) → no match
- Acceptance criteria (brief: "Every call is costed and logged; budgets are enforced; agents can be unit-tested with FakeLLM only"):
  - [x] Every call is costed — evidence: `test_llm_cost.py` (hand-computed off-peak and peak costs of both DeepSeek models, window edges, cache-read and cache-write tokens, `test_gateway_prices_at_the_wall_clock_instant`); `test_result_fields` and `test_invalid_then_valid_is_repaired_once` (result cost = sum of the billed responses); `test_unpriced_model_never_reaches_the_provider` and `test_answer_that_cannot_be_costed_or_read_is_an_error` (no uncosted success); live run (cost matches the published peak rates)
  - [x] Every call is logged — evidence: `test_gateway_writes_one_row_per_provider_response` (one `llm_usage` row per billed response with run id, agent, step, tier, model, tokens, cost, attempt, status; the rows sum to the result's cost), `test_every_billed_response_is_one_ledger_row` (an answer that fails validation is still recorded), `test_ledger_survives_reopening_the_database`, `test_storage_llm_usage.py` (11 tests on the table), `test_llm_logging.py` (`llm.call` line with tokens and cost, `run_id` in the JSON log, `llm.retry`, `llm.budget_exceeded`)
  - [x] Budgets are enforced — evidence: `test_llm_budget.py`: `test_max_tokens_above_the_cap_is_refused`, `test_daily_budget_stops_calls_and_resets_next_utc_day`, `test_per_run_budget_is_per_run_id`, `test_the_estimate_alone_can_refuse_a_call`, `test_in_flight_calls_reserve_budget`, `test_repair_retry_is_budget_checked_too` (each asserts `BudgetExceeded` and that the provider was not called); `test_budget_sees_spend_from_an_earlier_session` (spend persisted in DuckDB counts); `test_budget_exceeded_is_an_llm_error_so_callers_fail_safe`
  - [x] Agents can be unit-tested with FakeLLM only — evidence: `test_fake_llm_fixture_is_shared` (`LLMGateway(fake_llm, AtlasConfig())` works with no lake, key, clock or network), `test_llm_fake.py` (scripts keyed by agent / step), and the whole default suite of `tests/unit/llm` (198 tests) runs offline: FakeLLM, respx and a fake SDK client only
- Brief In-scope items (each checked against code and tests):
  - [x] `LLMGateway.complete(...) -> LLMResult{content, parsed, usage, cost_usd, model, latency_ms, cache_hit}` — `test_llm_gateway.py`
  - [x] Providers: `OpenAICompatibleProvider` (default, DeepSeek), `AnthropicProvider` (optional, official SDK), `FakeLLM` — `test_llm_openai_compat.py` (respx), `test_llm_anthropic.py` (fake client), `test_llm_fake.py`, `test_llm_build.py`
  - [x] Config for the new default and the `DEEPSEEK_API_KEY` secret — `test_config.py` (`test_llm_*`, `test_repo_yaml_llm_equals_defaults`, `test_deepseek_key_from_env_and_dotenv`, `test_env_example_lists_exactly_the_secrets`)
  - [x] Structured output with one repair retry, then `LLMError` — `test_llm_structured.py`
  - [x] Retries with exponential backoff on rate-limit / 5xx; timeout per call — `test_llm_retry.py`, provider tests (`test_base_url_trailing_slash_and_timeout`)
  - [x] Price table in config with time windows; prices checked on DeepSeek's page and with the `claude-api` skill; `CostLedger` per run / day / agent in DuckDB `llm_usage` — `test_llm_default_prices_are_the_checked_ones`, `test_llm_cost.py`, `test_llm_ledger.py`
  - [x] Budgets `daily_usd`, `per_run_usd`, `per_agent_call_max_tokens` → `BudgetExceeded` — `test_llm_budget.py`
  - [x] Prompt-injection hygiene — `test_llm_hygiene.py`, `test_every_call_carries_the_data_policy_once`; live test (the instruction planted in the data block was ignored)
- Waivers: none
- Merge into `main`: fast-forward 862ff46 → **47767b6** (2026-11-10, run in the owner's Terminal panel: the sandbox cannot write the main checkout). Merged result: `uv run pytest -q` → 4037 passed, 31 deselected, exit 0 · `make check` → exit 0 (lint, format, mypy clean, 4037 passed) · raw: [merged-main-pytest.txt](artifacts/merged-main-pytest.txt), [merged-main-check.txt](artifacts/merged-main-check.txt), [merged-main-merge.txt](artifacts/merged-main-merge.txt). Not pushed to `origin` yet (owner asked first)

**Result:** ALL CRITERIA VERIFIED
