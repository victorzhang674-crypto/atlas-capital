# Stage 36 — Research Director & thesis registry  → Milestone M3
**Phase:** 4 · **Size:** M · **Depends on:** 35, 27 · **Spec refs:** §7 InvestmentThesis

## Goal
Synthesise reports and the debate into a falsifiable `InvestmentThesis`, store it in a registry with lifecycle and re-evaluation triggers, and expose `atlas research TICKER --date`. This completes **M3**.

## In scope
- Research Director (deep tier): inputs = 6 analyst reports, debate transcript + balance, memory lessons, the existing thesis if any; output `InvestmentThesis` (direction, return distribution p10/p50/p90 over the horizon, conviction, catalysts with dates, **kill criteria** stated as checkable conditions, evidence)
- Kill criteria DSL (small, deterministic): `price_below(x)`, `drawdown_from_entry(pct)`, `metric_below(name, x)`, `date_passed(d)`, `event_occurred(type)`, evaluated daily by code
- Thesis registry (DuckDB `theses`): versions, status transitions (ACTIVE → INVALIDATED/CLOSED/EXPIRED), `review_due` date, triggers: kill criterion hit, earnings passed, price move > 2σ, thesis age > horizon
- `Prediction` records emitted for each thesis (for Stage 53 scoring)
- CLI: `atlas research AAPL --date 2024-05-01 [--replay]` → Rich-rendered thesis + HTML report in `data/runs/…`
- Comparison note (optional): the AI itself runs the same ticker/date in the reference TradingAgents (Project.md Rule 6) and records the differences in the handoff note

## Files
`src/atlas/agents/director.py`, `src/atlas/agents/thesis/{registry.py,kill_criteria.py}`, `src/atlas/cli/research_cmd.py`, prompt, tests

## Tasks
1. Kill-criteria DSL parser/evaluator with tests.
2. Registry lifecycle + triggers.
3. Director agent (FakeLLM) + validator (p10 ≤ p50 ≤ p90; conviction consistent with debate balance unless justified).
4. Wire the research subgraph end to end; CLI.
5. Live recorded run for 3 tickers (fixtures).

## Acceptance criteria
- `atlas research AAPL --date 2024-05-01 --replay` works offline and outputs a complete thesis; kill criteria are evaluated by code.

## Verify
`uv run pytest tests/unit/agents -q && uv run atlas research AAPL --date 2024-05-01 --replay`
