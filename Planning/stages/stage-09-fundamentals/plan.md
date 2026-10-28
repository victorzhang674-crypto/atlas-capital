# Stage 09 — Point-in-time fundamentals (SEC EDGAR) · Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (inline TDD). Steps use checkbox (`- [ ]`) syntax; tick only after the step's tests have run and passed in this chat.

- **Brief:** [brief.md](brief.md) (`Planning/stages/stage-09-fundamentals/brief.md`)
- **Status:** DONE (2026-10-28; see [verification.md](verification.md))
- **Gate check:** Stage 08 is DONE (its [verification.md](../stage-08-universe/verification.md) says ALL CRITERIA VERIFIED; evidence block also in `PROGRESS.md`); `uv run pytest -q` → 799 passed, 3 deselected, exit 0 (2026-10-28) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution method:** executing-plans (native, inline TDD). The owner asked in chat (2026-10-28) to "firstly generate the plans for Stage 9 then carefully implement the plans and complete the plans", then audit the stage against the plan, so the plan is executed straight after it is written (as in Stages 06–08). Open design choices take the option recorded below and are logged in the Decisions log.

## Context

Stage 08 is `DONE` (8/66): `instruments` and `index_membership` say which names were investable; `prices_daily` holds raw bars. Stage 09 adds what each company had **reported, as filed**. Later consumers checked: Stage 12 (earnings dates from EDGAR submissions), 13 (`DataView.fundamentals(symbol)` / `fundamentals_panel(symbols)` and "fundamentals jumps > 10× q/q" quality check wrap this stage), 14 (sync CLI: `--datasets fundamentals`; coverage ≥ 90% of current members), 16 (value/quality/growth/leverage/size factors from `fundamentals_panel` + prices), 29 (last 8 quarters, TTM, margins, growth; every number cited).

**Goal:** standardised quarterly/annual fundamentals as filed, keyed by the SEC acceptance timestamp, so a backtest only sees what was public at the time.

**Architecture:** package `atlas.data.fundamentals`. `edgar_client` talks to EDGAR (ticker→CIK map, companyfacts, submissions, rate limit, retries, raw JSON cache in `raw/sec/`). `parser` turns companyfacts JSON into the long table `fundamentals_facts` and submissions JSON into a filing index (accession → acceptance time), and sets `available_at`. `mapping` loads `config/xbrl_map.yaml` and resolves a concept per period with tag fallbacks and sums of parts. `pit` filters facts to what was known at `as_of` (latest filing per period wins, so restatements are point-in-time), builds discrete quarters (reported, YTD differences, Q4 = FY − Q1..Q3), TTM for flows, latest balance for stocks, derived ratios, and the wide view. `ingest` wires the client, parser and lake together per symbol.

**Tech stack:** httpx 0.28 + respx (client), pandas 3 (vectorised period arithmetic), Stage 06 `Lake`, Stage 05 `TradingCalendar` / `AsOf` / `pit_filter` / `assert_no_lookahead` / `to_available_at`, Stage 07 `RetryPolicy` / `call_with_retry` / `Throttle`.

**Spec:** brief above; design spec §3 principle 2 (point-in-time everywhere: data timestamped after `as_of` is never used) and principle 6 (fail safe: a missing input gives no value, never a guess); §11 (fundamentals = SEC EDGAR companyfacts, XBRL, keyed by filing date; free sources, A3); `atlas.core.pit.AVAILABILITY_RULES["sec_filing"]` (Stage 05: the filing's EDGAR acceptance timestamp); stage protocol §4–§5; Stage 08 handoff (symbols are identities = latest ticker; never log `run_id`/`mode`/`as_of`/`stage`).

## Source probe (2026-10-28, recorded before planning)

- `data.sec.gov` answers 200 to the descriptive User-Agent `AtlasCapital/0.1 (...)` (companyfacts, submissions, paged submissions files); `www.sec.gov/files/company_tickers.json` answers **403** to it (SEC wants contact details; `SEC_USER_AGENT` is not set here, `.env` absent). The fixture `company_tickers.json` is therefore built in the documented shape from the real submissions `cik`/`tickers`/`name` (README states it); the live test needs `SEC_USER_AGENT`.
- `acceptanceDateTime` in the submissions API ends in `Z` and **is UTC**: for 10-K/10-Q/8-K filings, EDGAR's filing-date rule (accepted after 17:30 ET → next business day) holds for 252 of 254 filings when read as UTC and for 58 when read as Eastern (the 2 exceptions are 8-Ks, which may be filed until 22:00) · raw: [artifacts/acceptance-utc-check.txt](artifacts/acceptance-utc-check.txt). Example: AAPL 10-K FY2023 accepted `2023-11-02T22:08:27Z` (18:08 EDT), filing date 2023-11-03.
- companyfacts: per tag and unit a list of facts `{start?, end, val, accn, fy, fp, form, filed, frame?}`; instants have no `start`; a 10-Q carries both the 3-month and the year-to-date durations; cash-flow lines are year-to-date only in 10-Qs; no duplicate `(start, end, accn)` in the mapped tags of AAPL/MSFT/JPM.
- Real restatements in the fixtures: JPM Q1 2012 revenue 26,712 M (10-Q, 2012-05-10) → 26,052 M (10-Q/A, filed 2012-08-09); MSFT FY2017 quarters recast for ASC 606 (Q1 FY17 revenue 20,453 M → 21,928 M, filed 2017-10-26).
- JPM has no `GrossProfit`, `OperatingIncomeLoss`, capex or R&D tags (→ NaN); its debt is `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities` + `ShortTermBorrowings`; its submissions `recent` holds only the last year (structured-note filings), so older 10-K/10-Q acceptance times are in the paged history files.

## Global constraints

- Symbols are Atlas identities (latest ticker, `normalize_symbol`: `BRK.B`). The CIK comes from `company_tickers.json` (current tickers only); a symbol it does not list fails that symbol ("no CIK"), never the batch.
- Point in time: a fact is known at `as_of` iff `available_at < as_of.cutoff` (`pit_filter`). `available_at` = EDGAR acceptance time (UTC) of the fact's filing; if the filing is not in the submissions index, `session_open(next_session(filed))` (the open of the first session after the filing date: brief's "filed date + 1 session", conservative). For each `(tag, unit, period)` the **latest known filing** wins, so an earlier `as_of` sees the original value of a later restatement.
- Units: USD for money lines, USD per share for EPS, shares for share counts; ratios are fractions (0.25 = 25%). Flows are summed over fiscal quarters; TTM = last four fiscal quarters (or the fiscal year when it ends at that date).
- Thresholds and URLs live in `config/fund.yaml › data.fundamentals` (`FundamentalsConfig`); the concept map in `config/xbrl_map.yaml` (path from `data.fundamentals.xbrl_map_file`), read by `load_concept_map`, not by `load_config` (reference facts, like Stage 08's ticker changes).
- No network in unit tests: recorded fixtures served by respx; one `@pytest.mark.live` test (skipped when `SEC_USER_AGENT` is unset). The User-Agent is a secret (`SEC_USER_AGENT`) and is never logged or printed.
- Tests live in `tests/unit/data/fundamentals/` (the brief's Verify target) with unique basenames `test_fundamentals_*.py` and helpers in `fundamentals_helpers.py`; config tests extend `tests/unit/core/test_config.py`.
- **One commit for the stage:** `stage-09: Point-in-time fundamentals (SEC EDGAR)` (+ attribution trailer), after DONE; then merge into `main` and re-verify (Rule 6A).

## Collisions with the overall plan

None with the roadmap or the design spec. Points checked:
- Spec §11 says "keyed by filing date"; the brief and Stage 05's `AVAILABILITY_RULES` say the acceptance timestamp. Acceptance time is the finer point-in-time key (it is on the filing date or, after 17:30 ET, the evening before); filed date + 1 session is the fallback. Same source, finer key: an interpretation, not a collision.
- Brief "fundamentals_pit(symbol, as_of) -> Series": a lake is needed, so the call is `Fundamentals(lake, cfg).pit(symbol, as_of)` (like Stage 08's `Universe(lake, cfg).members(as_of)`); the frame-level function `fundamentals_pit(facts, as_of, concept_map, config)` keeps the brief's name for already-loaded facts.
- Brief Files list has four modules; ingestion (client → parser → lake) gets a fifth, `ingest.py`, so no module mixes HTTP and table writing. Brief task order is kept; ingest is plan Task 7, docs/acceptance Task 8.

## Design decisions (also logged in PROGRESS.md › Decisions log)

| # | Decision | Why |
|---|---|---|
| D1 | `fundamentals_facts(symbol, cik, taxonomy, tag, unit, value, period_start?, period_end, duration_days, fy?, fp?, form, filed, accn, accepted_at?, available_at)`, PK `(symbol, taxonomy, tag, unit, period_end, duration_days, accn)`, partitioned by `symbol`. `duration_days` = 0 for instants (no `period_start`), else `(end − start).days + 1`. `value` float64. Only tags named in the concept map are stored; the raw JSON keeps everything. Re-ingest = upsert (idempotent). | Brief column list + `taxonomy` (us-gaap vs dei) + `duration_days` (PK columns cannot be null; also tells quarter from year-to-date). One partition per symbol keeps a symbol's read small. |
| D2 | `available_at` = `to_available_at(acceptanceDateTime)` (UTC, verified) when the accession is in the filing index; else `session_open(next_session(filed))`. `accepted_at` keeps the acceptance time (null on fallback). | Brief; Stage 05 rule; fail safe (fallback never earlier than reality). |
| D3 | `EdgarClient(config, *, user_agent, lake=None, http=None, sleep=time.sleep)`: `company_tickers()`, `companyfacts(cik)`, `submissions(cik)`, `submissions_page(name)`, `filing_index(cik, needed=None)`. One `Throttle` at `1 / max_requests_per_second` (10 req/s); retries (`call_with_retry`, Stage 07) on transport errors, 429 and 5xx; other non-200 → `DataError` at once; bad JSON → `DataError`. Raw JSON cached under `raw/sec/<dataset>/` (atomic write); `refresh=False` reads the cache when present; history pages are immutable and always cached. `build_edgar_client(cfg, lake)` takes the User-Agent from `cfg.secrets.sec_user_agent` (`ConfigError` naming `SEC_USER_AGENT` if unset). `call_with_retry` gains an `event` keyword (log event name; default unchanged). | Brief task 1; SEC fair-access policy (10 req/s, declared User-Agent); secrets never logged. |
| D4 | `CikMap`: `from_company_tickers(payload)`; tickers normalised (`BRK-B` → `BRK.B`); unparseable tickers skipped; a ticker listed for two CIKs keeps the first (warning). `cik(symbol) -> int | None`. | Brief task 1; Stage 08 identities are current tickers. |
| D5 | `filing_index(cik, needed: Mapping[accn, filed date] | None)`: `recent` plus only the history pages whose `[filingFrom, filingTo]` covers the filing date of a needed accession missing from `recent` (all pages when `None`); columns `accn, form, filed, accepted_at`. *(Signature changed in Task 2 before the code existed: it was `filed_dates`, which would also read pages for filings already in `recent`.)* | JPM has 71 history pages; only ~45 hold its periodic reports. |
| D6 | Concept map grammar (`config/xbrl_map.yaml`): per concept `kind` (`flow`/`stock`), `unit`, and exactly one of `tags` (fallback list) or `sum_of` (parts, each a fallback list). An alternative is a tag (`Name` = us-gaap, `dei:Name`) or a strict sum `A + B` (present only when every term is). Per period the first alternative with a value wins; `sum_of` adds the parts present (missing part = 0; absent if none). Validated by Pydantic in `load_concept_map(path)` (`ConfigError` naming the file and entry); the ratio inputs must be defined. 15 concepts from the brief. | Brief task 3 ("fallbacks across tags", "TotalDebt (sum of parts)"); companies switch tags over time (AAPL `SalesRevenueNet` → `RevenueFromContract…` in 2018). |
| D7 | Quarters (`quarterly_values`): durations are classed by `quarter_days` 80–100 and `annual_days` 350–380 (52/53-week years). Discrete quarters = reported quarters, then differences of consecutive cumulative periods sharing a start date when the gap is a quarter (H1 − Q1, 9M − H1, FY − 9M), then Q4 = FY − (Q1 + Q2 + Q3) when three quarters tile the fiscal year's first nine months and Q4 is still missing. Reported beats derived; a derived row carries the latest `available_at` of its inputs and `source="derived"`. | Brief task 4; cash-flow lines are YTD-only in 10-Qs. |
| D8 | TTM (flows): at the latest period end `E` where it can be built: the annual period ending at `E`, else four contiguous discrete quarters ending at `E`. Latest balance (stocks): the latest instant. A metric whose period ended more than `max_staleness_days` (400) before `as_of.day` is NaN. | Brief task 4; fail safe (no stale guess). |
| D9 | Wide view: `Fundamentals(lake, config=None, *, concept_map=None)` *(Task 7: no `calendar` parameter; a plain date's cutoff comes from `config.calendar`)* with `pit(symbol, as_of: date | AsOf) -> pd.Series` (float, name = symbol; index `<flow>_ttm`, `<stock>`, then `gross_margin, operating_margin, roe, roa, debt_to_equity, fcf_ttm`; NaN when unavailable) and `pit_detail(...) -> DataFrame` (`metric, value, period_start, period_end, available_at, accn, tag, source`). Ratios: margins over revenue TTM, ROE = net income TTM / latest equity, ROA = net income TTM / latest assets, debt/equity = total debt / equity, FCF = OCF TTM − capex TTM; a denominator ≤ 0 or NaN → NaN. A missing table → `DataError`; a symbol without rows → all NaN. `assert_no_lookahead` guards the frame actually used. | Brief task 6; fail safe. |
| D10 | Config `data.fundamentals` (`FundamentalsConfig`): `company_tickers_url`, `companyfacts_url`, `submissions_url`, `submissions_page_url` (templates with `{cik:010d}` / `{name}`), `max_requests_per_second` 10, `request_timeout_seconds` 30, `max_retries` 3, `backoff_seconds` 1.0, `xbrl_map_file` `config/xbrl_map.yaml`, `quarter_days` [80, 100], `annual_days` [350, 380], `max_staleness_days` 400. | Nothing hard-coded. |
| D11 | `ingest_fundamentals(lake, client, symbols, *, cik_map, config=None, calendar=None, concept_map=None, refresh=True) -> FundamentalsIngestReport(rows, symbols_ok, symbols_failed, failure_reasons, fallback_rows)`; per symbol: CIK → companyfacts → parse → filing index → `available_at` → upsert. A failing symbol never aborts the batch. | Mirrors Stage 07 `ingest_prices`. |

## File structure

| File | Responsibility |
|---|---|
| `src/atlas/core/config.py` (modify) | `FundamentalsConfig`, `DataConfig.fundamentals` |
| `config/fund.yaml` (modify) | `data.fundamentals` with comments |
| `config/xbrl_map.yaml` (create) | concept map |
| `src/atlas/data/providers/base.py` (modify) | `call_with_retry(..., event=...)` |
| `src/atlas/data/fundamentals/__init__.py` (create) | public exports; importing registers `fundamentals_facts` |
| `src/atlas/data/fundamentals/edgar_client.py` (create) | `EdgarClient`, `CikMap`, `build_edgar_client` |
| `src/atlas/data/fundamentals/parser.py` (create) | `FUNDAMENTALS_FACTS`, `parse_companyfacts`, `parse_submissions`, `attach_available_at` |
| `src/atlas/data/fundamentals/mapping.py` (create) | `Concept`, `ConceptMap`, `load_concept_map`, `resolve_concept` *(also built, audit: `latest_filings` (used by `known_facts`), `parse_alternative`, `RATIO_INPUTS`, `PERIOD_KEY`/`PERIOD_COLUMNS`)* |
| `src/atlas/data/fundamentals/pit.py` (create) | `known_facts`, `quarterly_values`, `ttm`, `latest_balance`, `fundamentals_pit_detail`, `fundamentals_pit`, `Fundamentals` *(also built, audit: `MetricValue`, `compute_ratios`, `metric_names`, `RATIO_NAMES`, `DETAIL_COLUMNS`, `QUARTER_COLUMNS`, `Fundamentals.facts`)* |
| `src/atlas/data/fundamentals/ingest.py` (create) | `FundamentalsIngestReport`, `ingest_fundamentals` |
| `tests/fixtures/fundamentals/` (create) | trimmed real companyfacts/submissions (AAPL, MSFT, JPM), built `company_tickers.json`, `README.md` |
| `tests/unit/data/fundamentals/fundamentals_helpers.py`, `test_fundamentals_{client,parser,mapping,quarters,pit,ingest,live}.py` (create) | helpers and all Stage 09 tests *(Task 8 added `test_fundamentals_guards.py`: error paths, 100% line coverage)* |
| `docs/data-limitations.md` (modify) | fundamentals limits |
| `Planning/stages/stage-09-fundamentals/artifacts/record_sec_fixtures.py` | fixture recorder |

## Review Focus

1. Acceptance after 17:30 ET (filed the next day) → known from the acceptance instant, not the filing date. Test: `test_available_at_uses_acceptance_time` (AAPL 10-K FY2023: `as_of` 2023-11-02 does not see it, 2023-11-03 does).
2. A restated value filed later → an earlier `as_of` still sees the original. Tests: `test_restated_value_not_seen_before_its_filing`, `test_jpm_q1_2012_restatement`.
3. A bank without gross profit/capex → NaN, never an error. Test: `test_jpm_missing_concepts_are_nan`.
4. YTD-only cash-flow lines and FY-only Q4 → correct discrete quarters. Tests: `test_quarters_from_ytd_chain`, `test_q4_from_fy_minus_three_quarters`, `test_aapl_q4_fy2023_revenue_derived`.
5. Future facts in the store change nothing; a bypassed filter is caught. Tests: `test_future_facts_do_not_change_result`, `test_guard_raises_if_filter_bypassed`.

---

### Task 1: EDGAR client, CIK map, rate limit (brief task 1)

**Files:** create `edgar_client.py`, `__init__.py`, `fundamentals_helpers.py`, `test_fundamentals_client.py`, fixture `company_tickers.json`; modify `config.py`, `fund.yaml`, `test_config.py`, `providers/base.py`.

**Interfaces — Produces:** `FundamentalsConfig` at `cfg.data.fundamentals` (D10); `CikMap` (D4); `EdgarClient` (D3, except `filing_index`, Task 2); `build_edgar_client(cfg, lake) -> EdgarClient`.

- [x] Write failing tests: `test_fundamentals_defaults` (config; repo YAML equals defaults), `test_cik_map_from_company_tickers` (AAPL → 320193; `BRK.B` and `brk-a` → 1067983; GOOG and GOOGL → 1652044; META → 1326801; unknown → None), `test_cik_map_duplicate_ticker_keeps_first`, `test_cik_map_skips_bad_tickers`, `test_client_sends_user_agent_and_caches_raw` (respx: one request; second call served from `raw/sec/companyfacts/CIK0000320193.json`; `refresh=True` requests again), `test_client_throttles_requests` (fake sleep: 0.1 s before every request but the first), `test_client_retries_server_errors` (503, 429, then 200), `test_client_gives_up_after_retries` (`DataError`), `test_client_404_is_not_retried`, `test_client_bad_json_raises`, `test_build_edgar_client_requires_user_agent` (`ConfigError` names `SEC_USER_AGENT`), `test_user_agent_is_not_logged`.
- [x] Run `uv run pytest tests/unit/data/fundamentals tests/unit/core/test_config.py -q` → FAIL (module missing).
- [x] Implement.
- [x] Run → PASS (and `tests/unit/data` for the `call_with_retry` change).

### Task 2: Fact parser, filing index, `available_at` (brief task 2 + acceptance timestamps)

**Files:** create `parser.py`, `mapping.py` (loader only), `test_fundamentals_parser.py`, fixtures (recorded by `artifacts/record_sec_fixtures.py`) and `tests/fixtures/fundamentals/README.md`; modify `edgar_client.py`.

**Interfaces — Produces:** `FUNDAMENTALS_FACTS` (D1, registered); `parse_companyfacts(payload, *, symbol, tags) -> DataFrame` (D1 columns without `accepted_at`/`available_at`; exact duplicates dropped, conflicting duplicates keep the first, warning; malformed → `DataError`); `parse_submissions(table: Mapping) -> DataFrame` (`accn, form, filed, accepted_at`); `EdgarClient.filing_index(cik, needed=None, *, refresh=False)` (D5); `attach_available_at(facts, index, calendar) -> DataFrame` (D2); `load_concept_map(path) -> ConceptMap` with `ConceptMap.tags() -> frozenset[tuple[str, str]]`.

- [x] Write failing tests: `test_load_concept_map_repo_file` (15 concepts; `revenue` flow USD; `total_debt` sum of 2 parts; `dei` shares), `test_load_concept_map_rejects_bad_file` (unknown kind, both `tags` and `sum_of`, bad tag, missing ratio input → `ConfigError`), `test_parse_companyfacts_aapl_fy2023_revenue` (tag `RevenueFromContractWithCustomerExcludingAssessedTax`, 2022-09-25 → 2023-09-30, 371 days, 383,285,000,000, 10-K, FY 2023, filed 2023-11-03, accn 0000320193-23-000106), `test_parse_companyfacts_instants_and_dei`, `test_parse_companyfacts_keeps_only_mapped_tags`, `test_parse_companyfacts_duplicates`, `test_parse_companyfacts_malformed_raises`, `test_parse_submissions_acceptance_is_utc`, `test_filing_index_fetches_only_needed_pages` (JPM: requests recent + only covering pages), `test_filing_index_reads_no_page_when_recent_has_the_filing`, `test_every_fixture_fact_has_an_acceptance_time`, `test_available_at_uses_acceptance_time`, `test_available_at_fallback_is_next_session_open` (filed Friday 2023-11-03 → 2023-11-06 14:30 UTC), `test_facts_frame_validates_against_schema`.
- [x] Run → FAIL.
- [x] Implement.
- [x] Run → PASS.

### Task 3: Concept mapping with fallbacks (brief task 3)

**Files:** modify `mapping.py`; create `test_fundamentals_mapping.py`.

**Interfaces — Produces:** `resolve_concept(facts, concept) -> DataFrame` (`period_start, period_end, duration_days, value, available_at, accn, tag`), one row per period, on facts already reduced to the latest known filing per period.

- [x] Write failing tests: `test_resolve_fallback_per_period`, `test_resolve_strict_sum_alternative`, `test_resolve_sum_of_parts_missing_part_is_zero`, `test_resolve_sum_of_parts_all_missing_is_empty`, `test_resolve_ignores_other_units` *(named `test_resolve_ignores_other_units_and_kinds`)*, `test_aapl_total_debt_fy2023` (105,103 M + 5,985 M = 111,088 M at 2023-09-30), `test_aapl_revenue_switches_tag_over_time` (FY2017 from `SalesRevenueNet`, FY2023 from `RevenueFromContract…`), `test_jpm_has_no_gross_profit` (empty frame, no error).
- [x] Run → FAIL. Implement. Run → PASS.

### Task 4: Discrete quarters, Q4 derivation, TTM, latest balance (brief task 4)

**Files:** create `pit.py` (quarters/TTM part), `test_fundamentals_quarters.py`.

**Interfaces — Produces:** `quarterly_values(periods, config) -> DataFrame` (`period_start, period_end, value, available_at, accn, source`); `ttm(periods, config) -> MetricValue | None`; `latest_balance(periods) -> MetricValue | None` (`MetricValue` frozen dataclass: `value, period_start, period_end, available_at, accn, tag, source`).

- [x] Write failing tests (synthetic): `test_quarters_reported`, `test_quarters_from_ytd_chain` (Q1, H1, 9M, FY → four quarters), `test_q4_from_fy_minus_three_quarters`, `test_reported_quarter_beats_derived`, `test_ttm_uses_annual_at_fiscal_year_end`, `test_ttm_sums_four_contiguous_quarters`, `test_ttm_skips_end_with_gap`, `test_latest_balance`; fixture: `test_aapl_q4_fy2023_revenue_derived` (383,285 − 117,154 − 94,836 − 81,797 = 89,498 M), `test_aapl_operating_cash_flow_quarters_from_ytd`.
- [x] Run → FAIL. Implement. Run → PASS.

### Task 5: Point-in-time query with restatements (brief task 5)

**Files:** modify `pit.py`; create `test_fundamentals_pit.py`.

**Interfaces — Produces:** `known_facts(facts, as_of) -> DataFrame` (D8 PIT rule; `assert_no_lookahead` guard).

- [x] Write failing tests: `test_known_facts_latest_filing_per_period`, `test_restated_value_not_seen_before_its_filing` (synthetic: value 100 filed 2020-02-01, restated 90 filed 2021-02-01: as_of 2020-06-01 → 100; 2021-06-01 → 90), `test_jpm_q1_2012_restatement` (as_of 2012-08-01 → 26,712 M; 2012-08-13 → 26,052 M), `test_msft_asc606_recast` (Q1 FY17 revenue 20,453 M as of 2017-06-01; 21,928 M as of 2018-06-01).
- [x] Run → FAIL. Implement. Run → PASS.

### Task 6: Wide PIT view, derived ratios, look-ahead (brief task 6)

**Files:** modify `pit.py`, `test_fundamentals_pit.py`.

**Interfaces — Produces:** `fundamentals_pit_detail(facts, as_of, concept_map, config)`, `fundamentals_pit(facts, as_of, concept_map, config) -> pd.Series`, `Fundamentals` (D9).

- [x] Write failing tests: `test_fundamentals_pit_aapl_fy2023` *(named `test_acceptance_fundamentals_pit_aapl_fy2023` so `-k acceptance` selects it; plus `test_acceptance_detail_cites_the_10k`)* (acceptance: as_of 2023-11-03: revenue_ttm 383,285 M, gross_profit_ttm 169,148 M, operating_income_ttm 114,301 M, net_income_ttm 96,995 M, eps_diluted_ttm 6.13, total_assets 352,583 M, total_liabilities 290,437 M, stockholders_equity 62,146 M, cash 29,965 M, total_debt 111,088 M, operating_cash_flow_ttm 110,543 M, capex_ttm 10,959 M, rnd_ttm 29,915 M, sbc_ttm 10,833 M; ratios from those), `test_fundamentals_pit_before_10k_uses_previous_ttm` (as_of 2023-11-02: revenue TTM to 2023-07-01 = 383,933 M), `test_ratios_known_values` (synthetic), `test_ratio_with_nonpositive_denominator_is_nan` *(named `test_ratio_with_missing_or_nonpositive_denominator_is_nan`: also a missing input)*, `test_jpm_missing_concepts_are_nan`, `test_stale_metric_is_nan`, `test_future_facts_do_not_change_result`, `test_guard_raises_if_filter_bypassed` (`LookAheadError`).
- [x] Run → FAIL. Implement. Run → PASS.

### Task 7: Ingestion into the lake

**Files:** create `ingest.py`, `test_fundamentals_ingest.py`, `test_fundamentals_live.py`.

**Interfaces — Produces:** D11; `Fundamentals` reads `fundamentals_facts`.

- [x] Write failing tests: `test_ingest_fixtures_writes_facts` (three symbols; every row has `available_at`; AAPL's 10-K FY2023 rows carry its acceptance time), `test_ingest_is_idempotent`, `test_failed_symbol_never_aborts_batch` (no CIK; companyfacts 404), `test_fundamentals_reads_lake` (`Fundamentals(lake).pit("AAPL", date(2023, 11, 3))` equals the frame-level result), `test_fundamentals_missing_table_raises`, `test_unknown_symbol_is_all_nan`, `test_live_edgar_aapl` (`@pytest.mark.live`, skipped without `SEC_USER_AGENT`).
- [x] Run → FAIL. Implement. Run → PASS.

### Task 8: Documentation and acceptance

**Files:** modify `docs/data-limitations.md`, test file of Task 6.

- [x] `test_data_limitations_doc_covers_fundamentals` (mentions "companyfacts", "acceptance", "restat", "CIK").
- [x] Write the fundamentals section (as-filed values, acceptance vs fallback, tag coverage and NaN concepts, derived Q4/EPS approximation, delisted names without a CIK, non-dimensional facts only).
- [x] Full verification (Rule 1): `uv run pytest -q`, `ruff check`, `ruff format --check`, `mypy src/atlas`, brief Verify, `make check`, `-k acceptance -W error`, docstrings; outputs to `artifacts/`; write `verification.md`, `PROGRESS.md`; commit; merge into `main` (Rule 6A) and re-verify; audit plan vs work. *(Done: verification, records, audit, commit `f0d1acb`. Then the owner fast-forwarded `main` to `f0d1acb` (the sandbox cannot write the main checkout) and the merged-main checks passed: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt).)*
