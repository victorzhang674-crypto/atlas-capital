# Stage 46 — Stress testing & scenarios
**Phase:** 6 · **Size:** M · **Depends on:** 19, 44

## Goal
Know how the current book would fare in historical crises and hypothetical shocks, and feed the worst case into the risk limits.

## In scope
- Historical replay scenarios (factor returns + ETF returns over the window applied to current exposures): GFC (2008-09-15 → 2008-11-20), Flash Crash 2010-05-06, Aug 2011 downgrade, Aug 2015 China deval, Q4 2018, COVID crash (2020-02-19 → 2020-03-23), 2022 rates shock (2022-01-03 → 2022-10-12), 2007 quant quake (2007-08-06 → 08-10, momentum/value reversal)
  - names that did not exist then: proxy through factor + sector exposures
- Hypothetical shocks: equity −10/−20%, rates +100bp (via TLT/IEF betas), credit spread +200bp, momentum crash (momentum factor −3σ), USD +5%, oil ±30%, sector-specific −25%
- Reverse stress: the smallest single-factor shock causing a −10% NAV loss
- `StressReport{scenario, pnl_pct, by_pod, by_factor, top_contributors}`; worst-case → risk-engine stress limit check
- Cache scenario factor paths

## Files
`src/atlas/risk/stress/{scenarios.py,historical.py,hypothetical.py,reverse.py}`, `config/scenarios.yaml`, tests

## Acceptance criteria
- A pure long-SPY book shows ≈ −34% in the COVID scenario (±3%); decomposition sums to the total.

## Verify
`uv run pytest tests/unit/risk/stress -q`
