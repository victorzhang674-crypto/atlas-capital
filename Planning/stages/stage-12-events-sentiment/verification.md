# Stage 12 — Events calendar & sentiment sources · Verification evidence

Filled in only with output from commands run in this stage's chat (Project.md Rule 1). Raw outputs are under [artifacts/](artifacts/); key lines are quoted here. Commands ran with `UV_CACHE_DIR=$TMPDIR/uv-cache UV_NO_SYNC=1` (the sandbox blocks `~/.cache/uv`).

#### Verification evidence — Stage 12 — 2026-10-29
- Commands run (this chat):
  - `uv run pytest -q` → 1372 passed, 0 failed, 11 deselected (live), exit 0 · raw: [artifacts/pytest.txt](artifacts/pytest.txt)
  - `uv run ruff check .` → All checks passed!, exit 0 · raw: [artifacts/ruff-check.txt](artifacts/ruff-check.txt)
  - `uv run ruff format --check .` → 174 files already formatted, exit 0 · raw: [artifacts/ruff-format.txt](artifacts/ruff-format.txt)
  - `uv run mypy src/atlas` → Success: no issues found in 79 source files, exit 0 · raw: [artifacts/mypy.txt](artifacts/mypy.txt)
  - `uv run pytest tests/unit/data/events tests/unit/data/sentiment -q` (brief's Verify) → 160 passed, 4 deselected (live), exit 0 · raw: [artifacts/verify-brief.txt](artifacts/verify-brief.txt)
  - Same with `-v -W error` → 160 passed, every test named · raw: [artifacts/verify-brief-verbose.txt](artifacts/verify-brief-verbose.txt)
  - `make check` → lint, format, mypy, 1372 passed, exit 0 · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
  - Coverage (`--cov=atlas.data.events --cov=atlas.data.sentiment`) → TOTAL 96% (950 statements, 38 missed) · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
  - Unit tests with every proxy variable pointed at a dead port → 160 passed (no network used) · raw: [artifacts/no-network.txt](artifacts/no-network.txt)
  - Docstrings of public functions/classes in the new modules → none missing · raw: [artifacts/docstrings.txt](artifacts/docstrings.txt) ([check_docstrings.py](artifacts/check_docstrings.py))
  - `-m live` (real EDGAR, FRED, Yahoo, LM download) → 4 passed · raw: [artifacts/live-tests.txt](artifacts/live-tests.txt)
  - Smoke run on real sources (scratch lake): 48 AAPL earnings events since 2015, 368 macro events, PIT queries as expected; the real lexicon written to `data/reference/lm_lexicon.csv` (gitignored) · raw: [artifacts/smoke-output.txt](artifacts/smoke-output.txt) (an earlier run whose EDGAR requests failed with SSL EOF errors, handled as a recorded symbol failure: [artifacts/smoke-output-run1-edgar-ssl-error.txt](artifacts/smoke-output-run1-edgar-ssl-error.txt))
  - Gate check at the start: `uv run pytest -q` → 1190 passed, 7 deselected · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- Acceptance criteria (brief):
  - [x] Event queries are PIT-correct — evidence: `test_acceptance_earnings_invisible_before_filing` (AAPL 8-K accepted 2023-08-03 16:30 ET unknown at that day's 08:00 cutoff, known the next morning), `test_events_reader_guard_raises_if_filter_bypassed[upcoming|past|reactions]` (look-ahead guard on every reader path), `test_future_rows_change_nothing`, `test_upcoming_includes_macro_and_scheduled` (CPI 08:30 upcoming at 08:00, gone at 09:00), `test_cancelled_fomc_visible_until_cancelled` (2020-03-18 upcoming on 03-10 and 03-13, gone on 03-16; the Sunday 03-15 meeting unknown before its statement), `test_scheduled_latest_snapshot_wins`, `test_scheduled_dropped_after_actual_event`, `test_dividend_known_from_ex_date_bar`, `test_reaction_available_after_window_closes`, `test_reactions_known_after_window`, `test_ingest_upcoming_uses_clock`, `test_ingest_upcoming_keeps_each_snapshot`, `test_social_evidence_pit_and_ids`, `test_social_evidence_guard`
  - [x] The sentiment aggregate is deterministic and tested — evidence: `test_aggregate_independent_of_row_order` (60 articles, five shuffles, identical results), `test_daily_series` (two calls give equal frames; values per session), `test_decay_known_answer` (1/3), `test_relevance_weights`, `test_no_articles_is_nan`, `test_duplicate_story_counted_once`, `test_sentiment_at_pit`, `test_sentiment_reader_guard_raises_if_filter_bypassed`, `test_score_known_sentences` (9 sentences, exact counts), `test_negation_window`, `test_lexicon_version_stable`
- Brief tasks, checked one by one:
  - [x] 1. Earnings dates from filing timestamps, tested against known AAPL dates — `test_acceptance_aapl_known_earnings_dates` (36 dates 2018-02-01 … 2026-07-30 from the recorded EDGAR submissions), `test_live_aapl_earnings_events` (live)
  - [x] 2. Earnings reaction computation, PIT — `test_reaction_values`, `test_reaction_adjusts_split_in_window`, `test_reaction_adjusts_dividend_in_window`, `test_reaction_available_after_window_closes`, `test_bad_vendor_action_skips_only_that_event`
  - [x] 3. FOMC/CPI calendar loader — `test_load_macro_calendar_repo_file`, `test_fomc_event_times`, `test_release_events_drop_revision_days`, `test_ingest_macro_calendar`, `test_live_fred_release_dates` (live)
  - [x] 4. Lexicon scorer + daily decayed aggregate; known-sentence tests — see the sentiment criterion above
  - [x] 5. `upcoming_events(as_of, horizon_days)` and `past_events(symbol, as_of, n)` — `test_upcoming_horizon_bounds`, `test_past_events_newest_first_n`, `test_frame_level_functions`
  - [x] In scope also: dividend ex-dates as events (`test_past_events_newest_first_n`, `test_dividend_events_frame`); optional Reddit/StockTwits behind a flag, off by default, `Evidence` output (`test_disabled_by_default`, `test_enabled_builds_both`, `test_social_evidence_pit_and_ids`)
- Waivers: none

**Result:** ALL CRITERIA VERIFIED
