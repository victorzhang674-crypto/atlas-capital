# Stage 43 — Position sizing, stops & hedging overlay
**Phase:** 5 · **Size:** M · **Depends on:** 42

## Goal
Final risk-shaping before the risk engine: Kelly-capped sizes, per-position stops, and an explicit hedging overlay that keeps beta and tail risk in bounds.

## In scope
- Fractional-Kelly cap per position: `w ≤ f × μ/σ²` with f = 0.25 (config), applied as an upper bound on optimiser output
- Conviction floor: drop positions whose posterior μ is below a cost hurdle (expected return over the horizon < 2× round-trip cost)
- Stops: volatility-based stop (k × ATR or thesis kill price, whichever is tighter) and trailing stop for winners; stop levels stored per position and evaluated daily by the risk monitor (Stage 45)
- Hedging overlay: residual beta → SPY (or QQQ/IWM by style fit) hedge; optional sector-ETF hedges for sector nets beyond a soft band; tail-hedge proxy in high-stress regimes (shift into TLT/GLD/cash within limits; true options are out of scope)
- Output: `TargetPortfolio` with a separate `hedges` dict and per-position `stop_level`

## Files
`src/atlas/portfolio/{sizing.py,stops.py,hedging.py}`, tests

## Acceptance criteria
- Post-overlay beta lies inside the configured band; the Kelly cap and the hurdle are applied; stop levels exist for all non-hedge positions.

## Verify
`uv run pytest tests/unit/portfolio -q`
