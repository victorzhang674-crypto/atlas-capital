# Stage 08 — Universe & historical index membership
**Phase:** 1 · **Size:** M · **Depends on:** 07 · **Spec refs:** §8, §16 (survivorship)

## Goal
Know which instruments were investable **on each date**: S&P 500 membership history (to limit survivorship bias), the ETF macro universe, sectors, and liquidity filters.

## In scope
- `instruments` reference table: symbol, name, asset_class, GICS sector/industry, exchange, first/last trade date, is_etf, shortable flag (default True for S&P 500 stocks)
- S&P 500 membership history: current constituents + the change log (Wikipedia "List of S&P 500 companies" tables, parsed with pandas.read_html from a **recorded HTML fixture**; live refresh is optional), rebuilt into `index_membership(symbol, start_date, end_date)`
- ETF macro universe from `config/universe.yaml` (~30 ETFs: SPY, QQQ, IWM, EFA, EEM, the 11 sector SPDRs, TLT, IEF, SHY, LQD, HYG, GLD, SLV, USO, DBC, UUP, FXE, FXY, VNQ, BIL)
- `Universe.members(as_of, name="sp500") -> list[str]`, `Universe.tradable(as_of, min_price=5, min_adv_usd=20e6, min_history_days=252)`
- Ticker change mapping (FB→META etc.) via a manual `config/ticker_changes.yaml`
- Document the residual survivorship bias in `docs/data-limitations.md`

## Out of scope
Paid point-in-time constituent data.

## Files
`src/atlas/data/universe.py`, `src/atlas/data/reference.py`, `config/universe.yaml`, `config/ticker_changes.yaml`, fixtures, tests, `docs/data-limitations.md`

## Tasks
1. Parse the fixture HTML → current list + changes; tests on known adds/removes.
2. Reconstruct membership intervals backwards from the change log; test known dates (e.g. TSLA added 2020-12-21).
3. Instruments table with sectors; ETF universe loader.
4. `tradable()` filters using prices from Stage 07 (ADV = 20-day mean of close×volume, PIT).
5. Ticker-change mapping test.

## Acceptance criteria
- `members(date(2019,6,3))` excludes TSLA and includes names later removed; `tradable` uses only data before `as_of`.

## Verify
`uv run pytest tests/unit/data/test_universe.py -q`
