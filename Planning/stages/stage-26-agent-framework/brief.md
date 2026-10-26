# Stage 26 — Agent framework, prompts & tool registry
**Phase:** 3 · **Size:** M · **Depends on:** 13, 25 · **Spec refs:** §6, §3.6

## Goal
One consistent way to build an agent: a role, a versioned prompt, PIT-safe tools, a structured output, an evidence trail and a trace.

## In scope
- `BaseAgent[OutT]`: `name, tier, prompt_template, output_schema, tools`; `run(ctx: AgentContext) -> AgentOutput[OutT]` (parsed output + evidence used + trace + cost)
- `AgentContext`: `as_of`, `DataView`, `run_id`, memory handle (Stage 27, optional), extra inputs
- Prompt system: Jinja2 templates with `{# version: N #}`; shared partials (`_data_block.j2`, `_output_rules.j2`, `_date_rules.j2` = "today is {{as_of}}; you know nothing after it; use only provided data")
- Tool registry: tools wrap DataView methods and return **evidence-tagged** text (`[E:news:123] headline…`, `[E:fund:AAPL:2024Q1:Revenue] …`); tool-calling loop with `max_tool_rounds` and a wrap-up turn (learned from the reference)
- Evidence validator: claims in outputs must reference known evidence IDs; unknown IDs → a repair retry → else the output is marked `low_quality`, with confidence capped at 0.3
- Agent trace persisted (`agent_runs` table): inputs digest, prompt version, tool calls, output, cost
- `AGENT_REPORT` audit events

## Out of scope
Specific agents (Phase 4); LangGraph wiring (Stage 28).

## Files
`src/atlas/agents/{base.py,context.py,tools.py,evidence.py,trace.py}`, `src/atlas/agents/prompts/_partials/*.j2`, tests

## Tasks
1. BaseAgent with FakeLLM: a toy "echo analyst" end to end.
2. Template loading + version extraction.
3. Tool loop with max rounds + wrap-up; test a model that keeps calling tools.
4. Evidence validator tests (valid, invalid, missing).
5. Trace persistence.

## Acceptance criteria
- The toy agent runs fully offline with FakeLLM; uncited claims are caught; tools cannot see beyond `as_of` (leak test via DataView).

## Verify
`uv run pytest tests/unit/agents -q`
