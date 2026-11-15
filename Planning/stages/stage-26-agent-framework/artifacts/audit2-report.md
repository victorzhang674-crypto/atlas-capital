# Stage 26 — post-DONE full audit (2026-11-14)

Owner's request: "can you help me to fully audit this stage and make sure everything is being fully completed?"
Self-review (no reviewer subagent), done in the stage's chat; every fix test-first.

## Method
1. Re-read every module of `src/atlas/agents` line by line against the brief, spec §3 (principles 2, 3, 6), §6–§7 (the output contracts Phase 4 agents will use) and Project.md hard rules.
2. Ran a tool-using agent on the real default provider (DeepSeek, both tiers): until then the framework had only run on FakeLLM.
3. Mutation testing (mutmut 3.8.1) over `src/atlas/agents/` to find behaviour no test pins.
4. Full verification and merged-`main` re-verification.

## Brief checklist (re-checked)

| Brief item | Status | Evidence |
|---|---|---|
| `BaseAgent[OutT]` (`name, tier, prompt_template, output_schema, tools`; `run → AgentOutput`: output + evidence + trace + cost) | complete | `base.py`; `test_agents_base.py` |
| `AgentContext` (`as_of`, `DataView`, `run_id`, memory handle, extra inputs) | complete | `context.py`; `test_agents_context.py` |
| Jinja2 templates with `{# version: N #}`; partials `_data_block`, `_output_rules`, `_date_rules` | complete | `prompting.py`; `test_agents_prompting.py` |
| Tools wrap DataView methods, evidence-tagged text; tool loop with `max_tool_rounds` and wrap-up | complete | `tools.py`, `base.py`; `test_agents_tools*.py`, `test_agents_tool_loop.py`; live run |
| Evidence validator: unknown ids → repair → `low_quality`, confidence ≤ 0.3 | complete (tightened by A3, A4) | `evidence.py`; `test_agents_evidence.py`, `test_agents_citations.py`, `test_agents_audit.py` |
| `agent_runs` trace (inputs digest, prompt version, tool calls, output, cost) | complete | `trace.py`, `db.py`; `test_agents_trace.py`, `test_storage_agent_runs.py` |
| `AGENT_REPORT` audit events | complete | `test_agents_trace.py`, `test_the_audit_payload_is_complete` |
| Acceptance: offline with FakeLLM; uncited claims caught; tools cannot see beyond `as_of` | verified | see `verification.md` |

## Findings

| # | Severity | Finding | Fix | Test |
|---|---|---|---|---|
| A1 (first audit) | defect | `agent_run_id` from the seeded id source collided on a same-seed re-run | random ULID | `test_a1_seeded_re_runs_do_not_collide_in_agent_runs` |
| A2 | defect | Jinja2 `auto_reload=True`: a template edited on disk renders new text under the old recorded version/digest | `auto_reload=False` | `test_a2_…` |
| A3 | gap in the brief's rule | an output that can cite but cites nothing (report with no signals, empty `evidence`) passed | uncited at `<root>` | `test_a3_…` (3) |
| A4 | gap | only `confidence` was capped; `InvestmentThesis` (Stage 36) has `conviction` | both capped | `test_a4_…` |
| A5 | gap | never run on a real model | `llm`-marked live test, both tiers, passed | `test_agents_live_deepseek.py` |
| A6 | defect | non-`AtlasError` exceptions left no trace | every exception traced, re-raised | `test_a6_…` |
| A7 | defect | with an `AuditLog` but no run context the run spent LLM money, then failed | refuses before any call | `test_a7_…` |
| A8 | defect | `price_history` of a blocked symbol said "(no data)" | error text "blocked" | `test_a8_…` |
| A9 | defect (found in the live output) | raw high/low across a split shown next to an adjusted return (79.41 vs close 36.85) | split-adjusted close range; tool → v2 | `test_a9_…` |
| M1–M14 | test gaps | mutation survivors showing unpinned behaviour: the agent's own tool list not enforced in a test; trace usage sum; `news` / `market_news` arguments; stale and missing fundamentals; evidence sources; exact price text; audit payload keys; a custom registry; a schema-less subclass; deep claim paths; `evidence` of models; a parsed answer of another type; `AtlasError` trace text; partial traversal after a shared include; repair-text id cap; error-outcome `call` | tests only (no code defect) | `test_agents_audit_mutation.py` (20 tests) |

Simplification: `tools._instant` lost a branch no input can reach (`available_at` is always tz-aware).

## Mutation testing
See [audit2-mutation-summary.txt](audit2-mutation-summary.txt): run 1 925 / 1117 killed → final 965 / 1109; the 144 remaining survivors are equivalent (listed by kind).

## Not changed (recorded)
- Evidence ids for fundamentals use the period end, not a quarter label (the lake knows period ends).
- `market_news` arguments are passed through, but the test world has one market article, so `limit` is not distinguishable there (it is for `news`).
- The optional Anthropic provider's tool loop is still not probed (no key) → Backlog.

## Verification after the audit
See `verification.md` § "Post-DONE full audit".
