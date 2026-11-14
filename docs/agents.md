# Building an agent

Stage 26, `atlas.agents`. Every LLM agent of Phase 4 onwards is built the same way: a role, a
versioned prompt, point-in-time tools, a structured output, an evidence trail and a trace.

## The parts

| Part | Where | What it does |
|---|---|---|
| `AgentContext` | `agents/context.py` | The run's inputs: a `DataView` (the only door to data), its `as_of`, the `run_id`, an optional memory handle (Stage 27) and extra `inputs` |
| `PromptRegistry` | `agents/prompting.py` | Loads Jinja2 templates, checks their version line, renders the `system` and `user` blocks |
| `ToolRegistry`, `Tool` | `agents/tools.py` | Tools that wrap `DataView` methods and return `Evidence` |
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
   run showed. Problems get `agents.evidence_repair_retries` repair turns; if they remain,
   the output is marked `low_quality` and its `confidence` is capped at
   `agents.low_quality_confidence_cap` (0.3).
4. Trace: one `agent_runs` row (status `ok`, `low_quality` or `failed`; prompt version and
   digest, inputs digest, tool calls, evidence ids, output, tokens, cost) and, with an
   `AuditLog`, an `AGENT_REPORT` event.

Any `AtlasError` (budget, provider, invalid output, look-ahead) is traced as `failed` and
re-raised: no output means no new risk (spec §3 principle 6).
