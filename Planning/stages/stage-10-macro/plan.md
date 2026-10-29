# Stage 10 — Macro data (FRED / ALFRED) · Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (inline TDD). Steps use checkbox (`- [ ]`) syntax; tick only after the step's tests have run and passed in this chat.

- **Brief:** [brief.md](brief.md) (`Planning/stages/stage-10-macro/brief.md`)
- **Status:** DONE (2026-10-29; see [verification.md](verification.md))
- **Gate check:** Stage 09 is DONE (its [verification.md](../stage-09-fundamentals/verification.md) says ALL CRITERIA VERIFIED; evidence block also in `PROGRESS.md`); `uv run pytest -q` → 948 passed, 4 deselected, exit 0 (2026-10-29) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution method:** executing-plans (native, inline TDD). The owner asked in chat (2026-10-29) to "firstly generate planning markdown files for stage 10 … then start with the implementation of the plan", so the plan is executed straight after it is written (as in Stages 06–09).

## Context

Stage 09 is `DONE` (9/66). Stage 10 adds US macro series as they were known at the time. Later consumers checked: Stage 13 (`DataView.macro(...)` wraps this stage; quality checks), 14 (sync CLI `--datasets macro`, backfill 8+ years, M1), 21 (regime detection: macro quadrant from growth/inflation, curve, credit spreads, VIX), 33 (Macro Economist: briefing from FRED + regime), 40 (Global Macro pod).

**Goal:** point-in-time macro series that respect release dates and revisions.

**Architecture:** package `atlas.data.macro`. `fred_client` talks to the FRED API (series metadata, vintage dates, observations by real-time period, pagination, rate limit, retries, raw JSON archive in `raw/fred/`). `series` holds the series catalog (`config/macro.yaml`), the two tables, the parsing of FRED JSON, the splitting of long vintage histories into request windows, the merging of those windows, and the `available_at` rules. `ingest` wires client → parser → lake per series. `pit` answers "what was known at `as_of`" (latest known vintage per observation) and builds the wide frame. `transforms` derives YoY, 3-month annualised, z-score, curve slope and real rate from a point-in-time frame.

**Tech stack:** httpx 0.28 + respx, pandas 3, Stage 06 `Lake`, Stage 05 `AsOf` / `pit_filter` / `assert_no_lookahead` / `MARKET_TZ`, Stage 07 `RetryPolicy` / `call_with_retry` / `Throttle`.

**Spec:** brief above; design spec §3 (point in time everywhere; fail safe), §5 steps 3–4 (regime detection and Macro Economist consume this), §11 (Macro = FRED / ALFRED, free, with vintages); `atlas.core.pit.AVAILABILITY_RULES["fred_observation"]` (release time of the vintage that first carried the value); stage protocol §4–§5.

## Source probe (2026-10-29, before planning)

- The FRED API needs `api_key` (`{"error_code":400,…"Variable api_key is not set"}`); **`FRED_API_KEY` is empty** in the project `.env`. So the unit fixtures are built in the documented API JSON shapes from the keyless ALFRED downloads (one CSV per vintage + the vintage list of the ALFRED download page) by [artifacts/record_fred_fixtures.py](artifacts/record_fred_fixtures.py) (output: [record-fixtures-output.txt](artifacts/record-fixtures-output.txt)); the live test needs the key. ALFRED drops requests with a custom User-Agent, so the recorder uses plain curl.
- API docs (`fred/series/observations`): `output_type=1` = observations by real-time period (`realtime_start`, `realtime_end`, `date`, `value`; missing value `"."`); `limit` ≤ 100000 with `offset`; **a JSON request may cover at most 2000 vintage dates** → long daily histories must be split into real-time windows.
- CPIAUCSL (670 vintages since 1972-07-21): March 2020 is absent in the 2020-04-09 vintage, first released **257.953** on **2020-04-10** (BLS CPI, 08:30 ET), revised to **257.989** in the 2021-02-08 vintage (seasonal-factor revision). Feb 2020: 259.050 (2020-03-11). March 2019: 254.148 (2019-04-10), 254.095 (2020-02-11), 254.147 (2021-02-08).
- DGS10 (5139 vintages since 2005-06-28): the value of day D first appears in the vintage of the next business day (2020-03-02 → 2020-03-03; Fri 2020-04-17 → Mon 2020-04-20); Good Friday 2020-04-10 is `"."`; no revisions in March–April 2020.
- **ICE BofA OAS (BAMLH0A0HYM2, BAMLC0A0CM) are served from 2023-10-30 only** (licence; ALFRED has no older vintages either). VIXCLS from 1990, DTWEXBGS from 2006, WALCL from 2002-12, NFCI from 1971.

## Global constraints

- Point in time: a row is known at `as_of` iff `available_at < as_of.cutoff` (strict; `pit_filter`). Per `(series_id, obs_date)` the known row with the latest `realtime_start` wins, so an earlier `as_of` sees the value as first released and a later one the revision.
- `available_at`, UTC: **release** series → `realtime_start` at the series' `release_time` (America/New_York); **market** series → first vintage of an observation: `obs_date + market_lag_days` (1, calendar days) at `market_available_time` (08:00 ET); a later revision row: the later of that and `realtime_start` at 08:00 ET.
- Values in FRED's native units (percent for yields, spreads, rates; index levels; thousands of persons; millions/billions USD; see `macro_series.units`). Transforms return percent or percentage points (3.0 = 3%), so they combine with yields.
- Settings in `config/fund.yaml › data.macro` (`MacroConfig`, defaults in code); the series set in `config/macro.yaml`, read by `load_macro_series`, not by `load_config` (like Stage 09's concept map). Every YAML key has a comment.
- `FRED_API_KEY` is a secret: never logged, never in an error message, never in a cache file name (it is a query parameter, so messages are built without it and any text is redacted).
- No network in unit tests (respx serves the fixtures; fake FRED for windowing); one `@pytest.mark.live` test, skipped when `FRED_API_KEY` is unset.
- Tests in `tests/unit/data/macro/` (the brief's Verify target), basenames `test_macro_*.py`, helpers in `macro_helpers.py`; config tests extend `tests/unit/core/test_config.py`.
- One stage commit `stage-10: Macro data (FRED / ALFRED)` after DONE, then merge into `main` and re-verify (Rule 6A).

## Collisions with the overall plan

None with the roadmap or the spec. Interpretations (logged in the Decisions log):
- Brief `macro_pit(series_ids, as_of, lookback)` needs the stored rows: `Macro(lake, cfg).pit(series_ids, as_of, lookback)` reads the lake; the frame-level `macro_pit(observations, series_ids, as_of, lookback)` keeps the brief's name (as Stage 09 did for `fundamentals_pit`).
- Brief Files list names three modules; the catalog/tables/availability rules get `series.py` and ingestion `ingest.py`, so no module mixes HTTP with table writing.
- Brief "market series available at obs_date + 1 day 08:00 ET" with Stage 05's strict cutoff (`<`) and the default 08:00 decision time: a daily value for D is first used at the decision of the **second** calendar day after D (08:00 is not before 08:00). Followed literally (fail safe); `market_available_time` is configurable. Flagged to the owner.
- DTWEXBGS is a daily series published weekly (Fed H.10, Mondays), so it is a **release** series (`realtime_start`), not a market one; the brief's market list ("yields, OAS, VIX") does not include it.

## Design decisions

| # | Decision | Why |
|---|---|---|
| D1 | `macro_observations(series_id, obs_date, value?, realtime_start, available_at)`, PK `(series_id, obs_date, realtime_start)`, partitioned by `series_id`; `value` float, NaN for FRED's `"."`. `macro_series(series_id, title, frequency, units, seasonal_adjustment, kind)`, static, PK `series_id` (descriptive fields only; no `last_updated` / `observation_end`, which would leak the future). | Brief columns; one partition per series. A vintage that blanks a value must override the older value, so missing rows are stored and dropped at query time. |
| D2 | Ingestion fetches the **whole vintage history** of each series: vintage dates in `[vintage_start, realtime_end]` → windows of ≤ `max_vintage_dates_per_request − 1` vintages (window 0 starts at `vintage_start`, window k at its first vintage, each ends the day before the next) → `output_type=1` observations per window → concatenated, then consecutive rows of an observation with the same value merged (keep the earliest `realtime_start`; NaN equals NaN). Re-ingesting gives the same keys (upsert, idempotent). | 2000-vintage limit; a window boundary is always a real vintage, so a clipped row either merges or is a genuine revision. |
| D3 | `available_at` rules as in Global constraints; `release_time` per series in `config/macro.yaml` (publisher's scheduled time, ET; the later one when it has varied). | Brief; release before the decision cutoff is never assumed. |
| D4 | `FredClient(config, *, api_key, lake=None, http=None, sleep=time.sleep)`: `series(id)`, `vintage_dates(id, *, realtime_start, realtime_end)`, `observations(id, *, observation_start, observation_end, realtime_start, realtime_end)` (all `refresh=False` keyword). Throttle `1 / max_requests_per_second` (FRED allows 120/min → 2/s); retries on transport errors, 429, 5xx; HTTP 400 → `DataError` with FRED's `error_message`; pagination by `offset` until `count` rows (`DataError` if short). Raw JSON archived in `raw/fred/<endpoint>/` (file names from series and dates, never the key); a cached file is served unless `refresh=True`. `build_fred_client(cfg, lake=None)` (`ConfigError` naming `FRED_API_KEY`). | Brief task 1; mirrors Stage 09's `EdgarClient`. |
| D5 | Catalog: `config/macro.yaml › series: {ID: {kind: market\|release, release_time: "HH:MM" (release only), description}}`; `MacroSeries` / `MacroCatalog` / `load_macro_series(path)` (`ConfigError` naming file and entry). Market: DGS3MO, DGS2, DGS10, T10Y2Y, T10Y3M, BAMLH0A0HYM2, BAMLC0A0CM, VIXCLS. Release (ET): CPIAUCSL, CPILFESL, UNRATE, PAYEMS, ICSA, RSAFS, NFCI 08:30; INDPRO 09:15; PCEPILFE, UMCSENT 10:00; M2SL 13:00; DTWEXBGS 16:15; WALCL 16:30; FEDFUNDS 17:00 (no fixed posting time → end of day). | Brief series set. |
| D6 | `MacroConfig` (`data.macro`): `api_url` `https://api.stlouisfed.org/fred`, `max_requests_per_second` 2 (≤ 2), `request_timeout_seconds` 30, `max_retries` 3, `backoff_seconds` 1.0, `series_file` `config/macro.yaml`, `observation_start` 2000-01-01, `vintage_start` 1776-07-04 (FRED's earliest), `max_vintage_dates_per_request` 2000 (2–2000), `page_limit` 100000 (≤ 100000), `market_lag_days` 1 (≥ 1), `market_available_time` 08:00, `transform_lag_tolerance_days` 7, `zscore_window_years` 10, `zscore_min_years` 5 (≤ window). | Nothing hard-coded. |
| D7 | PIT: `known_observations(obs, as_of)` (guarded by `assert_no_lookahead`); `macro_pit(obs, series_ids, as_of, lookback: timedelta) -> DataFrame` wide: index `obs_date` (tz-naive `DatetimeIndex`), one float column per requested id in request order (all NaN if none known), rows with `as_of.day − lookback ≤ obs_date ≤ as_of.day`; an observation whose latest known value is missing is dropped. `macro_pit_detail` = the long rows used (`series_id, obs_date, value, realtime_start, available_at`). `Macro(lake, config=None, *, catalog=None)`: `observations(ids)`, `pit`, `pit_detail`, `series_info()`; unknown id or bare string → `ValueError`; table never written → `DataError`. | Brief; fail safe. |
| D8 | Transforms on a `pd.Series` indexed by `obs_date` (one column of a PIT frame), thresholds from `MacroConfig` (`config=None` → defaults): `yoy_pct` = 100·(x_t / x_{t−12m} − 1); `annualized_3m_pct` = 100·((x_t / x_{t−3m})⁴ − 1); the lagged value is the latest observation on or before t − n months within `transform_lag_tolerance_days`, else NaN; `zscore` = (x_t − mean)/std (ddof 1) over the trailing `round(365.25·zscore_window_years)` days, NaN until the series spans `zscore_min_years`; `curve_slope(long, short)` = long − short on common dates (pp); `real_rate(nominal, inflation_pct)` = nominal − latest inflation observation on or before each date (pp). | Brief task 4; computed per snapshot, so no look-ahead. |
| D9 | `ingest_macro(lake, client, series_ids=None, *, config=None, catalog=None, observation_end=None, realtime_end=None, refresh=True) -> MacroIngestReport(rows, series_ok, series_failed, failure_reasons, revision_rows)`; `None` ids = whole catalog; ends default to 9999-12-31 (latest). Request checked first (bare string / unknown id → `ValueError`; de-duplicated). A failing series (`DataError`) never aborts the batch. | Mirrors Stage 09. |

## File structure

| File | Responsibility |
|---|---|
| `src/atlas/core/config.py`, `config/fund.yaml` (modify) | `MacroConfig`, `DataConfig.macro`, `data.macro` keys |
| `config/macro.yaml` (create) | the 22 series with kind, release time, description |
| `src/atlas/data/macro/__init__.py` | exports; importing registers both tables |
| `src/atlas/data/macro/fred_client.py` | `FredClient`, `build_fred_client` |
| `src/atlas/data/macro/series.py` | `MacroSeries`, `MacroCatalog`, `load_macro_series`, `MACRO_OBSERVATIONS`, `MACRO_SERIES`, `parse_series_metadata`, `parse_observations`, `vintage_windows`, `collapse_vintages`, `attach_available_at` |
| `src/atlas/data/macro/ingest.py` | `MacroIngestReport`, `ingest_macro` |
| `src/atlas/data/macro/pit.py` | `known_observations`, `macro_pit`, `macro_pit_detail`, `Macro` |
| `src/atlas/data/macro/transforms.py` | `yoy_pct`, `annualized_3m_pct`, `zscore`, `curve_slope`, `real_rate` |
| `tests/fixtures/macro/` | `series/`, `vintagedates/`, `observations/` for CPIAUCSL and DGS10, `README.md` |
| `tests/unit/data/macro/macro_helpers.py`, `test_macro_{catalog,client,ingest,pit,market,transforms,live}.py` | helpers, fake FRED, tests *(Task 7 added `test_macro_guards.py`: error paths, 100% line coverage; a few tests beyond the listed ones are named in the worklog)* |
| `docs/data-limitations.md` (modify) | macro section |

## Review Focus

1. A long daily history split into windows must give the same rows as one request. Test: `test_windowed_ingest_equals_single_request` (fake FRED, `max_vintage_dates_per_request=3`).
2. Release day before the release time: CPI out 08:30 on 2020-04-10 is not known at 08:00 that day. Test: `test_cpi_not_known_on_release_morning`.
3. DST: a market value available 08:00 ET is 13:00 UTC in winter and 12:00 UTC in summer. Test: `test_market_available_at_follows_dst`.
4. The API key never leaks (query parameter): not in logs, errors or cache names, even when a transport error's text contains the URL. Test: `test_api_key_never_logged_or_raised`.
5. A later vintage that blanks a value (`"."`) hides the old value; holidays give no row. Tests: `test_latest_missing_value_hides_older_value`, `test_good_friday_has_no_row`.

---

### Task 1: Config and series catalog

**Files:** modify `src/atlas/core/config.py`, `config/fund.yaml`, `tests/unit/core/test_config.py`; create `config/macro.yaml`, `src/atlas/data/macro/__init__.py`, `series.py` (catalog part), `tests/unit/data/macro/test_macro_catalog.py`.

**Produces:** `MacroConfig` at `cfg.data.macro` (D6); `MacroSeries(id: str, kind: Literal["market","release"], release_time: time | None, description: str)`; `MacroCatalog` (`ids -> tuple[str, ...]`, `get(id) -> MacroSeries` (`KeyError`), `__contains__`, `__len__`); `load_macro_series(path: Path) -> MacroCatalog`.

- [x] Failing tests: `test_macro_defaults` (D6 values; repo YAML equals defaults via the existing `test_yaml_covers_every_model_key`), `test_macro_validation` (rate 3 / 0, `max_vintage_dates_per_request` 1 / 2001, `page_limit` 100001, `market_lag_days` 0, `zscore_min_years` 11 → `ValidationError`); `test_catalog_repo_file_has_the_brief_series` (exactly the 22 ids; the 8 market ids of D5; CPIAUCSL release 08:30; DTWEXBGS release), `test_catalog_rejects_bad_files` (release without `release_time`, market with one, unknown kind, lower-case id, empty `series`, missing file → `ConfigError` naming the file).
- [x] Run `uv run pytest tests/unit/data/macro tests/unit/core/test_config.py -q` → FAIL (module missing).
- [x] Implement. Run → PASS.

### Task 2: FRED client with mocked responses (brief task 1)

**Files:** create `fred_client.py`, `tests/fixtures/macro/**` (recorded) + `README.md`, `macro_helpers.py`, `test_macro_client.py`.

**Consumes:** `MacroConfig`. **Produces:** `FredClient`, `build_fred_client` (D4).

- [x] Failing tests: `test_series_metadata_request` (respx: `GET …/series?series_id=CPIAUCSL&api_key=…&file_type=json`, payload returned, raw file `raw/fred/series/CPIAUCSL.json`, second call from cache, `refresh=True` asks again), `test_vintage_dates_paginates_and_filters` (fake: limit 2 → all CPIAUCSL vintages of 2020 = 13 dates incl. 2020-02-11 and 2020-04-10), `test_observations_request_params` (`output_type=1`, the four dates, `limit=page_limit`; fixture rows returned), `test_observations_paginates` (`page_limit=50` → 3 requests, 114 rows), `test_observations_short_count_raises`, `test_client_throttles` (0.5 s before every request but the first), `test_client_retries_429_and_5xx`, `test_client_gives_up_after_retries`, `test_http_400_reports_fred_message`, `test_bad_json_raises`, `test_build_fred_client_requires_key` (names `FRED_API_KEY`), `test_api_key_never_logged_or_raised`.
- [x] Run `uv run pytest tests/unit/data/macro/test_macro_client.py -q` → FAIL.
- [x] Implement. Run → PASS.

### Task 3: Vintage ingestion (brief task 2, storage part)

**Files:** extend `series.py`; create `ingest.py`, `test_macro_ingest.py`.

**Consumes:** `FredClient`, `MacroCatalog`. **Produces:** `MACRO_OBSERVATIONS`, `MACRO_SERIES` (D1, registered on import), `OBS_COLUMNS`; `parse_series_metadata(payload, series: MacroSeries) -> DataFrame`; `parse_observations(payload, series_id) -> DataFrame` (`series_id, obs_date, value, realtime_start`); `vintage_windows(dates: Sequence[date], realtime_start: date, realtime_end: date, max_dates: int) -> list[tuple[date, date]]`; `collapse_vintages(rows) -> DataFrame`; `attach_available_at(rows, series: MacroSeries, config: MacroConfig) -> DataFrame` (release rule here, market rule Task 5); `ingest_macro(...) -> MacroIngestReport` (D9).

- [x] Failing tests: `test_parse_observations_cpi` (114 rows; March 2020 rows 257.953 @ 2020-04-10 and 257.989 @ 2021-02-08), `test_parse_observations_missing_is_nan`, `test_parse_observations_malformed_raises` (no `observations`, bad date, non-numeric value), `test_parse_series_metadata` (title, `M`, units, `SA`, kind), `test_vintage_windows` (5 dates, max 3 → `[(start, d3−1), (d3, end)]`; no dates → one window; max 2 → one date per window), `test_collapse_vintages_merges_equal_values`, `test_release_available_at_is_release_time_et` (CPI March 2020 first row → 2020-04-10 12:30 UTC), `test_ingest_cpi_fixture` (rows 114, `series_ok == ("CPIAUCSL",)`, `revision_rows` = 114 − distinct obs dates, table and `macro_series` readable), `test_ingest_is_idempotent`, `test_windowed_ingest_equals_single_request`, `test_ingest_failing_series_does_not_abort`, `test_ingest_validates_request`.
- [x] Run → FAIL. Implement. Run → PASS.

### Task 4: Point-in-time query (brief task 2, query part + acceptance)

**Files:** create `pit.py`, `test_macro_pit.py`.

**Produces:** `known_observations`, `macro_pit`, `macro_pit_detail`, `Macro` (D7).

- [x] Failing tests: `test_acceptance_cpi_march_2020_unknown_on_2020_04_05` (latest CPI obs 2020-02-01 = 259.050; no 2020-03-01), `test_acceptance_cpi_march_2020_first_release_on_2020_04_15` (257.953), `test_cpi_not_known_on_release_morning` (2020-04-10: no March; 2020-04-13: 257.953), `test_revision_seen_only_after_its_vintage` (2021-02-08 → 257.953; 2021-02-09 → 257.989; March 2019: 2020-02-11 → 254.148, 2020-02-12 → 254.095), `test_acceptance_no_returned_value_released_after_as_of` (every month-end from 2019-01 to 2021-12: all `pit_detail.available_at < cutoff`, and the result equals the one from a store holding only rows known by then), `test_future_rows_change_nothing`, `test_guard_raises_if_filter_bypassed` (patched `pit_filter` → `LookAheadError`), `test_latest_missing_value_hides_older_value`, `test_macro_pit_shape_and_lookback` (columns in request order; unknown-to-store id all NaN; rows within lookback), `test_macro_reader_errors` (unknown id, bare string → `ValueError`; empty table → `DataError`).
- [x] Run → FAIL. Implement. Run → PASS.

### Task 5: Market series availability (brief task 3)

**Files:** modify `series.py`; create `test_macro_market.py`.

- [x] Failing tests: `test_market_first_vintage_available_next_day_0800_et` (DGS10 2020-03-02 → 2020-03-03 13:00 UTC), `test_market_available_at_follows_dst` (2020-03-09 → 2020-03-10 12:00 UTC), `test_market_revision_waits_for_its_vintage` (synthetic: revision with `realtime_start` 2020-03-20 → 2020-03-20 12:00 UTC), `test_dgs10_pit_strict_cutoff` (as of 2020-03-03: no 2020-03-02; as of 2020-03-04: 1.10), `test_good_friday_has_no_row` (as of 2020-04-20: no 2020-04-10; 2020-04-09 = 0.73).
- [x] Run → FAIL. Implement. Run → PASS.

### Task 6: Transforms (brief task 4)

**Files:** create `transforms.py`, `test_macro_transforms.py`.

- [x] Failing tests: `test_yoy_pct_cpi_first_release` (PIT at 2020-04-15: March 2020 YoY = 100·(257.953/254.095 − 1)), `test_yoy_needs_the_year_ago_value` (gap → NaN), `test_yoy_daily_uses_previous_business_day` (Sat t−1y → Fri), `test_annualized_3m_pct` (100, 101, 102, 103 → 100·(1.03⁴ − 1) = 12.550881), `test_zscore_known_value` (monthly 0..155 from 2000-01: value at 2012-12-01 equals the hand-computed trailing-window z; NaN before 2005-01-01), `test_curve_slope` (literal yields, common dates only), `test_real_rate_uses_latest_inflation` (DGS10 fixture minus monthly core PCE YoY 1.8 / 1.0).
- [x] Run → FAIL. Implement. Run → PASS.

### Task 7: Docs, live test, coverage, full verification

**Files:** create `test_macro_live.py`; modify `docs/data-limitations.md`; stage records.

- [x] `test_live_fred_cpi` (`-m live`; skip without `FRED_API_KEY`; ingest CPIAUCSL from 2019, assert March 2020 as of 2020-04-15 = 257.953).
- [x] Data-limitations section: BAML history from 2023-10-30, market rule and its two-day effect, release times, ALFRED first vintages, UMCSENT licence delay, units.
- [x] Coverage of `atlas.data.macro` (add tests for uncovered error paths); docstrings on every public name.
- [x] `make check`, the brief's Verify, `-W error`, `-k acceptance`, `-m live`; save raw output in `artifacts/`; write `verification.md`, `worklog.md`, `PROGRESS.md`; commit, merge into `main`, re-verify on the merged result.
