# Stage 16 — Fundamental factor library · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** DONE (2026-11-04; see [verification.md](verification.md))
- **Gate check:** Stage 15 is DONE (its [verification.md](../stage-15-technical-features/verification.md) says **ALL CRITERIA VERIFIED**); `uv run pytest -q` → 2160 passed, 14 deselected, exit 0 (2026-11-04) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution:** written and executed in one go, inline TDD, no subagents (owner's words: "Plan and execute Stage 16 (Fundamental factor library) of Atlas Capital, following Project.md and CLAUDE.md exactly … (owner's standing preference: plan and execute in one go, no review pause)"). The stage `worklog.md` is the ledger; one stage commit (Rule 2A).

**Goal:** cross-sectional fundamental factor exposures (value, quality, growth, leverage, size, issuance) for a universe at any as-of, built only from data known at that as-of, cleaned (winsorised, z-scored, filled, sector-neutral, optionally size-neutral), combinable into composites, and cached in the lake for monthly rebalance dates. Consumers: Stage 17 (IC research), Stage 19 (risk-model style exposures, √mcap weights), Stage 22 (alpha composites), Stage 29 (fundamental analyst).

**Architecture:** `atlas.quant.factors`:
- `fundamental.py` — `FactorSpec`/`FACTORS` (17 raw factors, each with a group, unit and orientation), `market_data` (PIT price × split-adjusted PIT shares → market cap, EV), `raw_factors` (pure: panel + history + market → symbol × factor frame) and `factor_panel(view, symbols=None)` (reads `DataView`, cleans, returns a `FactorPanel`).
- `cleaning.py` — pure cross-sectional transforms: `winsorize`, `zscore`, `fill_missing` (sector median, flagged), `neutralize` (sector demeaning, or regression on sector dummies + size), `clean_factors` (the pipeline).
- `composite.py` — `composite(factors, weights)` and `style_composites(exposures)` (equal weights within each group).
- `store.py` — the `factor_exposures` lake table, `FactorStore` (write a panel, read the latest stored rebalance at an as-of with a look-ahead guard), `monthly_rebalance_dates`, `build_factor_cache`.

Data access: two new public `DataView` methods (Stage 13 code, recorded as an earlier-stage change): `fundamentals_history(symbols, concepts)` (every known fiscal period of each concept: discrete quarter value and TTM for flows, balances for stocks) and `split_factors(symbols, lookback)` (the split factor of each session, from the usable split actions known at `as_of`). Stage 09 gains `ttm_values` (the TTM at every period end; `ttm` = its latest) and `concept_history` (pure).

**Tech stack:** pandas 3 / numpy (no new dependency); `numpy.linalg.lstsq` for size neutralisation; Pydantic v2 config; the Stage 06 lake for the cache.

## Collisions with the overall plan
None with the roadmap (row 16, depends on 13; Phase 2 / M2) or the spec (§3 principles 2 (DataView only), 6 (no guess: NaN, never invented), 7; §11 numerics; §12 `quant/ factors`; §15 A9). Interpretations and deviations, also logged in `PROGRESS.md › Decisions log`:

1. **Net debt / EBITDA → net debt / EBIT (deviation).** EBITDA needs D&A, which is not in the Stage 09 concept map, and `fundamentals_facts` stores only mapped tags: adding D&A means re-ingesting every company (live EDGAR, the main lake is not writable from this sandbox). The factor `net_debt_ebitda` uses EBIT = TTM operating income as the earnings measure (NaN when EBIT ≤ 0); D&A in the concept map → Backlog.
2. **History through `DataView`.** Growth (YoY, 3y CAGR), margin stability (8 quarters) and 12-month issuance need past fiscal periods; `fundamentals_panel` gives only the latest. New `DataView.fundamentals_history` returns them from the facts known at `as_of` (so a restatement known at `as_of` is used; one filed later is not). Comparisons use the latest period end `e0` and the period end nearest `e0 − 365 d` / `e0 − 1095 d` within `quant.factors.period_tolerance_days` (52/53-week years).
3. **PIT shares.** Shares = the latest `shares_outstanding` instant known at `as_of` (cover-page count), restated in today's shares with the usable splits known at `as_of` that went ex after the instant (`DataView.split_factors`). Price = the raw close of the latest known bar (no older than `quant.factors.max_price_age_sessions`). Market cap = price × shares (USD); EV = market cap + total debt − cash (NaN when debt or cash is missing: no guess).
4. **Orientation.** Raw factors are oriented as the brief names them; accruals and net share issuance enter negated (the brief's "(negative)"), margin stability is −std of the quarterly gross margin (higher = more stable). Leverage and size keep their natural sign.
5. **Cleaning order** (Barra style): winsorise each factor at its cross-sectional 1st/99th percentiles → z-score (population std, ddof 0) → fill missing with the sector median of the z-scores (0 if the sector has none), flagged → neutralise (demean within GICS sector; with `size_neutral`, OLS residual on sector dummies + the size exposure, which keeps sector sums at 0) → z-score again. `size` itself is never size-neutralised. A factor with fewer than `quant.factors.min_names` valid raw values is left all NaN (not filled). Symbols with no sector are never grouped together (audit fix, 2026-11-04): they are filled with the whole column's median and neutralised against the whole cross-section; the final z-score is taken over the names with a known sector. Sectors are today's (Stage 08 Backlog: not point-in-time; inherited).
6. **Cache.** "`factor_exposures(date, symbol, factor, value)`" gets extra columns: `raw` (the raw value), `filled` (bool), `config_hash` (the factor settings that produced it) and `available_at` (the computing as-of's cutoff − 1 µs: every input was known strictly before the cutoff). Rebalance dates = the last session of each month. Reading the cache goes through `FactorStore.read(as_of)` (PIT-guarded); stored rows with another `config_hash` are not used.
7. **Files.** The brief lists `fundamental.py, cleaning.py, composite.py`; persistence goes in a fourth file `store.py` (task 4 "persistence").

## Design decisions

- **D1 Config** `cfg.quant.factors` (`FactorsConfig`): `universe` sp500, `winsor_lower` 0.01, `winsor_upper` 0.99 (0 ≤ lower < upper ≤ 1), `min_names` 20, `size_neutral` false, `period_tolerance_days` 20, `margin_quarters` 8 (≥ 2), `min_abs_eps` 0.01 USD, `max_price_age_sessions` 5. YAML block in `config/fund.yaml`, a comment per key.
- **D2 Stage 09** `ttm_values(periods, config)` → one row per period end (best candidate: annual, else four contiguous quarters); `ttm` = its latest row (unchanged behaviour, pinned by existing tests). `concept_history(known, concept_map, names, config)` → `HISTORY_COLUMNS` = `concept, period_start, period_end, value, ttm, available_at, source` (flows: union of discrete-quarter ends and TTM ends; stocks: every instant).
- **D3 `DataView.fundamentals_history(symbols, concepts)`** → `symbol` + `HISTORY_COLUMNS`, unblocked symbols in request order, sorted by symbol, concept, period_end; unknown concept → `ValueError`; per-symbol results reuse the cross-day fundamentals cache (key adds `"history"` and the concepts). **`DataView.split_factors(symbols, lookback)`** → index `date` (the `sessions(lookback)` grid), one column per unblocked symbol: product of `1/ratio` over usable splits with ex-date after the session (1.0 when none). Both added to the leak-suite tables.
- **D4 `FACTORS`** (name — group — definition; USD inputs, ratios as fractions):
  - value: `earnings_yield` = NI_ttm / mcap · `book_yield` = equity / mcap · `fcf_yield` = FCF_ttm / mcap · `ebit_ev` = EBIT_ttm / EV · `sales_ev` = revenue_ttm / EV (EV > 0, mcap > 0)
  - quality: `roe`, `roa` (Stage 09 ratios) · `gross_profitability` = GP_ttm / assets · `accruals` = −(NI_ttm − OCF_ttm) / assets · `margin_stability` = −std(ddof 1) of GP_q / revenue_q over the last `margin_quarters` discrete quarters (all present, spanning ≤ `margin_quarters` × 92 + tolerance days, latest not stale)
  - growth: `revenue_growth` = R(e0)/R(e−1y) − 1 (R(e−1y) > 0) · `eps_growth` = (E(e0) − E(e−1y)) / |E(e−1y)| (|E(e−1y)| ≥ `min_abs_eps`) · `revenue_cagr_3y` = (R(e0)/R(e−3y))^(1/3) − 1 (both > 0), R/E = TTM revenue / diluted EPS
  - leverage: `debt_to_equity` (Stage 09) · `net_debt_ebitda` = (debt − cash) / EBIT_ttm (EBIT > 0; interpretation 1)
  - size: `size` = ln(mcap)
  - issuance: `net_issuance` = −(S(now)/S(≈12 m earlier) − 1), split-adjusted
  Every value is float64, ±inf → NaN.
- **D5 `market_data(view, symbols)`** → index symbol: `price`, `price_date`, `shares`, `shares_date`, `market_cap`, `enterprise_value`. Pure core `compute_market(...)` for known-value tests.
- **D6 `FactorPanel`** (frozen dataclass): `as_of` (AsOf), `raw`, `exposures` (cleaned), `filled` (bool frame), `market` (D5 frame), `sectors` (Series), `config_hash`. `factor_panel(view, symbols=None, *, config=None)`; `symbols=None` → `view.universe(cfg.quant.factors.universe)`.
- **D7 `composite(factors, weights, *, standardize=True)`**: weights over known columns (unknown → `ValueError`; all-zero → `ValueError`); per row Σ wᵢxᵢ / Σ|wᵢ| over the factors present (NaN when none), then z-scored when `standardize`. `style_composites(exposures)` → one column per group (equal weights).
- **D8 Store.** `FACTOR_EXPOSURES` schema (`date, symbol, factor, value, raw, filled, config_hash, available_at`; key `date, symbol, factor`; partitioned by year of `date`). `FactorStore(lake, config)`: `write(panel)` replaces the day's rows; `read(as_of) -> FactorPanel-like StoredExposures | None` (latest stored date ≤ as_of.day with the current hash, `assert_no_lookahead`); `dates()`. `monthly_rebalance_dates(calendar, start, end)`; `build_factor_cache(lake, dates, config, *, symbols=None)` (one view advanced date by date).

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

Commands run as `UV_CACHE_DIR=$TMPDIR/uv-cache-s16 UV_NO_SYNC=1 uv run …` (sandbox). Factor tests live in `tests/unit/quant/factors/` with unique basenames (`test_factor_*.py`); the synthetic world is `tests/unit/quant/factors/factor_world.py`.

**As built.** `FactorPanel`, `factor_panel` and `factor_config_hash` live in `fundamental.py` (no fifth file); `FactorStore.write` refuses a panel computed with other settings (no `strict` switch); `ttm_values` takes an optional `quarters=` so the history computes each flow's quarters once; the world has 16 quarters (a 3-year-ago TTM needs them all). Test files: `test_fundamentals_history.py` (Task 2), `test_view_history.py` (3), `test_factor_market.py` (4), `test_factor_raw.py` (5), `test_factor_cleaning.py` (6), `test_factor_panel.py` + `test_factor_pit.py` (7), `test_factor_composite.py` (8), `test_factor_store.py` (9).

### Task 1 — `quant.factors` config
- Files: modify `src/atlas/core/config.py`, `config/fund.yaml` · test `tests/unit/core/test_config.py`
- [x] Failing tests `test_quant_factors_defaults`, `test_quant_factors_validation` (bounds of D1)
- [x] Run `uv run pytest tests/unit/core/test_config.py -q -k factors` → FAIL
- [x] Add `FactorsConfig`, `QuantConfig.factors`, YAML block
- [x] Run the config tests → PASS (every key in YAML and commented)

### Task 2 — Stage 09: `ttm_values`, `concept_history`
- Files: modify `src/atlas/data/fundamentals/{pit,__init__}.py` · test `tests/unit/data/fundamentals/test_fundamentals_history.py`
- [x] Failing tests: TTM at every end (annual preferred, else 4 quarters; gap → none); `ttm` equals the last row; flows give quarter + TTM per end; stocks every instant; restatement known at as-of used
- [x] Run → FAIL (no names) · implement · run → PASS; `tests/unit/data/fundamentals -q` → PASS

### Task 3 — `DataView.fundamentals_history`, `DataView.split_factors`
- Files: modify `src/atlas/data/view.py` · tests `tests/unit/data/test_view_pit.py`, `tests/unit/data/test_view_history.py`
- [x] Failing tests: history rows/columns/order, unknown concept, cache reuse; split factors exact around a split, 1.0 without splits, later split invisible; both in `METHODS`, `LEAKS`, `MULTI`
- [x] Run → FAIL · implement · run → PASS; `tests/unit/data -q` → PASS

### Task 4 — market cap + EV with PIT shares
- Files: create `src/atlas/quant/factors/{__init__,fundamental}.py` · test `tests/unit/quant/factors/test_factor_market.py`
- [x] Failing tests: known mcap/EV; split after the shares instant restates shares; stale price → NaN; missing debt/cash → EV NaN; no shares → NaN
- [x] Run → FAIL · implement `compute_market`, `market_data` · run → PASS

### Task 5 — raw factors, known values
- Files: `fundamental.py` · test `tests/unit/quant/factors/test_factor_raw.py`
- [x] Failing tests: one known-value test per factor (17), denominators ≤ 0 → NaN, stale/missing history → NaN, tolerance window for year-ago ends, orientation signs, specs (names, groups, units)
- [x] Run → FAIL · implement `FactorSpec`, `FACTORS`, `raw_factors` · run → PASS

### Task 6 — cleaning pipeline
- Files: create `src/atlas/quant/factors/cleaning.py` · test `tests/unit/quant/factors/test_factor_cleaning.py`
- [x] Failing tests: winsor bounds (values within the 1/99 percentiles, order kept), z mean≈0 / std≈1, fill = sector median and flagged, sector-demeaned sums ≈ 0, size-neutral residual ⟂ size and sector sums 0, `min_names`, constant column, hypothesis property test of the invariants
- [x] Run → FAIL · implement · run → PASS

### Task 7 — `factor_panel` end to end + point-in-time
- Files: `fundamental.py`, `factor_world.py` · tests `tests/unit/quant/factors/test_factor_panel.py`, `test_factor_pit.py`
- [x] Failing tests: panel on the synthetic world (raw values match the pure function; invariants hold); blocked symbol absent; **filings after the as-of change nothing** (late 10-Q, late 10-K/A restatement, later split, later bars), **and do change the panel once known**; a filing accepted one second before the cutoff is used, one second after is not; truncation of the lake at the as-of gives an identical panel
- [x] Run → FAIL · implement · run → PASS

### Task 8 — composites
- Files: create `src/atlas/quant/factors/composite.py` · test `tests/unit/quant/factors/test_factor_composite.py`
- [x] Failing tests: weighted sum known values, missing factor renormalised, all missing → NaN, unknown name / zero weights → `ValueError`, standardised output, `style_composites` columns
- [x] Run → FAIL · implement · run → PASS

### Task 9 — persistence
- Files: create `src/atlas/quant/factors/store.py` · test `tests/unit/quant/factors/test_factor_store.py`
- [x] Failing tests: round trip (values, raw, filled), rewrite replaces the day, read picks the latest date ≤ as-of, ignores another config hash, look-ahead guard raises on a row known after the cutoff, monthly rebalance dates, `build_factor_cache` writes each date
- [x] Run → FAIL · implement · run → PASS

### Task 10 — real-lake smoke run (read only)
- [x] `factor_panel` at two month-ends on the main checkout's lake (`ATLAS__DATA__LAKE_PATH`); coverage per factor and timing → `artifacts/real-lake-smoke.txt` (evidence, not a gate)

### Task 11 — verification and records
- [x] Brief Verify `uv run pytest tests/unit/quant/factors -q`; `uv run pytest -q`; ruff check/format; mypy; `make check`; coverage of `atlas.quant.factors`; mutation checks → `artifacts/`
- [x] `verification.md`, `PROGRESS.md` (status, decisions, backlog, handoff), commit `stage-16: Fundamental factor library`, merge into `main`, merged-main checks, push
