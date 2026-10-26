# Stage 23 — Statistical arbitrage (pairs)  → Milestone M2
**Phase:** 2 · **Size:** M · **Depends on:** 17

## Goal
A market-neutral pairs engine: find cointegrated pairs within sectors and generate mean-reversion signals. This completes **M2**.

## In scope
- Pair selection (monthly, PIT): same sector, correlation (252d) > 0.7 prefilter, Engle-Granger test (p < 0.05) on log prices, hedge ratio by OLS (or Kalman-filtered rolling β), half-life of mean reversion between 2 and 60 days, Hurst < 0.5; cap pairs per name (≤ 2) and total (≤ 40)
- Signals: spread z-score (lookback = 2× half-life), entry |z| > 2, exit |z| < 0.5, stop |z| > 4 or time stop (3× half-life), breakdown check (rolling cointegration p-value)
- Output `PairSignal{pair_id, long, short, hedge_ratio, z, exp_return, confidence}` and translated per-symbol target weights (dollar-neutral per pair)
- Vectorised pairs backtest using Stage 17 utilities (costs included)
- `atlas research pairs --as-of …` CLI listing current pairs

## Out of scope
Baskets/Johansen (backlog).

## Files
`src/atlas/quant/statarb/{selection.py,signals.py,backtest.py}`, tests

## Tasks
1. Synthetic cointegrated pair generator; selection recovers the planted pair and rejects random walks.
2. Half-life/Hurst tests.
3. Signal state machine (flat → long-spread → exit/stop) tests.
4. Pairs backtest + CLI; in-sample results in the handoff note.

## Acceptance criteria
- Planted pairs are recovered and false positives are under 5% on random walks; the signal state machine is fully tested.

## Verify
`uv run pytest tests/unit/quant/statarb -q`
