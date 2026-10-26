# Stage 07 — Price data providers (yfinance, Alpaca)
**Phase:** 1 Data · **Size:** M · **Depends on:** 06 · **Spec refs:** §11, §16

## Goal
Reliable daily OHLCV (raw + adjusted), splits and dividends for stocks and ETFs, behind a provider interface with caching and a fallback.

## In scope
- `PriceProvider` protocol: `fetch_daily(symbols, start, end) -> DataFrame`, `fetch_actions(symbols, start, end) -> DataFrame` (splits, dividends)
- `YFinanceProvider` (batch download, retry with backoff, rate limiting, handling of empty/delisted symbols)
- `AlpacaPriceProvider` (data API v2, IEX feed on the free tier; adjustment=all/raw); optional, enabled when keys are set
- `FallbackPriceProvider(primary, secondary)`
- Curated tables: `prices_daily` (symbol, date, open, high, low, close, adj_close, volume, vendor, available_at), `corporate_actions` (symbol, ex_date, type, ratio/amount, available_at)
- `available_at` = session close + 15 min (calendar from Stage 05)
- Recompute the adjustment factor ourselves from actions and cross-check it against the vendor's adj_close (record mismatches > 0.5%)
- Recorded fixtures for 3 symbols (AAPL with its 2020 split, SPY, a delisted name) under `tests/fixtures/prices/`

## Out of scope
Intraday bars; quality scoring (Stage 13); universe (Stage 08).

## Files
`src/atlas/data/providers/{base.py,yfinance_provider.py,alpaca_provider.py,fallback.py}`, `src/atlas/data/prices.py` (ingest → lake), tests + fixtures

## Key interfaces
```python
ingest_prices(lake, provider, symbols, start, end) -> IngestReport{rows, symbols_ok, symbols_failed, mismatches}
```

## Tasks
1. Protocol + fake provider for tests.
2. yfinance adapter normalising columns/timezones; fixture-based tests (no network).
3. Alpaca adapter (HTTP via httpx; respx-mocked tests).
4. Fallback logic tests (primary empty → secondary used; both fail → `DataError`).
5. Ingest to lake with `available_at`; idempotent re-ingest.
6. Adjustment-factor cross-check test on the AAPL 4:1 split (2020-08-31).
7. One `@pytest.mark.live` smoke test per provider.

## Acceptance criteria
- Ingest of the fixtures yields correct split-adjusted series; re-running is idempotent; a failed symbol never aborts the batch.

## Verify
`uv run pytest tests/unit/data -q` (and optionally `-m live`)
