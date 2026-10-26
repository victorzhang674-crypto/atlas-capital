# Stage 09 — Point-in-time fundamentals (SEC EDGAR)
**Phase:** 1 · **Size:** L · **Depends on:** 06 · **Spec refs:** §3.2, §11

## Goal
Standardised quarterly/annual fundamentals **as filed**, keyed by the SEC acceptance timestamp, so a backtest only sees what was public at the time.

## In scope
- EDGAR client: `company_tickers.json` (ticker→CIK), `companyfacts` API; required `User-Agent` from config; 10 req/s rate limit; raw JSON cached in `raw/sec/`
- Parse us-gaap facts into a long table `fundamentals_facts(cik, symbol, tag, unit, value, period_start, period_end, fp, fy, form, filed, accepted_at→available_at, accn)`
- Concept mapping (`config/xbrl_map.yaml`), with fallbacks across tags: Revenue, GrossProfit, OperatingIncome, NetIncome, EPS diluted, SharesOutstanding (dei), TotalAssets, TotalLiabilities, StockholdersEquity, CashAndEquivalents, TotalDebt (sum of parts), OperatingCashFlow, CapEx, R&D, SBC
- Derive Q4 from FY minus Q1–Q3 where only the annual figure is reported; TTM calculation for flow items; the latest balance for stock items
- Restatements: keep each filing; the PIT query returns the **latest value filed before as_of** for each period
- Wide PIT view: `fundamentals_pit(symbol, as_of) -> Series` of standardised metrics + derived ratios (gross margin, op margin, ROE, ROA, debt/equity, FCF = OCF − CapEx)
- Filing acceptance timestamps from the submissions API (the `acceptanceDateTime` field) where `filed` alone is only a date; otherwise assume filed date + 1 session

## Out of scope
Analyst estimates; non-US filers; text of filings (MD&A is optional in Stage 29).

## Files
`src/atlas/data/fundamentals/{edgar_client.py,parser.py,mapping.py,pit.py}`, `config/xbrl_map.yaml`, fixtures (AAPL, MSFT, JPM companyfacts JSON trimmed), tests

## Tasks
1. CIK mapping + client with rate limiter (respx-mocked).
2. Fact parser → long table; test against fixture values (e.g. AAPL FY2023 revenue = 383,285 M).
3. Concept mapping with fallbacks; bank-specific gaps (JPM has no gross profit → NaN, not an error).
4. Q4 derivation + TTM; tests.
5. PIT query handling restatements: a test where a restated value is filed later and the earlier as_of still sees the original.
6. Derived ratios; look-ahead test.

## Acceptance criteria
- `fundamentals_pit("AAPL", as_of)` matches fixture-derived figures; restatement and look-ahead tests pass.

## Verify
`uv run pytest tests/unit/data/fundamentals -q`
