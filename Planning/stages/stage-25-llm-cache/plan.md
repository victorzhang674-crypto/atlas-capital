# Stage 25 — LLM record/replay cache · Plan

- **Brief:** [brief.md](brief.md) · **Spec:** [00-design-spec.md](../../00-design-spec.md) §3.3 (principle 3: determinism and replay), §11
- **Status:** DONE 2026-11-14 (see [verification.md](verification.md))
- **Gate check:** Stage 24 is DONE (its `verification.md`: ALL CRITERIA VERIFIED); `uv run pytest -q` → 4244 passed, 71 deselected, exit 0 (2026-11-14) · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
- **Execution:** written and executed in one go, inline TDD, no subagents (kick-off: "plan, implement, execute and audit stage 25"; the owner's standing preference: plan.md first, then execute, no review pause). `worklog.md` is the ledger.

**Goal:** identical LLM requests return stored responses (cost 0, recorded as a hit), and replay mode forbids new calls, so LLM-driven backtests are reproducible and cheap.

**Architecture:** `atlas.llm.cache` holds the key function, `CacheEntry`, `LLMCache` (DuckDB table `llm_cache` in the lake's `atlas.duckdb`, or in memory without a lake, like `CostLedger`) and `CacheMode`. The gateway looks the request up after it is fully resolved (tier model, data-policy system text, schema, tools, temperature, thinking) and before the budget check; a hit returns the stored `LLMResult` with cost 0 and writes a `cache_hit = true` row to `llm_usage`. Only successful, validated results are stored.

## Global constraints
- No real network or LLM call in unit tests (FakeLLM only). No `datetime.now()`: cache timestamps come from the gateway's injected clock; the CLI uses `LiveClock`.
- Nothing hard-coded: the mode default lives in `config/fund.yaml` (`llm.cache.mode`).
- Fail safe: a replay miss raises `CacheMiss` (an `LLMError`: callers already holding on `LLMError` hold on it); a corrupt stored entry raises `LLMError`, never a guess.
- Existing 198 gateway tests keep their meaning: a gateway built directly with no `cache` argument never caches.

## Collisions with the overall plan
None found (roadmap row 25: S, depends on 24; spec principle 3 and §3.3 match the brief). Readings and decisions, also logged in PROGRESS.md › Decisions log:

- **D1 Key = SHA-256 of canonical JSON** (`atlas.core.audit.canonical_json`, sorted keys) of: provider name, model, messages (as sent, i.e. after the data-policy text), schema name + **schema fingerprint** (SHA-256 of the schema's JSON Schema, so a changed field changes the key without anyone bumping a number: this is the brief's "schema name+version"), temperature, tools, thinking mode (from the Stage 24 note), and `prompt_version` = `metadata["prompt_version"]` ("" when absent: the brief's "prompt template version"; Stage 26's prompt registry will set it). `agent`, `step`, `run_id` and `max_tokens` are not in the key: they label or cap a call, they do not change what the model is asked, so two agents asking the same question share an answer and a config change of the token cap does not void a recorded backtest. **Corrected by the post-DONE audit (A2):** `max_tokens` is sent to the provider and an answer cut at one cap is not the answer to another, so it is in the key since `KEY_VERSION` 2; the temperature is hashed as a float (A3).
- **D2 The cache wraps the whole call, not each provider response.** It stores the final validated answer (after any repair retry) with the summed usage and cost of the call; a hit costs 0 and reports zero tokens (nothing was billed), `cache_hit = True`, `attempts = 0` (so `LLMResult.attempts` becomes `ge=0`: 0 = no provider response).
- **D3 Modes:** `off` (no lookup, no store), `read_write` (hit → return; miss → call and store, first writer wins), `replay_only` (hit → return; miss → `CacheMiss`, no provider call), `record` (always call, store, replacing an older entry). Hits skip the budget check: they spend nothing.
- **D4 Config and wiring:** `llm.cache.mode` defaults to `read_write` (brief: default dev/backtest) and to `off` in `config/profiles/paper.yaml` (live data must never be answered from a recording). `build_gateway(config, lake=...)` builds the cache (DuckDB-backed with a lake, in memory without) and uses the configured mode; `LLMGateway(..., cache=, cache_mode=)` takes both explicitly. A mode other than `off` with no cache raises `ConfigError`.
- **D5 Storage:** native DuckDB table `llm_cache(key PK, created_at, provider, model, request_json, response_json, usage)` in `atlas.duckdb` (reserved name, like `llm_usage`; the spec's `llm_cache/` folder is not used: the brief names a table). `usage` is JSON (tokens and `cost_usd` of the original call, so stats can say what a hit saved). **Compressed blobs are not built** (the brief says "optional"; JSON text is enough at this size) → Backlog.
- **D6 Stats:** `LLMCache.stats()` (entries, stored bytes, entries per model, oldest/newest) plus `CostLedger.cache_counts()` (hits, paid first attempts) → hit rate. The CLI prints both.
- **D7 CLI:** `atlas llm cache stats|export|prune` on `--store` (default `data.lake_path`); `prune --older-than 30d|12h|YYYY-MM-DD` (+ `--dry-run`); `export OUT.jsonl` writes one JSON line per entry.
- **Docs note** (brief): `docs/llm-cache.md` says replay is valid only while prompts and data are unchanged.

## File map
| File | Responsibility |
|---|---|
| `src/atlas/core/errors.py` | + `CacheMiss(LLMError)` (`key`) |
| `src/atlas/core/config.py`, `config/fund.yaml`, `config/profiles/paper.yaml` | `LLMCacheConfig`, `llm.cache.mode` |
| `src/atlas/core/storage/db.py`, `schema.py` | native `llm_cache` table, `put_llm_cache`, `prune_llm_cache`, reserved name |
| `src/atlas/llm/cache.py` | `CacheMode`, `cache_key`, `CacheEntry`, `CacheStats`, `LLMCache` |
| `src/atlas/llm/cost.py` | `CostLedger.cache_counts()` |
| `src/atlas/llm/gateway.py`, `schemas.py` | lookup/store around `_run`, hit ledger row, `attempts ge=0` |
| `src/atlas/cli/llm_cmd.py`, `app.py` | `atlas llm cache …` |
| `docs/llm-cache.md` | the replay-validity note |
| `tests/unit/llm/test_llm_cache_*.py`, `tests/unit/cli/test_llm_cmd.py`, storage/config/error tests | mirror the modules |

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

### Task 1 — Error, config, storage table
- [x] Tests: `CacheMiss` is an `LLMError` with `key`; `LLMCacheConfig` default/validation/paper profile; `llm_cache` table exists empty, `put_llm_cache` (ignore vs replace), `prune_llm_cache`, reserved name
- [x] Red → implement → green

### Task 2 — Key canonicalisation and `LLMCache`
- [x] Tests: dict order irrelevant (tool parameters, metadata not keyed); a `prompt_version` bump, model, provider, temperature, thinking, tools, schema field change and message change each change the key; agent/step/max_tokens do not; stable literal digest
- [x] Tests: put/get round trip (memory and DuckDB), first-writer-wins vs replace, stats, export lines, prune by cutoff
- [x] Red → implement → green

### Task 3 — Gateway modes and ledger
- [x] Tests: `read_write` twice → 1 provider call (acceptance), hit cost 0 / `cache_hit` / zero usage / `attempts` 0, hit row in `llm_usage`; `replay_only` miss raises `CacheMiss` with no call, `replay_only` after a recording hits; `record` always calls and replaces; `off` and no cache never cache; structured hit returns `parsed`; tool calls survive; failures and invalid output are never stored; repair-retry call stores the final answer once; corrupt entry → `LLMError`; a hit ignores an exhausted budget; mode without cache → `ConfigError`; `build_gateway` wiring; `CostLedger.cache_counts`
- [x] Red → implement → green

### Task 4 — CLI and docs
- [x] Tests: `atlas llm cache stats|export|prune` (counts, dry run, bad `--older-than` exit 2, missing lake exit 1, help)
- [x] Red → implement → green; write `docs/llm-cache.md`

### Task 5 — Verify and record
- [x] `uv run pytest tests/unit/llm -q`, `make check`, coverage and a mutation run of `src/atlas/llm/cache.py`; `verification.md`, `PROGRESS.md`, commit, merge, merged-`main` checks

## Review focus (failure modes the brief implies but does not list; each has a test in its task)
1. A key that ignores something the model sees (tools, schema, thinking) would replay a wrong answer (Task 2).
2. A replay that quietly calls the provider on a miss would break "replay forbids new calls" (Task 3).
3. A hit that spends budget or lacks a ledger row would break "cost 0, recorded as a hit" (Task 3).
4. An unvalidated or failed answer stored in the cache would be replayed for ever (Task 3).
