# Stage 58 — Robustness: walk-forward, deflated Sharpe, PBO
**Phase:** 8 · **Size:** M · **Depends on:** 57 · **Spec refs:** §13, §16

## Goal
Evidence that results are not luck or overfitting, before anything goes near paper trading.

## In scope
- Walk-forward: rolling or anchored windows (train/calibrate 3y → test 1y), re-calibrating only the parameters allowed to adapt (IC weights, risk aversion λ); the stitched OOS equity curve
- **Fixed hold-out:** 2022-01 → latest is never used for any tuning (enforced: the tuning API refuses dates in the hold-out unless `--final-evaluation`)
- Deflated Sharpe ratio (Bailey & López de Prado) using the number of trials logged in a `trials` registry (every backtest config tried is logged automatically)
- Probability of Backtest Overfitting via CSCV (16 partitions) over the trial set
- Monte Carlo: block bootstrap of daily returns → Sharpe/MaxDD confidence intervals; randomised-entry test (shuffle signals → performance should vanish)
- Parameter sensitivity heatmaps (target vol, Kelly fraction, debate rounds, rebalance cadence)
- LLM-leakage check: compare performance before vs after the LLM's training cutoff window (sub-period table)

## Files
`src/atlas/backtest/{walk_forward.py,deflated_sharpe.py,pbo.py,bootstrap.py,sensitivity.py,trials.py}`, tests

## Acceptance criteria
- DSR and PBO match published worked examples on synthetic inputs; the hold-out guard is enforced; a robustness section is added to the tear sheet.

## Verify
`uv run pytest tests/unit/backtest -q`
