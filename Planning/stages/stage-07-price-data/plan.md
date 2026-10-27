# Stage 07 — Price data providers (yfinance, Alpaca) · Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking; tick only after the step's tests have run and passed in this chat.

- **Brief:** [brief.md](brief.md) (`Planning/stages/stage-07-price-data/brief.md`)
- **Status:** DONE (2026-10-27; see [verification.md](verification.md))
- **Gate check:** Stage 06 is DONE (its [verification.md](../stage-06-storage/verification.md) says ALL CRITERIA VERIFIED; evidence block also in `PROGRESS.md`); `uv run pytest -q` → 627 passed, exit 0 (2026-10-27) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution method:** executing-plans (native, inline TDD). The owner asked in chat (2026-10-27) to "firstly generate the plans for Stage 7 then carefully implement the plans and complete the plans" and then audit the stage against the plan, so the plan is executed straight after it is written; open design choices take the option recorded below and are logged in the Decisions log. A self-review of the whole diff runs before DONE; the owner-requested audit (plan vs work) runs after DONE.

## Context

Stage 06 is `DONE` (6/66). Stage 07 is the first data stage: it fetches daily bars and corporate actions from free vendors (spec A3) and writes them to the lake as point-in-time curated tables. Later consumers checked: Stage 08 (universe, delisted names), 13 (quality scoring, `DataView` reads `prices_daily` filtered on `available_at`), 14 (backfill CLI calls `ingest_prices`), 15 (features from adjusted prices), 52 (accounting applies `corporate_actions` to positions).

**Goal:** Reliable daily OHLCV (raw + adjusted), splits and dividends for stocks and ETFs, behind a provider interface with caching and a fallback.

**Architecture:** `providers/base.py` fixes the frame contract every provider returns (raw prices, vendor `adj_close`, actions with raw cash amounts) plus shared request checks, retry and throttle helpers. Two adapters (`yfinance_provider.py`, `alpaca_provider.py`) turn vendor payloads into that contract; `fallback.py` chains two providers and builds the configured chain; `cache.py` memoises finished windows on disk. `atlas.data.prices` registers the two curated tables, stamps `available_at` from the Stage 05 calendar, recomputes the adjustment factor from actions, cross-checks it against the vendor's `adj_close`, and upserts per symbol so one bad symbol never aborts the batch.

**Tech stack:** yfinance 1.7 (batch `download`, `Ticker.splits`), httpx 0.28 (Alpaca data API v2), respx 0.23 (HTTP mocks in tests), pandas 3, Stage 06 `Lake`.

**Spec:** brief above; design spec §3 (point-in-time, fail safe), §11 (yfinance free, Alpaca market data), §15 A3 (free data only), A4 (paper only), §16 (vendor breakage → provider interface + cache + fallback; survivorship → delisted-ticker handling); stage protocol §4–§5; Stage 05 handoff (`bar_available_at`, 16:15 ET / 13:15 ET on early closes, collision C1); Stage 06 handoff (register in `DEFAULT_REGISTRY`, frames must carry real dtypes, re-ingest with `mode="upsert"`, never log `run_id`/`mode`/`as_of`/`stage`).

## Global constraints

- Symbols in every frame are canonical (`normalize_symbol`: `BRK.B`); vendor spellings only at the HTTP/yfinance boundary (`vendor_symbol(s, "-")` for Yahoo).
- `open, high, low, close` are **raw** (as traded that day, not split-adjusted) USD floats; `volume` is raw shares (`int64`); `adj_close` is the vendor's split- **and** dividend-adjusted close as of fetch time. Action `ratio` = new shares per old share (4.0 for 4:1, 0.1 for 1:10); dividend `amount` = raw cash per share on the ex-date, USD.
- `available_at` = `bar_available_at(day, calendar)` (session close + `bar_publish_lag_minutes`), UTC.
- Providers raise `DataError` for vendor failures (after retries); other exceptions propagate (bugs are never swallowed).
- No real network in unit tests: yfinance is injected (`download`, `splits` callables), Alpaca goes through respx. Live smoke tests are `@pytest.mark.live` (deselected by default).
- No wall-clock reads; retry waits use an injected `sleep` (default `time.sleep`). API keys are `SecretStr`, sent only as headers, never logged or put in messages.
- Thresholds and retry settings live in `config/fund.yaml › data.prices`, read through `AtlasConfig` (`PriceDataConfig`).
- Test basenames unique (no `__init__.py`): `tests/unit/data/test_price_*.py`; fixtures under `tests/fixtures/prices/`.
- **One commit for the stage:** `stage-07: Price data providers (yfinance, Alpaca)` (+ attribution trailer), after DONE.

## Collisions with the overall plan

None found with the roadmap or the design spec. Points checked:
- Brief Goal says "with caching" but no In-scope line names a cache; spec §16 lists "Provider interface + cache + fallback provider" as the mitigation for vendor breakage. Resolution: a small on-disk `CachedPriceProvider` (Task 5) — it implements the Goal and the spec, not new scope. Not a collision (the higher-ranked spec and the brief agree).
- Brief says `available_at` = session close + 15 min; it is listed under both curated tables. `corporate_actions` gets `bar_available_at` of its ex-date session (the first session on or after `ex_date`): the ex-date bar is the first price that shows the action, so the action is never known earlier than the bar it explains. Conservative, no look-ahead.

## Design decisions (also logged in PROGRESS.md › Decisions log)

| # | Decision | Why |
|---|---|---|
| D1 | Yahoo's `Close`/`Open`/`High`/`Low`, `Volume` and `Dividends` are already split-adjusted (probed 2026-10-27: AAPL 2020-08-28 close 124.81 = 499.23 / 4). The adapter un-adjusts them with the symbol's **full** split history (`yf.Ticker(s).splits`): raw price = yahoo × F(d), raw volume = round(yahoo / F(d)), raw dividend = yahoo × F(d), F(d) = Π ratio of splits with ex-date > d. | Brief asks for raw + adjusted; splits after the window are baked into Yahoo's numbers too, so the window's own split column is not enough. |
| D2 | yfinance requests: `download(vendor_symbols, start, end + 1 day, auto_adjust=False, actions=True, group_by="ticker", threads=False, progress=False)` in batches of `batch_size`; all-NaN rows are dropped (that is how Yahoo pads a missing/delisted ticker); a symbol with no rows, or whose split history is `None`, is left out of the result (logged). Downloads and split histories are memoised per provider instance, so `fetch_actions` after `fetch_daily` on the same window makes no second request. | yfinance's `end` is exclusive; delisted names (SIVB) come back as NaN columns and `splits = None`. |
| D3 | Alpaca: `GET {data_url}/v2/stocks/bars` with `timeframe=1Day, feed=<config>, adjustment=raw` and again with `adjustment=all`, `limit=10000, sort=asc`, following `next_page_token`; OHLCV from `raw`, `adj_close` from `all.c`; bar `t` (UTC) → New York date. Actions from `GET {data_url}/v1/corporate-actions?types=forward_split,reverse_split,cash_dividend`; `ratio = new_rate / old_rate`, `amount = rate`. Retries on transport errors, 429 and 5xx; other 4xx → `DataError` at once. `AlpacaPriceProvider.from_config(cfg)` returns `None` when either key is unset. | Brief: data API v2, IEX on the free tier, adjustment raw/all, optional when keys are set. |
| D4 | `FallbackPriceProvider(primary, secondary)`: `fetch_daily` asks the primary; symbols it did not return (or all, if it raised `DataError`) go to the secondary. Both raising → `DataError` (chained). Empty results are not failures (a delisted name has no data). `fetch_actions` routes each symbol to the provider that served its prices in the last `fetch_daily` (primary if never fetched), with the other provider as backup on `DataError`. | Brief task 4; actions must come from the same source as the prices they adjust. |
| D5 | `build_price_provider(cfg)`: primary = `cfg.data.providers.prices`; secondary = the other vendor if usable (Alpaca needs both keys). `prices: alpaca` without keys → `ConfigError`. Returns a single provider when no secondary is usable. | "Alpaca optional, enabled when keys are set". |
| D6 | `CachedPriceProvider(inner, cache_dir)`: one Parquet file per symbol and window, `cache_dir/<inner.name>/{daily,actions}/<SYMBOL>_<start>_<end>.parquet`, written atomically; cached symbols make no inner call, the rest are fetched in one call. Cached: daily rows of symbols that returned rows; actions (even empty) of symbols whose bars for the window are cached. Never cached: a symbol with no rows (may be a transient gap), a call that raised, a window with `end >= get_clock().today()` (the last bar may change). *(Refined during Task 5 from "one hashed file per request", which would have cached a transient empty answer for a symbol.)* | Spec §16 / Goal "caching"; backtest re-runs and vendor outages. |
| D7 | Curated tables (registered in `DEFAULT_REGISTRY` on import of `atlas.data.prices`): `prices_daily` (symbol, date, open, high, low, close, adj_close, volume, vendor, available_at), PK `(symbol, date)`, partitioned by `year` of `date`; `corporate_actions` (symbol, ex_date, type, ratio?, amount?, vendor, available_at), PK `(symbol, ex_date, type)`, unpartitioned; `type ∈ {split, dividend}`. `vendor` on actions is an addition to the brief's column list. | Brief column lists; year partitions keep an upsert of one year's bars to one file per year. |
| D8 | `ingest_prices` fetches the batch; on a batch `DataError` it retries symbol by symbol. A symbol fails (recorded with a reason, never raised) when it has no rows, its prices or actions could not be fetched, its rows fail `validate_frame`, or a bar falls on a non-session. Good symbols are upserted (idempotent). | Acceptance: "a failed symbol never aborts the batch"; "re-running is idempotent". |
| D9 | Adjustment check (CRSP-style, backward): total factor T(d) = Π over actions with ex-date > d of (1 / ratio) for splits and (1 − amount / prev_close) for dividends, prev_close = last raw close before the ex-date. Ours = close × T(d). Vendor `adj_close` also carries actions after the window, so both series are divided by their value on the symbol's last bar before comparing; |ours/vendor − 1| > `adj_mismatch_tolerance` (0.005) is a mismatch, recorded in the report and logged at warning. | Brief: "recompute the adjustment factor ourselves … record mismatches > 0.5%". |
| D10 | Config `data.prices` (`PriceDataConfig`): `batch_size` 50, `max_retries` 3, `backoff_seconds` 1.0 (doubles each retry), `min_request_interval_seconds` 0.5 (pause between vendor requests), `request_timeout_seconds` 30, `alpaca_feed` iex, `alpaca_data_url` https://data.alpaca.markets, `adj_mismatch_tolerance` 0.005. | Stage protocol §4: nothing hard-coded. |

## File structure

| File | Responsibility |
|---|---|
| `src/atlas/core/config.py` (modify) | `PriceDataConfig`, `DataConfig.prices` |
| `config/fund.yaml` (modify) | `data.prices` keys with comments |
| `src/atlas/data/providers/__init__.py` | Re-exports |
| `src/atlas/data/providers/base.py` | `PriceProvider` protocol, column contracts, `normalize_daily/actions`, `check_request`, `RetryPolicy`, `call_with_retry`, `Throttle` |
| `src/atlas/data/providers/yfinance_provider.py` | `YFinanceProvider` |
| `src/atlas/data/providers/alpaca_provider.py` | `AlpacaPriceProvider` |
| `src/atlas/data/providers/fallback.py` | `FallbackPriceProvider`, `build_price_provider` |
| `src/atlas/data/providers/cache.py` | `CachedPriceProvider` |
| `src/atlas/data/prices.py` | Table schemas, `IngestReport`, `ingest_prices`, `adjustment_factors`, `cross_check_adjustment` |
| `tests/unit/data/price_helpers.py` | `FakePriceProvider`, fixture loaders, fake yfinance `download`/`splits` |
| `tests/unit/data/conftest.py` | Fixtures (`fake_provider`, `yf_fixture`, `cal`) |
| `tests/fixtures/prices/` | Recorded yfinance CSVs (AAPL, SPY, SIVB) + split histories; Alpaca JSON in the documented v2 shape; `README.md` |

## Tasks (execute in order; brief tasks 1–7 map to Tasks 1–4, 6–8; Task 5 is the Goal's cache)

### Task 1 — Contract, config and fake provider (brief task 1)
**Files:** create `providers/__init__.py`, `providers/base.py`, `tests/unit/data/price_helpers.py`, `tests/unit/data/conftest.py`, `tests/unit/data/test_price_base.py`; modify `core/config.py`, `config/fund.yaml`, `pyproject.toml` (deps yfinance, httpx; dev respx; mypy override for `yfinance`).

**Produces:**
- `DAILY_COLUMNS = ("symbol","date","open","high","low","close","adj_close","volume","vendor")`, `ACTION_COLUMNS = ("symbol","ex_date","type","ratio","amount","vendor")`, `ActionType = Literal["split","dividend"]`.
- `class PriceProvider(Protocol)` (runtime-checkable): `name: str`; `fetch_daily(symbols: Sequence[str], start: date, end: date) -> pd.DataFrame`; `fetch_actions(symbols, start, end) -> pd.DataFrame`. Both inclusive of `start` and `end`.
- `empty_daily() / empty_actions() -> pd.DataFrame`; `normalize_daily(df) / normalize_actions(df) -> pd.DataFrame` (exact columns in order, dtypes `str`/`date` objects/`float64`/`int64`, sorted by symbol then date, fresh index; missing column → `DataError`).
- `check_request(symbols, start, end) -> tuple[str, ...]` (canonical, de-duplicated in first-seen order; `start > end` → `ValueError`; invalid symbol → `ValueError`).
- `@dataclass(frozen=True) RetryPolicy(max_retries, backoff_seconds, min_interval_seconds)` with `RetryPolicy.from_config(PriceDataConfig)`; `call_with_retry(fn: Callable[[], T], policy, sleep, what: str, retry_on: tuple[type[BaseException], ...]) -> T` (waits `backoff × 2**i`; exhausted → `DataError(… what …)` chained); `Throttle(min_interval, sleep).wait()` (no pause on the first call, `min_interval` before each later one).
- `PriceDataConfig` (D10) at `cfg.data.prices`.

- [x] Write failing tests: `test_fake_provider_satisfies_protocol`, `test_normalize_daily_orders_columns_and_dtypes`, `test_normalize_daily_missing_column_raises`, `test_normalize_actions_dtypes_and_nullable_ratio_amount`, `test_check_request_normalises_and_dedupes` (`["aapl"," AAPL","brk-b"]` → `("AAPL","BRK.B")`), `test_check_request_rejects_reversed_window`, `test_call_with_retry_backs_off_then_succeeds` (sleeps `[1.0, 2.0]`), `test_call_with_retry_exhausted_raises_data_error`, `test_call_with_retry_does_not_catch_other_errors`, `test_throttle_pauses_between_calls`, `test_price_config_defaults_and_yaml` (`load_config().data.prices.adj_mismatch_tolerance == 0.005`, `alpaca_feed == "iex"`).
- [x] Run `uv run pytest tests/unit/data/test_price_base.py -q` → FAIL (module missing).
- [x] Implement; run → PASS; `uv run pytest tests/unit/core/test_config.py -q` → PASS (every YAML key commented).

### Task 2 — yfinance adapter (brief task 2)
**Files:** create `providers/yfinance_provider.py`, `tests/unit/data/test_price_yfinance.py`, fixtures `tests/fixtures/prices/yfinance_{AAPL,SPY,SIVB}.csv`, `yfinance_splits.csv`, `README.md` (already recorded 2026-10-27 by `artifacts/record_yfinance_fixtures.py`: window 2020-08-03 → 2020-09-30, 42 sessions).

**Consumes:** Task 1 helpers. **Produces:** `YFinanceProvider(config: PriceDataConfig | None = None, *, download: DownloadFn | None = None, splits: SplitsFn | None = None, sleep: Callable[[float], None] = time.sleep)`, `name = "yfinance"`. `DownloadFn = Callable[[list[str], date, date], pd.DataFrame]` (vendor symbols, start, exclusive end; returns yfinance's `group_by="ticker"` frame); `SplitsFn = Callable[[str], pd.Series | None]` (vendor symbol → split ratios indexed by timestamps). Defaults wrap the real yfinance calls (D2) and silence only yfinance's `Pandas4Warning`.

- [x] Write failing tests (fake `download`/`splits` built from the recorded CSVs): `test_aapl_prices_are_unadjusted_before_split` (2020-08-28 close = 124.807503 × 4 ± 1e-6, volume = 187630000 / 4; 2020-08-31 close = 129.039993), `test_split_after_window_still_unadjusts` (window 2020-08-03 → 2020-08-28 still × 4), `test_adj_close_is_vendor_value`, `test_actions_aapl_split_and_raw_dividend` (split 2020-08-31 ratio 4.0; dividend 2020-08-07 amount 0.82), `test_actions_spy_dividend` (2020-09-18, 1.339), `test_delisted_symbol_is_absent_not_an_error` (SIVB), `test_download_args_end_exclusive_and_vendor_symbols` (`BRK.B` → `BRK-B` → `BRK.B`), `test_batches_and_throttles` (`batch_size=2`, 3 symbols → 2 downloads, 1 throttle sleep), `test_retry_then_success`, `test_download_keeps_failing_raises_data_error`, `test_actions_reuse_the_daily_download`, `test_tz_aware_index_uses_new_york_date`, `test_partial_nan_row_is_kept_for_validation` (row with NaN volume stays; ingest rejects it — Review focus 2).
- [x] Run → FAIL; implement; run → PASS.

### Task 3 — Alpaca adapter (brief task 3)
**Files:** create `providers/alpaca_provider.py`, `tests/unit/data/test_price_alpaca.py`, fixtures `alpaca_bars_raw.json`, `alpaca_bars_all.json`, `alpaca_bars_page2.json`, `alpaca_corporate_actions.json` (built in the documented v2 response shape from the recorded yfinance numbers — no keys were available to record; noted in the fixtures README).

**Produces:** `AlpacaPriceProvider(key_id: SecretStr, secret_key: SecretStr, config: PriceDataConfig | None = None, *, client: httpx.Client | None = None, sleep=time.sleep)`, `name = "alpaca"`, `from_config(cfg: AtlasConfig) -> AlpacaPriceProvider | None`, `close()`.

- [x] Write failing tests (respx): `test_daily_merges_raw_and_adjusted`, `test_requests_carry_key_headers_feed_and_dates`, `test_follows_next_page_token`, `test_retries_429_then_succeeds`, `test_unauthorized_raises_without_retry_or_secret_in_message`, `test_actions_split_reverse_split_and_dividend` (reverse split new 1 / old 10 → 0.1), `test_missing_symbol_absent`, `test_from_config_none_without_keys`.
- [x] Run → FAIL; implement; run → PASS.

### Task 4 — Fallback and provider factory (brief task 4)
**Files:** create `providers/fallback.py`, `tests/unit/data/test_price_fallback.py`.
**Produces:** `FallbackPriceProvider(primary, secondary)` (`name = "fallback"`, D4); `build_price_provider(cfg: AtlasConfig) -> PriceProvider` (D5).

- [x] Write failing tests: `test_primary_empty_uses_secondary`, `test_primary_partial_fills_missing_from_secondary`, `test_primary_error_uses_secondary`, `test_both_fail_raises_data_error`, `test_actions_follow_the_provider_that_served_prices`, `test_factory_yfinance_only_without_keys`, `test_factory_adds_alpaca_when_keys_set`, `test_factory_alpaca_primary_without_keys_is_config_error`.
- [x] Run → FAIL; implement; run → PASS.

### Task 5 — On-disk cache (brief Goal "caching", spec §16)
**Files:** create `providers/cache.py`, `tests/unit/data/test_price_cache.py`.
**Produces:** `CachedPriceProvider(inner: PriceProvider, cache_dir: Path)` (`name = inner.name`, D6).

- [x] Write failing tests: `test_second_call_is_served_from_cache`, `test_cache_key_depends_on_symbols_and_window`, `test_window_reaching_today_is_not_cached` (uses `sim_clock`), `test_inner_error_is_not_cached`, `test_missing_symbol_is_not_cached`, `test_actions_cached_only_for_symbols_with_cached_bars`.
- [x] Run → FAIL; implement; run → PASS.

### Task 6 — Ingest to lake (brief task 5)
**Files:** create `src/atlas/data/prices.py`, `tests/unit/data/test_price_ingest.py`.
**Produces:** `PRICES_DAILY`, `CORPORATE_ACTIONS` (`TableSchema`, D7); `@dataclass(frozen=True) AdjustmentMismatch(symbol: str, date: date, ours: float, vendor: float, rel_diff: float)`; `@dataclass(frozen=True) IngestReport(rows: int, action_rows: int, symbols_ok: tuple[str, ...], symbols_failed: tuple[str, ...], failure_reasons: FrozenDict[str, str], mismatches: tuple[AdjustmentMismatch, ...])`; `ingest_prices(lake: Lake, provider: PriceProvider, symbols: Sequence[str], start: date, end: date, *, calendar: TradingCalendar | None = None, config: PriceDataConfig | None = None) -> IngestReport`.

- [x] Write failing tests (fake yfinance provider over the fixtures + `tmp_lake`): `test_ingest_fixtures_writes_prices_and_actions` (AAPL + SPY 42 rows each; SIVB failed "no price data"), `test_available_at_is_close_plus_lag` (2020-08-31 → 20:15 UTC; early close 2020-11-27 → 18:15 UTC with a fake provider), `test_action_available_at_is_ex_date_bar`, `test_reingest_is_idempotent` (same frame after a second run; `rows` equal), `test_reingest_replaces_changed_values`, `test_failed_symbol_never_aborts_batch` (provider raising for one symbol only → others written), `test_batch_error_retries_symbol_by_symbol`, `test_invalid_rows_fail_only_that_symbol` (NaN volume), `test_bar_on_non_session_fails_symbol` (Review focus 1), `test_actions_error_fails_symbol`, `test_empty_request_writes_nothing`.
- [x] Run → FAIL; implement (mismatches empty for now); run → PASS.

### Task 7 — Adjustment-factor cross-check (brief task 6)
**Files:** modify `src/atlas/data/prices.py`; create `tests/unit/data/test_price_adjustment.py`.
**Produces:** `adjustment_factors(daily: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame` (adds `split_factor`, `total_factor`, `split_adj_close`, `calc_adj_close`, same row order); `cross_check_adjustment(daily, actions, tolerance: float) -> tuple[AdjustmentMismatch, ...]` (D9); `ingest_prices` fills `IngestReport.mismatches` and logs `prices.adj_mismatch`.

- [x] Write failing tests: `test_aapl_split_adjusted_series_is_continuous` (split_adj_close 2020-08-28 = 124.807503 ± 1e-4; 2020-08-31 unchanged; day-over-day move on 2020-08-31 is +3.39%, not −74%), `test_aapl_recomputed_matches_vendor_within_tolerance` (0 mismatches, max deviation < 0.05%), `test_spy_dividend_factor` (dates before 2020-09-18 × (1 − 1.339 / close 2020-09-17)), `test_tampered_vendor_adj_close_is_recorded` (vendor pre-split values not divided by 4 → mismatches on every pre-split date), `test_reverse_split_and_first_bar_dividend` (Review focus 4), `test_ingest_report_carries_mismatches`.
- [x] Run → FAIL; implement; run → PASS.

### Task 8 — Live smoke tests (brief task 7)
**Files:** create `tests/unit/data/test_price_live.py`.
- [x] `test_yfinance_live_smoke` and `test_alpaca_live_smoke` (`@pytest.mark.live`; Alpaca skips without keys): fetch SPY for one recent week; assert rows, positive closes, raw columns present.
- [x] `uv run pytest tests/unit/data -q` → the live tests are deselected; `uv run pytest tests/unit/data -m live -q` → run where network/keys exist (optional per brief; record the outcome honestly).

### Task 9 — Verification and records
- [x] `make check`; `uv run pytest -q`; `ruff check .`; `ruff format --check .`; `mypy src/atlas`; brief's Verify `uv run pytest tests/unit/data -q`; optional `-m live`. Save raw output to `artifacts/`.
- [x] Self-review the diff; write `verification.md`, `worklog.md`, PROGRESS.md (handoff, evidence, Decisions log, backlog); commit `stage-07: Price data providers (yfinance, Alpaca)`.

## Review Focus

1. A vendor bar dated on a non-session (holiday) → that symbol fails with a reason; the batch continues (`test_bar_on_non_session_fails_symbol`).
2. A row with some NaN fields (e.g. missing volume) → the symbol fails validation, other symbols are written (`test_partial_nan_row_is_kept_for_validation`, `test_invalid_rows_fail_only_that_symbol`).
3. A split after the requested window → raw prices are still un-adjusted (`test_split_after_window_still_unadjusts`).
4. Reverse split (ratio < 1) and a dividend on the window's first bar → factors stay correct (`test_reverse_split_and_first_bar_dividend`).
5. Messy symbol lists (`"aapl"`, `" AAPL"`, `"BRK-B"`) and secrets in error paths → canonical, de-duplicated symbols; no key text in any error (`test_check_request_normalises_and_dedupes`, `test_unauthorized_raises_without_retry_or_secret_in_message`).

## Acceptance criteria → evidence (filled in verification.md)

| Criterion | Tests |
|---|---|
| Ingest of the fixtures yields correct split-adjusted series | `test_ingest_fixtures_writes_prices_and_actions`, `test_aapl_split_adjusted_series_is_continuous`, `test_aapl_recomputed_matches_vendor_within_tolerance` |
| Re-running is idempotent | `test_reingest_is_idempotent`, `test_reingest_replaces_changed_values` |
| A failed symbol never aborts the batch | `test_failed_symbol_never_aborts_batch`, `test_batch_error_retries_symbol_by_symbol`, `test_invalid_rows_fail_only_that_symbol`, `test_delisted_symbol_is_absent_not_an_error` |
