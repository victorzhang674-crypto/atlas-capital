# Stage 64 — Streamlit dashboard
**Phase:** 9 · **Size:** L · **Depends on:** 63

## Goal
A local web dashboard for watching the fund live, digging into decisions, and reviewing backtests.

## In scope
Pages (Streamlit multipage, reading DuckDB read-only):
1. **Overview:** NAV vs benchmarks, today's P&L, exposures, circuit-breaker state, alerts
2. **Risk:** VaR/CVaR history, limit utilisation bars, factor exposures, stress results, correlation heatmap
3. **Pods:** allocation over time, pod P&L/drawdown, scorecards
4. **Positions:** table with pod, weight, P&L, stop level, thesis link, days held
5. **Research:** thesis browser (status filter), full thesis with debate transcript, analyst reports, evidence links
6. **Agents:** trust weights, calibration plots, LLM cost by agent/day, lessons browser
7. **Orders & TCA:** blotter, fills, slippage stats
8. **Backtests:** run list, tear-sheet embed, run comparison, ablation table
9. **Audit:** searchable audit log with hash-chain verification status
- Read-only by design; the only action is a "Request kill" button that requires a typed confirmation and calls the same kill-switch API
- `atlas dashboard` launches it on localhost

## Files
`src/atlas/dashboard/{app.py,pages/*.py,queries.py,components.py}`, tests (query functions unit-tested; a page smoke test via `streamlit.testing`)

## Acceptance criteria
- All pages load against a fixture run without errors (AppTest); queries are tested.

## Verify
`uv run pytest tests/unit/dashboard -q && uv run atlas dashboard`
