# Stage 10 — Macro data (FRED / ALFRED)
**Phase:** 1 · **Size:** M · **Depends on:** 06 · **Spec refs:** §5 step 3–4

## Goal
Point-in-time macro series that respect release dates and revisions, for the regime model and the Macro Economist.

## In scope
- FRED client (httpx, API key from env), series metadata, observations; ALFRED vintages (`realtime_start/end`) for revised series
- Series set in `config/macro.yaml`: DGS3MO, DGS2, DGS10, T10Y2Y, T10Y3M, FEDFUNDS, CPIAUCSL, CPILFESL, PCEPILFE, UNRATE, PAYEMS, ICSA, INDPRO, RSAFS, UMCSENT, BAMLH0A0HYM2 (HY OAS), BAMLC0A0CM (IG OAS), VIXCLS, DTWEXBGS (USD), NFCI, WALCL, M2SL
- Curated `macro_observations(series_id, obs_date, value, realtime_start, available_at)`; market-based daily series (yields, OAS, VIX) are available at obs_date + 1 day 08:00 ET; monthly releases use the ALFRED `realtime_start`
- `macro_pit(series_ids, as_of, lookback) -> DataFrame` (latest vintage known at as_of)
- Derived transforms: YoY %, 3m annualised, z-score vs 10y, curve slope, real rate (DGS10 − core PCE YoY)

## Out of scope
Non-US macro; economic-calendar consensus figures.

## Files
`src/atlas/data/macro/{fred_client.py,pit.py,transforms.py}`, `config/macro.yaml`, fixtures (CPI with vintages, DGS10), tests

## Tasks
1. Client + mocked responses.
2. Vintage ingestion; a test that CPI for March 2020 as of 2020-04-05 is unavailable and as of 2020-04-15 is the first-release value.
3. Daily market series availability rule.
4. Transforms with known-value tests.

## Acceptance criteria
- PIT queries never return a value whose release came after as_of; revisions are handled.

## Verify
`uv run pytest tests/unit/data/macro -q`
