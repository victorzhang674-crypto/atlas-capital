# LLM gateway

Stage 24, `atlas.llm`. Every model call in Atlas goes through one `LLMGateway`: it picks the
tier's model, adds the data policy to the system text, refuses a call that could break a
budget, retries transient failures, validates structured output, costs each response and
writes it to the usage ledger. Agents never talk to a vendor SDK themselves (design spec §3,
§11). The record/replay cache in front of the provider has its own page,
[llm-cache.md](llm-cache.md); building agents on top of the gateway is in
[agents.md](agents.md).

## Tiers and providers

A call asks for a tier, not a model:

| Tier | Used for | `openai_compatible` (default) | `anthropic` (optional) |
|---|---|---|---|
| `quick` | cheap, fast calls: extraction, summaries, most analysts | `deepseek-flash` | `claude-haiku-5-5` |
| `deep` | long-form reasoning: research, debate, IC | `deepseek-v4-pro` | `claude-opus-5-5` |

`provider` picks the client. `openai_compatible` posts one chat-completions request to
`base_url` (DeepSeek by default, spec A2) over httpx and takes the models from `tiers`;
`anthropic` uses the official SDK (the `llm` dependency group, imported only when chosen) and
`anthropic_tiers`. The key comes from the environment or `.env`: `DEEPSEEK_API_KEY` for
`openai_compatible`, `ANTHROPIC_API_KEY` for `anthropic`. A missing key is a `ConfigError`
naming the variable, except in cache mode `replay_only`, which needs no key (below).

`thinking` sets DeepSeek's reasoning mode per tier (`quick: disabled`, `deep: enabled`;
`omit` sends nothing, for a server without the field). The Anthropic provider ignores it:
current Claude models always reason, and they also reject sampling parameters, so a Claude
call carries no `temperature`, `thinking` or forced tool choice.

Structured output differs by provider, not by caller. DeepSeek has no `json_schema` format,
so the JSON Schema is written into the system text and the request asks for
`response_format: json_object`; Claude gets the schema through `output_config.format`.
Either way the gateway validates the answer itself.

```python
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.llm import Message, build_gateway, data_block

config = load_config("dev")
gateway = build_gateway(config, lake=Lake(config.data.lake_path))  # ledger + cache in atlas.duckdb
result = gateway.complete(
    [Message.system("You extract facts."), Message.user(data_block(text, source="news:123"))],
    tier="quick",
    schema=Facts,  # a Pydantic model: result.parsed is a Facts
    metadata={"agent": "news_analyst", "step": "extract"},
)
result.parsed, result.cost_usd, result.usage, result.cache_hit
```

Without `lake`, the ledger and the cache are in memory and are gone with the process.

## A call

`LLMGateway.complete(messages, *, tier, schema, tools, temperature, max_tokens, metadata)`:

1. **Resolve.** The tier's model and its price (a model without a price in `prices` is a
   `ConfigError`; nothing is sent). `max_tokens` defaults to, and may not exceed,
   `per_agent_call_max_tokens`. `temperature` is in [0, 2] (default 0) and is a hint.
2. **Data policy.** `DATA_POLICY` is appended to the leading system message, or added as one.
3. **Cache.** With a cache, a recorded answer is returned at cost 0 (see
   [llm-cache.md](llm-cache.md)); `replay_only` raises `CacheMiss` on a miss.
4. **Budget.** The worst-case cost is reserved against the day and the run (below).
5. **Send.** One provider attempt, retried on transient failures (below).
6. **Validate.** With `schema`, the answer must be JSON of that Pydantic model (one
   surrounding markdown fence is tolerated). After a failure the model is asked once more
   with the validation error shown (one repair retry); still invalid is an `LLMError`. An
   answer with tool calls is not validated: the agent's tool loop runs the tools and asks
   again.
7. **Cost and record.** Each billed response becomes one `llm_usage` row; a repair retry is a
   second row with `attempt = 2`, and a failed one has `status = invalid_output` (it was billed
   all the same).

The result (`LLMResult`) holds `content`, `parsed`, `tool_calls`, `finish_reason`, the summed
`usage` and `cost_usd` of all billed responses, `attempts` (0 for a cache hit, 2 after a
repair) and `latency_ms`, retry waits included.

`metadata` labels the call: `agent` and `step` go to the ledger and key `FakeLLM` scripts;
`BaseAgent` also sends `prompt_version`, which is part of the cache key. Labels are not sent to
the vendor.

**Errors.** Everything the gateway raises for a failed call is an `LLMError`
(`atlas.core.errors`): `BudgetExceeded`, `CacheMiss`, and a final failure after the retries.
`LLMTransientError` (also an `LLMError`) is raised by a provider for one failed attempt and
only reaches a caller when the gateway turns it into a plain `LLMError` after the last retry.
Callers treat every `LLMError` the same way: fail safe, no new research, no new risk.

## Budgets

Hard caps in USD (spec A8), checked before anything is sent, under `budgets`:

| Key | Default | What it caps |
|---|---|---|
| `daily_usd` | 5.0 (`dev` profile: 1.0) | spend per UTC calendar day of the wall clock |
| `per_run_usd` | 25.0 | spend per `run_id`; calls outside a run share one bucket |
| `per_agent_call_max_tokens` | 8000 | output tokens of one provider response, reasoning included |
| `estimate_chars_per_token` | 3.0 | prompt characters per token in the pre-call estimate (lower = more cautious) |

Before each attempt the gateway estimates the call's worst case: `max_tokens` output tokens
plus the prompt (its characters divided by `estimate_chars_per_token`, rounded up) at the
uncached input rate. The call is refused with `BudgetExceeded` (`scope` `daily_usd` or
`per_run_usd`) when spend so far + reservations of calls in flight + this estimate would
exceed a cap, so parallel calls cannot overshoot together. The reservation is released once
the real cost is in the ledger. A `max_tokens` above `per_agent_call_max_tokens` is refused
with scope `per_agent_call_max_tokens`. Cache hits are free and check no budget.

`per_run_usd` and `per_agent_call_max_tokens` are provisional: they are revisited with the
first real agent prompts (Stage 29) and the first LLM backtest (Stage 56).

## Prices and the usage ledger

`prices` maps each model id to USD per 1M tokens: `input` (uncached prompt), `cached_input`
(prompt read from the vendor's cache), `cache_write` (prompt written to it; `null` = charged
as input) and `output` (generated tokens, reasoning included). `windows` replace the base
rates during UTC hours on given weekdays: DeepSeek's base rates are off-peak, and its peak
hours (01:00–04:00 and 06:00–10:00 UTC, Monday to Friday) cost double. The table in
`config/fund.yaml` holds `deepseek-flash`, `deepseek-v4-pro`, `claude-opus-5-5` and
`claude-haiku-5-5`, with the date the prices were checked. Every tier model of the configured
provider must have a price, or the config does not load.

A response is priced at the **wall-clock** UTC instant it was billed, never the simulated
date of a backtest: vendors bill in real time, and the daily budget counts real days.
Chinese public holidays are off-peak at DeepSeek but priced as peak here (never
under-estimated); Claude Haiku's higher rate for prompts above 100K tokens is not modelled.

The ledger (`CostLedger`) is the DuckDB table `llm_usage` in the lake's `atlas.duckdb`: one
row per billed response or cache hit, with `run_id`, `agent`, `step`, tier, provider, model,
the four token counts, `cost_usd`, latency, `cache_hit`, `attempt` and `status`. Read it with:

```bash
atlas llm usage                        # total, then per day, run, agent and provider/model
atlas llm usage --run 01J… --by agent  # one run; --day 2026-11-17 for one UTC day
atlas llm cache stats                  # cache entries and hit rate
```

## Retries and outages

A provider makes exactly one attempt per call and raises `LLMTransientError` for a failure
worth retrying (HTTP 408, 409, 429, a 5xx, a timeout, a dropped connection) and `LLMError`
for the rest. The gateway owns retries, under `retry`:

| Key | Default | Meaning |
|---|---|---|
| `max_retries` | 3 | retries after the first failed attempt |
| `backoff_seconds` | 1.0 | wait before retry 1; doubles on each retry |
| `max_backoff_seconds` | 30.0 | longest wait (a longer `Retry-After` from the server still wins) |
| `outage_failures` | 8 | transient failures in a row that mark the vendor down (0 = off) |
| `outage_cooldown_seconds` | 300 | while marked down, every call fails at once for this long |

The outage guard counts transient failures across all of a gateway's calls (a rate limit
that came with a `Retry-After` does not count). Once the vendor is marked down, every call
that needs the provider raises `LLMError` at once, also in the middle of a retry loop, so a
dead vendor costs minutes rather than hours of backoff; cache hits are still served. After
the cool-down the next call tries again, and one more failure marks the vendor down again.

`request_timeout_seconds` (120) bounds one HTTP request. It is the only time limit on a call:
there is no per-node timeout in the research graph yet (Backlog, Stage 55).

## Untrusted text

News, filings and tool output may contain text written to steer a model. Such text goes into
the prompt only inside a data block:

```python
data_block(article.body, source="news:123")  # <data source="news:123"> … </data>
```

`data_block` neutralises any `<data` or `</data` inside the text, so it cannot close the
block early or open a fake one, and the `source` label may hold only letters, digits and
`_ . : / -`. `DATA_POLICY`, which the gateway adds to the system text of every call, tells
the model that text in data blocks is material to analyse, never instructions. The agent
framework's tools wrap their results this way.

## Testing without a vendor

Unit tests make no network or LLM calls (Project.md Rule 5).

**`FakeLLM`** is a scripted provider keyed by the call's `agent` and `step` metadata:

```python
fake = FakeLLM({("news_analyst", "extract"): Facts(...), "risk_officer": "ok"}, default="{}")
fake.add(
    "pm",
    None,
    FakeReply(tool_calls=(ToolCall(id="1", name="news", arguments={}),)),
    '{"weights": {}}',
)  # consumed in order; more calls than scripted raise LLMError
fake.add("analyst", None, lambda request: answer_for(request))  # a reply from the request
gateway = LLMGateway(fake, config)
```

A reply is text, a Pydantic model or a mapping (sent as JSON), an exception to raise, a
`FakeReply` (tool calls, own usage, finish reason), or a function of the `ProviderRequest`
that returns one of those, so one script can answer each symbol of a parallel fan-out from
its own prompt. One reply repeats for ever; a list is consumed in order. Lookup goes
`(agent, step)`, then `(agent, None)`, then `default`; nothing found is an `LLMError`. Every
request is kept in `fake.calls`. Usage is deterministic (one token per 4 characters), so
costs and budgets are testable.

**`OfflineProvider`** stands in for the configured provider when there is no key and the
cache mode is `replay_only`: it has the real provider's name and endpoint, so recorded cache
keys match, and it raises `LLMError` if it is ever asked to call a model. `build_gateway`
uses it on its own; a test can build one directly (see [llm-cache.md](llm-cache.md)).

**Live tests** are marked `llm` (deselected by default) and skip when the key is missing.
`uv run pytest -m llm` runs them against DeepSeek for a fraction of a cent. The Anthropic
provider has never answered a live call: it needs `ANTHROPIC_API_KEY` in `.env`.

## Configuration

All keys live under `llm` in `config/fund.yaml`; profiles override some of them
(`config/profiles/dev.yaml`: `daily_usd` 1.0; `paper.yaml`: cache `off`, since paper trading
reads live data and a recorded answer must never stand in for a new call).

| Key | Default | Meaning |
|---|---|---|
| `provider` | `openai_compatible` | `openai_compatible` or `anthropic` |
| `base_url` | `https://api.deepseek.com` | OpenAI-compatible API base (`<base_url>/chat/completions`) |
| `tiers` | `deep`, `quick` | model id per tier for `openai_compatible` |
| `anthropic_tiers` | `deep`, `quick` | model id per tier for `anthropic` |
| `thinking` | `quick: disabled`, `deep: enabled` | reasoning mode per tier: `enabled`, `disabled` or `omit` |
| `request_timeout_seconds` | 120 | timeout of one HTTP call, in seconds |
| `retry` | see above | retries and the outage guard |
| `budgets` | see above | spend caps and the output token cap |
| `cache` | `mode: read_write` | record/replay cache: `mode`, `compress_min_bytes` ([llm-cache.md](llm-cache.md)) |
| `prices` | four models | USD per 1M tokens, with UTC `windows` |

Any key can also be set from the environment as `ATLAS__LLM__<KEY>` (for example
`ATLAS__LLM__BUDGETS__DAILY_USD=2`).
