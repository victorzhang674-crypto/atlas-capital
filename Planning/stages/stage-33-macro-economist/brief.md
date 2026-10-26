# Stage 33 — Macro economist agent
**Phase:** 4 · **Size:** M · **Depends on:** 21, 28

## Goal
A fund-level daily (or weekly) `MacroOutlook`: regime interpretation, the rates, inflation, growth and credit picture, and sector/asset tilts, used by the Macro pod, the CIO and the analysts' context.

## In scope
- Tools: `get_regime_state` (Stage 21), `get_macro_dashboard` (key series with transforms and z-scores), `get_market_news` (macro headlines), `get_cross_asset_moves` (ETF returns 1w/1m/3m), `get_event_calendar` (FOMC/CPI/NFP next 2 weeks)
- Output `MacroOutlook{as_of, regime_summary, growth, inflation, policy, credit, risks[], asset_views[{etf, direction, conviction, horizon}], sector_tilts{sector: −1..1}, risk_appetite∈[0,1], evidence}`
- Consistency validator: if `RegimeState.stress_flag` is set, `risk_appetite` ≤ 0.5 unless justified
- Cached once per as_of for all consumers

## Files
`src/atlas/agents/research/macro.py`, `src/atlas/agents/tools/macro_tools.py`, prompt, tests

## Acceptance criteria
- Valid outlook offline; the validator works; the 2020-03-16 recorded fixture shows low risk appetite.

## Verify
`uv run pytest tests/unit/agents/research/test_macro.py -q`
