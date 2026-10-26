# Stage 15 — Technical feature library
**Phase:** 2 Quant · **Size:** M · **Depends on:** 13

## Goal
Vectorised, tested technical features computed from `DataView`, used by the quant models and summarised for the Technical Analyst agent.

## In scope
- Feature registry: `@feature(name, lookback, kind="ts"|"xs")`; `compute_features(view, symbols, names) -> DataFrame[date×symbol] per feature` (wide) or long
- Features: returns (1d, 5d, 21d, 63d, 126d, 252d), momentum 12-1, short-term reversal (5d), realised vol (21d, 63d), downside vol, ATR(14), RSI(14), MACD(12,26,9) + signal + hist, SMA/EMA 20/50/200 and price-to-MA ratios, Bollinger %B, distance from 52-week high/low, max drawdown (252d), beta and idiosyncratic vol vs SPY (252d), volume z-score, Amihud illiquidity, gap statistics
- A `TechnicalSnapshot` model per symbol (latest values + percentile vs own history + vs universe) for the agents
- Lookback-aware: the view loads exactly `lookback + buffer` sessions

## Out of scope
Fundamental factors (Stage 16); signals or decisions.

## Files
`src/atlas/quant/features/{registry.py,technical.py,snapshot.py}`, tests

## Tasks
1. Registry + compute plumbing on the `tiny_universe` fixture.
2. Each feature test-first against hand-computed or TA-Lib-reference values on a short known series (store the expected values in a fixture CSV).
3. Beta/idio vol on synthetic data with a known beta (e.g. 1.3).
4. Snapshot builder + percentiles.
5. Performance test: 500 symbols × 2,000 days for all features in under 20 s.

## Acceptance criteria
- Every feature matches reference values within 1e-6 (or a documented tolerance); there is no look-ahead (features at t use data ≤ t only, enforced by a shift test).

## Verify
`uv run pytest tests/unit/quant/features -q`
