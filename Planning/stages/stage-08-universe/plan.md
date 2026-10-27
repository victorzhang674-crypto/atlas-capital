# Stage 08 — Universe & historical index membership · Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking; tick only after the step's tests have run and passed in this chat.

- **Brief:** [brief.md](brief.md) (`Planning/stages/stage-08-universe/brief.md`)
- **Status:** DONE (2026-10-27; see [verification.md](verification.md))
- **Gate check:** Stage 07 is DONE (its [verification.md](../stage-07-price-data/verification.md) says ALL CRITERIA VERIFIED; evidence block also in `PROGRESS.md`); `uv run pytest -q` → 727 passed, 2 deselected, exit 0 (2026-10-27) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution method:** executing-plans (native, inline TDD). The owner asked in chat (2026-10-27) to "firstly generate the plans for Stage 8 then carefully implement the plans and complete the plans", then audit the stage against the plan, so the plan is executed straight after it is written (as in Stage 07). Open design choices take the option recorded below and are logged in the Decisions log.

## Context

Stage 07 is `DONE` (7/66): raw daily bars live in the lake table `prices_daily` (PK symbol+date, `available_at` = session close + 15 min). Stage 08 answers "which instruments were investable on each date". Later consumers checked: Stage 09 (EDGAR maps ticker→CIK itself), 13 (`DataView.universe(name)` / `tradable()` / `instrument(symbol)` wrap this stage), 14 (sync CLI refreshes the reference data), 22/23/38/39 (pods take `members`/`tradable` lists), 51 (ADV for impact).

**Goal:** know which instruments were investable on each date: S&P 500 membership history (to limit survivorship bias), the ETF macro universe, sectors, and liquidity filters.

**Architecture:** `atlas.data.reference` owns the source side: the `instruments` table, the Wikipedia parsers (constituents table and change log, located by their HTML ids), the manual ticker-change map and the optional live fetch. `atlas.data.universe` rebuilds `index_membership` intervals backwards from today's list and the change log, writes both tables (`refresh_universe_reference`), and serves `Universe.members()` / `Universe.tradable()`, the latter from Stage 07 `prices_daily` filtered point-in-time.

**Tech stack:** pandas 3 `read_html` (lxml 6.1 parser, `extract_links` for exchange links), httpx 0.28 + respx (optional fetch), Stage 06 `Lake`, Stage 05 `TradingCalendar` / `AsOf` / `pit_filter`.

**Spec:** brief above; design spec §8 (pods: S&P 500 and ~30 ETFs), §15 A1 (market), §16 (survivorship: historical membership, document residual bias, delisted-ticker handling; look-ahead: tests); stage protocol §4–§5; Stage 07 handoff (prices are raw; canonical symbols `BRK.B`; delisted names have no rows; never log `run_id`/`mode`/`as_of`/`stage`).

## Source probe (2026-10-27, recorded before planning)

- The current "List of S&P 500 companies" article (revision 1382178819) holds only the constituents table (`id="constituents"`, 503 rows). On 2026-08-11 (revision 1368903137, "move to [[Historical components of the S&P 500]]") the change log moved to the article "Historical components of the S&P 500" (revision 1379179365, table `id="changes"`, 412 rows, 1976-07-01 → 2026-10-06; ≥ 16 rows a year from 2011, 20–30 from 2015).
- Each constituent's ticker links to its listing exchange (nyse.com 345, nasdaq.com 157, cboe.com 1).
- Change-log quirks: tickers with junk (`"ALLE |"`, `"JCP |"`, `"ITT |"`); some ticker changes appear as add-new/remove-old rows on one date (CDAY→DAY, WLTW→WTW, DWDP→DD, Q→IQV, DOW→DWDP); others not at all (FB, PCLN, …); a few tickers were reused by another company (IR, DOW, UA, Q, DD, T, AGN).
- Ticker-change rows deleted from the log on 2026-07-18 (revision 1364845917) are still readable in revision 1364118843 (SATS→ECHO, FLT→CPAY, RE→EG): used as sources for `config/ticker_changes.yaml`.

## Global constraints

- Symbols are canonical (`normalize_symbol`: `BRK.B`). An instrument's identity is its **latest** ticker (META, not FB): membership, instruments and prices all use it (yfinance serves full history under the current ticker).
- Membership intervals: `start_date` inclusive, `end_date` exclusive (the removal's effective date), `end_date` null = still a member. A change is effective before the open of its date, so `members(d)` includes names added on `d` and excludes names removed on `d`.
- Point in time: `members(as_of)` uses `as_of.day` only (changes are announced days before they take effect); `tradable(as_of)` uses only `prices_daily` rows with `available_at` strictly before `as_of.cutoff` (`pit_filter`), so the bar of `as_of.day` itself is never used at the 08:00 decision time.
- Both reference tables are `static` lake tables (no `available_at`; see D2). They are rewritten whole (`overwrite`) on every refresh, so a refresh is idempotent. *(Audit follow-up A8: the refresh now replaces only the `SP500` rows of `index_membership` and upserts `instruments` by symbol, so data of other indices survives; still idempotent. `test_refresh_keeps_other_indices`.)*
- Thresholds live in `config/fund.yaml › data.tradable` and URLs in `data.reference` (read through `AtlasConfig`); ticker renames in `config/ticker_changes.yaml` (path from `data.reference.ticker_changes_file`).
- No network in unit tests: fixtures are recorded HTML; the fetch goes through respx; one `@pytest.mark.live` smoke test.
- Test basenames unique: all Stage 08 data tests in `tests/unit/data/test_universe.py` (the brief's Verify target), helpers in `tests/unit/data/universe_helpers.py`; config and contract tests extend the existing `tests/unit/core/test_config.py` and `tests/unit/core/types/test_types_instruments.py`.
- **One commit for the stage:** `stage-08: Universe & historical index membership` (+ attribution trailer), after DONE.

## Collisions with the overall plan

None with the roadmap or the design spec. Points checked:
- Brief: parse "Wikipedia 'List of S&P 500 companies' tables". The change-log table has moved to a sibling article (probe above). Resolution: the parsers find each table by its HTML id, so they read either layout; the fixtures record the two current articles at fixed revisions; the live refresh fetches both URLs (configurable). Same source, same tables: an interpretation, not a collision.
- Brief lists `exchange` as a column; the free source gives it only as the symbol's quote link. Resolution: exchange MIC from the link host (`XNYS`, `XNAS`, `BATS`); null for removed names and ETFs.
- Brief task order 1–5 vs this plan: ticker mapping (brief task 5) is needed by the reconstruction (brief task 2), so it is plan Task 2. Mapping: brief 1 → plan 1; 5 → 2; 2 → 3; 3 → 4; 4 → 5; optional live refresh → 6; docs + acceptance → 7.

## Design decisions (also logged in PROGRESS.md › Decisions log)

| # | Decision | Why |
|---|---|---|
| D1 | Reconstruction walks the change log newest → oldest from today's constituents. Per row: added ticker resolved as of the row date, removed ticker as of the day before (its last member day), both through the ticker map. Same canonical on both sides of one row = a rename row → skipped (counted, not an anomaly). Added X: X leaves the set, its interval starts here. Removed Y: Y enters the set, its interval ends here. Added X not in the set, or removed Y already in it → `MembershipAnomaly` (kind `added_not_member` / `removed_while_member`), logged as a warning, set unchanged. Unparseable ticker → anomaly `bad_symbol`, side skipped. | Brief task 2; the log has errors and ticker reuse, which must be visible, not fatal. |
| D2 | `index_membership(index_id, symbol, start_date, end_date?)`, PK `(index_id, symbol, start_date)`, `static`. A start the source cannot date = `SP500_INCEPTION` (1957-03-04); for a current member with no "added" row, the constituents table's `Date added` is used instead. *(Changed in Task 3, annotated in the audit follow-up (A1): `Date added` is used only for current members that **no change-log row names**, because it restarts at renames: DuPont's says 2019-06-03, the day DowDuPont, a member since 2017-09-01, took the DD ticker; `test_date_added_ignored_for_names_in_the_log`.)* | PK columns cannot be null (Stage 06). Membership time is carried by the effective dates; `available_at` per interval is ill-defined (an interval's end is learned later), and the effective-date rule above is the point-in-time rule. |
| D3 | `instruments(symbol, name?, asset_class, sector?, industry?, exchange?, currency, first_trade_date?, last_trade_date?, is_etf, is_shortable)`, PK `symbol`, `static`. Rows: current constituents (GICS sector/sub-industry, exchange from link), removed names (name from their latest log row, no sector), ETFs of every ticker universe in `cfg.data.universes` (`ETF`). `is_shortable = True` for all (brief default). First/last trade dates from `prices_daily` when the lake has it, else null. Built through the `Instrument` contract (Stage 04 gains `name`, `first_trade_date`, `last_trade_date`, `is_etf`; Stage 04 D8 backlog item). | Brief column list; one validated contract. |
| D4 | Ticker map entry: `old`, `new`, `effective` (first day under `new`), optional `since` (first day `old` meant this company, for reused tickers), `source`, `note`. `canonical(t, on)` replaces `t` while an entry has `old == t` and `since ≤ on < effective`, then continues from `max(on, effective)` (chains WLP→ANTM→ELV). *(Changed in Task 2 before any code: no entry can apply twice, because it needs `on < effective` and `on` then becomes `effective`, so a rename-back A→B→A terminates and is legitimate; `test_ticker_map_cycle_raises` became `test_ticker_map_rename_back_terminates`. `historical` walks back through renames with strictly earlier effective dates.)* `historical(symbol, day)` is the inverse. File body `changes: [...]`; validated by Pydantic; read by `load_ticker_map(path)`, not by `load_config` (reference facts, not settings; keeps `AtlasConfig()` defaults equal to the repo YAML). | Brief task 5; IR, DOW, UA, Q were reused. |
| D5 | `tradable(as_of, name="sp500", *, min_price, min_adv_usd, min_history_days)` (each `None` → `cfg.data.tradable`): members at `as_of`, then per symbol from known bars: count ≥ `min_history_days` (252), last raw close ≥ `min_price` (5.0 USD), ADV = mean of raw `close × volume` over the last `adv_window_days` (20) bars ≥ `min_adv_usd` (20e6 USD; fewer bars → not tradable), and last bar no older than `max_stale_sessions` (5) sessions before the latest session whose bar is known. Symbols without bars are excluded (delisted names), never an error. | Brief task 4 + spec §16 delisted handling; staleness rule is an addition (fail safe: no new risk on a name that stopped trading). |
| D6 | `Universe(lake, config=None, *, calendar=None)`; `members(as_of: date \| AsOf, name="sp500") -> list[str]` sorted; ticker universes return their configured tickers; index universes read `index_membership` (cached per lake generation). Unknown name → `ValueError`; no rows for the index → `DataError` ("refresh the reference data"). | Brief signatures. |
| D7 | Config: `data.tradable` (`TradableConfig`: `min_price` 5.0, `min_adv_usd` 20000000, `adv_window_days` 20, `min_history_days` 252, `max_stale_sessions` 5); `data.reference` (`ReferenceDataConfig`: `sp500_constituents_url`, `sp500_changes_url`, `user_agent` "AtlasCapital/0.1 (research and paper-trading simulator)", `request_timeout_seconds` 30, `ticker_changes_file` `config/ticker_changes.yaml`). | Nothing hard-coded. |
| D8 | Optional live refresh: `fetch_sp500_pages(config, client=None) -> tuple[str, str]` (two GETs with the configured User-Agent; non-200 → `DataError`). Fixtures: the two tables cut verbatim from the recorded revisions by `artifacts/record_wikipedia_fixtures.py`, with a provenance comment; README with CC BY-SA attribution. | Brief: recorded fixture; live refresh optional. |
| D9 | Runtime dependency `lxml` declared explicitly (already installed via yfinance, 6.1.3). | `read_html` needs it; do not rely on a transitive dep. |

## File structure

| File | Responsibility |
|---|---|
| `src/atlas/core/config.py` (modify) | `TradableConfig`, `ReferenceDataConfig`, `DataConfig.tradable/reference` |
| `config/fund.yaml` (modify) | `data.tradable`, `data.reference` with comments |
| `config/ticker_changes.yaml` (create) | manual renames with sources |
| `src/atlas/core/types/instruments.py` (modify) | `Instrument.name/first_trade_date/last_trade_date`, `is_etf` |
| `src/atlas/data/reference.py` (create) | `INSTRUMENTS`, `TickerChange`, `TickerMap`, `load_ticker_map`, `parse_constituents`, `parse_changes`, `build_instruments`, `instruments_frame`, `trade_date_ranges`, `fetch_sp500_pages` *(also built, not planned here; audit A4: `read_instruments`, `TickerMap.changes`; and `read_config_file` in `atlas.core.config`)* |
| `src/atlas/data/universe.py` (create) | `INDEX_MEMBERSHIP`, `SP500_INCEPTION`, `MembershipAnomaly`, `MembershipBuild`, `build_membership`, `ReferenceReport`, `refresh_universe_reference`, `Universe` |
| `docs/data-limitations.md` (create) | residual survivorship bias and other limits |
| `tests/fixtures/universe/` (create) | `wikipedia_sp500_constituents.html`, `wikipedia_sp500_changes.html`, `README.md` |
| `tests/unit/data/universe_helpers.py`, `tests/unit/data/test_universe.py` (create) | helpers and all Stage 08 data tests |
| `pyproject.toml`, `uv.lock` (modify) | `lxml` dependency |

## Review Focus

1. Junk in ticker cells (`"ALLE |"`, footnotes) → cleaned to the leading ticker; a cell that still fails `normalize_symbol` is recorded as `bad_symbol`, not raised. Test: `test_parse_changes_cleans_ticker_junk`, `test_bad_symbol_is_an_anomaly`.
2. Rename rows (same company added and removed on one date) → no interval break, no anomaly. Test: `test_rename_row_is_not_a_membership_change`.
3. `as_of` on a weekend/holiday or with a cutoff before that day's close → tradable uses the previous known bars only. Tests: `test_tradable_ignores_bar_of_as_of_day`, `test_tradable_on_weekend_uses_friday_bars`.
4. A member with no price data (delisted) or with bars that stopped → excluded, no error. Tests: `test_member_without_prices_is_not_tradable`, `test_stale_member_is_not_tradable`.
5. Unknown universe name / empty membership table → clear errors. Tests: `test_unknown_universe_raises`, `test_members_without_reference_data_raises`.

---

### Task 1: Fixtures and Wikipedia parsers (brief task 1)

**Files:** create `tests/fixtures/universe/*`, `Planning/stages/stage-08-universe/artifacts/record_wikipedia_fixtures.py`, `src/atlas/data/reference.py`, `tests/unit/data/universe_helpers.py`, `tests/unit/data/test_universe.py`; modify `pyproject.toml`, `uv.lock`.

**Interfaces — Produces:**
- `parse_constituents(html: str) -> pd.DataFrame` columns `symbol, name, sector, industry, exchange, date_added` (`date_added` a `date` or `None`, `exchange` MIC or `None`), sorted by symbol, unique symbols (duplicate → `DataError`).
- `parse_changes(html: str) -> pd.DataFrame` columns `date, added, added_name, removed, removed_name, reason` (`date` a `date`; tickers cleaned strings or `None`, **not yet normalised**: normalisation happens in Task 3 so failures become anomalies), newest first, source order kept within a date.
- Missing table id → `DataError` naming the id. Unparseable date → `DataError`.
- Helpers: `FIXTURES = tests/fixtures/universe`, `constituents_html()`, `changes_html()`.

- [x] Record fixtures: run the recorder (fetches revisions 1382178819 and 1379179365 by `oldid`, cuts `table#constituents` / `table#changes`); add `lxml` with `uv add lxml`.
- [x] Write failing tests: `test_parse_constituents_fixture` (503 rows; MMM name "3M", sector "Industrials"; `BRK.B` present; AAPL exchange `XNAS`, MMM `XNYS`; `date_added` of TSLA = 2020-12-21), `test_parse_changes_fixture` (412 rows; first row 2026-10-06; row 2020-12-21 added TSLA removed AIV; 2013-12-23 added FB), `test_parse_changes_cleans_ticker_junk` (`"ALLE |"` → `"ALLE"`), `test_parse_missing_table_raises`, `test_parse_changes_bad_date_raises`.
- [x] Run `uv run pytest tests/unit/data/test_universe.py -q` → FAIL (module missing).
- [x] Implement in `reference.py` (`pd.read_html(StringIO(html), attrs={"id": ...}, extract_links="body")` for constituents; ticker cleaning regex `^[A-Za-z0-9.\-/]+`).
- [x] Run → PASS.

### Task 2: Ticker-change mapping (brief task 5)

**Files:** modify `reference.py`, `test_universe.py`; create `config/ticker_changes.yaml`; modify `config.py`, `fund.yaml`, `tests/unit/core/test_config.py` (for `ReferenceDataConfig` only).

**Interfaces — Produces:** `TickerChange` (Pydantic, fields of D4; symbols normalised; `old != new`; `since < effective`), `TickerMap(changes: Sequence[TickerChange] = ())` with `canonical(symbol: str, on: date) -> str`, `historical(symbol: str, day: date) -> str`, `changes` property; `load_ticker_map(path: Path) -> TickerMap` (missing file → empty map; bad content → `ConfigError` naming the file); `ReferenceDataConfig` at `cfg.data.reference`.

- [x] Write failing tests: `test_ticker_map_fb_to_meta` (`canonical("FB", 2013-12-23) == "META"`; `canonical("META", 2024-01-02) == "META"`; `historical("META", 2015-01-02) == "FB"`; `historical("META", 2022-06-09) == "META"`), `test_ticker_map_follows_chains` (WLP→ANTM→ELV), `test_ticker_map_respects_reuse` (IR on 2010-11-17 → TT; IR on 2020-03-02 → IR), `test_ticker_map_since_window` (Q on 2011-03-30 → Q; Q on 2017-08-29 → IQV), `test_ticker_map_rename_back_terminates` (was `test_ticker_map_cycle_raises`, see D4), `test_ticker_change_validation`, `test_load_ticker_map_repo_file` (repo file loads; FB, RE, SATS entries present), `test_load_ticker_map_rejects_bad_file` (unknown key / same old+new → `ConfigError`); in `test_config.py`: `test_reference_data_defaults`.
- [x] Run → FAIL.
- [x] Implement; write `config/ticker_changes.yaml` (entries: SATS→ECHO 2026-06-24, FLT→CPAY 2024-03-25, CDAY→DAY 2024-02-01, RE→EG 2023-07-10, ANTM→ELV 2022-06-28, FB→META 2022-06-09, WLTW→WTW 2022-01-10, COG→CTRA 2021-10-04, IR→TT 2020-03-02, JEC→J 2019-12-09, HRS→LHX 2019-07-01, DWDP→DD 2019-06-03, KORS→CPRI 2019-01-02, LUK→JEF 2018-05-24, PCLN→BKNG 2018-02-27, DLPH→APTV 2017-12-05, Q→IQV 2017-11-15 since 2016-10-03, DOW→DWDP 2017-09-01, TSO→ANDV 2017-08-01, UA→UAA 2016-04-08, WLP→ANTM 2014-12-03; each with `source`).
- [x] Run → PASS (also `tests/unit/core/test_config.py`).

### Task 3: Membership reconstruction (brief task 2)

**Files:** create `src/atlas/data/universe.py`; modify `test_universe.py`, `universe_helpers.py`.

**Interfaces — Consumes:** Task 1 frames, Task 2 `TickerMap`. **Produces:** `SP500_INCEPTION = date(1957, 3, 4)`; `INDEX_MEMBERSHIP` (D2, registered in `DEFAULT_REGISTRY`); `MembershipAnomaly(date, symbol, kind, detail)` and `MembershipBuild(intervals: pd.DataFrame, renames: int, anomalies: tuple[MembershipAnomaly, ...])` frozen dataclasses; `build_membership(constituents, changes, *, index_id="SP500", ticker_map=TickerMap()) -> MembershipBuild` (intervals in `INDEX_MEMBERSHIP` column order, sorted by symbol, start_date); helper `members_on(intervals, day) -> list[str]` (pure, used by `Universe`).

- [x] Write failing tests on synthetic frames: `test_reconstruct_add_and_remove` (current {A, B}; 2020-01-02 add B remove C → B starts 2020-01-02, C ends 2020-01-02 with start = inception), `test_rename_row_is_not_a_membership_change`, `test_added_not_member_is_an_anomaly`, `test_removed_while_member_is_an_anomaly`, `test_bad_symbol_is_an_anomaly`, `test_current_member_without_add_row_uses_date_added`; on the fixture: `test_fixture_tsla_added_2020_12_21` (not in `members_on(..., 2020-12-18)`, in on 2020-12-21; AIV the reverse), `test_fixture_fb_history_is_meta`, `test_fixture_member_counts` (2018-01-02, 2019-06-03, 2021-01-04: 500–510 names; today's 503), `test_fixture_anomalies_all_before_backtest_start` (every anomaly dated before 2018-01-01, cfg `backtest.start`).
- [x] Run → FAIL.
- [x] Implement (one pass over rows; intervals collected in dicts; final frame vectorised).
- [x] Run → PASS.

### Task 4: Instruments table, ETF universe, refresh (brief task 3)

**Files:** modify `instruments.py`, `test_types_instruments.py`, `reference.py`, `universe.py`, `test_universe.py`.

**Interfaces — Produces:** `Instrument.name: str | None`, `first_trade_date: date | None`, `last_trade_date: date | None` (first ≤ last), property `is_etf`; `INSTRUMENTS` (D3, registered); `build_instruments(constituents, changes, intervals, *, etf_symbols: Sequence[str], ticker_map, trade_dates: pd.DataFrame | None = None) -> list[Instrument]`; `instruments_frame(instruments) -> pd.DataFrame`; `trade_date_ranges(lake) -> pd.DataFrame` (`symbol, first_trade_date, last_trade_date`; empty if no prices); `ReferenceReport(instruments, intervals, renames, anomalies)`; `refresh_universe_reference(lake, constituents_html, changes_html, *, config: AtlasConfig | None = None, ticker_map: TickerMap | None = None) -> ReferenceReport` (ticker map default: `load_ticker_map(config.data.reference.ticker_changes_file)`; ETFs: all tickers of ticker universes in `config.data.universes`).

- [x] Write failing tests: `test_instrument_new_fields` and `test_instrument_trade_dates_ordered` (types file); `test_build_instruments_fixture` (AAPL equity, sector "Information Technology", exchange `XNAS`, not ETF; AIV present with name "Aimco"/latest log name, sector None; 30 ETFs from `macro_etfs` with `is_etf`; all shortable); `test_instruments_trade_dates_from_prices` (lake with fake prices → first/last dates); `test_refresh_writes_tables_and_is_idempotent` (two refreshes → same frames; report counts > 0).
- [x] Run → FAIL.
- [x] Implement.
- [x] Run → PASS (and `tests/unit/core/types`).

### Task 5: `Universe.members` and `tradable` (brief task 4)

**Files:** modify `universe.py`, `config.py`, `fund.yaml`, `test_config.py`, `test_universe.py`, `universe_helpers.py`.

**Interfaces — Consumes:** `INDEX_MEMBERSHIP` rows, Stage 07 `prices_daily` (via `ingest_prices` + `FakePriceProvider` in tests). **Produces:** `TradableConfig` at `cfg.data.tradable`; `Universe` (D5, D6).

- [x] Write failing tests: `test_members_static_universe` (macro_etfs → 30 tickers, any date), `test_members_index_universe` (synthetic intervals), `test_unknown_universe_raises`, `test_members_without_reference_data_raises`, `test_tradable_filters` (four synthetic names: liquid; price 4.0 → out; ADV 10e6 → out; 200 bars → out), `test_tradable_ignores_bar_of_as_of_day` (a huge-volume bar on `as_of.day` would lift ADV above the floor; still out) , `test_tradable_ignores_future_bars` (adding bars after `as_of` changes nothing), `test_tradable_on_weekend_uses_friday_bars`, `test_member_without_prices_is_not_tradable`, `test_stale_member_is_not_tradable`, `test_tradable_overrides` (explicit `min_price=3.0` admits the 4.0 name), `test_tradable_only_members` (a liquid non-member is excluded); `test_tradable_defaults` in `test_config.py`.
- [x] Run → FAIL.
- [x] Implement (vectorised groupby; `assert_no_lookahead` on the known frame as a guard).
- [x] Run → PASS.

### Task 6: Optional live refresh (brief: "live refresh is optional")

**Files:** modify `reference.py`, `test_universe.py`.

**Interfaces — Produces:** `fetch_sp500_pages(config: ReferenceDataConfig) -> tuple[str, str]`. *(Changed in Task 6: no `client` parameter; one `httpx.get` per page, because Wikimedia answered the second request on a shared client with 403 while recording the fixtures (Task 1). Added `test_fetch_sp500_pages_transport_error_raises`.)*

- [x] Write failing tests: `test_fetch_sp500_pages_sends_user_agent` (respx: both URLs, header checked), `test_fetch_sp500_pages_http_error_raises` (503 → `DataError`), `test_live_wikipedia_pages_parse` (`@pytest.mark.live`: fetch, parse, ≥ 490 constituents, ≥ 400 changes).
- [x] Run → FAIL; implement; run → PASS; run the live test once with `-m live` (network allowed for en.wikipedia.org).

### Task 7: Documentation and acceptance

**Files:** create `docs/data-limitations.md`; modify `test_universe.py`.

- [x] Write failing tests: `test_acceptance_members_2019_06_03` (fixture refreshed into a lake; `Universe.members(date(2019, 6, 3))` excludes TSLA, includes names later removed: AIV, XEC, CTVA-era leavers chosen from the fixture, i.e. members on 2019-06-03 not in today's constituents, ≥ 90 of them), `test_acceptance_tradable_uses_only_data_before_as_of` (same lake plus fake prices: result equals the result computed from a lake truncated to bars known before `as_of`), `test_data_limitations_doc` (file exists, names "survivorship", "delisted", "ticker", "GICS").
- [x] Write `docs/data-limitations.md` (residual survivorship bias: change-log coverage, snapshot reconstruction, source errors and anomaly count, ticker reuse, delisted names lack free prices, current GICS sectors, effective vs announcement dates, Stage 07 `adj_close`).
- [x] Run → PASS.
- [x] Full verification (Rule 1): `uv run pytest -q`, `ruff check`, `ruff format --check`, `mypy src/atlas`, brief Verify, `make check`, `-m live` for Stage 08; outputs to `artifacts/`; write `verification.md`, `PROGRESS.md`; commit; merge into `main` (Rule 6A) and re-verify.
