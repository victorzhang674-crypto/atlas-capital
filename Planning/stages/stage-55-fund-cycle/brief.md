# Stage 55 — Fund daily cycle graph (end-to-end)
**Phase:** 8 Orchestration · **Size:** L · **Depends on:** 37–54 · **Spec refs:** §5

## Goal
Wire everything into one LangGraph "Fund Day" (spec §5, steps 1–17) that runs identically in backtest and paper modes.

## In scope
- `FundDayGraph`: data check → mark-to-market/NAV → monitor/circuit breakers → regime → macro outlook (cadence-aware) → screening queue → research subgraphs (parallel, budget-aware) → pod proposals (by each pod's schedule) → CIO allocation (monthly/emergency) → BL → optimiser → sizing/hedging → risk engine → CRO memo → IC (conditional) → compliance → OMS → venue → post-trade (accounting, TCA, settlement, reflection) → reports → audit
- Cadence manager: which steps run today (calendar: daily, weekly on Mondays or the first session of the week, monthly on the first session, event triggers)
- Mode adapters: `BacktestEnv` (SimClock, SimulatedVenue, replay cache) vs `PaperEnv` (LiveClock, Alpaca venue)
- Fail-safe paths: any step failure → "no new risk" branch (keep the book, still run risk/accounting); everything recorded in `errors`
- Run summary object + `RUN_STARTED/FINISHED` audit events
- CLI: `atlas fund run-day --date D --mode backtest [--replay]`

## Files
`src/atlas/orchestration/{fund_graph.py,cadence.py,env.py,summary.py}`, `src/atlas/cli/fund_cmd.py`, tests (integration with FakeLLM + tiny universe)

## Acceptance criteria
- An integration test runs 10 consecutive simulated days on the tiny universe offline: orders → fills → NAV, with cadence respected, and a forced failure in research still lets risk/accounting run.

## Verify
`uv run pytest tests/integration/test_fund_day.py -q -m integration`
