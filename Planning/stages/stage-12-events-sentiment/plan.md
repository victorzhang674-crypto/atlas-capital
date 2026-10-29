# Stage 12 — Events calendar & sentiment sources · Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (inline TDD). Steps use checkbox (`- [x]`) syntax; tick only after the step's tests have run and passed in this chat.

- **Brief:** [brief.md](brief.md) (`Planning/stages/stage-12-events-sentiment/brief.md`)
- **Status:** DONE (2026-10-29; see [verification.md](verification.md)); deviations as `Ruling:` lines in [worklog.md](worklog.md)
- **Gate check:** Stage 11 is DONE (its [verification.md](../stage-11-news/verification.md) says ALL CRITERIA VERIFIED; evidence block also in `PROGRESS.md`); `uv run pytest -q` → 1190 passed, 7 deselected, exit 0 (2026-10-29) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution method:** executing-plans (native, inline TDD). The owner asked in chat (2026-10-29): "can you help me to generate a fully broken-down plan for Stage 12, and execute the plan according to the planning files?", so the plan is executed straight after it is written (as in Stages 06–11).
- **Branch:** `stage-12-events-sentiment` from `main` `34f731d`.

## Context

Stage 11 is `DONE` (11/66). Stage 12 adds corporate and macro events and a numeric news-sentiment baseline. Later consumers checked: Stage 13 (`DataView.events(...)`, `DataView.sentiment(symbol)` wrap this stage), 14 (sync `--datasets events`), 31 (News & Event analyst tools `get_upcoming_events` (earnings date/time, ex-div), `get_past_earnings_reactions` (last 8)), 32 (Sentiment analyst `get_sentiment_series` (daily decayed lexicon score, 90d), `get_social_posts` if enabled), 33 (macro economist), 40 (Event-Driven pod: names with earnings within 10 sessions or 60 days after; PEAD).

**Goal:** know upcoming and past corporate events (earnings especially) and macro events point-in-time, and give agents a cheap, deterministic numeric sentiment baseline.

**Architecture:** two packages. `atlas.data.events`: `base` (the `events` and `macro_events` tables, the event-frame contract, time-of-day classification), `earnings` (earnings dates from EDGAR filings, the optional Yahoo upcoming-earnings provider, their ingestion), `reactions` (`earnings_reactions`: price reaction after each earnings event, available only after its window closes), `macro_calendar` (FOMC from `config/macro_calendar.yaml`, CPI/NFP dates from FRED release calendars), `pit` (`upcoming_events`, `past_events`, `Events` reader; dividend ex-dates derived from Stage 07's `corporate_actions`). `atlas.data.sentiment`: `lexicon` (Loughran-McDonald word lists: download/convert, load, score text, `news_sentiment` table), `aggregate` (decayed per-symbol score and its daily series over Stage 11's de-duplicated news), `social` (optional StockTwits/Reddit providers, off by default, `Evidence` output).

**Tech stack:** httpx 0.28 + respx, yfinance 1.7 (`Ticker.calendar`), pandas 3, Stage 06 `Lake`, Stage 05 `AsOf` / `pit_filter` / `assert_no_lookahead` / `TradingCalendar` / `bar_available_at` / `get_clock`, Stage 07 `RetryPolicy` / `call_with_retry` / `Throttle` / `adjustment_factors` / `PRICES_DAILY` / `CORPORATE_ACTIONS`, Stage 09 `EdgarClient` / `CikMap`, Stage 10 `FredClient`, Stage 11 `News` / `news_pit_detail` / `Evidence`.

**Spec:** brief above; design spec §3 (point in time everywhere; fail safe), §6 (News & Event / Sentiment analysts), §8 (Event-Driven pod: names with earnings in the next 10 days; PEAD), §15 A3 (free data only); stage protocol §4–§5; Stage 09 Backlog (8-K `items` 2.02 for earnings dates); Stage 07 Backlog (actions known at the ex-date bar).

## Source probe (2026-10-29, before planning)

- **EDGAR submissions** (with the owner's `SEC_USER_AGENT`): `filings.recent` of AAPL has columns `accessionNumber, filingDate, reportDate, acceptanceDateTime, form, items, …`; 1002 filings back to 2015-10-02, one older page. Every AAPL quarter since 2018 has an 8-K with `items` `"2.02,9.01"` accepted at 20:30/21:30 UTC (16:30 ET), the 10-Q/10-K a day later; plus the 2019-01-02 guidance-cut 8-K 2.02 (16:30 ET). Known AAPL earnings 8-Ks used by tests: 2018-02-01, 2018-05-01, 2018-07-31, 2018-11-01, 2019-01-02, 2019-01-29, 2019-04-30, 2019-07-30, 2019-10-30, 2020-01-28, 2020-04-30, 2020-07-30 (18:55 ET), 2020-10-29, …, 2026-07-30.
- **FRED** (owner's `FRED_API_KEY`): `fred/release/dates?release_id=10` (CPI) and `=50` (Employment Situation) with `include_release_dates_with_no_data=true` list past and scheduled dates (to 2026-12). They include revision-only dates: CPI seasonal-factor days (2019-02-11, 2020-02-11, 2021-02-08, 2022-02-08, 2023-02-10, 2024-02-09) and NFP 2020-05-11, 2024-01-10, 2024-08-21. `series/observations?output_type=4` (initial release only) of `CPIAUCSL` / `PAYEMS` gives each month's first-release date; every headline release date is in the release calendar, and the revision-only dates are exactly the release dates that are not first-release dates.
- **Federal Reserve** `fomccalendars.htm` (2021–2027) and `fomchistorical{2018,2019,2020}.htm`: scheduled meetings, unscheduled 2019-10-04 (call, no statement), 2020-03-02 (statement 2020-03-03 10:00 ET), 2020-03-15 (Sunday statement 17:00 ET), March 17–18 2020 cancelled, notation votes (2020, 2025-08-22) not meetings.
- **Loughran-McDonald** master dictionary 1993–2025 (sraf.nd.edu → Google Drive CSV, 9.1 MB, 86,553 words): columns `Word, …, Negative, Positive, Uncertainty, …` hold the year added (> 0) or removed (< 0); 2,345 negative, 347 positive, 297 uncertainty words. Licence: "free for use in academic research; commercial licences on request" → not committed; downloaded into the gitignored lake.
- **yfinance** `Ticker(s).calendar` answers a dict with `Earnings Date: [date, …]` (AAPL 2026-11-02, MSFT 2027-01-27, JPM 2027-01-14) plus consensus estimates (ignored: estimates are out of scope).

## Global constraints

- Point in time: a row is known at `as_of` iff `available_at < as_of.cutoff` (strict; `pit_filter`, then `assert_no_lookahead`). Every reader path has a look-ahead test (protocol §5).
- Units: returns are simple fractions (0.01 = 1%); horizons and lookbacks in calendar days unless named `_sessions`; sentiment scores in [−1, 1]; all instants UTC-aware; `event_date` is the New York calendar date.
- Settings in `config/fund.yaml › data.events` and `data.sentiment` (defaults in code; every key commented; repo YAML equals the code defaults, pinned by tests). Macro calendar data in `config/macro_calendar.yaml`.
- No network in unit tests (respx or injected fetch functions); live tests are `@pytest.mark.live`.
- No `datetime.now()`: "now" comes from `get_clock()` or an explicit argument.
- Tests in `tests/unit/data/events/` and `tests/unit/data/sentiment/` (the brief's Verify targets), helpers `events_helpers.py` / `sentiment_helpers.py`, fixtures in `tests/fixtures/events/` and `tests/fixtures/sentiment/`; config tests extend `tests/unit/core/test_config.py`.
- One stage commit `stage-12: Events calendar & sentiment sources` after DONE, then merge into `main` and re-verify (Rule 6A).

## Collisions with the overall plan

None with the roadmap or the spec. Interpretations (logged in the Decisions log):
1. Brief `upcoming_events(as_of, horizon_days)` / `past_events(symbol, as_of, n)` need stored rows: `Events(lake, cfg).upcoming(as_of, horizon_days=None, symbols=None)` and `.past(symbol, as_of, n=None)` read the lake; frame-level `upcoming_events(events, as_of, horizon_days, …)` / `past_events(events, symbol, as_of, n)` keep the brief's names (as Stages 09–11).
2. Modules beyond the brief's Files list: `events/base.py`, `events/reactions.py`, `events/pit.py` (contracts, reactions and queries kept apart from fetching).
3. Columns beyond the brief's `events(symbol, event_type, event_time, time_of_day, available_at)`: `event_id` (stable key), `event_date` (New York date; date-only events have `event_time` NaT), `source`. Macro events go in their own `macro_events` table (no symbol) with `withdrawn_at` (a cancelled meeting disappears from the moment it was cancelled).
4. `data/reference/lm_lexicon.csv` is under the gitignored lake (licence: research use only) → "download script" branch of the brief: `download_lm_lexicon` builds it from the master dictionary; tests use a small excerpt.
5. "Earnings surprise proxy … open-to-close and 3-day abnormal return vs SPY": stored `gap_return`, `oc_return`, `oc_abnormal` (vs SPY) and `car` (3-session buy-and-hold abnormal return vs SPY). The gap is added because open-to-close alone misses an after-close announcement's overnight move.
6. **Flagged:** a scheduled (upcoming) earnings date exists only from the day a provider snapshot was taken; there is no free history of scheduled dates, so in backtests `upcoming` earnings appear only from live snapshots on (macro events and past events are complete). A cadence-based estimate is a Backlog item for Stage 13/40.
7. **Flagged:** a scheduled macro date of year Y is treated as known from 00:00 New York on 1 January Y (the Fed and BLS publish next year's schedule months earlier, so this is never early for a date that kept its schedule). FRED shows actual dates, so a release moved later (2013 and 2025 government shutdowns) appears known from 1 January — documented limitation.

## Design decisions

| # | Decision | Why |
|---|---|---|
| D1 | `events(event_id, symbol, event_type, event_date: date, event_time: ts?, time_of_day, source, available_at)`, PK `(symbol, event_id, available_at)`, partitioned by `symbol`, version 1. `event_type` ∈ {`earnings`}; `time_of_day` ∈ {`BMO`, `AMC`, `unknown`}; `source` ∈ {`sec_8k`, `sec_10q`, `sec_10k`, `yahoo`}. Ids: `sec-<accession>`, `yahoo-<SYMBOL>-<YYYY-MM-DD>`. `available_at` is in the key so every provider snapshot is kept (the earliest knowledge of a date is never overwritten by a later fetch). | Brief contract plus the columns a stable, PIT key needs. |
| D2 | `classify_time_of_day(event_time, calendar)`: New York date *d* of the instant; *d* a session: `< session_open(d)` → `BMO`, `≥ session_close(d)` → `AMC`, else `unknown`; *d* not a session or `event_time` NaT → `unknown`. | Brief labels; a mid-session filing usually follows a pre-open press release, so it is not labelled. |
| D3 | Earnings from filings: `parse_filings(table)` → `accn, form, filed, report_date?, accepted_at, items`. An `8-K` whose `items` (comma list) holds `2.02` is an earnings event (`sec_8k`). A `10-Q`/`10-K` is an earnings event (`sec_10q`/`sec_10k`) only if no 2.02 8-K was accepted in the `periodic_match_days` (60) before it. `event_time = available_at = accepted_at`, `event_date = market_date(accepted_at)`. Filings without `accepted_at` are skipped (counted, warned): no guessed time. History pages are read only when their `filingTo ≥ history_start` (2015-01-01). | Brief: dates from 10-Q/10-K/8-K item 2.02 timestamps; acceptance is when the market could know (conservative: never before the filing). |
| D4 | Upcoming earnings (optional): `EarningsCalendarProvider` protocol (`name`, `fetch_upcoming(symbols) -> DataFrame[symbol, event_date]`); `YahooEarningsCalendar(config, *, calendar_fn=None, sleep)` reads `calendar_fn(vendor_symbol)["Earnings Date"]` (default `yf.Ticker(s).calendar`), earliest date kept, retried then `DataError`. `ingest_upcoming_earnings(lake, provider, symbols, *, known_at=None)`: `known_at` defaults to `get_clock().now()`; rows `source="yahoo"`, `event_time` NaT, `time_of_day="unknown"`, `available_at=known_at`; dates before `market_date(known_at)` dropped. `build_upcoming_provider(cfg)` → `None` when `data.events.upcoming_earnings == "none"`. | Brief "upcoming dates from Yahoo/Finnhub-free (optional provider)"; snapshot time = knowledge time. |
| D5 | Reactions: `earnings_reactions(symbol, event_id, event_time, reaction_date, window_end, gap_return, oc_return, oc_abnormal, car, benchmark, available_at)`, PK `(symbol, event_id)`. Reaction session D0 = first session whose close is after `event_time`; Dp = previous session; Dn = D0 + (`reaction_days` − 1) sessions. Prices are raw bars adjusted inside the window [Dp, Dn] with `adjustment_factors` (splits and dividends with ex-date in the window): `gap = open0/closeP − 1`, `oc = close0/open0 − 1`, `oc_abnormal = oc − oc_SPY`, `car = (closeN/closeP − 1) − (SPY same)`. `available_at = bar_available_at(Dn)`. A missing bar (symbol or benchmark) or a window not yet closed → no row (counted as `skipped`). Only `sec_*` events are used. | Brief: reaction only available after its window closes; fail safe: never a guessed price. |
| D6 | Macro calendar: `config/macro_calendar.yaml` = `fomc: {statement_time, meetings: {YEAR: [decision days]}, unscheduled: [{date, time}], cancelled: [{date, cancelled_at}]}` and `releases: {cpi: {fred_release_id: 10, headline_series: CPIAUCSL, release_time: "08:30"}, nfp: {fred_release_id: 50, headline_series: PAYEMS, release_time: "08:30"}}`, covering FOMC 2018–2027 from the Fed pages. `macro_events(event_id, event_type, event_date, event_time, source, available_at, withdrawn_at?)`, PK `event_id` (`fomc-2020-03-15`, `cpi-2024-01-11`). Release events = FRED release dates that are first-release dates of `headline_series` (`output_type=4`), plus every release date after the last first release (scheduled). Availability: scheduled → 00:00 New York, 1 Jan of the event year; unscheduled FOMC → its statement time; cancelled meeting → `withdrawn_at = cancelled_at`. `ingest_macro_calendar(lake, client: FredClient \| None, *, config)` replaces the rows of each source it rebuilt (`fomc_calendar` always; `fred_release` only with a client), so rescheduled dates do not linger. `FredClient` gains `release_dates(release_id, *, realtime_start)` and `initial_release_dates(series_id, *, observation_start)`. | Brief: FOMC static YAML per year, CPI/NFP from ALFRED release calendars; revision-only dates are not market events. |
| D7 | Queries over a combined *event view* (`EVENT_VIEW_COLUMNS = event_id, symbol (None for macro), event_type, event_date, event_time, time_of_day, source, available_at, withdrawn_at`). Known = `pit_filter` + `assert_no_lookahead`, minus rows withdrawn before the cutoff. `upcoming_events(view, as_of, horizon_days, *, scheduled_match_days)`: `event_date ∈ [as_of.day, as_of.day + horizon_days]` and (`event_time` NaT or `≥ cutoff`); `yahoo` rows only from each symbol's latest known snapshot, dropped when a known actual earnings event of that symbol has `event_date ≥ scheduled date − scheduled_match_days` (10); one row per `event_id`; sorted by `event_date`, `event_time` (NaT last), `symbol`, `event_id`. `past_events(view, symbol, as_of, n)`: the symbol's known non-`yahoo` events that happened before the cutoff (`event_time < cutoff`, or NaT and `event_date < as_of.day`), newest first, `n` rows. `dividend_events(actions)`: each `dividend` action → `event_type="ex_dividend"`, `event_id="div-<SYMBOL>-<ex_date>"`, `event_time` NaT, `source="corporate_actions"`, its `available_at` (the ex-date bar). `Events(lake, cfg)`: `.upcoming`, `.past`, `.reactions(symbol, as_of, n=None)` (known reactions, newest first). Defaults `horizon_days` 14, `past_n` 8. | Brief task 5; Stage 31 tools (upcoming incl. ex-div, last 8 reactions); Stage 40 (earnings within 10 sessions). |
| D8 | Lexicon: `build_lm_lexicon(master) -> DataFrame[word, category]` (`negative`/`positive`/`uncertainty` where the master column > 0; words upper case, sorted); `download_lm_lexicon(config, *, http=None) -> Path` (GET `lexicon_url`, follow redirects, convert, atomic write to `lexicon_file`); `load_lexicon(path) -> Lexicon(positive, negative, uncertainty: frozenset[str], version: str)` (`version` = first 12 hex of sha256 of the sorted file content; missing file → `DataError` naming `download_lm_lexicon`). `tokenize(text)` = runs of ASCII letters, upper-cased. `score_text(text, lexicon, negation_window=3) -> TextSentiment(score, pos, neg, uncertainty, words)`: a positive word with NO/NOT/NONE/NEITHER/NEVER/NOBODY among the `negation_window` tokens before it is not counted (Loughran-McDonald 2011); `score = (pos − neg)/(pos + neg)`, 0.0 when both are 0. Article text = headline + " " + summary. `news_sentiment(article_id, published_at, available_at, score, pos, neg, uncertainty, words, lexicon_version)` PK `(article_id, published_at)`, year partitions (as `news`); `ingest_news_sentiment(lake, lexicon, start, end, *, config=None) -> int`. | Brief: finance lexicon over headline + summary → `news_sentiment(article_id, score, pos, neg, uncertainty)`. |
| D9 | Aggregate: weight of article *i* at cutoff *c* = `relevance_i × 0.5 ** (age_i / half_life_days)`, `age_i = (c − available_at_i)` in days; `decayed_sentiment(detail, as_of, *, half_life_days) -> SentimentAggregate(score, articles, weight)` = weighted mean of scores (NaN and 0 articles when none). Input = `news_pit_detail` rows (known, de-duplicated, roundups dropped, relevance) with scores. `Sentiment(lake, cfg, lexicon=None)`: `.at(symbol, as_of)`, `.daily(symbol, as_of, days=None) -> DataFrame[date, score, articles]` for the `days` sessions ending at `as_of.day`, each at that session's decision cutoff, reading the news window once. Scores are computed with D8's function (equal to the stored table). Defaults: `half_life_days` 3, `lookback_days` 14, `max_articles` 500, `daily_days` 90. | Brief: daily aggregate per symbol with decay; deterministic. |
| D10 | Social (flag `data.sentiment.social.enabled`, default false): `SocialProvider` protocol (`name`, `fetch_posts(symbol, start, end) -> DataFrame[post_id, symbol, created_at, available_at, source, text, url]`); `StockTwitsProvider` (`GET {stocktwits_url}/{SYMBOL}.json`, `messages[]` `id, body, created_at`, paging with `max=cursor.max` until older than `start` or `max_items`); `RedditProvider` (`GET {reddit_url}/r/{sub}/search.json?q={SYMBOL}&restrict_sr=1&sort=new&limit=100&t=month` per subreddit, `data.children[].data` `id, title, selftext, created_utc, permalink`); both send `user_agent`, retry transport/429/5xx, other errors → `DataError`; `available_at = created_at`. `social_evidence(posts, as_of, *, lookback_days, limit) -> list[Evidence]` (PIT, newest first, id `social:<post_id>`). `build_social_providers(cfg)` → `()` when disabled. No table (live only). | Brief: optional, behind a flag, off by default, same `Evidence` output. |
| D11 | Config: `EventsConfig` (`data.events`): `macro_calendar_file` `config/macro_calendar.yaml`, `history_start` 2015-01-01, `periodic_match_days` 60, `upcoming_earnings` `yahoo` (`yahoo`\|`none`), `max_retries` 3, `backoff_seconds` 1.0, `min_request_interval_seconds` 0.5, `scheduled_match_days` 10, `reaction_days` 3 (≥ 1), `reaction_benchmark` `SPY`, `horizon_days` 14, `past_n` 8. `SentimentConfig` (`data.sentiment`): `lexicon_file` `data/reference/lm_lexicon.csv`, `lexicon_url` (the Google Drive download URL), `negation_window` 3, `half_life_days` 3.0, `lookback_days` 14, `max_articles` 500, `daily_days` 90, `social: SocialConfig(enabled false, providers (stocktwits, reddit), stocktwits_url https://api.stocktwits.com/api/2/streams/symbol, reddit_url https://www.reddit.com, reddit_subreddits (wallstreetbets, stocks, investing), max_items 100, lookback_days 7, max_retries 3, backoff_seconds 1.0, min_request_interval_seconds 1.0, request_timeout_seconds 30, user_agent "AtlasCapital/0.1 (research and paper-trading simulator)")`. | Nothing hard-coded. |

## File structure

| File | Responsibility |
|---|---|
| `src/atlas/core/config.py`, `config/fund.yaml` (modify) | `EventsConfig`, `SentimentConfig`, `SocialConfig`, `DataConfig.events/.sentiment` |
| `config/macro_calendar.yaml` (create) | FOMC meetings 2018–2027, unscheduled/cancelled, CPI/NFP release definitions |
| `src/atlas/data/macro/fred_client.py` (modify) | `release_dates`, `initial_release_dates` |
| `src/atlas/core/pit.py` (modify) | `AVAILABILITY_RULES` rows for filings-as-events, scheduled macro events, reactions |
| `src/atlas/data/events/{__init__,base,earnings,reactions,macro_calendar,pit}.py` | see Architecture |
| `src/atlas/data/sentiment/{__init__,lexicon,aggregate,social}.py` | see Architecture |
| `tests/fixtures/events/` | AAPL submissions (8-K/10-Q/10-K since 2018, recorded), FRED release dates + initial releases (CPI, NFP; recorded), Yahoo calendar answers (recorded), `README.md` |
| `tests/fixtures/sentiment/` | LM master excerpt (real rows), StockTwits/Reddit pages (documented shapes), `README.md` |
| `tests/unit/data/events/`, `tests/unit/data/sentiment/` | tests per module + `*_helpers.py` + live tests |
| `docs/data-limitations.md` (modify) | `## Events and sentiment (Stage 12)` |
| `Planning/stages/stage-12-events-sentiment/artifacts/record_event_fixtures.py` | fixture recorder |

## Review focus

1. A 10-Q filed with no 8-K 2.02 before it (company that announces in the 10-Q) still yields an earnings event, and AAPL's 10-Qs (each preceded by a 2.02 8-K) do not double it → `test_periodic_filing_is_event_only_without_8k`.
2. A split or dividend inside the reaction window does not appear as a price reaction → `test_reaction_adjusts_split_in_window`.
3. The cancelled 2020-03-18 FOMC meeting is upcoming on 2020-03-10 and gone on 2020-03-16 → `test_cancelled_fomc_visible_until_cancelled`.
4. A Yahoo date whose earnings already happened (actual 8-K known a day earlier) is not "upcoming" → `test_scheduled_dropped_after_actual_event`.
5. Sentiment of the same articles in a shuffled row order is identical (determinism), and a copied story counts once → `test_aggregate_independent_of_row_order`, `test_duplicate_story_counted_once`.

---

### Task 1 — Config: `data.events`, `data.sentiment`

**Files:** modify `src/atlas/core/config.py`, `config/fund.yaml`, `tests/unit/core/test_config.py`

**Interfaces:** Produces `EventsConfig`, `SentimentConfig`, `SocialConfig` (D11) at `cfg.data.events`, `cfg.data.sentiment`, `cfg.data.sentiment.social`. `reaction_benchmark` and social symbols normalised with `normalize_symbol`.

- [x] Write failing tests: `test_events_config_defaults`, `test_sentiment_config_defaults` (every D11 value), `test_repo_yaml_events_equals_defaults`, `test_repo_yaml_sentiment_equals_defaults`, parametrised `test_events_config_rejects` (`reaction_days` 0, `upcoming_earnings` "finnhub", `horizon_days` 0, `periodic_match_days` 0), `test_sentiment_config_rejects` (`half_life_days` 0, `negation_window` -1, `social.providers` `["twitter"]`, `social.reddit_subreddits` `[]`).
- [x] Run `uv run pytest tests/unit/core/test_config.py -q` → FAIL (names missing).
- [x] Implement the three classes, `DataConfig.events/.sentiment`, commented YAML blocks.
- [x] Run the same → PASS.

### Task 2 — Event contract, `events` table, time of day

**Files:** create `src/atlas/data/events/{__init__,base}.py`, `tests/unit/data/events/{events_helpers,test_events_base}.py`

**Interfaces:** Produces `EVENT_COLUMNS` (D1 order), `EVENTS: TableSchema`, `MACRO_EVENT_COLUMNS`, `MACRO_EVENTS: TableSchema` (D6), `EVENT_VIEW_COLUMNS` (D7), `TimeOfDay = Literal["BMO","AMC","unknown"]`, `classify_time_of_day(event_time: datetime | None, calendar: TradingCalendar) -> TimeOfDay`, `normalize_events(df: DataFrame) -> DataFrame` (columns in order, dtypes, symbols normalised, sorted by `symbol, event_date, event_id`; naive timestamps → `DataError`), `empty_events()`, `empty_macro_events()`. Helpers: `utc(...)`, `et(y,m,d,h,mi)` (aware New York), `event_row(**fields)`, `FIXTURES`.

- [x] Write failing tests: `test_events_table_declaration` (PK, `symbol` partition), `test_macro_events_table_declaration`, `test_import_registers_event_tables`, parametrised `test_classify_time_of_day` (2023-08-03 20:30Z → AMC; 2023-08-03 13:00Z (09:00 ET) → BMO; 15:00Z → unknown; 2024-11-29 18:30Z early close 13:30 ET → AMC; Saturday → unknown; None → unknown), `test_normalize_events_contract`, `test_normalize_events_rejects_naive`.
- [x] Run `uv run pytest tests/unit/data/events/test_events_base.py -q` → FAIL.
- [x] Implement `base.py`, `__init__.py`.
- [x] Run → PASS.

### Task 3 — Earnings dates from EDGAR filings (brief task 1)

**Files:** create `src/atlas/data/events/earnings.py`, `tests/fixtures/events/submissions/CIK0000320193.json` (recorded, trimmed to 8-K/10-Q/10-K filed ≥ 2018-01-01, all columns; `filings.files` kept), `tests/fixtures/events/README.md`, recorder `artifacts/record_event_fixtures.py`, `tests/unit/data/events/test_events_earnings.py`

**Interfaces:** Produces `parse_filings(table: Mapping[str, Any]) -> DataFrame` (D3 columns), `fetch_filings(client: EdgarClient, cik: int, *, since: date, refresh: bool = False) -> DataFrame`, `earnings_events(filings: DataFrame, symbol: str, calendar: TradingCalendar, *, periodic_match_days: int) -> DataFrame` (event contract), `EventsIngestReport(rows, symbols_ok, symbols_failed, failure_reasons, skipped_filings)`, `ingest_earnings(lake, client, symbols, *, cik_map, config=None, calendar=None, refresh=True) -> EventsIngestReport`.

- [x] Write failing tests: `test_parse_filings_columns` (items kept, blank report date → None, ragged → `DataError`), `test_acceptance_aapl_known_earnings_dates` (fixture → `event_date`s include the 2018–2026 list of the probe, all `sec_8k`, all `AMC`, 2020-07-30 `event_time` 22:55Z), `test_guidance_cut_8k_is_event` (2019-01-02), `test_non_earnings_8k_ignored` (5.02, 8.01), `test_periodic_filing_is_event_only_without_8k` (synthetic: 10-Q alone → `sec_10q` event; 10-Q one day after a 2.02 → none; 10-Q 61 days after → event), `test_filing_without_acceptance_skipped`, `test_fetch_filings_reads_needed_pages_only` (respx: page with `filingTo` < since not requested), `test_ingest_earnings_idempotent` (twice → same table), `test_ingest_records_symbol_without_cik`, `test_ingest_validates_request_first` (bare string → `ValueError`, no request).
- [x] Run `uv run pytest tests/unit/data/events/test_events_earnings.py -q` → FAIL.
- [x] Record the fixture; implement.
- [x] Run → PASS.

### Task 4 — Optional upcoming earnings (Yahoo)

**Files:** modify `earnings.py`; create `tests/fixtures/events/yahoo_calendar.json` (recorded AAPL/MSFT/JPM answers, dates as ISO strings), `tests/unit/data/events/test_events_upcoming.py`

**Interfaces:** Produces `EarningsCalendarProvider` (Protocol, runtime_checkable), `YahooEarningsCalendar(config: EventsConfig | None = None, *, calendar_fn: Callable[[str], Mapping[str, Any]] | None = None, sleep=time.sleep)` (`name = "yahoo"`), `ingest_upcoming_earnings(lake, provider, symbols, *, known_at: datetime | None = None) -> int`, `build_upcoming_provider(cfg: AtlasConfig) -> EarningsCalendarProvider | None`.

- [x] Write failing tests: `test_yahoo_parses_calendar` (fixture → AAPL 2026-11-02 …), `test_yahoo_two_dates_keeps_earliest`, `test_yahoo_missing_date_skipped` (no `Earnings Date`, empty list), `test_yahoo_vendor_spelling` (`BRK.B` → `BRK-B`), `test_yahoo_retries_then_data_error`, `test_ingest_upcoming_uses_clock` (`sim_clock` → `available_at` = clock now), `test_ingest_upcoming_keeps_each_snapshot` (two `known_at` → two rows, first kept), `test_ingest_upcoming_drops_past_dates`, `test_build_upcoming_provider_none`.
- [x] Run → FAIL; implement; run → PASS.

### Task 5 — Earnings reactions (brief task 2)

**Files:** create `src/atlas/data/events/reactions.py`, `tests/unit/data/events/test_events_reactions.py`

**Interfaces:** Produces `EARNINGS_REACTIONS: TableSchema`, `REACTION_COLUMNS`, `reaction_window(event_time: datetime, calendar, reaction_days: int) -> tuple[date, date, date]` (Dp, D0, Dn), `compute_reactions(events, daily, actions, calendar, *, reaction_days, benchmark) -> tuple[DataFrame, int]` (rows, skipped), `ingest_earnings_reactions(lake, symbols=None, *, config=None, calendar=None) -> ReactionsIngestReport(rows, skipped)`.

- [x] Write failing tests (synthetic bars with known answers): `test_reaction_window_amc_bmo_weekend` (Thu 16:30 ET → D0 Fri; Thu 07:00 ET → D0 Thu; Sat → Mon; Dn = D0 + 2 sessions), `test_reaction_values` (stock closeP 100, open0 110, close0 121, closeN 130; SPY 100/100/101/102 → gap 0.10, oc 0.10, oc_abnormal 0.09, car 0.28), `test_reaction_adjusts_split_in_window` (2:1 split on D0 → gap/car as unsplit), `test_reaction_adjusts_dividend_in_window`, `test_reaction_available_after_window_closes` (= `bar_available_at(Dn)`; unknown at Dn's 08:00 cutoff, known next morning), `test_missing_bar_skipped`, `test_open_window_skipped`, `test_only_actual_events_used` (yahoo row ignored), `test_ingest_reactions_from_lake` (events + prices + actions in `tmp_lake`), `test_ingest_reactions_idempotent`.
- [x] Run → FAIL; implement; run → PASS.

### Task 6 — FOMC / CPI / NFP calendar (brief task 3)

**Files:** create `config/macro_calendar.yaml`, `src/atlas/data/events/macro_calendar.py`, fixtures `tests/fixtures/events/fred/{release_dates_10,release_dates_50,initial_CPIAUCSL,initial_PAYEMS}.json` (recorded), `tests/unit/data/events/test_events_macro.py`; modify `src/atlas/data/macro/fred_client.py`, `tests/unit/data/macro/test_macro_fred_client.py` (or the existing client test file)

**Interfaces:** Produces `MacroCalendar` (frozen model: `statement_time`, `meetings: tuple[date, ...]`, `unscheduled: tuple[(date, time)]`, `cancelled: tuple[(date, datetime)]`, `releases: Mapping[str, ReleaseDef(fred_release_id, headline_series, release_time)]`), `load_macro_calendar(path) -> MacroCalendar`, `fomc_events(calendar_def) -> DataFrame`, `release_events(name, definition, release_dates, initial_dates) -> DataFrame`, `scheduled_available_at(day: date) -> datetime`, `ingest_macro_calendar(lake, client: FredClient | None, *, config=None) -> int`; `FredClient.release_dates(release_id: int, *, realtime_start: date, refresh=False) -> tuple[date, ...]`, `FredClient.initial_release_dates(series_id: str, *, observation_start: date, refresh=False) -> tuple[date, ...]`.

- [x] Write failing tests: `test_load_macro_calendar_repo_file` (8 scheduled meetings in every year 2018–2027, 80 in all; 2020-03-18 is a meeting and in `cancelled`; 2 unscheduled; 2019-10-04 and 2025-08-22 absent), `test_macro_calendar_rejects` (duplicate date, meeting outside its year key, naive cancelled_at), `test_fomc_event_times` (2024-01-31 19:00Z; 2020-03-15 21:00Z unscheduled `available_at` = event_time; scheduled `available_at` = 2024-01-01 05:00Z), `test_cancelled_fomc_visible_until_cancelled` (via `upcoming_events` in Task 9; here: `withdrawn_at`), `test_release_events_drop_revision_days` (CPI 2021-02-08 dropped, 2021-02-10 kept; NFP 2024-08-21 dropped, 2024-08-02 kept; 2026-11-10 scheduled kept), `test_release_event_time` (08:30 ET), `test_fred_release_dates_request` / `test_fred_initial_release_dates_request` (respx; params, paging, `DataError` on bad body, key never logged), `test_ingest_macro_calendar` (FOMC + releases), `test_ingest_without_client_keeps_fomc_only`, `test_reingest_replaces_rescheduled_release`.
- [x] Run → FAIL; record fixtures; write YAML; implement; run → PASS.

### Task 7 — Lexicon scorer and `news_sentiment` (brief task 4, part 1)

**Files:** create `src/atlas/data/sentiment/{__init__,lexicon}.py`, `tests/fixtures/sentiment/lm_master_sample.csv` (real rows: ~40 words incl. every word the tests use, plus a removed (< 0) entry), `tests/fixtures/sentiment/README.md`, `tests/unit/data/sentiment/{sentiment_helpers,test_sentiment_lexicon}.py`

**Interfaces:** Produces `Lexicon`, `TextSentiment`, `build_lm_lexicon(master: DataFrame) -> DataFrame`, `download_lm_lexicon(config: SentimentConfig, *, http: httpx.Client | None = None) -> Path`, `load_lexicon(path: Path) -> Lexicon`, `tokenize(text: str) -> list[str]`, `score_text(text, lexicon, negation_window=3) -> TextSentiment`, `SCORE_COLUMNS`, `score_articles(articles, lexicon, settings=None) -> DataFrame`, `NEWS_SENTIMENT`, `ingest_news_sentiment(lake, lexicon, start, end, *, config=None) -> int`.

- [x] Write failing tests: `test_build_lexicon_from_master` (categories, removed word excluded, upper case), `test_load_lexicon_missing_file_names_download`, `test_lexicon_version_stable`, `test_tokenize` (`"Apple's Q3: beats!"` → `["APPLE", "S", "Q", "BEATS"]`), known sentences: `"Profit fell on weak demand and a lawsuit"` (neg 3? per sample lexicon, exact counts asserted), `"Strong gains and record profitability"` → score 1.0, `"Results may be uncertain"` → uncertainty 1, score 0.0, `"The company did not achieve gains"` → negated positive not counted, `""` → zeros; `test_score_articles_columns_and_version`, `test_ingest_news_sentiment_roundtrip` (news in `tmp_lake` → table equals `score_articles`), `test_download_lm_lexicon` (respx serves the sample master → file written, loadable).
- [x] Run `uv run pytest tests/unit/data/sentiment/test_sentiment_lexicon.py -q` → FAIL; implement; run → PASS.

### Task 8 — Decayed daily aggregate (brief task 4, part 2)

**Files:** create `src/atlas/data/sentiment/aggregate.py`, `tests/unit/data/sentiment/test_sentiment_aggregate.py`

**Interfaces:** Consumes `News(lake, cfg).pit_detail(symbol, as_of, lookback_days, limit)` / `news_pit_detail`. Produces `SentimentAggregate(score: float, articles: int, weight: float)`, `decayed_sentiment(detail: DataFrame, as_of: AsOf, *, half_life_days: float) -> SentimentAggregate` (detail has `relevance`, `available_at`, `score`), `Sentiment(lake, config=None, lexicon=None)` with `.at(symbol, as_of) -> SentimentAggregate` and `.daily(symbol, as_of, days=None) -> DataFrame[date, score, articles]`.

- [x] Write failing tests: `test_decay_known_answer` (two articles: score +1 age 0 d, −1 age 3 d, relevance 1, half-life 3 → (1 − 0.5)/(1 + 0.5) = 1/3), `test_relevance_weights`, `test_no_articles_is_nan`, `test_aggregate_independent_of_row_order`, `test_duplicate_story_counted_once` (two copies in `tmp_lake` news), `test_sentiment_at_pit` (article at 08:30 invisible at 08:00), `test_daily_series` (sessions only, `days` rows, deterministic over two calls), `test_sentiment_reader_guard_raises_if_filter_bypassed` (monkeypatched `pit_filter` → `LookAheadError`).
- [x] Run → FAIL; implement; run → PASS.

### Task 9 — Event queries (brief task 5)

**Files:** create `src/atlas/data/events/pit.py`, `tests/unit/data/events/test_events_pit.py`

**Interfaces:** Consumes Tasks 2–6. Produces `dividend_events(actions: DataFrame) -> DataFrame` (event view rows), `event_view(events, macro_events, dividends) -> DataFrame` (`EVENT_VIEW_COLUMNS`), `upcoming_events(view, as_of, horizon_days, *, scheduled_match_days) -> DataFrame`, `past_events(view, symbol, as_of, n) -> DataFrame`, `known_reactions(reactions, symbol, as_of, n) -> DataFrame`, `Events(lake, config=None)` with `.upcoming(as_of, horizon_days=None, symbols=None)`, `.past(symbol, as_of, n=None)`, `.reactions(symbol, as_of, n=None)`; `as_of` a `date` (configured decision time) or `AsOf`, `datetime` → `TypeError`; `horizon_days`/`n` < 1 → `ValueError`.

- [x] Write failing tests: `test_acceptance_earnings_invisible_before_filing` (AAPL 2023-08-03 16:30 ET 8-K not in `past` at 2023-08-03 08:00, in `past` on 2023-08-04), `test_upcoming_includes_macro_and_scheduled` (CPI 08:30 on as_of day upcoming at 08:00 cutoff), `test_upcoming_horizon_bounds` (day + horizon inclusive), `test_cancelled_fomc_visible_until_cancelled`, `test_scheduled_latest_snapshot_wins`, `test_scheduled_dropped_after_actual_event`, `test_past_events_newest_first_n` (incl. ex-dividend from `corporate_actions`, excludes yahoo rows), `test_dividend_known_from_ex_date_bar`, `test_reactions_known_after_window` (last 8, newest first), `test_events_reader_guard_raises_if_filter_bypassed` (each of `upcoming`, `past`, `reactions`), `test_future_rows_change_nothing` (adding rows available after the cutoff changes no answer), `test_events_reader_empty_lake` (no tables → empty frames, not errors, for `upcoming`; `past`/`reactions` likewise).
- [x] Run → FAIL; implement; run → PASS.

### Task 10 — Optional social providers (flag, off by default)

**Files:** create `src/atlas/data/sentiment/social.py`, `tests/fixtures/sentiment/{stocktwits_aapl_p1,stocktwits_aapl_p2,reddit_wsb_aapl}.json`, `tests/unit/data/sentiment/test_sentiment_social.py`

**Interfaces:** Produces `POST_COLUMNS`, `SocialProvider` (Protocol), `StockTwitsProvider(config: SocialConfig | None = None, *, http=None, sleep=time.sleep)`, `RedditProvider(...)`, `build_social_providers(cfg) -> tuple[SocialProvider, ...]`, `social_evidence(posts, as_of, *, lookback_days, limit) -> list[Evidence]`.

- [x] Write failing tests: `test_disabled_by_default` (`build_social_providers(AtlasConfig()) == ()`), `test_enabled_builds_both`, `test_stocktwits_pages_until_start`, `test_reddit_search_each_subreddit` (params, User-Agent), `test_posts_window_filter`, `test_http_errors` (503 retried then `DataError`; 403 → `DataError`; bad body → `DataError`), `test_social_evidence_pit_and_ids` (post at cutoff excluded; id `social:stocktwits-…`; newest first; `limit`).
- [x] Run → FAIL; implement; run → PASS.

### Task 11 — Live tests, real lexicon, docs

**Files:** create `tests/unit/data/events/test_events_live.py`, `tests/unit/data/sentiment/test_sentiment_live.py`; modify `docs/data-limitations.md`, `src/atlas/core/pit.py` (`AVAILABILITY_RULES`)

- [x] Live tests (`@pytest.mark.live`, skipped without keys): `test_live_aapl_earnings_events` (EDGAR), `test_live_fred_release_dates` (CPI 2024-01-11 present), `test_live_yahoo_calendar`, `test_live_download_lm_lexicon` (≥ 2,000 negative words). Run `uv run pytest tests/unit/data/events tests/unit/data/sentiment -m live -q` → passed.
- [x] Download the real lexicon into `data/reference/lm_lexicon.csv` with `download_lm_lexicon` (gitignored); score one real headline as a smoke check; save output under `artifacts/`.
- [x] `AVAILABILITY_RULES` gains `sec_event`, `scheduled_macro_event`, `earnings_reaction`; its test updated; `docs/data-limitations.md` section (scheduled dates only from live snapshots; macro schedule known from 1 Jan; shutdown reschedules; lexicon is the 2025 version applied to all years; reactions use a market-adjusted model, beta 1; social not stored).
- [x] `uv run pytest tests/unit/data/events tests/unit/data/sentiment -q` → PASS.

### Task 12 — Verification, records, commit, merge

- [x] Run and save under `artifacts/`: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/atlas`, the brief's Verify command (plus `-v -W error`), `make check`, coverage of `atlas.data.events` and `atlas.data.sentiment`, docstring check, unit tests with dead proxies (no network).
- [x] Self-review the branch diff; fix findings test-first.
- [x] Write `verification.md` (ALL CRITERIA VERIFIED only with evidence), `worklog.md`, `PROGRESS.md` (tick, handoff note, Decisions rows, Backlog).
- [ ] Review the staged diff; commit `stage-12: Events calendar & sentiment sources`; merge into `main`; re-run `uv run pytest -q` and `make check` on `main`; record hashes (records-only follow-up commit, as in Stages 09–11).
