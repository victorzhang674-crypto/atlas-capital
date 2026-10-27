# Stage 07 — Price data providers (yfinance, Alpaca) · Work log

Append entries in order. Record failures and dead ends as honestly as successes.

## 2026-10-27 — Gate check and setup
- Did: read `Project.md`, `PROGRESS.md` (Stage 06 DONE, its verification.md says ALL CRITERIA VERIFIED), the brief, spec §3/§11/§15/§16 and the Stage 05/06 handoffs. Set up the worktree env (`UV_CACHE_DIR="$TMPDIR/uv-cache" UV_PROJECT_ENVIRONMENT=.venv.nosync uv sync --group dev`, `ln -s .venv.nosync .venv`).
- Ran: `uv run pytest -q` → 627 passed, exit 0 · raw: artifacts/gate-check-pytest.txt
- Added deps: `uv add "yfinance>=0.2.50" "httpx>=0.27"` (locked yfinance 1.7.0, httpx 0.28.1), `uv add --group dev "respx>=0.21"` (respx 0.23.1).
- Probed: Yahoo reachable through the sandbox proxy (bare curl → 429; yfinance's browser impersonation works); Alpaca data API reachable (401 without keys). A real `yf.download(..., auto_adjust=False, actions=True, group_by="ticker")` showed that Yahoo's Close/Volume/Dividends are already split-adjusted (AAPL 2020-08-28 close 124.81 = 499.23 / 4) → plan D1. SIVB comes back as all-NaN columns and `Ticker("SIVB").splits` is `None`.
- Recorded fixtures with `artifacts/record_yfinance_fixtures.py` (first run crashed on `splits is None` for SIVB; handled and re-run) → `tests/fixtures/prices/yfinance_{AAPL,SPY,SIVB}.csv` (2020-08-03 → 2020-09-30, 42 sessions) and `yfinance_splits.csv` (AAPL's 5 splits) · output: artifacts/record-fixtures-output.txt
- Wrote plan.md; set PROGRESS current stage to IN PROGRESS.

## 2026-10-27 — Task 1: contract, config, fake provider
- Did: `PriceDataConfig` (`cfg.data.prices`, D10) + commented `config/fund.yaml › data.prices`; `atlas.data.providers.base` (`PriceProvider`, `DAILY_COLUMNS`/`ACTION_COLUMNS`, `normalize_daily/actions`, `empty_*`, `check_request`, `RetryPolicy`, `call_with_retry`, `Throttle`); `tests/unit/data/price_helpers.py` (`FakePriceProvider`, `daily_rows`).
- Ran: `uv run pytest tests/unit/data/test_price_base.py -q` → collection ERROR (module missing; red). After implementing: `uv run pytest tests/unit/data/test_price_base.py tests/unit/core/test_config.py -q` → 112 passed.
- mypy: added `yfinance` to the `ignore_missing_imports` override (no `py.typed`).

## 2026-10-27 — Task 2: yfinance adapter
- Did: `atlas.data.providers.yfinance_provider.YFinanceProvider` (batched `yf.download`, retry, throttle, raw un-adjustment from the full split history, per-instance memo of downloads and split histories); `FakeYFinance` + fixture loaders in `price_helpers.py`; `tests/fixtures/prices/README.md`.
- Ran: `uv run pytest tests/unit/data/test_price_yfinance.py -q` → collection ERROR (red). After implementing → 16 passed, 1 failed: `test_batches_and_throttles` expected 1 throttle pause, got 3. Cause: the test was wrong, not the code — split-history lookups are vendor requests too (2 downloads + 2 split lookups = 4 requests → 3 pauses). Fixed the assertion (now also checks `split_calls`) → 17 passed.
- Lint/type: ruff flagged `×` in docstrings (RUF002), two long lines and two non-raw `match=` patterns; mypy flagged `Hashable` vs `date` on split-history iteration and the `frame[vsym]` slice type. Fixes: `*` in docstrings, raw strings, split history stored as `tuple[(date, ratio), ...]`, slice wrapped in `pd.DataFrame`. → `ruff check` All checks passed; `mypy src/atlas` Success.

## 2026-10-27 — Task 3: Alpaca adapter
- Did: `atlas.data.providers.alpaca_provider.AlpacaPriceProvider` (bars `raw` + `all`, paging, retry on transport/429/5xx, other 4xx → `DataError`, corporate actions with a 90-day look-ahead on the query because the API filters dividends by processing date, `from_config`, key-free `repr`). Fixtures built (not recorded; no keys) with `artifacts/build_alpaca_fixtures.py` (first run had an unpacking bug in the builder; fixed).
- Ran: `uv run pytest tests/unit/data/test_price_alpaca.py -q` → collection ERROR (red); after implementing → 13 passed. `ruff check`, `mypy` clean.

## 2026-10-27 — Task 4: fallback and factory
- Did: `FallbackPriceProvider` (per-symbol gap fill, actions routed to the provider that served the prices), `build_price_provider(cfg)`.
- Ran: red (collection ERROR) → 15 passed. `ruff`/`mypy` clean.

## 2026-10-27 — Task 5: on-disk cache
- Did: `CachedPriceProvider`. Refined D6 while writing the tests: per-symbol files instead of one hashed file per request, so a symbol that came back empty (possibly a transient vendor gap) is never cached as "no data"; actions cached only for symbols whose bars are cached. Plan D6 and Task 5 test list updated.
- Ran: red (collection ERROR) → 7 passed; mypy flagged an untyped lambda in `atomic_write(...)` → replaced with `functools.partial(_write_parquet, rows)`. `uv run pytest tests/unit/data -q` → 67 passed.

## 2026-10-27 — Task 6: ingest to lake
- Did: `atlas.data.prices` — `PRICES_DAILY` / `CORPORATE_ACTIONS` registered in `DEFAULT_REGISTRY`, `IngestReport`, `AdjustmentMismatch`, `ingest_prices` (batch fetch → per-symbol retry on `DataError`; per-symbol `available_at` + `validate_frame`; failures recorded with reasons; upsert of the good symbols).
- Ran: `uv run pytest tests/unit/data/test_price_ingest.py -q` → collection ERROR (red); after implementing → 17 passed first time. `ruff`/`mypy` clean.

## 2026-10-27 — Task 7: adjustment-factor cross-check
- Did: `adjustment_factors` (split and CRSP-style dividend factors; a dividend sharing its ex-date with a split is compared with the prior close restated per new share; a dividend ≥ prior close → `DataError`), `cross_check_adjustment` (both series rescaled to 1.0 on the last bar; |rel| > tolerance recorded); wired into `ingest_prices` (`IngestReport.mismatches`, warning `prices.adj_mismatch`; a mismatch is recorded, not fatal).
- Ran: red (ImportError) → 10 passed. `uv run pytest tests/unit/data -q` → 94 passed.

## 2026-10-27 — Task 8: live smoke tests
- Did: `tests/unit/data/test_price_live.py` (`pytestmark = pytest.mark.live`; SPY 2024-03-04 → 2024-03-08).
- Ran: `uv run pytest tests/unit/data -q` → 94 passed, 2 deselected. `uv run pytest tests/unit/data -m live -v -rs` → `test_yfinance_live_smoke` PASSED (real Yahoo), `test_alpaca_live_smoke` SKIPPED ("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set"; no keys in this environment).

## 2026-10-27 — Self-review of the diff (before verification)
- Found and fixed: (1) `_fetch_each` chose its mode by comparing function identity (`normalize is normalize_daily`); now takes an explicit `kind: Literal["prices", "actions"]`. (2) The yfinance adapter dropped every row without bar data, so a dividend Yahoo puts on a day without a bar would be lost silently. `_bars_of` now keeps rows with a dividend; `fetch_daily` drops bar-less rows itself. New test `test_dividend_on_a_day_without_a_bar_is_kept`: confirmed FAIL against the old code (`assert [] == [datetime.date(2020, 9, 18)]`), PASS after.
- Checked, no change: log keys avoid `run_id`/`mode`/`as_of`/`stage`; no wall-clock reads (cache uses `get_clock().today()`); keys only in headers, `repr` key-free; no live-money/trading endpoint (data API only); `DataError` is the only exception the chain/ingest catch.
- Ran: `uv run pytest tests/unit/data -q` → 95 passed, 2 deselected; ruff, mypy clean.

## 2026-10-27 — Verification run 1: full suite NOT green → fixed
- Ran: `uv run pytest -q` → **670 passed, 2 deselected, 52 errors**; `make check` exit=2 (lint, format, mypy were clean). Every error: `ValueError: a different schema is already registered as 'prices_daily'` at setup of a Stage 06 storage test.
- Root cause (systematic-debugging): `tests/unit/core/storage/conftest.py::lake` registered the storage tests' own stand-in `prices_daily` schema on `tmp_lake`, whose registry is a copy of `DEFAULT_REGISTRY`. Importing `atlas.data.prices` (collected with `tests/unit/data`) now registers the real `prices_daily` there, so the copy already holds a different schema under that name. Order-dependent: `tests/unit/data` alone passed. The fixture's assumption (an empty `DEFAULT_REGISTRY`) was true in Stage 06 and became wrong exactly as Stage 06's handoff foresaw ("register curated tables in `DEFAULT_REGISTRY` at import time (e.g. `atlas.data.prices`)").
- Fix: the storage `lake` fixture now builds `Lake(tmp_path / "lake", registry=SchemaRegistry([prices_schema]))`: same conditions as when Stage 06 wrote the tests, no assertion touched (Project.md Rule 1.6: the fixture was wrong, not the tests; recorded in the Decisions log and the handoff).
- Ran: `uv run pytest -q` → 722 passed, 2 deselected; `uv run pytest tests/unit/core/storage -q` → 115 passed (Stage 06's count); `tests/unit/data tests/unit/core/storage` and the reverse order → 210 passed, 2 deselected each.

## 2026-10-27 — Verification run 2 (final tree) and records
- Docstring check found one public-looking nested helper (`attempt` inside `AlpacaPriceProvider._get`); renamed `_attempt`. That made one line 101 characters long (ruff E501 + format diff), caught by the re-run; `ruff format` fixed it. Everything below was re-run after that change.
- Ran (all exit 0): `uv run pytest -q` → 722 passed, 2 deselected; `ruff check .` → All checks passed!; `ruff format --check .` → 91 files already formatted; `mypy src/atlas` → Success (47 files); brief's Verify `uv run pytest tests/unit/data -q` → 95 passed, 2 deselected; `-m live` → yfinance PASSED, Alpaca SKIPPED (no keys); `make check` → exit 0; acceptance tests with `-W error` → 12 passed; docstrings → missing none. Real-data ingest check (real Yahoo, twice) → `rows=84 action_rows=3 … mismatches=0` both runs.
- Wrote verification.md (ALL CRITERIA VERIFIED), ticked plan.md, updated PROGRESS.md (checkbox, status, handoff, evidence, Decisions log, Backlog).
- Code files created: `src/atlas/data/providers/{__init__,base,yfinance_provider,alpaca_provider,fallback,cache}.py`, `src/atlas/data/prices.py`; tests `tests/unit/data/{price_helpers,test_price_base,test_price_yfinance,test_price_alpaca,test_price_fallback,test_price_cache,test_price_ingest,test_price_adjustment,test_price_live}.py`; fixtures `tests/fixtures/prices/` (README, 4 yfinance CSVs, 4 Alpaca JSONs). Changed: `src/atlas/core/config.py` (`PriceDataConfig`), `config/fund.yaml` (`data.prices`), `pyproject.toml` + `uv.lock` (yfinance, httpx, respx; mypy override), `tests/unit/core/storage/conftest.py` (isolated registry for the storage `lake` fixture).
