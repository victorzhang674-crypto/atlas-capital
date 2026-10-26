# Stage 17 — Signal research toolkit
**Phase:** 2 · **Size:** M · **Depends on:** 15, 16

## Goal
Measure whether a signal predicts returns before any agent or pod trusts it: IC, quantile spreads, decay, turnover, plus a simple vectorised long-short backtest.

## In scope
- `forward_returns(view_factory, horizons=[1,5,21,63])` computed safely (labels only used for evaluation; a guard prevents their use as features)
- Information coefficient: Pearson and Spearman rank IC per date, mean, t-stat (Newey-West), IC IR, hit rate; IC by sector and by regime (regime is plugged in later)
- Quantile analysis: 5 buckets, mean forward return per bucket, monotonicity score, long-short spread, cumulative spread chart data
- Decay: IC across horizons; autocorrelation of signal ranks (turnover proxy)
- Vectorised L/S backtest: top/bottom quintile, equal or signal-weighted, rebalance frequency, simple cost bps → daily returns + summary (Sharpe, turnover)
- `SignalTearSheet` (dict + matplotlib figures saved to `data/runs/research/<name>/`)
- CLI: `atlas research signal --name momentum_12_1 --from 2016 --to 2021`

## Out of scope
The full event-driven backtester (Stage 56).

## Files
`src/atlas/quant/research/{labels.py,ic.py,quantiles.py,vector_backtest.py,tearsheet.py}`, `src/atlas/cli/research_cmd.py`, tests

## Tasks
1. Forward-return labels + guard; test that the labels align correctly (t → t+h).
2. IC computations on synthetic data with an injected known IC (≈0.05).
3. Quantile spreads; monotonic synthetic test.
4. Vector backtest + cost handling; zero-signal → ~0 return test.
5. Tear sheet + CLI.

## Acceptance criteria
- On synthetic data with a planted signal, the toolkit recovers the IC within ±0.01; the CLI produces a tear sheet for a real factor.

## Verify
`uv run pytest tests/unit/quant/research -q`
