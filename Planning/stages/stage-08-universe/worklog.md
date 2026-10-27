# Stage 08 — Universe & historical index membership · Work log

Append entries in order. Record failures and dead ends as honestly as successes.

## 2026-10-27 16:15 — Gate check and setup
- Did: read Project.md, PROGRESS.md (Stage 07 DONE, 7/66), Stage 08 brief, spec §8/§12/§15/§16, protocol §4–§6, Stage 07 plan and handoff. Built the worktree venv (`UV_PROJECT_ENVIRONMENT=.venv.nosync uv sync --group dev`, `ln -s .venv.nosync .venv`).
- Ran: `uv run pytest -q` → 727 passed, 2 deselected, exit 0 (artifacts/gate-check-pytest.txt; first attempt only captured the nanobind shutdown noise with `tail`, re-run saved in full). Stage 07 `verification.md` says ALL CRITERIA VERIFIED.
- Next: probe the source, write plan.md.

## 2026-10-27 16:20 — Source probe (before planning)
- Did: fetched the current Wikipedia article: it has only the constituents table; the change log moved to "Historical components of the S&P 500" on 2026-08-11 (revision history via the MediaWiki API). Fetched that article and revision 1364118843 of the old article (holds ticker-change rows deleted on 2026-07-18). Probed a backwards reconstruction in a scratch script: coverage per year, anomalies, member counts (506 on 2019-06-03; TSLA in from 2020-12-21, AIV out the same day).
- Problems / fixes: a naive date-aware rename map broke on rename rows (CDAY→DAY appears as add DAY / remove CDAY on 2024-02-01) → D1: resolve removed tickers as of the day before, added as of the date, skip rows whose two sides map to one instrument. Reused tickers (IR, DOW, UA, Q, DD, T, AGN) → D4 `since`/`effective` window.
- Slip to report: the very first probe request (curl of the current article) sent a User-Agent that contained the owner's e-mail address to en.wikipedia.org. Every later request and all code use the generic UA "AtlasCapital/0.1 (research and paper-trading simulator)". Recorded here for transparency.
- Next: write plan.md.

## 2026-10-27 16:28 — plan.md written
- Did: [plan.md](plan.md): 7 tasks, D1–D9, Review Focus 1–5, collisions check (none; source move recorded as an interpretation).

## 2026-10-27 16:30 — Task 1: fixtures and parsers
- Did: wrote `artifacts/record_wikipedia_fixtures.py`; recorded `tests/fixtures/universe/wikipedia_sp500_constituents.html` (revision 1382178819) and `wikipedia_sp500_changes.html` (revision 1379179365) + README (provenance, CC BY-SA). `uv add "lxml>=5.0"` (lock already had lxml 6.1.3 via yfinance). Tests first in `tests/unit/data/test_universe.py` (+ `universe_helpers.py`), then `src/atlas/data/reference.py` (`parse_constituents`, `parse_changes`).
- Ran: `uv run pytest tests/unit/data/test_universe.py -q` → collection error (module missing: red) → after implementing, 2 failed, 3 passed → fixes below → 5 passed. `uv run mypy src/atlas` → Success (48 files) after fix.
- Problems / fixes:
  - Recorder: the second request on one `httpx.Client` got HTTP 403 "Please respect our robot policy" every time, even after waits; single requests were served. Fixed by one fresh request per page with a wait-and-retry on 403 (artifacts/record-fixtures-output.txt).
  - `parse_constituents` on a page without the table fell back from lxml to the html5lib flavor (not installed) → pass `flavor="lxml"`.
  - `test_parse_changes_fixture` expected `["FB"]` on 2013-12-23; the log adds ADS, MHK and FB that day. The test was wrong (never green); changed to `"FB" in ...`.
  - mypy rejected `**kwargs` into `pd.read_html` overloads → explicit `links: bool` parameter.

## 2026-10-27 16:45 — Task 2: ticker-change mapping
- Did: tests first (`test_ticker_map_*`, `test_ticker_change_validation`, `test_load_ticker_map_*` in `test_universe.py`; `test_reference_data_defaults` in `tests/unit/core/test_config.py`); then `ReferenceDataConfig` (`cfg.data.reference`) + `config/fund.yaml › data.reference`; public `read_config_file` in `atlas.core.config` (wraps the loader's YAML rules); `TickerChange`, `TickerMap`, `load_ticker_map` in `reference.py`; `config/ticker_changes.yaml` (21 renames, each with its source: `log`, `rev 1364118843` or `manual`).
- Ran: tests → collection errors (red) → `uv run pytest tests/unit/data/test_universe.py tests/unit/core/test_config.py -q` → 116 passed; `ruff check .` → one E501 in a test, fixed; `ruff format`; `mypy src/atlas` → Success.
- Plan change (before code): `test_ticker_map_cycle_raises` replaced by `test_ticker_map_rename_back_terminates`. While writing `canonical` it was clear no rename can apply twice (it needs `on < effective`, then `on` becomes `effective`), so a rename back A→B→A is valid data, not a cycle. Plan D4 annotated.
- Problems / fixes: the extra-key case first matched the pattern `"n"`, too weak; tightened to `changes\.0\.n: Extra`.

## 2026-10-27 16:55 — Task 3: membership reconstruction
- Did: tests first (synthetic: add/remove, re-add, rename row, three anomaly kinds, `date_added`; fixture: TSLA/AIV on 2020-12-18/21, META/DD/DOW/TT/IR histories, member counts, anomalies before `backtest.start`); then `src/atlas/data/universe.py` (`INDEX_MEMBERSHIP`, `SP500_INCEPTION`, `MembershipAnomaly`, `MembershipBuild`, `build_membership`, `members_on`).
- Ran: collection error (red) → 1 failed, 28 passed → fix → 30 passed. ruff (one E501 fixed), format, mypy → clean.
- Problems / fixes:
  - **Real bug found by `test_fixture_fb_history_is_meta`:** DuPont's interval started 2019-06-03 because the constituents table's "Date added" restarts at a rename (DowDuPont → DuPont). That would have dropped DowDuPont from the index for 2017-09-01 → 2019-06-02, inside the backtest window. Fix: `date_added` dates only current members that no change-log row names (`test_date_added_ignored_for_names_in_the_log`).
  - The same test's expectation `DD` starts 2017-09-01 was wrong (never green): `DOW` resolved on 2017-08-31 maps to DD, so DD is the Dow Chemical → DowDuPont → DuPont line, one interval from the inception floor. Assertion corrected and a 2018-06-01 membership check added.
- Fixture result: 8 rename rows skipped, 12 anomalies, all dated before 2018-01-01 (latest: DD removed_while_member 2017-09-01, old E.I. du Pont, reused ticker).

## 2026-10-27 17:05 — Task 4: instruments, ETF universe, refresh
- Did: tests first (`test_instrument_new_fields`, `test_instrument_trade_dates_ordered` in `tests/unit/core/types/test_types_instruments.py`; `test_build_instruments_fixture`, `test_instruments_frame_matches_schema`, `test_refresh_writes_tables_and_is_idempotent`, `test_refresh_default_ticker_map_comes_from_config`); then `Instrument.name/first_trade_date/last_trade_date` + `is_etf` property (Stage 04 contract; D8 backlog item for Stage 08), `INSTRUMENTS`, `build_instruments`, `instruments_frame`, `read_instruments`, `trade_date_ranges` (`reference.py`), `ReferenceReport`, `refresh_universe_reference` (`universe.py`).
- Ran: collection error (red) → 1 failed, 276 passed → fix → 277 passed (`test_universe.py` + `tests/unit/core/types`). mypy: 3 errors (loose `itertuples` types) → fixed with `str()` and a column `zip` → Success.
- Problems / fixes: `test_refresh_writes_tables_and_is_idempotent` guessed `> 900` intervals; the fixture gives 877 (858 symbols). The guess was wrong (never green); replaced by exact counts (877 intervals, 858 + 30 instruments, 8 renames, 12 anomalies), since the fixture is fixed.

## 2026-10-27 17:15 — Task 5: Universe.members / tradable
- Did: tests first (13 in `test_universe.py`, `test_tradable_defaults` + `test_tradable_validation` in `test_config.py`); then `TradableConfig` (`cfg.data.tradable`, `config/fund.yaml › data.tradable`, validator: `min_history_days >= adv_window_days`) and `Universe` (`members`, `tradable`, `_stale_floor`).
- Ran: collection errors (red) → 152 passed at first run; `ruff check` → 4 findings (two `×` RUF002 in docstrings, import order, B008 default `D(...)`) fixed → `uv run pytest tests/unit/data/test_universe.py tests/unit/core -q` → 659 passed; mypy Success.

## 2026-10-27 17:20 — Task 6: optional live refresh
- Did: tests first (`test_fetch_sp500_pages_sends_user_agent`, `..._http_error_raises`, `..._transport_error_raises`, live `test_live_wikipedia_pages_parse`); then `fetch_sp500_pages(config)`.
- Plan change: no `client` parameter, one `httpx.get` per page (Wikimedia 403 on a shared client in Task 1); plan Task 6 annotated.
- Ran: collection error (red) → 51 passed, 1 deselected; `uv run pytest tests/unit/data/test_universe.py -m live -v -rs` → `test_live_wikipedia_pages_parse PASSED` (real Wikipedia; artifacts/live-wikipedia-task6.txt).

## 2026-10-27 17:30 — Task 7: documentation and acceptance
- Did: `test_acceptance_members_2019_06_03`, `test_acceptance_tradable_uses_only_data_before_as_of`, `test_data_limitations_doc`; `docs/data-limitations.md`.
- Ran: 1 failed (doc missing), 53 passed → doc written → 54 passed. The two acceptance tests passed on first run: they check behaviour built in Tasks 3–5, so they had no red step (recorded honestly; their criteria were first driven red by `test_fixture_tsla_added_2020_12_21` and the Task 5 look-ahead tests).
- Problems / fixes: the doc's first coverage figures ("1–3 rows a year before 2007") were wrong; recounted from the fixture (0–7 before 2007, 8–19 for 2007–2014, ≥ 17 from 2015) and corrected.
- Self-review additions: `test_tradable_without_any_prices_is_empty` (Review focus 4: a lake with no `prices_daily` at all); docstring check (`artifacts/docstrings.py`, copied from Stage 07) found the nested `resolve` without a docstring → added → "missing: none".

## 2026-10-27 17:40 — Full verification (Rule 1)
- Ran (raw output in `artifacts/`): `uv run pytest -q` → 791 passed, 3 deselected, exit 0; `ruff check .` → All checks passed!; `ruff format --check .` → 97 files already formatted; `mypy src/atlas` → Success (49 files); brief's Verify `uv run pytest tests/unit/data/test_universe.py -q` → 55 passed, 1 deselected; acceptance tests with `-W error` → 2 passed; `-m live` → `test_live_wikipedia_pages_parse PASSED`; `make check` → exit 0; docstrings → none missing.
- Wrote `verification.md` (ALL CRITERIA VERIFIED), PROGRESS.md (status, checkbox, Decisions log rows, Backlog "Stage 08", handoff note with the evidence block); plan status DONE, all steps ticked.
- Count check while writing records: the PROGRESS draft said 14 `manual` renames; `grep -c "source: manual" config/ticker_changes.yaml` → 13 (5 `log`, 3 `rev`), corrected.
