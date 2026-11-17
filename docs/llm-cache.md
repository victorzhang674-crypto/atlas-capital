# LLM record/replay cache

Stage 25, `atlas.llm.cache`. Identical LLM requests get the stored answer, so an LLM-driven
backtest is cheap to re-run and gives identical results (design spec §3.3, principle 3).

## Modes (`llm.cache.mode` in `config/fund.yaml`)

| Mode | A hit | A miss |
|---|---|---|
| `off` | not looked up | provider is called, nothing stored (the `paper` profile) |
| `read_write` (default) | stored answer, cost 0 | provider is called, answer stored (first writer wins) |
| `replay_only` | stored answer, cost 0 | `CacheMiss` (an `LLMError`); no provider is ever called |
| `record` | the first call of a key in a gateway is re-recorded (an unreadable recording too); later identical calls of that gateway replay it | provider is called, answer stored, replacing an older one |

A hit spends nothing: no budget is checked, the result has `cache_hit = True`, `cost_usd = 0`,
empty `usage` and `attempts = 0`, and `llm_usage` gets a `cache_hit = true` row with cost 0.
Only answers that passed validation are stored; failures never are.

## What is in the key

SHA-256 of the canonical JSON of: provider and its endpoint (the OpenAI-compatible base URL, so two vendors never share recordings), model, the messages as sent (after the data-policy
system text), the schema's name and a fingerprint of its JSON Schema, temperature (as a
float), the output token cap, tools, thinking mode, and the prompt template version
(`metadata["prompt_version"]`). `agent`, `step` and the timeout are not part of it, so a
change of `llm.budgets.per_agent_call_max_tokens` is a miss (an answer cut at one cap is not
the answer to another). The key is built by
`atlas.llm.cache.cache_key`; its pinned digest in `tests/unit/llm/test_llm_cache_key.py` guards
against changing it by accident.

## Replay is only valid while prompts and data are unchanged

The key covers the exact messages, so changed data or a changed prompt text is a miss. A change
the messages cannot show (a different tool implementation behind the same tool name, new agent
logic that reads the answer differently) is **not** visible to the key: bump the prompt template
version (`prompt_version`) when you change a template, and re-record. A `replay_only` backtest
whose prompts or data moved fails with `CacheMiss` instead of quietly calling a model.

## Replays give the recorded results

Calls with the same key are serialised inside one gateway (one lock per key: different requests
never wait for each other), so two threads that miss the same key make one provider call and
both get the stored answer; in `record` mode a gateway re-records each key once and replays it
afterwards. New answers are stored with a compare-and-set: when two gateways share a cache and
another one stored the key first, this call (already paid) returns the stored answer instead of
its own. A run therefore sees exactly the answers a later replay of it will see, even when the
model is not deterministic (spec §3, principle 3). Two processes cannot share a lake at all:
DuckDB lets one process open `atlas.duckdb` at a time.

## Storage and commands

Entries live in the native DuckDB table `llm_cache` of the lake's `atlas.duckdb` (`key`,
`created_at`, `provider`, `model`, `request_json`, `response_json`, `usage`). The cost of a hit
is not in `llm_usage`: the cost of what it saved is in the entry's `usage` JSON. Request and
response bodies of at least `llm.cache.compress_min_bytes` (4096) UTF-8 bytes are stored
zlib-compressed (`zlib:` + base64); every reader unpacks them, and `stats` reports stored bytes.

```bash
atlas llm cache stats                      # entries, size, per model, hit rate
atlas llm cache export cache.jsonl         # one JSON line per entry
atlas llm cache import cache.jsonl         # store an export (checked line by line); --replace
atlas llm cache prune --older-than 30d     # or 12h, or 2026-11-01; add --dry-run to count
```

## Recordings as files: fixtures and replays without a key

A recording can leave the lake it was made in. `atlas llm cache export FILE` writes one JSON
line per entry and `atlas llm cache import FILE [--store LAKE] [--replace]` stores such a file
in a lake's cache (creating the cache if the lake has none). In code: `write_export(entries,
path)`, `read_export(path)` and `LLMCache.load(entries, replace=False)`. Reading checks every
line: its key must be the SHA-256 of its request, the answer must be readable, a key may
appear once. A file with one bad line is refused as a whole (`LLMError` naming the line), so
an edited request can never stand in for a recording. An export of the same entries is the
same bytes (sorted keys), so a re-recorded fixture shows a clean diff.

A replay needs no API key. With `llm.cache.mode: replay_only` and no key in the environment,
`build_gateway` puts an `OfflineProvider` behind the gateway: it carries the configured
provider's name and endpoint (so the keys of earlier recordings match) and raises `LLMError`
if it is ever asked to call a model. Every other mode still needs the key.

A test that replays a recorded live run:

```python
cache = LLMCache()
cache.load(read_export(FIXTURES / "fundamental_aapl_2024-05-01.jsonl"))
provider = OfflineProvider("openai_compatible", "https://api.deepseek.com")
gateway = LLMGateway(provider, config, cache=cache, cache_mode="replay_only")
```

The key covers the whole conversation, tool results included: the replay must give the agent
the same data the recording run read, or it is a `CacheMiss`.
