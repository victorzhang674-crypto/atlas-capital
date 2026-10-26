# Stage 53 — Prediction scoring & performance attribution
**Phase:** 7 · **Size:** M · **Depends on:** 52, 19 · **Spec refs:** §10

## Goal
Answer "who made money, who forecast well, and why": settle every prediction and attribute fund returns to pods, factors, sectors and selection.

## In scope
- Prediction settlement: for each `Prediction` whose horizon has elapsed (PIT), compute the realised return and alpha vs SPY/sector ETF; scores: direction Brier, hit rate, quantile calibration (fraction of outcomes inside p10–p90 ≈ 80%), information coefficient across predictions, PnL-weighted accuracy
- Agent and pod scorecards: rolling 3/6/12m stats → `scorecards` table
- Return attribution: (a) by pod (from the sub-ledgers), (b) factor attribution through the risk model: factor return × exposure + specific (selection) + costs, (c) Brinson-Fachler by sector vs SPY for the long book, (d) alpha/beta decomposition
- Cost attribution: commissions, impact, borrow and fees as a drag line

## Files
`src/atlas/performance/{predictions.py,scoring.py,scorecards.py,attribution.py}`, tests

## Acceptance criteria
- Attribution components sum to the total return (within 1 bp/day); scoring has known-answer tests; settlement is PIT-correct.

## Verify
`uv run pytest tests/unit/performance -q`
