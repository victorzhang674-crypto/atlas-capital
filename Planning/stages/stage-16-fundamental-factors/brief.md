# Stage 16 — Fundamental factor library
**Phase:** 2 · **Size:** M · **Depends on:** 13

## Goal
Cross-sectional fundamental factor exposures (value, quality, growth, leverage, size), cleaned and neutralised, point-in-time.

## In scope
- Inputs: `fundamentals_panel` (Stage 09 via DataView) + prices for market cap (shares × close)
- Raw factors: Value (E/P TTM, B/P, FCF yield, EBIT/EV, sales/EV), Quality (ROE, ROA, gross profitability = GP/assets, accruals (negative), margin stability over 8q), Growth (revenue YoY TTM, EPS YoY, 3y revenue CAGR), Leverage (debt/equity, net debt/EBITDA), Size (log market cap), Payout/issuance (net share issuance 12m, negative)
- Cleaning pipeline: winsorise (1/99 pct), cross-sectional z-score, missing-value handling (sector median fill, flagged), sector neutralisation (demean within GICS sector), optional size neutralisation by regression
- Composite builder: `composite(factors, weights)`
- Factor panel cache stored in the lake (`factor_exposures(date, symbol, factor, value)`) for monthly rebalance dates

## Out of scope
Factor returns and the risk model (Stage 19); alpha weighting (Stage 22).

## Files
`src/atlas/quant/factors/{fundamental.py,cleaning.py,composite.py}`, tests

## Tasks
1. Market cap + EV construction with PIT shares; tests.
2. Each raw factor with fixture fundamentals; known-value tests.
3. Cleaning pipeline tests (winsor bounds, z mean≈0/std≈1, sector-demeaned sums ≈ 0).
4. Composite + persistence.

## Acceptance criteria
- Factor panels for any date use only filings available at that date; the cleaning invariants hold.

## Verify
`uv run pytest tests/unit/quant/factors -q`
