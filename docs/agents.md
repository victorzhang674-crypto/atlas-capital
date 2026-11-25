# Building an agent

Stage 26, `atlas.agents`. Every LLM agent of Phase 4 onwards is built the same way: a role, a
versioned prompt, point-in-time tools, a structured output, an evidence trail and a trace.

## The parts

| Part | Where | What it does |
|---|---|---|
| `AgentContext` | `agents/context.py` | The run's inputs: a `DataView` (the only door to data), its `as_of`, the `run_id`, an optional `AgentMemory` (Stage 27, read with `ctx.recall`) and extra `inputs` |
| `PromptRegistry` | `agents/prompting.py` | Loads Jinja2 templates, checks their version line, renders the `system` and `user` blocks |
| `ToolRegistry`, `Tool` | `agents/tools/registry.py` | Tools that wrap `DataView` methods and return `Evidence` |
| `default_tools()` | `agents/tools/dataview_tools.py` | The built-in tools: `news`, `market_news`, `fundamentals`, `price_history`. An analyst's own tools go in `agents/tools/<name>_tools.py`, import only `registry`, and are joined to these in one `ToolRegistry([*default_tools().tools, *mine])` |
| `check_citations` | `agents/evidence.py` | Checks that an output cites only evidence the run showed the model |
| `TraceStore` | `agents/trace.py` | Persists one `AgentTrace` per run in the `agent_runs` table (or in memory) |
| `BaseAgent[OutT]` | `agents/base.py` | Ties it all together: `run(ctx) -> AgentOutput[OutT]` |

## A minimal agent

```python
class FundamentalAnalyst(BaseAgent[AnalystReport]):
    name = "fundamental_analyst"  # labels LLM calls, traces and audit events
    tier = "quick"  # quick | deep (spec §6)
    prompt_template = "fundamental_analyst.j2"
    output_schema = AnalystReport
    tools = ("fundamentals", "news")  # names in the tool registry

    def gather(self, ctx):  # evidence shown up front (optional)
        return ctx.view.news(ctx.inputs["symbol"], limit=5)

    def prompt_inputs(self, ctx):  # template variables (default: ctx.inputs)
        return {"symbol": ctx.inputs["symbol"]}


agent = FundamentalAnalyst(
    build_gateway(cfg, lake=lake),
    config=cfg,
    traces=TraceStore(get_database(lake)),
    audit=audit_log,
)
out = agent.run(AgentContext(DataView(lake, day), inputs={"symbol": "AAPL"}))
out.output, out.evidence, out.low_quality, out.cost_usd, out.trace
```

In tests use `LLMGateway(FakeLLM(...), AtlasConfig())`: FakeLLM scripts are keyed by the
agent `name` and the step (`round-1`, `round-2`, ..., `wrap_up`, `evidence_repair`). The toy
`EchoAnalyst` in `tests/unit/agents/agent_helpers.py` is a complete example.

A FakeLLM reply may also be a function of the request (`lambda request: ...`, returning any
reply kind). Use it when the same agent is asked about several symbols at once, as in the
research graph's fan-out: the function reads the ticker and the evidence ids from the prompt
and answers that request, whatever order the threads arrive in.
`tests/integration/test_phase3_run.py` runs two agents this way inside the graph, with the
cache, the memory, traces and the audit log.

## Prompt templates

A template is one `.j2` file in `src/atlas/agents/prompts/` whose **first line is
`{# version: N #}`** and which defines `{% block system %}` and `{% block user %}`. Bump N when
you change the template. Shared partials live in `prompts/_partials/` (also versioned):

- `_date_rules.j2`: "today is {{ as_of }}; you know nothing after it; use only provided data";
- `_output_rules.j2`: one JSON object, cite `[E:<id>]` ids exactly as given, every claim cites;
- `_data_block.j2`: the `evidence` variable as `[E:<id>] <time>Z <text>` lines in a data block.

Variables are strict: an undefined one raises `ConfigError`. `BaseAgent` sets `agent`, `as_of`,
`evidence` (the gathered items) and `output_schema`; agent inputs cannot override them.

Every LLM call carries `metadata["prompt_version"] = "<template>@v<N>|<tool>@v<M>,..."`, which
is part of the Stage 25 cache key: bumping a template's or a tool's version makes recorded
answers miss (see [llm-cache.md](llm-cache.md)).

## Tools and evidence

A tool's body gets the `AgentContext` and its validated arguments and returns `Evidence`; it
must read data only through `ctx.view` (the `DataView`). The registry refuses (with
`LookAheadError`) any item published at or after the cutoff, and sends the items to the model
as `[E:<id>] ...` lines in `<data source="tool:<name>">` blocks (untrusted text). An unknown
tool, invalid arguments or a `DataError` come back to the model as `ERROR: ...`. Built-in
tools: `news`, `market_news`, `fundamentals` (`fund:<SYM>:<period end>:<metric>`) and
`price_history` (`px:<SYM>:<last session>:<n>d`).

## The run

1. Gather evidence (checked against `as_of`) and render the prompt.
2. Tool loop: up to `agents.max_tool_rounds` turns may ask for tools; if the model still asks,
   a wrap-up turn without tools follows, and a wrap-up that asks again fails the run.
3. Citation check: a claim is any nested model with `evidence_ids` and must cite at least one
   id; ids in `evidence_ids`, an `evidence` list or inline `[E:<id>]` tags must be ids the
   run showed; an output that could cite but cites nothing at all is uncited too. An agent
   may also override `check_output(ctx, output) -> problems` (default: none) for checks of
   its own (a value range, numbers equal to the tool numbers, p10 <= p50 <= p90); they must be
   deterministic. Problems of both kinds share `agents.evidence_repair_retries` repair turns
   (step `evidence_repair`, or `output_repair` when only the check failed); if any remain,
   the output is marked `low_quality` (`AgentOutput.problems` lists what the check still
   found) and its top-level `confidence` and `conviction` are capped at
   `agents.low_quality_confidence_cap` (0.3). When the run showed no evidence at all (a
   symbol with no news), bad citations cannot be repaired, so there is no repair turn: the
   output goes straight to `low_quality`.
4. Trace: one `agent_runs` row (status `ok`, `low_quality` or `failed`; prompt version and
   digest, inputs digest, tool calls, evidence ids, output, tokens, cost) and, with an
   `AuditLog`, an `AGENT_REPORT` event.

Any exception (budget, provider, invalid output, look-ahead, a bug) is traced as `failed` and
re-raised: no output means no new risk (spec §3 principle 6). An agent given an `AuditLog`
refuses to start outside a run context, before any LLM call. Templates are loaded once per
`PromptRegistry`: editing a file on disk does not change a running process's prompts.

## Memory

Stage 27, `atlas.agents.memory`. Agents remember past situations, their decisions and lessons,
and recall the most similar ones without ever seeing the future.

| Part | Where | What it does |
|---|---|---|
| `AgentMemory` | `agents/memory/retrieval.py` | The entry point: `record_episode`, `settle_episode`, `add_lesson`, `recall` |
| `MemoryStore` | `agents/memory/store.py` | The records (source of truth): native DuckDB tables `episodes` and `lessons`, or memory without a lake |
| `VectorIndex` | `agents/memory/index.py` | ChromaDB collections under `<lake>/<agents.memory.index_dir>` (ephemeral without a lake); rebuilt from the records |
| `Embedder` | `agents/memory/embedder.py` | `MiniLMEmbedder` (all-MiniLM-L6-v2 in ONNX, 384-d, downloaded once into `agents.memory.model_dir`) or `HashEmbedder` (deterministic words; tests and offline) |
| `memory_block` | `agents/memory/render.py` | Memories as 2-4 prompt lines each, inside a `memory` data block |

```python
memory = AgentMemory(make_embedder(cfg.agents.memory), database=get_database(lake), config=cfg)
ep = memory.record_episode(
    "fundamental_analyst",
    situation,
    {"action": "buy"},
    created_at=decision_time,
    symbol="AAPL",
    run_id=run_id,
)
memory.settle_episode(ep.id, {"return_5d": 0.03}, settled_at=five_days_later)
memory.add_lesson(
    "global",
    "Guidance raises matter more than the beat",
    created_at=reflection_time,
    source_episode_ids=[ep.id],
    quality_score=0.7,
)

ctx = AgentContext(view, memory=memory)
memories = ctx.recall("fundamental_analyst", "AAPL beat earnings, raised guidance", k=5)
```

In an agent, read memory with `ctx.recall(...)`: it is `memory.recall(agent, query_text,
as_of, k, symbol)` at the context's `as_of`, so an agent cannot ask for a later moment. Show
memories with the partial `{% include "_partials/_memory.j2" %}` (variable `memories`, optional
`memory_max_chars`) or the template global `memory_block(memories)`.

**Point in time.** Visibility follows `AsOf.knows` (strictly before the cutoff), like every
dataset: a memory is visible iff it was created before the cutoff, and an episode's outcome
and `settled_at` iff it settled before it. Times are the caller's (the simulated decision time
in a backtest), never the wall clock. The index filters on creation time, and every hit is
checked again against the records (`LookAheadError` otherwise). An episode settles once; a
lesson may cite only episodes created at or before it.

**Who sees what.** An agent recalls its own episodes (of one `symbol` if given), its own
lessons and the `global` ones, never another agent's. Results are ranked by cosine similarity
(then newer first); memories under `min_similarity` (0.3, set from the live MiniLM run; the
hash embedder needs a lower floor) are dropped.

**Lesson curation** (`agents.memory.lessons`):
- decay: effective quality = `quality_score * 0.5^(age_days / half_life_days)`, age to the
  decision cutoff (unrated lessons start at `default_quality`);
- near-duplicates: a new lesson with cosine above `dedupe_cosine` (0.92) to an active lesson
  of the same scope is stored only if its quality beats the old one's effective quality, and
  then supersedes it from its own `created_at` on (the old one stays visible before that);
- caps: at recall each scope keeps its `max_per_agent` (50) best lessons by effective quality,
  drops those under `min_quality`, and shows an agent lesson and a global near-duplicate once.

**The index** can always be rebuilt from the records: opening `AgentMemory` adds missing
records (`sync`); switching embedder needs `rebuild=True` (re-embeds everything). Writing
lessons is Stage 54's job and scoring Stage 53's; this stage provides the store and recall.

## The fundamental analyst

Stage 29, `atlas.agents.research.FundamentalAnalyst` (quick tier, `AnalystReport`). It reads
point-in-time filings, factor ranks against its sector, valuation and red flags, and writes
a cited report.

```python
analyst = FundamentalAnalyst(build_gateway(cfg, lake=lake), config=cfg)
out = analyst.run(AgentContext(DataView(lake, day, cfg), inputs={"symbol": "AAPL"}))
out.output  # AnalystReport: signals quality, valuation, growth, balance_sheet; confidence
```

**Tools** (`agents/tools/fundamental_tools.py`; settings `agents.fundamental`). Each returns
one evidence item per group of numbers, so every number has an id next to it:

| Tool | Evidence ids | What it shows |
|---|---|---|
| `get_fundamentals_summary` | `fsum:<SYM>:<period end>:<line>` | Last `summary_quarters` (8) quarters of revenue, profits, EPS, cash flow, capex with the TTM; balance-sheet lines; TTM margins and growth (and the 3-year revenue CAGR) |
| `get_factor_ranks` | `frank:<SYM>:<day>:<group>` | Value, quality, growth and leverage: style-composite and per-factor percentiles among the index members of the same GICS sector (Stage 16 factor panel) |
| `get_valuation` | `val:<SYM>:<day>:<multiple>` | Market cap and EV; P/E, P/S, P/B, EV/EBITDA, FCF yield now, against their own history at the fiscal quarter ends of `valuation_years` (5) and the sector median |
| `get_red_flags` | `rflag:<SYM>:<day>:<check>` | Accruals spike, receivables growing faster than revenue, negative FCF with rising debt, dilution, going concern: FLAG, CLEAR or NOT EVALUATED with the numbers (thresholds `agents.fundamental.red_flags`) |

The run prefetches all four for the symbol (one LLM round normally) and still offers them for
a peer. A tool that fails for the symbol other than the summary leaves a `gap:` item instead;
a blocked symbol, or one without fundamentals, fails the run. `check_output` holds the report
to the six summary sections (business quality, growth trajectory, valuation, balance-sheet
risk, red flags, what would change my mind), the four signals and "every number cited": a
sentence that states a number carries an `[E:<id>]` tag; problems get one repair turn.

Receivables need the concept `accounts_receivable` (Stage 29). A lake ingested before it has
no receivables rows until the fundamentals are re-ingested (the main lake was, 2026-11-18), so
the receivables check reads NOT EVALUATED there.

**Going concern** (Stage 29 follow-ups 2 and 3) reads the MD&A of the latest 10-K or 10-Q known
at the as-of whose MD&A was found (`DataView.mda`, the `filings` dataset). The check is FLAG when one MD&A sentence holds all of
`going_concern_terms` ("going concern", "substantial doubt") and none of
`going_concern_exclusions`; the evidence quotes that sentence. The exclusions exist because, on
the main lake, most such sentences describe the ASU 2014-15 standard ("evaluate whether there
is substantial doubt about an entity's ability …"), are hypothetical, are about another party
(a tenant, an investee) or deny a doubt ("no longer"). With them, 76 of the main lake's 30,927
10-K and 10-Q MD&As are flagged (2026-11-23, 23 companies): 60 about the company itself
(Frontier, Endo, Mallinckrodt, Valaris, Chesapeake, Bed Bath & Beyond, Enphase, …), 8 about a subsidiary
(FirstEnergy's FES, NRG's GenOn, Harley-Davidson's LiveWire) and 8 about another company
(Toshiba, in Western Digital's and Southern Company's), all reviewed. The
check is NOT EVALUATED, with the reason, when the lake has no MD&A of the symbol, when the
filing's period ended more than `mda_max_age_days` (460) days before the as-of, or when no
filing's MD&A was found. A doubt first disclosed in a 10-Q is read as soon as the 10-Q is known.

**Recorded run.** `tests/fixtures/recorded/stage-29-fundamental-aapl-2024-05-01/` holds a live
run on AAPL for 2024-05-01: the exported LLM cache, the lake slice it read (`atlas data slice`)
and the report. Its test replays it with no lake, no key and no provider call.

## The technical analyst

Stage 30, `atlas.agents.research.TechnicalAnalyst` (quick tier, `TechnicalReport`). It
interprets precomputed technical features, prices, levels and the market regime; it never
computes an indicator itself.

```python
analyst = TechnicalAnalyst(build_gateway(cfg, lake=lake), config=cfg)
inputs = {"symbol": "AAPL", "regime": regime}  # regime: the day's RegimeState (optional)
out = analyst.run(AgentContext(DataView(lake, day, cfg), inputs=inputs))
out.output  # TechnicalReport: signals trend, momentum, volatility, levels
```

**Output.** `TechnicalReport` is an `AnalystReport` (spec §7) whose signals are
`TechnicalSignal`s: each also has a `timing` (`NOW`, `WAIT_FOR_PULLBACK`, `AVOID`) and a
`suggested_stop_pct`, the stop distance as a fraction of the last close (0.08 = 8% below it
for a LONG view, above it for a SHORT view). Every consumer of an `AnalystReport` takes it.

**Tools** (`agents/tools/technical_tools.py`; settings `agents.technical`):

| Tool | Evidence ids | What it shows |
|---|---|---|
| `get_technical_snapshot` | `tsnap:<SYM>:<session>:<group>`, `regime:<day>:market` | Every Stage 15 feature in five groups (trend, momentum, volatility, levels, participation) with its percentile (0-100) against the stock's own last 252 sessions and against the index members (USD levels have none); the fund's market regime (Stage 21: vol and trend regimes, HMM, macro quadrant, stress, risk scalar) |
| `get_price_summary` | `pxs:<SYM>:<last bar>:<ohlc, range, ma, levels, stops>` | The last `ohlc_sessions` (10) bars; the 52-week (`range_sessions`, 252) high and low with dates and the close's place in the range; SMA 20/50/200 (Stage 15 values); support below and resistance above the close (`swing_sessions` 20 and 63 lows and highs, 52-week high and low, the averages); stop references (ATR(14) and `atr_stop_multiples` 2 and 3 x ATR, distances to the swing lows and highs) |

Prices are split- and dividend-adjusted with the actions known at the as-of, relative to the
stock's last bar, so every level is comparable with the last close; volume is in today's
shares. A short history says so ("only 80 sessions of history"); a last bar older than the
latest session is named, and the price summary's windows end at it. The snapshot is taken at
the cross-section's latest session (Stage 15), so for such a stale symbol most features read
n/a (a delisted or halted name), never a stale value passed off as today's. The regime comes from `inputs["regime"]` (the fund graph's
`FundState.regime`) or, without it, from `regime_at(view)` (about 15 s on the main lake); a
regime of another day is refused before any LLM call.

The run prefetches both tools for the symbol and still offers them for a peer. A snapshot or
regime that cannot be computed leaves a `gap:` item; a blocked symbol, or one without bars,
fails the run. `check_output` holds the report to the six summary sections (trend, momentum,
volatility regime, levels, timing and entry, invalidation), the four signals, "every number
cited" (Stage 29's rule, `atlas.agents.research.checks`) and the stop rules: a LONG or SHORT
signal has a stop within [`min_stop_pct`, `max_stop_pct`] (0.01 to 0.25); a FLAT signal has
none and timing `AVOID`. Problems get one repair turn.

**Recorded run.** `tests/fixtures/recorded/stage-30-technical-aapl-2024-05-01/` holds a live
run on AAPL for 2024-05-01: the lake slice (the S&P 500 members on the day and SPY, prices
since 2021-10-01), the day's regime computed on the main lake (`regime.json`), the exported
LLM cache and the report. Its test replays it with no lake, no key and no provider call.
