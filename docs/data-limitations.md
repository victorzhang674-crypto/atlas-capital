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

## Fundamentals (Stage 09)

Fundamentals come from SEC EDGAR's free XBRL **companyfacts** API, stored **as filed**: every
filing that reports a period (the original 10-Q, the 10-K comparative a year later, a 10-Q/A)
is its own row in `fundamentals_facts`, and a query at `as_of` uses, per tag and period, the
latest filing that was public before the decision cutoff. So a **restatement** is seen only
from the moment the restating filing was accepted (JPM's 2012 10-Q/A cut Q1 2012 revenue from
26,712 M to 26,052 M on 2012-08-09; MSFT recast fiscal 2017 for ASC 606 in its 2017-10-26
10-Q), never earlier.

### What is left

1. **Knowledge time is the EDGAR acceptance time.** It comes from the submissions API
   (`acceptanceDateTime`, which is UTC: checked against EDGAR's 17:30 ET filing-date rule,
   Stage 09 artifacts). A filing accepted after 17:30 ET carries the next day's filing date
   but is public the same evening (AAPL's FY2023 10-K: accepted 2023-11-02 18:08 ET, filed
   2023-11-03). When a filing is not in the index, `available_at` falls back to the open of the
   first session after the filing date (conservative, at most a day late).
2. **Former members need a reviewed CIK.** EDGAR's `company_tickers.json` lists current
   registrants only, and an old ticker may now belong to another company (APC is now ARKO
   Petroleum, STI is now Solidion). `config/cik_overrides.yaml` pins a verified **CIK** for every
   S&P 500 member since 2018-01-01 that has left the index (164 names, each checked against its
   own 10-K/10-Q filings during its membership; `build_cik_map` applies them). Two have none:
   First Republic Bank and Signature Bank filed with the FDIC, not the SEC, so they have no
   fundamentals. Members that left before 2018 (before the backtest window) are not covered.
3. **Coverage depends on the tag list.** Standardised concepts are defined in
   `config/xbrl_map.yaml` as fallback lists of us-gaap/dei tags. A company that reports a line
   under a tag the map does not list gets **NaN** for it, never a guess; so does a business
   that has no such line (a bank has no gross profit, operating income, capex or R&D tag, so
   its gross margin, operating margin and FCF are NaN). A bank that tags no total revenue gets
   net interest income + noninterest income (its `RevenueFromContractWithCustomer…` tag covers
   fee income only). Total liabilities is NaN for filers that
   report only "liabilities and equity". Total debt adds long-term debt (with its current
   portion) and short-term borrowings; leases are not included.
   Within one period the first tag in the list wins, even if a later tag was filed later: a
   restatement filed under a different tag than the original is not picked up.
4. **Only non-dimensional facts.** companyfacts carries company-wide values, not segment or
   share-class (**dimension**) breakdowns; multi-class companies may lack the cover-page share
   count (then `CommonStockSharesOutstanding` from the balance sheet is used).
5. **Derived quarters.** Cash-flow lines are reported year-to-date in 10-Qs and many companies
   report the fourth quarter only inside the fiscal year. Discrete quarters are therefore
   derived (H1 − Q1, 9M − H1, **Q4** = FY − Q1..Q3). A derived EPS quarter is approximate
   (share counts change during the year). TTM is the fiscal year at a fiscal year end, else
   four contiguous quarters; a metric whose period ended more than 400 days before `as_of`
   (`max_staleness_days`) is NaN.
6. **Ratios use simple definitions.** ROE and ROA divide TTM net income by the latest equity
   and assets (not averages); a ratio with a missing or non-positive denominator (negative
   equity) is NaN.

## Macro (Stage 10)

US macro series come from FRED, and their history **as it was known** comes from ALFRED, the
St. Louis Fed's archive of past **vintages** (`atlas.data.macro`). Every stored row is one value
of one observation in one vintage, so a backtest sees the first release of a statistic until a
revision was published, then the revised value (`Macro(lake).pit(series_ids, as_of, lookback)`).

### What is left

1. **Release times are scheduled times, not posting times.** A release series is known from
   its vintage's `realtime_start` date at the `release_time` in `config/macro.yaml` (for
   example CPI 08:30 New York). ALFRED records dates, not times, so the time is the publisher's
   scheduled one (the later one where it has varied: core PCE 10:00). With the default 08:00
   decision time a statistic released that morning is first used the next day. FEDFUNDS has no
   fixed posting time and is taken at 17:00.
2. **Market series use a fixed rule, not the vintage.** Treasury yields, credit spreads and VIX
   are known `obs_date` + 1 calendar day at 06:00 New York (`market_lag_days`,
   `market_available_time`), even where FRED posted the value later (Friday's yield appears on
   FRED on Monday; the quote itself was public on Friday). The close of day D (VIX at 16:15,
   Treasury yields and ICE spreads published that evening) is public by then, so it is used at
   the 08:00 decision of D + 1. The time must stay before the decision time: a value must be
   known *strictly before* the cutoff, so one available at 08:00 would wait until D + 2.
   Revisions of a market value wait for their own vintage.
3. **Credit spreads have three years of history.** ICE licensing limits FRED to the last
   three years of the ICE BofA option-adjusted spreads (`BAMLH0A0HYM2`, `BAMLC0A0CM`): data
   starts **2023-10-30** and ALFRED has no older vintages. A 2018+ backtest has no free
   HY/IG spread before late 2023; a 10-year z-score of them is NaN for years.
4. **Before ALFRED's first vintage, history is "known from the first vintage".** A series'
   oldest observations carry the date of its first archived vintage (DGS10's archive starts
   2005-06-28; CPIAUCSL's 1972-07-21), which is later than the real release, so they look
   unavailable before that date. Ingestion reads every vintage from `vintage_start`
   (1776-07-04 = all) and observations from `observation_start` (2000-01-01).
5. **Licensed or delayed series.** FRED posts University of Michigan sentiment (`UMCSENT`)
   with a delay under its licence; the vintage dates reflect FRED's posting, so the model sees
   it later than subscribers did (conservative, never early).
6. **Units are FRED's.** Values are stored in each series' own units (`macro_series.units`):
   percent for yields, spreads and rates, index levels for prices and activity, thousands of
   persons for payrolls, millions or billions of USD for balance sheets and money. The
   transforms (`yoy_pct`, `annualized_3m_pct`, `curve_slope`, `real_rate`) return percent or
   percentage points; `zscore` is unitless and needs 5 of its 10 years of history.
7. **Missing values.** FRED's "." (a holiday, no quote) is stored as NaN; such an observation
   is left out of point-in-time results, and a later vintage that blanks a value hides the old
   one.

## News (Stage 11)

Company and market news come from Alpaca's news API (the Benzinga feed, history from 2015) with
Yahoo Finance as a recent-only fallback (`atlas.data.news`). Each vendor article is stored once
in `news`, keyed by `<provider>-<vendor id>`, and an agent cites it as `news:<article_id>`
(`News(lake).pit(symbol, as_of)`), an id that does not change when the news is re-ingested.

### What is left

1. **Only the headline and summary are stored.** Article bodies are never requested
   (`include_content=false`) and summaries are cut to 1000 characters, for copyright and size.
   An agent reasons from headlines and summaries, not full text. Yahoo items have no summary.
2. **Availability is the publish time, and later edits leak.** An article is known from its
   vendor publish time (`available_at = published_at`, strictly before the cutoff). The stored
   headline and summary are the vendor's latest version at ingest time; when the vendor
   updated an article after publishing it (`updated_at`), a backtest reads the edited text at
   the original time. Benzinga's edits are mostly small, but a corrected headline can leak.
3. **Yahoo is recent only and found by search.** The fallback serves whatever Yahoo's search
   API still lists (about the last days to weeks), so a historical backfill without Alpaca keys
   holds almost no history. yfinance's `Ticker.get_news` endpoint answered HTTP 404 in
   October 2026, so the adapter uses the search API, whose results are kept only when Yahoo
   tagged them with the ticker. Without `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`, news
   ingestion runs on Yahoo alone and logs `news.alpaca_unavailable`.
4. **Candidates are the vendor's tags.** News about a ticker means articles the vendor tagged
   with it; an untagged article that names the company is missed. A headline "names" the
   ticker only through the symbol (`$AAPL`, `(AAPL)`, `NASDAQ:AAPL`, or a bare symbol of three
   or more letters), never the company name, so most Benzinga headlines score as tag-only
   (relevance 0.5 rather than 1.0).
5. **Roundups are dropped.** An article tagged with more than 10 tickers is a roundup ("12
   stocks to watch") and is left out for a ticker unless its headline names that ticker.
6. **Duplicates are hidden at query time.** The same story from two sources, or re-published,
   is one story when the copies are at most 48 hours apart and their normalised content hashes
   match or their headlines score at least 92 (rapidfuzz ratio). The earliest-known copy is
   kept and later copies are hidden, using only articles known at `as_of` among the ones the
   query considers (those tagged with the ticker, or the market-wide ones), so a duplicate
   never hides news based on the future. A vendor id stored twice (a re-dated article) is
   cited once, from its earliest version. Both copies stay in the table. Differently worded
   reports of one event (below 92) count as separate articles.
7. **Market-wide news** is untagged news or news tagged with SPY or QQQ (`market_symbols`); it
   is only as broad as the vendor's tagging.
