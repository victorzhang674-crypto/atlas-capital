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
