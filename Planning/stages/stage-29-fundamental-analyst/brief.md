# Stage 29 — Fundamental analyst agent
**Phase:** 4 Research · **Size:** M · **Depends on:** 16, 28 · **Spec refs:** §6

## Goal
An analyst that reads PIT fundamentals, factor ranks and trends, and writes a cited `AnalystReport` on business quality, valuation and red flags.

## In scope
- Tools: `get_fundamentals_summary` (last 8 quarters of key lines, TTM, margins, growth), `get_factor_ranks` (value/quality/growth percentiles vs sector), `get_valuation` (multiples vs own 5y history and sector median), `get_red_flags` (deterministic checks: accruals spike, receivables growth >> revenue, negative FCF with rising debt, dilution, going-concern keywords if MD&A is available)
- Prompt `fundamental_analyst.j2` (v1): structure = business quality, growth trajectory, valuation, balance-sheet risk, red flags, what would change my mind; every number cited
- Output `AnalystReport` with signals (quality, valuation, growth, balance_sheet) and confidence
- Golden tests with FakeLLM; one recorded live run on AAPL 2024-05-01 stored as a fixture for later stages

## Out of scope
Earnings call transcripts (backlog).

## Files
`src/atlas/agents/research/fundamental.py`, `src/atlas/agents/tools/fundamental_tools.py`, `src/atlas/agents/prompts/fundamental_analyst.j2`, tests

## Tasks
1. Tools (deterministic, testable) with evidence IDs.
2. Red-flag rules with synthetic cases.
3. Agent + prompt; FakeLLM test validates the schema and evidence references.
4. Live run fixture (`-m llm`) recorded into the replay cache.

## Acceptance criteria
- The report validates, cites evidence for every quantitative claim, and runs offline from the cache.

## Verify
`uv run pytest tests/unit/agents/research/test_fundamental.py -q`
