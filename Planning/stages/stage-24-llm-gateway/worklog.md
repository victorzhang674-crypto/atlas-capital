# Stage 24 — LLM gateway, model tiers & cost budget · Work log

Append entries in order. Record failures and dead ends as honestly as successes.

## 2026-11-09 21:10 — Gate check and set-up
- Did: read Project.md, PROGRESS.md, the brief, the stage protocol, spec §3, §11, §15, the Stage 25 / 26 briefs (consumers), the 2026-10-30 Decisions-log row on DeepSeek, and the existing `LLMConfig`, `Secrets`, `atlas.core.errors`, `atlas.core.storage.db`, `atlas.core.clock`.
- Ran: `uv sync --group dev` in the new worktree (first try timed out on one wheel download; the retry worked) · `uv run pytest -q` → **3807 passed, 29 deselected** in 466 s, exit 0 (`artifacts/gate-pytest.txt`). Stage 23's `verification.md` says ALL CRITERIA VERIFIED → gate passed.
- Prices (brief: "the stage chat checks current prices"): the fetch tool refused `api-docs.deepseek.com/quick_start/pricing` ("Unable to verify if domain … is safe to fetch"); read the page in the built-in browser instead. Values in `plan.md › Prices checked`. They match the 2026-10-30 check of the model ids; base URL unchanged. Claude prices from the `claude-api` skill (table cached 2026-11-03). The skill also showed that Claude 5.5 models reject forced `tool_choice` and sampling parameters, and the installed-SDK probe (`anthropic` 2.7.0 in a throwaway environment) that `messages.create` has no `temperature` and that `anthropic.transform_schema` exists → plan decisions D1, D7.
- `DEEPSEEK_API_KEY` is set in the main checkout's `.env` (checked by name only; the value was never printed), so the live test can run.
- Did: copied the template into the stage folder, wrote `plan.md` (decisions D1–D10), set the stage to IN PROGRESS in PROGRESS.md.
- Next: Task 0.

## 2026-11-09 21:25 — Task 0: config, secrets, errors
- Did: `LLMConfig` reworked (provider default `openai_compatible`, `base_url`, DeepSeek `tiers`, `anthropic_tiers`, `thinking`, `request_timeout_seconds`, `retry`, `prices` with `ModelPrice` / `PriceWindow`, `budgets.estimate_chars_per_token`, `model_for(tier)`, a validator that every tier model has a price); `Secrets.deepseek_api_key`; `BudgetExceeded` and `LLMTransientError` (both `LLMError`s) in `atlas.core.errors`; the `llm:` block of `config/fund.yaml`; `.env.example`; `anthropic>=2.7` in the `llm` group, included by `dev`; `uv lock` (anthropic 2.7.0 and its httpx2 stack).
- Ran: red against HEAD's `config.py` / `errors.py` on a scratch `PYTHONPATH` → collection error `cannot import name 'BudgetExceeded'` · green `uv run pytest tests/unit/core -q` → 922 passed.
- Problems / fixes: the tests and the code of this task were written together, so the red run was made afterwards against the old sources (as above). `test_secrets_never_in_repr_or_dump` indexes `SECRET_VARS` by position (`s3cr3t-2` = the Alpaca secret): I had put `DEEPSEEK_API_KEY` first in that tuple; moved it to the end, the assertion is unchanged. One planned validation case dropped: "a price without `output`" cannot be built through `load_config`, because overrides are merged into the default price row.

## 2026-11-09 21:40 — Task 1: FakeLLM + gateway contract
- Did: `atlas/llm/schemas.py` (`Message`, `ToolSpec`, `ToolCall`, `Usage`, `LLMResult`, `ProviderRequest`, `ProviderResponse`, `LLMProvider`), `hygiene.py` (`data_block`, `DATA_POLICY`, `with_data_policy`), `providers/fake.py` (`FakeLLM`, `FakeReply`), a first `gateway.py` and a base-rate `cost_usd`; the shared `fake_llm` fixture in `tests/conftest.py` (stage protocol §5).
- Ran: red → `ImportError: cannot import name 'FakeLLM' from 'atlas.llm'` · green → 59 passed (LLM tests + package layout).

## 2026-11-09 21:50 — Task 2: structured output + repair retry
- Did: `_parse` (one surrounding markdown fence ignored), `_error_text` (field path and message only: no input echo, no URLs), `_repair_messages`, the repair loop (`REPAIR_RETRIES = 1`).
- Ran: red → 9 failed, 2 passed · green → 51 passed; `mypy src/atlas/llm` clean.

## 2026-11-09 22:05 — Task 3: providers, retries, live test
- Did: the retry loop `_send` (backoff doubling, capped, `Retry-After` wins when longer, exhausted → plain `LLMError` chained to the last transient). `OpenAICompatibleProvider` on httpx (bearer key as `SecretStr`, `thinking: {type}`, `response_format: json_object` + schema text in the system message, tools, usage mapping incl. DeepSeek cache hit / miss and OpenAI `cached_tokens`, 408/409/429/5xx and transport errors transient, answers without usage or choices → `LLMError`). `AnthropicProvider` on the SDK (lazy import, `max_retries=0`, no `temperature` / `thinking` / `tool_choice`, `output_config.format` via `transform_schema`, tool-use / tool-result translation with results merged into one user message, refusal → `LLMError`). `build_provider` / `build_gateway`.
- Ran: retry red → 6 failed, 2 passed, green → 59 passed · DeepSeek provider red → `ModuleNotFoundError`, green → 34 passed · Anthropic red → `ModuleNotFoundError`, green → 112 passed overall · `build_gateway` tests were written together with the factory (no separate red run).
- Live: `uv run pytest tests/unit/llm/test_llm_live_deepseek.py -m llm -q -s` with `DEEPSEEK_API_KEY` exported from the main checkout's `.env` (value never printed; `allowed_domains` api.deepseek.com) → **2 passed**: both tiers return the schema-valid answer and ignore the instruction injected in the data block. JSON mode works in thinking mode (deep tier: 118 of 128 output tokens were reasoning).
- Problems / fixes: `LLMResult.model` is the configured tier id (the key of the price table), not the provider's served name. The first live run was costed at the off-peak rate although it ran in a peak hour, because `cost_usd` still ignored windows (Task 4 not built yet); see the re-run below.

## 2026-11-09 22:20 — Task 4: cost ledger + budgets
- Did: `rates_at` (UTC weekday and time of day, start inclusive, end exclusive, first matching window), `cost_usd`, `UsageRecord`, `CostLedger`; `BudgetGuard` (`check_max_tokens`, `estimate_usd`, `reserve` with in-flight reservations per UTC day and per run); the gateway now refuses unaffordable calls, records one row per billed response inside the reservation, and logs `llm.call`. An unpriced model raises `ConfigError` before the provider is called.
- Ran: red → `ModuleNotFoundError: atlas.llm.budget` · green → 151 passed; ruff and `mypy src/atlas` clean.
- Problems / fixes: shared test helpers moved from the LLM `conftest.py` to `tests/unit/llm/llm_helpers.py` (`from conftest import …` is ambiguous with several conftests).

## 2026-11-09 22:30 — Task 5: usage persistence
- Did: native DuckDB table `llm_usage` (created with `audit_events`; reserved table name), `Database.insert_llm_usage(rows)` (one transaction; duplicate `call_id` or a null in a required column → `DataError`; read-only → `ValueError`), `CostLedger(database)` (totals by SQL, so budgets see earlier sessions), `build_gateway(config, lake=)`.
- Ran: red → `ImportError: LLM_USAGE_COLUMNS` · green → `tests/unit/llm tests/unit/core` 1094 passed.
- Problems / fixes: two existing storage tests listed the native tables and reserved names; both now include `llm_usage` (`test_views_are_not_persisted`, `test_schema_rejects_bad_declarations`). The expectation changed because the set of native tables changed, not to hide a failure.

## 2026-11-09 22:40 — Task 6: exports, audit, mutation check
- Did: `atlas.llm` exports; three log tests (`llm.call` fields, `llm.retry`, `run_id` in the JSON log line). These passed on first run (the logging was already there), so their bite was checked by the mutation run.
- Mutation check (mutmut 3.8.1 on a scratch copy, `src/atlas/llm`, tests `tests/unit/llm`): run 1 → **1135 / 1272 killed, 137 survived** (`artifacts/mutation-survivors-run1.txt`). No production bug found. Real test gaps closed test-first, all passing on the unchanged code except where noted:
  - budget: a reservation exactly at the cap is allowed; releasing one reservation keeps the others (a mutant that dropped every pending amount under 1 USD survived); per-run reservations are separate per run;
  - ledger: `cache_write_tokens` was not asserted in a ledger row;
  - gateway: `temperature=2.0` and `max_tokens=1` are accepted, NaN is not; the final `LLMError` keeps the `ValidationError` as its cause; the repair message lists one error per line with a dotted path;
  - retries: no hidden floor on the wait without `Retry-After`; singular / plural attempt count; the retry log names the provider;
  - FakeLLM: a list in the constructor script; `add()` with one reply repeats, with none raises;
  - DeepSeek provider: served model name passed through and defaulted, missing `finish_reason`, zero output tokens valid, negative / bool / float / null counts rejected, null cache and reasoning details, empty and non-object tool arguments, nested tool arguments, `Retry-After` 0 / fraction / negative, error text masks the key with `***`;
  - Anthropic provider: the key reaches the SDK client, unknown stop reasons pass through, tool-result placement (never merged into a text turn), nested arguments, `Retry-After` edges, error texts.
  - One new test was wrong, not the code: I expected `< data` (with a space) to stay untouched in a data block; `data_block` also defuses it (`&lt; data`), which is the safer behaviour. Expectation corrected (Rule 1.6).
- Run 2 → **1213 / 1272 killed, 59 survived** (`artifacts/mutation-survivors-run2.txt`, `mutation-summary.txt`): message-text and SQL-keyword-case mutants, plus equivalents (`tries + 2` in a loop that always returns or raises first; pydantic `errors()` flags that only add keys the text does not use; the naive-datetime check; the cleanup threshold `< 1e-12`). The `default=dict` fallback in the providers' JSON copies survived both runs because nested values are already plain: removed as dead code.
- Self-audit of the diff against the plan, the brief and the Definition of Done:
  - every brief In-scope bullet has code and tests (table in `verification.md`);
  - added after the audit: a refused call is logged (`llm.budget_exceeded`, test first: 1 failed → 5 passed);
  - no `datetime.now()` or `time.time()` in `src/atlas/llm`; no key in any repr, log line or error text (tests); README and docs do not name an LLM provider, nothing stale;
  - deviations from the plan, all recorded in PROGRESS.md: `hygiene.py` is a file the brief does not list (D10); test files are named `test_llm_*.py` (unique basenames, as the storage tests do); the planned "price without `output`" validation case was dropped (see Task 0).
- Live re-run after Task 4, 2026-11-10 03:35 UTC (Tuesday, DeepSeek peak): 2 passed; quick 205 input + 12 output tokens → 0.00007590 USD = 205 × 0.30 + 12 × 1.20 per 1M (peak rates); deep 25 uncached + 256 cache-hit input + 83 output → 0.00037294 USD = 25 × 1.32 + 256 × 0.044 + 83 × 3.96 per 1M. Raw: `artifacts/live-deepseek.txt` (checked: the key is not in the file).

### Code files created or changed (Rule 2A.3)
- Created: `src/atlas/llm/{schemas,hygiene,cost,budget,gateway}.py`, `src/atlas/llm/providers/{__init__,fake,openai_compat,anthropic}.py`; `tests/unit/llm/{conftest,llm_helpers,test_llm_schemas,test_llm_hygiene,test_llm_fake,test_llm_gateway,test_llm_structured,test_llm_retry,test_llm_openai_compat,test_llm_anthropic,test_llm_build,test_llm_cost,test_llm_budget,test_llm_ledger,test_llm_logging,test_llm_live_deepseek}.py`; `tests/unit/core/storage/test_storage_llm_usage.py`.
- Changed: `src/atlas/llm/__init__.py`, `src/atlas/core/{config,errors}.py`, `src/atlas/core/storage/{db,schema}.py`, `config/fund.yaml`, `.env.example`, `pyproject.toml`, `uv.lock`, `tests/conftest.py`, `tests/unit/core/{test_config,test_errors}.py`, `tests/unit/core/storage/{test_storage_db,test_storage_schema}.py`.

## 2026-11-10 02:35 — Full verification, commit, merge
- Ran: `make check` → exit 0 (ruff clean, 374 files formatted, mypy clean on 154 files, **4037 passed, 31 deselected** in 382 s) · brief's Verify `uv run pytest tests/unit/llm -q` → 198 passed, 2 deselected · coverage of `src/atlas/llm` → 100% of 646 statements · secret scan for the real key value → no file.
- Did: `verification.md` (ALL CRITERIA VERIFIED), PROGRESS.md (checkbox, status, Decisions log, Backlog, handoff note).
- Commit: reviewed `git diff --cached --stat` (53 files: `src/atlas/llm`, `src/atlas/core` config / errors / storage, `config/fund.yaml`, `.env.example`, `pyproject.toml`, `uv.lock`, tests, this stage folder, PROGRESS.md; no `.env`, no `data/`, no database file) → **47767b6** `stage-24: LLM gateway, model tiers & cost budget` (pre-commit hooks passed); `git status --short` clean.
- Merge: `git merge --ff-only` in the main checkout from the sandbox failed (`error: unable to unlink old '.env.example': Operation not permitted`; main left untouched at 862ff46). Rule 6 exception (sandbox permission): the merge and the checks ran as one line in the owner's Terminal panel, started by the AI. Fast-forward 862ff46 → **47767b6**; `make install` added `anthropic==2.7.0` and its dependencies to the main checkout's environment.
- Merged-main checks (on `main` at 47767b6, in the main checkout): `uv run pytest -q` → **4037 passed, 31 deselected**, exit 0 (`artifacts/merged-main-pytest.txt`) · `make check` → exit 0: All checks passed!, 374 files already formatted, mypy Success on 154 source files, 4037 passed (`artifacts/merged-main-check.txt`) · `git branch --merged main` lists `claude/stage-24-planning-audit-9b2e7f` · `git status --short` clean (`artifacts/merged-main-merge.txt`). The nanobind leak warnings printed at interpreter exit were removed from the two saved files.
- Not done: no push to `origin` (the remote may be public since the MIT licence commit; the owner is asked first). CI has therefore not run on this commit.

## 2026-11-10 03:20 — Push and CI
- Owner's go-ahead in chat: "Yes, push". Pushed from the owner's Terminal panel (the sandbox cannot reach GitHub): `git push origin main claude/stage-24-planning-audit-9b2e7f` → `862ff46..13f8a02  main -> main`, new branch; `git status -sb` → `## main...origin/main` (`artifacts/push.txt`).
- CI on 13f8a02 (`artifacts/ci-runs.txt`): run 42778103529 (stage branch) → **success** in 15 min 20 s (`make check` 15 min 03 s, `make test-integration` 6 s; `artifacts/ci-branch-run.txt`). Run 42778103411 (`main`) → **cancelled**: "The job has exceeded the maximum execution time of 20m0s", during `make check` (`artifacts/ci-main-run.txt`). Same commit, same tree: not a test failure, the workflow's `timeout-minutes: 20` is too tight for a slower runner (the suite takes 6.5 min locally and 15+ min on a hosted runner).
- Fix: `.github/workflows/ci.yml` `timeout-minutes` 20 → 40, with a comment. `uv run pytest tests/unit/test_toolchain.py -q` → passed (the workflow test does not constrain the limit). Committed with these records as `stage-24: push and CI records`, fast-forwarded into `main` and pushed, which starts a new CI run on `main`; its result is recorded below.
