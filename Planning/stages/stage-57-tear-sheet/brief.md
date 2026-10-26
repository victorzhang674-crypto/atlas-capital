# Stage 57 — Backtest analytics & tear sheet
**Phase:** 8 · **Size:** M · **Depends on:** 56 · **Spec refs:** §13, A9

## Goal
A professional tear sheet that shows whether the fund meets its targets, vs benchmarks, with full transparency.

## In scope
- Metrics from Stage 20 over the run + benchmarks (SPY, 60/40 SPY/AGG monthly rebalanced, BIL)
- Charts (Plotly → static HTML): equity curve vs benchmarks (log), underwater/drawdown, rolling 6m Sharpe/vol/beta, monthly returns heatmap, exposure history (gross/net/beta), sector nets, factor exposure history, turnover and costs, pod P&L contribution and pod allocation over time, regime-shaded timeline, return distribution
- Tables: summary vs targets (pass/fail coloured against spec §13), yearly returns, worst 10 drawdowns, top/bottom contributors, TCA summary, prediction scorecards by agent/pod
- Output: `data/runs/<run_id>/tearsheet.html` + `summary.json`
- CLI: `atlas backtest report <run_id>`; `atlas backtest compare <run_a> <run_b>`

## Files
`src/atlas/backtest/analytics.py`, `src/atlas/reporting/tearsheet/{builder.py,templates/tearsheet.html.j2}`, tests

## Acceptance criteria
- Generated for a fixture run with all sections populated; numbers match the Stage 20 functions (test).

## Verify
`uv run atlas backtest report <run_id>`
