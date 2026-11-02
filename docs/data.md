# Data: sources, terms, limitations and refreshing

Atlas keeps all market data in a local, point-in-time lake (`data/` by default, `data.lake_path`
in `config/fund.yaml`; gitignored). One command fills it and one keeps it current:

```bash
uv run atlas data sync --from 2015-01-01   # backfill (resumable)
uv run atlas data update                   # fetch only what is new
uv run atlas data status                   # rows, date ranges, last sync, coverage, quality
uv run atlas data quality --as-of 2024-05-01
```

Strategy and agent code never reads the lake directly: it goes through `DataView`
(Stage 13), which answers only with what was known at the decision time. The per-dataset
point-in-time rules and known gaps are in [data-limitations.md](data-limitations.md).

## Datasets

`atlas data sync --datasets a,b` takes these names (default: all, in this order):

| Dataset | Steps (checkpoint names) | Source | Tables | Mode |
|---|---|---|---|---|
| `universe` | `universe` | Wikipedia: S&P 500 constituents and change log | `index_membership`, `instruments` | snapshot (daily) |
| `prices` | `prices` | Yahoo Finance (yfinance), Alpaca IEX bars as fallback | `prices_daily`, `corporate_actions` | windowed per symbol |
| `fundamentals` | `fundamentals` | SEC EDGAR XBRL `companyfacts` + `submissions` | `fundamentals_facts` | snapshot per symbol |
| `macro` | `macro` | FRED / ALFRED (every vintage) | `macro_observations`, `macro_series` | snapshot per series |
| `news` | `news` | Alpaca news (Benzinga); Yahoo (recent only) without Alpaca keys | `news` | windowed per symbol, 90-day windows |
| `events` | `earnings`, `upcoming`, `macro_calendar`, `reactions` | EDGAR 8-K item 2.02 / 10-Q / 10-K; Yahoo calendar; FOMC YAML + FRED release dates; computed from prices | `events`, `macro_events`, `earnings_reactions` | snapshots |
| `sentiment` | `news_sentiment`, `social` | Loughran-McDonald lexicon over stored news; StockTwits / Reddit (off by default) | `news_sentiment`, `social_posts` | windowed |
| `quality` | `quality` | Stage 13 checks over the lake | `data_quality_issues` | snapshot (the next decision day) |

Symbols: every member of the S&P 500 at any time in the window (from `index_membership`, so
the list is free of survivorship bias) plus the tickers of every ticker-list universe in
`config/universe.yaml` (the macro ETFs). The EDGAR steps skip the ETFs, and the scheduled-dates
snapshot asks only for today's members. `--symbols A,B` replaces the list.

## Sources

- **Wikipedia** — `List_of_S&P_500_companies` (today's constituents) and
  `Historical_components_of_the_S&P_500` (the change log), one request per page with a
  descriptive User-Agent (`data.reference.user_agent`). Membership before 2018 has
  anomalies the change log cannot resolve (see data-limitations.md › Universe).
- **Yahoo Finance via yfinance** — daily OHLCV, splits and dividends (raw prices are
  un-adjusted from the split history, one extra request per symbol), the next earnings and
  ex-dividend dates, and recent news when Alpaca is not configured. yfinance is an unofficial
  client of Yahoo's web endpoints; it can break or be throttled without notice.
- **Alpaca Market Data** (needs `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`) — IEX daily
  bars as the fallback for symbols Yahoo misses, and the Benzinga news archive from 2015
  (headlines and summaries only; `include_content=false`).
- **SEC EDGAR** (needs `SEC_USER_AGENT` with contact details) — XBRL company facts and the
  filing index (acceptance times give `available_at`), plus 8-K/10-Q/10-K filings for actual
  earnings dates. Raw JSON is archived under `data/raw/sec/`.
- **FRED / ALFRED** (needs `FRED_API_KEY`) — the 22 series in `config/macro.yaml` with every
  vintage, and the release calendars of CPI and payrolls. Raw JSON is archived under
  `data/raw/fred/`.
- **Federal Reserve** — FOMC meeting dates, curated in `config/macro_calendar.yaml`.
- **Loughran-McDonald Master Dictionary** — downloaded once to
  `data/reference/lm_lexicon.csv` (`data.sentiment.lexicon_url`).
- **StockTwits / Reddit** — optional social posts (`data.sentiment.social.enabled: false`).

## Licences and terms of use

Notes for a research and paper-trading project, not legal advice. Atlas stores data for its
owner's own use and never redistributes it; check each provider's current terms before
sharing data or anything derived from it.

- **Wikipedia** text is CC BY-SA 4.0; the membership list derived from it is a list of facts.
  Wikimedia's robot policy asks for a descriptive User-Agent and a modest request rate (two
  requests per sync).
- **Yahoo Finance** data is for personal, non-commercial use under Yahoo's terms; yfinance is
  not endorsed by Yahoo. Do not redistribute prices fetched this way.
- **Alpaca** market data and news are licensed to account holders under Alpaca's market data
  terms (news from Benzinga); the free tier allows 200 requests per minute
  (`data.news.min_request_interval_seconds: 0.3`). No redistribution.
- **SEC EDGAR** data is public, under the SEC's fair-access policy: a User-Agent naming the
  requester and at most 10 requests per second (`data.fundamentals.max_requests_per_second`).
- **FRED** is free with an API key under the FRED API terms of use; some series belong to third
  parties (for example ICE BofA credit spreads, CBOE VIX) and keep their owners' copyright, so
  cite FRED and the original source and do not republish those series.
- **Loughran-McDonald** word lists are free for academic and research use; commercial use
  needs a licence from the authors.
- **StockTwits / Reddit** APIs have their own developer terms and rate limits; they are off by
  default.

## Limitations

The full list, per dataset, is in [data-limitations.md](data-limitations.md). The ones that
matter most for a backfill:

- **Delisted names.** Yahoo has no bars for most companies that were acquired or went
  bankrupt, so former members can lack prices (they are `empty` in the sync summary, not
  errors). Survivorship-free membership does not mean survivorship-free prices.
- **Former members' fundamentals.** EDGAR's ticker map lists current registrants only;
  `config/cik_overrides.yaml` adds former members since 2018. Members that left in 2015–2017
  get "no CIK" (fundamentals and earnings `empty`).
- **Short histories.** Companies listed after the window start (spin-offs, IPOs) have fewer
  years of prices; `atlas data status` counts them against the 8-year rule.
- **Mixed vendors.** A symbol served by the Alpaca fallback has IEX-only volume; the quality
  report warns (`vendor_mix`).
- **News depth.** Without Alpaca keys news is Yahoo's recent headlines only. Untagged
  market-wide news is not backfilled (only articles tagged with a synced symbol, which
  includes SPY and QQQ).
- **Scheduled dates.** Yahoo gives only the next dates as of each sync, so the history of
  announced earnings dates starts with the first sync (estimates fill the gap; see
  data-limitations.md › Events).
- **Raw payloads.** Only EDGAR and FRED answers are archived under `raw/`; prices and news
  are re-fetched when needed.

## How to refresh

1. Put the API keys in `.env` (never in `config/*.yaml`): `SEC_USER_AGENT`, `FRED_API_KEY`
   and, for news history and the price fallback, `ALPACA_API_KEY_ID` /
   `ALPACA_API_SECRET_KEY`. A dataset whose key is missing is reported as an error and the
   others still run (`atlas data sync` then exits 1).
2. Backfill once: `uv run atlas data sync --from 2015-01-01`. It can take hours (news is the
   longest step; the request rate is bounded by each provider's throttle). Progress bars show
   each step; a summary table ends the run.
3. **Resume.** The checkpoint `data/cache/sync_state.json` (`data.sync.state_file`) records,
   per step and key, the window already covered. It is written after every chunk, so if a
   run stops (Ctrl-C, a crash, a network outage) re-run the same command: covered chunks are
   skipped and the rest is fetched. Keys that failed at the vendor stay uncovered and are
   retried; keys the vendor has no data for are `empty` and not retried.
4. **Keep it current**: `uv run atlas data update` (daily; Stage 61 schedules it at 06:30 New
   York). It starts from the earliest covered date, so each symbol is fetched from its last
   covered day (minus `data.sync.update_overlap_days` for late corrections), new index members
   are backfilled in full, and snapshot steps (universe, fundamentals, macro, events, quality)
   refresh once per day. Prices stop at the latest session whose bar is published (close +
   15 minutes).
5. **Check**: `uv run atlas data status` (read-only, safe while a sync runs) shows rows, keys
   and date ranges per table, the last sync per dataset, coverage of the current members
   (≥ 8 years of prices, fundamentals, every macro series, news, earnings) and the latest
   persisted quality report. `uv run atlas data quality --as-of D [--persist]` runs the quality
   checks for one decision day.
6. **Start over** for one dataset by deleting its entries from the state file, or delete the
   whole file to re-fetch everything (stored rows are upserted, so nothing is duplicated).

Every writing run gets an id: its JSON log is `data/runs/<run_id>/sync.log` (`update.log`,
`quality.log`) and its audit trail `data/runs/<run_id>/audit.jsonl` (`RUN_STARTED`, one
`DATA_SYNC` event per step with keys, rows, failures and timing, `RUN_FINISHED`). Concurrency
is one thread pool per provider (`data.sync.workers`); keep `yahoo: 1` (yfinance is not
thread-safe). Chunk sizes are `data.sync.chunk_size`.
