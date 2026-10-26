# Stage 56 — Event-driven backtest engine
**Phase:** 8 · **Size:** L · **Depends on:** 55 · **Spec refs:** §16, A7

## Goal
Run the whole fund over years, day by day, point-in-time, with costs, at controlled LLM cost, and with exact reproducibility.

## In scope
- `Backtest(config, start, end, mode="full"|"budget"|"quant_only")`: loops sessions with SimClock, invokes `FundDayGraph`, persists daily state (NAV, positions, exposures, risk, orders, fills) to `data/runs/<run_id>/`
- **Budget mode:** LLM pods research at a lower cadence (e.g. bi-weekly), with a capped queue size and the quick tier everywhere; **quant_only:** LLM pods disabled (ablation baseline and fast iteration)
- LLM spend projection before start (estimate from cache hit rate and queue sizes) with a confirm prompt when over budget
- Resume from the last completed day; deterministic seeds; replay-only rerun produces bit-identical outputs (test)
- Warm-up period handling (risk model and regime need history: start trading after the warm-up)
- Parallelism: optional multiprocessing across non-overlapping walk-forward windows (Stage 58)
- CLI: `atlas backtest run --from 2018-01-01 --to 2024-12-31 --mode budget`

## Files
`src/atlas/backtest/{engine.py,state_store.py,projection.py}`, `src/atlas/cli/backtest_cmd.py`, tests

## Acceptance criteria
- A 2-year quant_only backtest on the full universe runs in under 30 min; a replay rerun is identical; resume works.

## Verify
`uv run atlas backtest run --from 2019-01-01 --to 2020-12-31 --mode quant_only`
