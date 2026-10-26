# Stage 40 — Global Macro & Event-Driven pods
**Phase:** 5 · **Size:** L · **Depends on:** 33, 38

## Goal
The two remaining discretionary pods: Global Macro (an ETF book driven by the macro outlook and trend) and Event-Driven (earnings-focused).

## In scope
- **Global Macro pod:** universe = ETF list; inputs = MacroOutlook asset views, TSMOM signals (Stage 22), regime; PM agent (deep) blends the macro views with trend (rule: a view against a strong trend needs conviction ≥ 0.7); outputs views on ETFs; can hold hedges (e.g. TLT, GLD, UUP) with a correlation-to-equity note
- **Event-Driven pod:** universe = names with earnings within 10 sessions or within 60 days after; pre-event: the news analyst + quant reports (lightweight research path, quick tier) → small position or none; post-event: PEAD signal + LLM read of the reaction and news → drift trade; position size capped (≤ 1.5% NAV each), a hard exit at horizon; never hold a short through earnings when shortability is uncertain
- Lightweight research path: a reduced subgraph (news + quant + technical, no debate) to save cost

## Files
`src/atlas/agents/pods/{global_macro.py,event_driven.py}`, prompts, configs, tests

## Acceptance criteria
- Both pods produce valid proposals offline; event exits trigger at horizon; the macro trend-conflict rule is enforced.

## Verify
`uv run pytest tests/unit/agents/pods -q`
