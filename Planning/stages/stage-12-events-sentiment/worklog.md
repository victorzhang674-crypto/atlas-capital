# Stage 12 — Events calendar & sentiment sources · Work log

Append entries in order. Record failures and dead ends as honestly as successes. This file is the executing-plans ledger (`Ruling:` lines are decisions taken during execution).

## 2026-10-29 16:00 — Gate check and set-up
- Did: read `Project.md`, `Planning/README.md`, `PROGRESS.md` (Stage 11 handoff and Backlog), `02-stage-protocol.md`, the roadmap row and design spec §3/§6/§8/§11/§15, the brief and the later briefs that consume this stage (13, 14, 31, 32, 40). Stage 11 is `DONE`; its `verification.md` says ALL CRITERIA VERIFIED.
- Ran: `uv run pytest -q` → 1190 passed, 7 deselected, exit 0 (`artifacts/gate-check-pytest.txt`). Commands use `UV_CACHE_DIR=$TMPDIR/uv-cache UV_NO_SYNC=1` (the sandbox blocks `~/.cache/uv`).
- Git: branch `stage-12-events-sentiment` from `main` `34f731d`; template copied into the stage folder; `PROGRESS.md` set to `IN PROGRESS`.
- Source probe (live, before planning): EDGAR AAPL submissions (items column), FRED release dates 10/50 and `output_type=4` initial releases, Federal Reserve FOMC calendars 2018–2027, Loughran-McDonald master dictionary (downloadable; research-only licence), yfinance `Ticker.calendar`. Findings in the plan's "Source probe".
- Plan written with the writing-plans skill: [plan.md](plan.md) (12 tasks). Executed straight after it (executing-plans, inline TDD), as the owner asked.

## Pre-flight (interfaces shared between tasks)
- Task 2 → Tasks 3, 4, 5, 6, 9: `EVENT_COLUMNS` / `EVENTS` / `MACRO_EVENTS` / `EVENT_VIEW_COLUMNS` / `classify_time_of_day` / `normalize_events` — consistent in the plan.
- Task 3/4 → Task 5: reactions read `events` rows with `source` `sec_*` — consistent.
- Task 6 → Task 9: `macro_events.withdrawn_at` is consumed by the event view — consistent.
- Task 7 → Task 8: `score_articles` columns (`score`) joined onto `news_pit_detail` by `article_id` — consistent.
- Task 1 → all: `cfg.data.events`, `cfg.data.sentiment(.social)` — consistent.

## 2026-10-29 16:30 — Task 1: config
- Did: tests `test_events_config_defaults`, `test_sentiment_config_defaults`, `test_repo_yaml_events_equals_defaults`, `test_repo_yaml_sentiment_equals_defaults`, `test_events_benchmark_normalised`, `test_events_config_rejects` (7 cases), `test_sentiment_config_rejects` (10 cases) in `tests/unit/core/test_config.py`.
- Ran: `uv run pytest tests/unit/core/test_config.py -q` → collection error (`EventsConfig` missing; RED). Implemented `EventsConfig`, `SocialConfig`, `SentimentConfig`, `DataConfig.events/.sentiment` and the commented `data.events` / `data.sentiment` blocks in `config/fund.yaml` → 162 passed (GREEN).
- Task 1: complete (tests: `uv run pytest tests/unit/core/test_config.py -q` → 162 passed)

## 2026-10-29 16:45 — Task 2: event contract and tables
- Did: `tests/unit/data/events/{events_helpers,test_events_base}.py` (table declarations, registration, 10 time-of-day cases incl. the open/close instants and an early close, naive rejection, contracts, empty frames).
- Ran: `uv run pytest tests/unit/data/events/test_events_base.py -q` → collection error (package missing; RED) → implemented `src/atlas/data/events/{__init__,base}.py` → 23 passed (GREEN).
- Ruling: added `normalize_macro_events` (plan named only `normalize_events` and `empty_macro_events`) — Task 6 writes `macro_events` and needs the same validation — cost if wrong: one extra public function.
- Task 2: complete (tests: `uv run pytest tests/unit/data/events/test_events_base.py -q` → 23 passed)

## 2026-10-29 17:00 — Task 3: earnings dates from EDGAR filings
- Did: recorded the fixtures with `artifacts/record_event_fixtures.py` (output `artifacts/record-fixtures-output.txt`): AAPL submissions trimmed to 108 8-K/10-Q/10-K filings since 2018; FRED release dates (CPI 151, NFP 146) and initial releases (141 each); Yahoo calendar for AAPL/MSFT/JPM. Checked that neither `FRED_API_KEY` nor `SEC_USER_AGENT` appears in any fixture or artifact (`grep -rlF` → clean). Tests in `test_events_earnings.py` (14).
- Ran: RED (module missing) → implemented `earnings.py` → 8 failed: `numpy` cannot subtract a timedelta from the object array a tz-aware `to_numpy()` gives; fixed by comparing naive-UTC `datetime64` values → 38 passed (GREEN).
- Ruling: `earnings_events` returns `(events, skipped)` instead of a frame — the ingest report needs the count of filings skipped for lack of an acceptance time — cost if wrong: callers unpack a tuple.
- Ruling: the ingest test serves AAPL's history page 001 from Stage 09's fixture folder (`fixtures/fundamentals/submissions`) rather than recording it again — same file — cost if wrong: none (read-only reuse).
- Task 3: complete (tests: `uv run pytest tests/unit/data/events -q` → 38 passed)

## 2026-10-29 17:15 — Task 4: optional upcoming earnings (Yahoo)
- Did: `test_events_upcoming.py` (16 tests: recorded calendar, earliest of a range, missing/invalid dates, datetimes, vendor spelling, retries 1/2/4 s then `DataError`, clock default, snapshots kept, stale dates dropped, naive `known_at`, provider factory).
- Ran: RED (names missing) → implemented `EarningsCalendarProvider`, `YahooEarningsCalendar`, `ingest_upcoming_earnings`, `build_upcoming_provider` → 54 passed (GREEN). `ruff`/`mypy` on the package: one `_event` parameter type widened to `Mapping[Any, Any]` (pandas records), two long lines shortened → clean.
- Task 4: complete (tests: `uv run pytest tests/unit/data/events -q` → 54 passed)

## 2026-10-29 17:35 — Task 5: earnings reactions
- Did: `test_events_reactions.py` (17 tests: window for AMC/BMO/in-session/at-close/Saturday events, known-answer values gap 0.10 / oc 0.10 / oc_abnormal 0.09 / car 0.28, split and dividend inside the window, action outside the window, availability at `bar_available_at(Dn)` (unknown at Dn's 08:00 cutoff, known the next morning), missing stock or SPY bar, open window, scheduled rows ignored, ingest from the lake, idempotence, no events).
- Ran: RED (module missing) → implemented `reactions.py` → 1 failed: my test helper built `ratio`/`amount` as `object` columns (all `None`), which the lake rejects; fixed in the helper (cast to float) → 71 passed. `mypy`: `DataFrame.at` is typed as a scalar union → price lookups go through `float(prices[column].loc[day])` → clean.
- Task 5: complete (tests: `uv run pytest tests/unit/data/events -q` → 71 passed)

## 2026-10-29 17:55 — Task 6: FOMC / CPI / NFP calendar
- Did: `config/macro_calendar.yaml` (80 scheduled FOMC decisions 2018–2027 from the Fed pages, 2 unscheduled, the cancelled 2020-03-18 meeting, CPI/NFP release definitions); `FredClient.release_dates` and `.initial_release_dates`; `macro_calendar.py`; `test_events_macro.py` (23 tests). Expected counts computed independently from the fixtures with a separate script: CPI 151 FRED dates → 143 kept (8 revision-only days dropped: 2015-02-20, 2017-02-13, 2019-02-11, 2020-02-11, 2021-02-08, 2022-02-08, 2023-02-10, 2024-02-09), NFP 146 → 143 (2020-05-11, 2024-01-10, 2024-08-21 dropped).
- Ruling: first-release dates are fetched from 1 January of the year before `history_start` — otherwise January's release of December data (e.g. CPI 2015-01-16) is not a first-release day inside the window and would be dropped as a revision day — cost if wrong: one extra year of observations per release. The initial-release fixtures were re-recorded from 2014-01-01 (the recorder changed accordingly).
- Ruling: FRED release dates are requested from `history_start` (`realtime_start=2015-01-01`), the same start as earnings — cost if wrong: none.
- Deviation (TDD): I wrote the implementation before running the new tests, so the first run was already green (23 passed). RED was then shown after the fact: with `macro_calendar.py` moved aside → collection error; with the revision-day filter removed → `test_release_events_drop_revision_days` and `test_ingest_macro_calendar` fail (2 failed, 21 passed); restored → 94 passed for the events package.
- Ran: `ruff`, `mypy src/atlas/data` (35 files) clean; `uv run pytest tests/unit/data/macro tests/unit/data/events -q` → 168 passed, 1 deselected (Stage 10 client tests unaffected).
- Task 6: complete (tests: `uv run pytest tests/unit/data/events -q` → 94 passed)

## 2026-10-29 18:15 — Task 7: lexicon scorer and `news_sentiment`
- Did: `tests/fixtures/sentiment/lm_master_sample.csv` — 41 real rows of the LM master dictionary 1993–2025 (every column), chosen to cover the test sentences, two words removed from the positive list in 2020 (BENEFICIAL, BENEFIT) and a word in two lists (VOLATILITY); `sentiment_helpers.py`; `test_sentiment_lexicon.py` (24 tests: build/load/version, tokenizer, 9 known sentences with exact counts, negation window, article scoring, table, ingest round trip and idempotence, download via respx, HTTP error).
- Ran: RED (package missing) → implemented `src/atlas/data/sentiment/{__init__,lexicon}.py` → 24 passed. One test sentence was wrong (the negator was five words back, not four); the sentence was corrected before the first run, not the assertion.
- Task 7: complete (tests: `uv run pytest tests/unit/data/sentiment -q` → 24 passed)

## 2026-10-29 18:30 — Task 8: decayed daily aggregate
- Did: `test_sentiment_aggregate.py` (13 tests: known answer 1/3 at ages 3 and 6 days with half-life 3, relevance weights, NaN without articles, 60 random articles shuffled five ways give an identical result, look-ahead rows rejected, PIT at the 08:00 cutoff, copied story counted once, other symbols and old news ignored, daily series values and determinism, default 90 sessions from a Saturday, argument errors, no news table, reader guard with `pit_filter` bypassed → `LookAheadError`).
- Ran: RED (module missing) → implemented `aggregate.py` (`math.fsum` sums, so the score does not depend on row order) → 37 passed for the package; ruff and mypy clean.
- Task 8: complete (tests: `uv run pytest tests/unit/data/sentiment -q` → 37 passed)

## 2026-10-29 18:50 — Task 9: event queries
- Did: `test_events_pit.py` (18 tests: 8-K invisible before acceptance and visible next morning, CPI at 08:30 upcoming at the 08:00 cutoff and gone at 09:00, horizon bounds inclusive, cancelled 2020-03-18 FOMC upcoming on 03-10 and 03-13 (the Sunday meeting unknown on Friday) and gone on 03-16, latest snapshot wins, scheduled date dropped after an early actual 8-K, symbol filter keeps macro events, past events newest first with an ex-dividend and without scheduled rows, dividend known only from its ex-date bar, reactions known after their window (last 8), guard with `pit_filter` bypassed → `LookAheadError` for `upcoming`/`past`/`reactions`, future rows change nothing, empty lake, argument errors, frame-level functions).
- Ran: RED (module missing) → implemented `pit.py` (+ `event_view`, `known_reactions`, `Events.view`) → 18 passed. One test fixture date fixed before the first green run (a reaction window ending on Saturday 2023-01-07 is not a session; moved to the 6th). Mutation checks: without the `withdrawn_at` filter → `test_cancelled_fomc_visible_until_cancelled` fails; without the coverage rule → `test_scheduled_dropped_after_actual_event` fails; restored → 112 passed (events).
- Ruling: `Events.upcoming(symbols=...)` restricts company events only; macro events are always returned — they concern every symbol — cost if wrong: a caller wanting only company events filters `symbol.notna()`.
- Ran: `ruff` clean, `mypy src/atlas` → no issues (78 files); events + sentiment → 149 passed.
- Task 9: complete (tests: `uv run pytest tests/unit/data/events -q` → 112 passed)

## 2026-10-29 19:05 — Task 10: optional social providers
- Did: fixtures `tests/fixtures/sentiment/{stocktwits_aapl_p1,stocktwits_aapl_p2,reddit_wsb_aapl}.json` **built** in the documented API shapes (StockTwits `streams/symbol` with `cursor`; Reddit `search.json` listing) — no real answer recorded: the feature is off by default and not an acceptance criterion; `test_sentiment_social.py` (10 tests: off by default, both built when enabled and in order, StockTwits paging until a page reaches before `start`, stop at `max_items`, Reddit search per subreddit with its parameters and User-Agent, malformed item skipped, window `[start, end)`, 503 retried 1/2/4 s then `DataError`, 403 / non-object / non-JSON → `DataError`, window and symbol validation, evidence PIT with ids `social:<post_id>` and limit, guard with `pit_filter` bypassed → `LookAheadError`).
- Ran: RED (module missing) → implemented `social.py` → 10 passed.
- Ruling: the throttle waits once per logical request, outside the retry loop (retries are paced by the back-off alone) — the retry back-off already spaces failed calls — cost if wrong: retries come up to `min_request_interval_seconds` sooner.
- Ruling: `max_items` bounds how many StockTwits posts are collected before paging stops (StockTwits has no page-size parameter) and is Reddit's `limit` per subreddit — cost if wrong: a little more or less history per call.
- Task 10: complete (tests: `uv run pytest tests/unit/data/sentiment -q` → 47 passed)

## 2026-10-29 19:35 — Task 11: live tests, real lexicon, docs
- Did: `AVAILABILITY_RULES` gains `earnings_event`, `scheduled_event`, `earnings_reaction` (test `tests/unit/core/test_pit.py` extended first: RED 1 failed → GREEN 25 passed); live tests `test_events_live.py` (EDGAR AAPL events, FRED CPI dates vs first releases, Yahoo calendar) and `test_sentiment_live.py` (LM download); `docs/data-limitations.md › Events and sentiment (Stage 12)`; fixture READMEs.
- Ran: `uv run pytest tests/unit/data/events tests/unit/data/sentiment -m live -v` → 4 passed (`artifacts/live-tests.txt`).
- Ran: `artifacts/smoke_events_sentiment.py` (real sources, scratch lake). Run 1: the lexicon downloaded to `data/reference/lm_lexicon.csv` (gitignored; version f0587e3b9410: 2,345 negative, 347 positive, 297 uncertainty), FRED macro calendar 368 rows, but EDGAR answered with `SSL: UNEXPECTED_EOF_WHILE_READING` four times — the client retried, `ingest_earnings` recorded AAPL as failed and carried on (fail safe, as designed); output kept as `artifacts/smoke-output-run1-edgar-ssl-error.txt`. Run 2 (same script, minutes later): 48 AAPL earnings events since 2015, 368 macro events; `past("AAPL", 2024-02-02)` → 2024-02-01, 2023-11-02, 2023-08-03 (AMC); `upcoming(2024-01-29, 14)` → FOMC 01-31, NFP 02-02; `upcoming(2020-03-10)` → CPI 03-11 and the 03-18 FOMC; `upcoming(2020-03-16)` → none (`artifacts/smoke-output.txt`). Checked: no secret in any artifact.
- Observed: the LM lists leave common market words neutral ("beats", "surge", "record") — documented (item 7), not changed (the brief names this lexicon).
- Task 11: complete (tests: `uv run pytest tests/unit/data/events tests/unit/data/sentiment -q` → 159 passed; `-m live` → 4 passed)

## 2026-10-29 19:55 — Final review (self-review) and fix
- Final review: self-review of the branch diff (no reviewer subagent: this session's rules allow one only when the owner asks). A self-review by the author is weaker than a fresh reviewer.
- Final: fixed — one vendor action that `adjustment_factors` cannot apply (a dividend at or above the prior close raises `DataError`) aborted the reactions of every symbol in the batch; now that event is skipped (logged `events.reaction_skipped`, counted) — `test_bad_vendor_action_skips_only_that_event` RED (`DataError`) → GREEN, reactions 18/18; the empty `try/except DataError: raise` in `ingest_earnings_reactions` removed.
- Final: minor (deferred): `StockTwitsProvider` / `RedditProvider` create an `httpx.Client` when none is given and never close it (no `close()`); harmless for short runs, matters if Stage 32 builds providers per call.
- Final: minor (deferred): `YahooEarningsCalendar` retries any exception from its `calendar_fn`, so a programming error in a custom function looks like a vendor outage (same trade-off as Stage 11's Yahoo news adapter).
- Final: minor (deferred): Yahoo's calendar also lists the next ex-dividend date, which would make ex-dividend events "upcoming" from a snapshot; not used (the brief takes dividends from corporate actions).
- Final: Ruling: uncovered lines (96% coverage) are defensive branches (calendar-bounds `ValueError`, empty-table early returns, malformed vendor items) — each is a fail-safe return, not a computed value — cost if wrong: an untested error message.

## 2026-10-29 20:05 — Verification (Task 12)
- Ran (raw output in `artifacts/`): `uv run pytest -q` → 1372 passed, 11 deselected; `ruff check .` → All checks passed!; `ruff format --check .` → 174 files already formatted; `mypy src/atlas` → no issues in 79 files; brief's Verify → 160 passed, 4 deselected (also `-v -W error`); `make check` → exit 0; coverage of the two packages 96%; unit tests with dead proxies → 160 passed; docstrings → none missing; `-m live` → 4 passed.
- Checked every test named in `verification.md` against the verbose and live outputs (all present, all PASSED).
- Wrote `verification.md` (ALL CRITERIA VERIFIED), the `PROGRESS.md` handoff note, Decisions rows and Backlog items.
- Code files created: `src/atlas/data/events/{__init__,base,earnings,reactions,macro_calendar,pit}.py`, `src/atlas/data/sentiment/{__init__,lexicon,aggregate,social}.py`, `config/macro_calendar.yaml`; changed: `src/atlas/core/config.py`, `config/fund.yaml`, `src/atlas/core/pit.py`, `src/atlas/data/macro/fred_client.py`, `docs/data-limitations.md`. Tests created: `tests/unit/data/events/{events_helpers,test_events_base,test_events_earnings,test_events_upcoming,test_events_reactions,test_events_macro,test_events_pit,test_events_live}.py`, `tests/unit/data/sentiment/{sentiment_helpers,test_sentiment_lexicon,test_sentiment_aggregate,test_sentiment_social,test_sentiment_live}.py`; changed: `tests/unit/core/{test_config,test_pit}.py`. Fixtures: `tests/fixtures/events/`, `tests/fixtures/sentiment/`.

## 2026-10-29 20:20 — Commit, merge into main, merged-main checks
- Reviewed the staged diff (`git diff --cached --stat`: 65 files, only the stage's code, tests, fixtures, config, docs and records; no `data/`, `.env`, lexicon or cache; neither secret appears in the diff).
- Commit: `d836494 stage-12: Events calendar & sentiment sources` on `stage-12-events-sentiment` (from `34f731d`); `git status --short` → clean.
- Merge: `git merge --ff-only stage-12-events-sentiment` on `main` → `main` = `d836494` (fast-forward from `34f731d`); `git branch --merged main` lists `stage-12-events-sentiment`.
- On merged `main`: `uv run pytest -q` → 1372 passed, 11 deselected, exit 0; `make check` → exit 0 (All checks passed!, 174 files already formatted, no issues in 79 source files, 1372 passed); brief's Verify → 160 passed, 4 deselected · raw: `artifacts/merged-main-check.txt` (its `git status` line counts that output file itself, written by the same command).
- Records-only follow-up commit `stage-12: merged-main check records` (this entry, the evidence line in `verification.md` and `PROGRESS.md`, the plan's last box), fast-forwarded into `main` — the hashes and merged-main checks cannot be in the commit they describe (Project.md Rule 6A over the one-commit default, as in Stages 09–11).

## 2026-10-29 21:00 — Gap-closing follow-up (owner: "can you fix the remaining gaps and limitations and complete the stage?")
- Probe: FRED `release/dates` with a past real-time window → 0 dates (no schedule vintages); BLS schedule pages → HTTP 403 to automated clients (so the 2025 shutdown reschedule dates cannot be verified and are not invented); Fed `fomchistorical2015/2016/2017.htm` → 8 meetings each; LM year codes: additions 2009–2014, 17 words removed in 2020.
- Plan: addendum G1–G7 in [plan.md](plan.md) (tasks A1–A6). Kept in the Backlog as other stages' work: sync wiring (Stage 14), Stage 11's Yahoo news retry breadth.

## 2026-10-29 21:15 — A1: macro calendar history and schedule point in time (G1, G2)
- Did: tests first in `test_events_macro.py`: repo YAML now 104 meetings (8 a year 2015–2027) and one disruption window; `test_reingest_keeps_releases_without_client`, `test_new_future_release_known_from_fetch`, `test_moved_release_withdrawn_at_fetch`, `test_past_release_dropped_by_fred_is_removed`, `test_disruption_window_known_only_at_release`, `test_ingest_uses_clock_by_default`; ingest tests pass `known_at` = 2026-10-29 16:00 UTC (the recording day).
- Ran: RED (`MacroCalendar` has no `disruptions`; `ingest_macro_calendar` has no `known_at`) → YAML 2015–2017 meetings from the Fed pages and `schedule_disruptions` (2025-10-01 → 2025-12-31); `ScheduleDisruption`, `release_events(..., disruptions=)`, `merge_release_snapshot`, `ingest_macro_calendar(..., known_at=None)` (clock default) → 1 failed: my test expected a 2030 date first seen in 2026 to be known from the fetch, but the rule is "the later of the fetch and 1 January of its year"; the test was wrong (the plan says "from the fetch at the earliest") and was corrected → 118 passed.
- Mutation checks: disruption rule removed → `test_disruption_window_known_only_at_release` fails; withdrawal removed → 2 tests fail; restored → 118 passed.
- Ruling: a stored date that was withdrawn and then reappears in FRED's list is stored again as new (known from the fetch, no longer withdrawn); the table keeps one row per event id, so the withdrawn interval is not kept — cost if wrong: a date that was moved away and back looks continuously scheduled from its return.
- Ruling: the disruption window ends 2025-12-31 (the shutdown ended in November 2025, and releases into December were rescheduled after it) — chosen wide on purpose: inside it CPI/NFP are never "upcoming", which is fail safe — cost if wrong: two or three releases not shown in advance in backtests.
- A1: complete (tests: `uv run pytest tests/unit/data/events -q` → 118 passed)

## 2026-10-29 21:40 — A2 + A5: estimated earnings dates, scheduled ex-dividends, Yahoo retry breadth (G3, G4, G7)
- Did: tests first — `test_events_upcoming.py`: provider frame now `symbol, event_type, event_date`, `test_yahoo_returns_ex_dividend` (recorded AAPL 2026-08-09, MSFT 2026-11-18, JPM 2026-10-05), `test_yahoo_programming_error_not_retried` (a `TypeError` raised once, no sleep), `test_ingest_upcoming_stores_ex_dividend`; `test_events_pit.py`: `test_estimated_earnings_from_last_year` (2024-05-02 from 2023-05-04; the real report was 2024-05-02), `test_estimate_skips_guidance_8k_cluster` (2019-01-31 despite the 2019-01-02 warning 8-K; the real report was 2019-01-29), `test_estimate_yields_to_scheduled_date`, `test_estimate_uses_only_known_events`, `test_estimates_can_be_switched_off`, `test_scheduled_ex_dividend_upcoming_until_seen`; config `estimate_earnings`, `estimate_min_gap_days` (defaults and rejection).
- Ran: RED (import error `ingest_upcoming_events`; 6 query tests failed) → implemented: `YahooEarningsCalendar` returns earnings and ex-dividend rows, retries only `_VendorError` (programming errors propagate); `ingest_upcoming_events` (`ingest_upcoming_earnings` kept as an alias); `_current_schedule` per symbol and event type, coverage by an actual event of the same type; `estimated_earnings` and `upcoming_events(..., estimate_min_gap_days=None)`; `Events.upcoming` uses the config → 182 passed (provider + config), 127 passed (events). ruff, mypy clean.
- Ruling: estimates are computed at query time from the rows known at `as_of` and never stored — they are a pure function of known actual events, so no availability rule is needed beyond those rows — cost if wrong: a little query time per call.
- Ruling: an estimate takes the `time_of_day` of the report it is projected from (AAPL: AMC) — the company's habit is the best free guess; the row is marked `source="estimated"` — cost if wrong: an Event-pod rule keyed on time of day may act on a guessed time.
- Ruling: `ingest_upcoming_earnings` stays as an alias of the new `ingest_upcoming_events` so the Stage 12 handoff name keeps working — cost if wrong: one extra name.
- A2: complete; A5: complete (tests: `uv run pytest tests/unit/data/events tests/unit/core -q` → all passed, see the next run lines)

## 2026-10-29 22:00 — A3: point-in-time lexicon (G5)
- Did: tests first in `test_sentiment_lexicon.py`: the built table has `added`/`removed` years (BENEFIT removed 2020, STRONG added 2009); `test_lexicon_for_year`, `test_removed_word_counts_before_removal`, `test_load_lexicon_two_columns` (old files still load), `test_score_articles_uses_publish_year` ("A clear benefit": 1.0 in 2019, 0.0 in 2021).
- Ran: RED (5 failed) → `Lexicon.spans` + `for_year`, `build_lm_lexicon` keeps removed words with their year, `load_lexicon` reads both formats, `score_articles` scores each article with its publish year's lists → 1 failed: `test_lexicon_version_stable` re-saves the file through pandas, which writes `2009.0`; the loader now accepts whole-number floats (`_year`), the test unchanged → 51 passed; ruff, mypy clean.
- Ruling: a removed word with no addition year counts from the start of the lists (the master dictionary keeps one number per cell, so a removed word's addition year is not recorded; the lists began in 2009, before any Atlas data) — cost if wrong: none for data from 2015.
- A3: complete (tests: `uv run pytest tests/unit/data/sentiment -q` → 51 passed)

## 2026-10-29 22:20 — A4: stored social posts and closable providers (G6)
- Did: tests first in `test_sentiment_social.py`: `test_ingest_social_posts_roundtrip` (3 posts stored, re-ingest idempotent), `test_failing_provider_recorded` (StockTwits `DataError` recorded, Reddit still stored), `test_social_reader_pit` (post at the cutoff excluded; other symbol empty; 30-day lookback; never-written table → `[]`), `test_social_reader_guard` (`pit_filter` bypassed → `LookAheadError`), `test_provider_close_only_own_client`; config test for the new `social.limit`.
- Ran: RED (import error `SOCIAL_POSTS`) → `social_posts` table (PK `symbol, post_id, created_at`, year partitions), `SocialIngestReport`, `ingest_social_posts`, `Social(lake).evidence`, `close()` / context manager on both providers, config `data.sentiment.social.limit` (50, commented in `config/fund.yaml`) → 219 passed (sentiment + config); ruff (an unsorted `__all__`, one long docstring line fixed) and mypy clean.
- Ruling: the table key includes the symbol — one StockTwits message tagged `$AAPL $MSFT` is fetched for each symbol and must be found under both — cost if wrong: one duplicate text per extra symbol.
- Ruling: added config key `data.sentiment.social.limit` (query default, 50 posts) — the reader needs a default and nothing may be hard-coded — cost if wrong: one key.
- A4: complete (tests: `uv run pytest tests/unit/data/sentiment -q` → 56 passed)

## 2026-10-29 22:45 — A6: docs, smoke run, verification (gap follow-up)
- Did: `docs/data-limitations.md › Events and sentiment` items 2, 3, 5, 7, 8 rewritten for the fixes; `AVAILABILITY_RULES["scheduled_event"]` text updated; live Yahoo test checks the event types; smoke script uses `use_clock(LiveClock())` and shows the estimates, the shutdown window, 2015 FOMC and the year-aware lexicon.
- Ran (raw in `artifacts/*-gaps.txt`): smoke on real sources → 48 AAPL events, 392 macro rows, 1 scheduled row (AAPL earnings 2026-11-02; Yahoo's ex-dividend 2026-08-09 is past and dropped), estimated AAPL dates 2024-02-01 / 2024-05-02 / 2025-10-30 equal the real 8-K days, no CPI/NFP upcoming at 2025-10-06, `data/reference/lm_lexicon.csv` rewritten as `word,category,added,removed` (version cfce9c622af2); `-m live` → 4 passed; `uv run pytest -q` → 1396 passed, 11 deselected; ruff, format (174 files), mypy (79 files) clean; brief's Verify `-v -W error` → 183 passed; `make check` → exit 0; coverage 96%; dead proxies → 183 passed; docstrings → none missing. Every test named in the follow-up block of `verification.md` checked present and PASSED. No secret in any artifact.
- Final review of the follow-up: self-review (no reviewer subagent without the owner's request). Checked: estimates read only rows known at `as_of` (`test_estimate_uses_only_known_events`); snapshot merge never moves a stored `available_at` earlier; withdrawn rows hidden from the withdrawal instant; disruption rule only delays knowledge. No new finding.
