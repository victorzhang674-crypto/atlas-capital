# Data limitations

What the free data behind Atlas can and cannot tell us, and the biases left after the
mitigations. Read this before trusting a backtest number. Each section names the stage that
introduced the data; later stages add their own sections.

## Universe and S&P 500 membership (Stage 08)

The S&P 500 universe is rebuilt point-in-time from free Wikipedia tables: today's constituents
plus the change log, walked backwards (`atlas.data.universe.build_membership`). That removes
most **survivorship bias** (names that left the index are members on the dates they were in,
so a 2019 backtest sees AIV, CELG, ETFC, …, not today's list), but not all of it.

### What is left

1. **Delisted names have no free prices.** This is the largest residual survivorship bias.
   `members()` includes former members, but `tradable()` needs price history, and yfinance
   returns nothing for most acquired or bankrupt companies (SIVB in the Stage 07 fixtures).
   Those names drop out of the tradable set while they were still in the index, so the
   tradable universe leans towards companies that survived to today. A paid point-in-time
   price source (CRSP, Norgate) is the fix; it is out of scope (spec A3, free data only).
2. **The change log is incomplete before about 2015.** It has 0–7 rows a year before 2007,
   8–19 from 2007 to 2014, and 17–30 a year from 2015 (2016–2026 all ≥ 17), close to the
   real number of changes. Before 2015 some joins and leaves are missing, so membership drifts from the truth
   the further back you go. The backtest starts in 2018 (spec A7).
3. **Unknown start dates.** A member whose "added" row is missing gets the constituents
   table's "Date added" (only for names the log never mentions), else the index's inception
   date, 1957-03-04 (`SP500_INCEPTION`). Treat a 1957 start as "at least since the log began".
4. **Source errors are kept visible, not fixed.** A row the reconstruction cannot apply
   (an added name that is not a member just after, a removed name that already is, a cell
   that is not a ticker) becomes a `MembershipAnomaly`, is logged as a warning and changes
   nothing. In the recorded snapshot (2026-10-27) there are **13 anomalies, all dated before
   2018-01-01** (a test pins this): mostly reused tickers and gaps before 2000, plus one
   anachronism (the log writes Under Armour's class C shares, added 2016-04-08 as `UA.C`,
   under their later ticker `UA`; so before 2016-12-07 the reconstruction gives the class A
   line `UAA` a start of 2016-04-08 instead of 2014-05-01, and `UA` a start of 1957).
5. **Ticker identity.** An instrument is identified by its latest ticker (FB is META in every
   year). Renames come from the manual list `config/ticker_changes.yaml` (22 entries, each
   with its source); a rename missing from it splits one company into two names, each a
   member for its own period (still correct membership, but two price histories). Every
   date there comes from the change log, an older log revision or the company's SEC filings;
   four (`sec-consistent`: WLP, TSO, IR, ANTM) are consistent with the filings, which do not
   name the first trading day outright. **Reused tickers** (the same ticker for two companies: DD for E.I. du Pont and for
   today's DuPont, T for AT&T Corp. and SBC/AT&T Inc., AGN, Q) cannot be told apart without a
   permanent security id: before the reuse, both companies share one symbol (one anomaly
   each, all before 2018).
6. **Effective date, not announcement date.** Membership on day D uses changes effective on
   or before D. S&P announces changes days earlier, so at D's decision time the change is
   known; but a strategy that trades the announcement (buy on announcement, before inclusion)
   cannot be tested, because announcement dates are not recorded.
7. **Sectors, names and exchanges are today's.** GICS sectors and sub-industries in
   `instruments` are the current classification, not point-in-time. GICS changed a lot
   (2018: Communication Services created from Telecom, Technology and Consumer
   Discretionary; 2023: payments companies moved to Financials), so sector-neutral or
   sector-tilted backtests before those dates use the later sectors. Former members have no
   sector or exchange at all (the change log names the security only).
   `first_trade_date` / `last_trade_date` come from the prices in the lake **when the
   reference data was last refreshed**: null if prices were ingested later, and stale until
   the next `refresh_universe_reference`. `tradable()` reads `prices_daily` directly, so it
   never depends on them.
8. **One snapshot.** The fixtures record one day (constituents revision 1382178819, change
   log revision 1379179365). Re-running `refresh_universe_reference` on a later live snapshot
   rebuilds the whole history from that snapshot; Wikipedia edits can change past intervals.

### Liquidity filters

`tradable()` uses raw close and raw volume (Stage 07 `prices_daily`), bars known strictly
before the decision cutoff only. Alpaca's free IEX feed reports IEX-only volume (Stage 07
backlog), so ADV is understated for symbols served by Alpaca: a name can fail `min_adv_usd`
there while passing with Yahoo's consolidated volume.

## Prices (Stage 07)

- `prices_daily.adj_close` is the vendor's value at fetch time and embeds dividends and splits
  after the bar's date: it is **not** point-in-time. Use raw prices with `corporate_actions`
  (`atlas.data.prices.adjustment_factors`).
- Corporate actions are known from their ex-date bar; announcement dates are not captured.
