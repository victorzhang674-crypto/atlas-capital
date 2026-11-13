# Phase 2 checkpoint (Stages 15–23, M2): report — 2026-11-11

Scope: are Stages 15–23 complete against their briefs, what gaps remain, and what is worth
improving before Phases 3–5 build on the quant engine. Kept in the Stage 23 folder because
Stage 23 completes M2. Fixes are recorded in the folder of the stage whose code they change
(Rule 2A), as in the Phase 0 and Phase 1 checkpoints.

Base: branch `claude/phase-two-checkpoint-stages-a4dbeb` at `f0b4d01` (= `main` = `origin/main`;
Stage 24 is `DONE`, Stage 25 `NOT STARTED`).

## 1. Checks re-run in this chat

| Check | Result |
|---|---|
| `make check` (run 2) | exit 0: `ruff check .` All checks passed · `ruff format --check .` 374 files already formatted · `mypy src/atlas` no issues in 154 source files · `pytest -q` **4037 passed**, 0 failed, 31 deselected, 6 min 46 s |
| `make check` (run 1) | **1 failed**, 4036 passed: `test_every_source_dir_has_init`. Cause: an empty `src/atlas/.claude/.cc-writes` folder left by the assistant's own shell during this checkpoint, not project state. Removed; run 2 is the result that counts. Kept as `…-make-check-run1-failed.txt` (Rule 1.6) |
| The eight Verify commands of Stages 15–23 (`uv run pytest tests/unit/quant/<pkg> -q`) | all exit 0: features 339 · factors 115 · research 63 · risk_model 193 (Stages 18 and 19) · metrics 238 · regime 674 · alpha 68 · statarb 126 (1,816 tests, 378 s of the suite's 407 s) |
| `uv run pytest -m integration -v -rs` on the main lake (read only) | **15 passed, 1 skipped** in 13 min 54 s: metrics 4, phase 0 run 1, regime 6 (March 2020 crisis, 2022 bear market), risk model 4; skipped: the flag-gated 2018–2024 calibration (`ATLAS_RISK_MODEL_FULL=1`, ~2 h) |
| Main lake contents (read only) | `regime_history` 2,724 rows; **no `factor_exposures`, `factor_returns` or `risk_model_snapshots` table**; `instruments` 890 rows, 503 with a sector |
| Profile of `quality_report` on the main lake (two as-of days) | 96.8 s (2020-03-16) and 70.7 s (2024-06-03); **97% inside `check_fundamentals_jumps`** |

Raw output: `phase2-checkpoint-2026-11-11-*.txt` in this folder; the commands are in
`phase2-checkpoint-2026-11-11-checks.sh`.

## 2. Stage by stage

Every stage is `DONE` with `ALL CRITERIA VERIFIED` in its `verification.md`, evidence in
`PROGRESS.md`, and no waivers. Every test named as acceptance evidence still exists and passed
today. No `skip` / `xfail` marker hides a criterion (the only skips are missing keys or a
missing lake).

| Stage | Acceptance criteria | Still holds | Open in records |
|---|---|---|---|
| 15 Technical features | reference values within 1e-6; no look-ahead (shift test) | yes (339 passed; 500 × 2,000 in 2.4 s) | no `xs` feature registered; no feature cache; recursive features depend on the buffer |
| 16 Fundamental factors | panels use only filings known at the date; cleaning invariants | yes (115 passed) | **the lake cache `factor_exposures` was never built on the main lake and no command builds it**; net debt / EBIT instead of EBITDA (no D&A concept); 357 former members without a sector; 41 names without a market cap |
| 17 Signal research | planted IC within ±0.01; CLI tear sheet for a real factor | yes (63 passed) | audit items D–H noted, not fixed; a fundamental factor costs one fresh `factor_panel` per date (~3 h for 72 dates); CLI has no `--regimes` although Stage 21's history now exists |
| 18 Covariance & vol | outputs PSD; synthetic truth recovered | yes (part of 193) | no `DataView` helper; one-step GARCH only |
| 19 Factor risk model | synthetic recovery and bias; decomposition adds up; build < 2 s | yes (part of 193; real-lake integration 4 passed) | **`factor_returns` and `risk_model_snapshots` were never stored on the main lake and no command builds them**; real-data coverage ~387 of ~503 members a day; evidence run used quarterly anchors |
| 20 Risk metrics | documented with units; tested; single source of truth | yes (238 passed; real lake 4 passed) | fat-tail VaR variants deferred to Stages 44 / 46 |
| 21 Regime detection | PIT-safe; stable labels; 2020 and 2022 flagged | yes (674 passed; real lake 6 passed) | risk-scalar rule comparison assigned to Stage 43 (owner decision); the Backlog item on the slow quality report names this checkpoint |
| 22 Systematic alpha | calibrated expected returns; in-sample composite IC positive with a t-stat | yes (68 passed); the IC criterion holds only through the owner-authorised in-sample re-specification (+0.026, t 1.91), declared as such | out-of-sample check assigned to Stage 58; calibration slopes far from 1 on real data |
| 23 Stat-arb (M2) | planted pairs recovered; false positives < 5%; state machine fully tested | yes (126 passed) | net in-sample Sharpe 0.10 after costs (gross 0.83); walk-forward variants assigned to Stages 39 / 58 |

**Conclusion: no stage needs to reopen.** Every criterion is met by the code. The gaps are of
three kinds: outputs the briefs ask to be persisted exist as code but were never produced on
the real lake; real-lake runs are slow for one identifiable reason; and several hardening and
coverage items were noted and left.

### What M2 promises, checked

"Research factors, measure IC, build a risk model, run vectorised factor and pairs backtests."

| Capability | From the command line | Through the API |
|---|---|---|
| Research a factor, measure IC, vectorised factor backtest | `atlas research signal` (works; technical ~38 min per 72 dates, fundamental ~3 h) | yes |
| Build a risk model | **no command; nothing stored on the main lake** | `build_and_store` (never run on the main lake; evidence run was a script with quarterly anchors) |
| Pairs backtest | no command (`atlas research pairs` lists current pairs only) | `pairs_backtest` (run by the Stage 23 artifact script) |
| Regimes | `atlas regime build` (main lake filled to 2026-11-02) | yes |

## 3. Findings of this checkpoint (new)

1. **Why every real-lake run is slow.** A `DataView` computes the whole quality report on its
   first call for each as-of, to learn which symbols are blocked. The report takes 71–97 s on
   the main lake, and 97% of that is `check_fundamentals_jumps` (per symbol and concept:
   `resolve_concept` 45 s, `quarterly_values` 45 s). That check's severity is `warning`, and
   only `critical` issues block, so it cannot change the block list. The four checks that can
   block (`stale_price`, `ohlc_inconsistent`, `nonpositive_price`, `split_mismatch`) all read
   prices only and take a few seconds. The Backlog item (Stage 21) blamed the macro vintages;
   the profile shows otherwise. This one cost is behind "~51 s per as-of" (Stage 21),
   "~16–30 s per view" (Stage 17) and much of "~136 s per `factor_panel`" (Stages 16 / 19).
2. **Persisted outputs missing on the main lake** (Stages 16 and 19, table above). Later
   stages are told to read them (`FactorStore.read`, `RiskModelStore.read_snapshot`): Stage 34
   (quant analyst), 37 (screen), 41 (Black-Litterman), 44 (risk engine).
3. **No offline test runs the quant chain end to end.** The three real-lake integration tests
   skip in CI (no lake there), so CI's integration step exercises Phase 0 only. Each package
   is well tested alone; nothing checks features → factors → risk model → alpha → backtest →
   metrics together on one synthetic lake.
4. **The hold-out is a convention, not a mechanism.** `backtest.in_sample_end` (2021-12-31,
   spec A7) is read by nothing outside config validation: `atlas research signal --to 2024`
   runs without a word. Stages 22 and 23 kept to 2016–2021 by discipline. (The risk model,
   metrics and regime evidence runs read 2022+ on purpose: their briefs ask for it and no alpha
   parameter was fitted there.)
5. **The unit suite takes 6 min 46 s** (protocol §5: under 60 s until Phase 8; CI needs
   15–20 min). Phase 2 tests are 378 s of the 407 s: statarb 122 s, factors 98 s, risk model
   45 s, alpha 43 s, regime 38 s. The slowest are acceptance tests (false-positive rate 29 s,
   planted pairs 18 s) and tests that rebuild the same synthetic lake.
6. **Every `atlas` command imports matplotlib, statsmodels and hmmlearn** at start-up (5.5 s
   for `atlas data status` under load; 16 s when matplotlib has no writable cache).
7. **`test_every_source_dir_has_init` fails on any tool-created dot-folder under `src/atlas`**
   (it failed run 1 today on `.claude`).

## 4. Gaps and improvements, in tiers

### Tier 1 — real gaps (promised outputs missing, the cost that blocks producing them, untested chain)
1. **(13) `DataView.blocked` runs only the checks that can block.** Same block list by
   construction (checks whose configured severity is below `critical` are skipped for
   `blocked`; `quality_issues()` still gives the full report on request). Expected: the first
   call per as-of drops from 71–97 s to a few seconds; measured before and after on the main
   lake, with the block lists compared on several dates.
2. **(16) `atlas factors build --from --to [--store] [--refresh]`**: fills `factor_exposures`
   on month-ends (wraps `build_factor_cache`, like `atlas regime build`).
3. **(19) `atlas risk-model build --from --to [--store]`**: stores daily `factor_returns` and
   month-end `risk_model_snapshots` (wraps `build_and_store`; the snapshot loop advances one
   view instead of opening a fresh one per month).
4. **(16, 19) Build both on the main lake** (factors from 2015, risk model 2018 → today, monthly
   anchors as configured) and record coverage and the bias statistics. Long background runs;
   writing the main lake goes through the owner's Terminal panel.
5. **(23 folder; all quant packages) Offline end-to-end test** `tests/integration/test_phase2_run.py`:
   one synthetic lake through features → factor panel and cache → risk model build and store →
   alpha frames → signal tear sheet → metrics, so CI covers the chain.
6. **Records.** Correct the Stage 21 Backlog diagnosis with the profile; mark the items this
   tier closes.

### Tier 2 — correctness and integrity hardening
1. **(17, 23) Hold-out guard**: `atlas research signal` (and a pairs backtest command, Tier 4)
   refuse a window past `backtest.in_sample_end` unless `--allow-holdout` is given, and the
   override is written to the audit log. A behaviour change, so it waits for the owner's word.
2. (17 audit E) `weights_backtest` raises when a day's portfolio return reaches −100% instead
   of dividing by zero or a negative number.
3. (17 audit F) `ForwardReturns.daily` / `.labels` handed out as copies (today: shared mutable
   frames).
4. (17 audit D, 21) By-regime IC: a t-statistic that respects the gaps between a regime's
   dates; `atlas research signal --regimes vol|trend|quadrant` reading the stored
   `regime_history` (the brief's "regime is plugged in later").
5. (17 audit H) The tear sheet header counts dates with an IC, not dates requested.
6. (22) `MultiFactorModel.horizon` reads the same config `predict` uses.
7. (16) Net debt / EBITDA as the brief says: add the D&A tags to `config/xbrl_map.yaml`,
   re-ingest fundamentals (only mapped tags are stored), switch `net_debt_ebitda` to EBIT + D&A.
   Needs an EDGAR re-ingest of the main lake.

### Tier 3 — coverage and performance
1. **(08, 16, 19, 22, 23) Sectors for the 387 instruments without one** (all former members):
   from EDGAR SIC codes mapped to GICS sectors, each mapping reviewed. Raises the risk model's
   ~387 of ~503 names a day and the alpha cross-section; removes the "sector known = still a
   member" asymmetry.
2. (09, 16) The 34–41 members without a market cap (share counts) and multi-class share counts
   (GOOGL / GOOG, BRK).
3. (09, 13) Make `resolve_concept` / `quarterly_values` cheaper (per-symbol tag index instead of
   boolean masks per tag; fewer tiny-frame sorts and merges): it is what remains of
   `factor_panel`'s cost and of the full quality report after Tier 1 item 1. Values compared
   one for one on the main lake.
4. (17, 19, 22) Consumers read the caches where the timing is the same: the risk-model loader's
   anchors and `MultiFactorModel` from `FactorStore`; an opt-in `--cached-factors` for
   `atlas research signal` (a stored month-end panel is one session older than a fresh one, so
   it is not a silent swap).
5. Suite time back toward the protocol's budget without dropping a test: `pytest-xdist` in
   `make test` and CI, session- or module-scoped synthetic lakes for the factor, risk-model and
   alpha worlds.

### Tier 4 — housekeeping
1. (17) Lazy imports in the CLI (matplotlib, statsmodels, hmmlearn only when a command needs
   them).
2. (23) `atlas research pairs --backtest --from --to`; (17) `--horizons`, `--rebalance`,
   `--weighting` options.
3. (18) `covariance_from_view` helper; `VolForecast` hashable.
4. (01) `test_every_source_dir_has_init` ignores dot-folders.
5. (15) Split-adjusted share-volume features (`DataView.split_factors` exists since Stage 16).

### Deferred by design (owned by a later stage; no action now)
Risk-scalar rule comparison (43, owner decision); out-of-sample check of the multi-factor
composite, trial count, HMM calibration, pair multiple-testing control (58); cost-aware pair
entry and β-neutral legs (39 / 58); borrow, financing and delisting proceeds (51 / 56);
walk-forward calibration of TSMOM / PEAD priors and the multi-factor IC (53); fat-tail and
multi-day VaR (44 / 46); daily appends of factor returns and regimes (55 / 61); point-in-time
GICS and prices of delisted members (no free source); ML alpha, Johansen baskets, Kalman β,
PCA risk model (briefs' Out of scope).

## 5. Status
- Tier 1: items 1, 2, 3, 5 and 6 done 2026-11-11; item 4 done 2026-11-12. Tier 1 is complete.
  - Item 1: block lists equal on six main-lake days; 0.8–1.2 s instead of 38–52 s per as-of.
  - Items 2–3: both commands tested and tried on the main lake into a scratch store
    (factors: 6 month-ends × 502 names in 15 min; risk model: 271 sessions, 10 snapshots,
    30 ETFs in 26 min). Found and fixed on the way: a risk-model build with ETFs failed on any
    month-end with 63–125 sessions of history (the `etfs=` branch had never run), and `--to`
    stopped one session early.
  - Item 5: `tests/integration/test_phase2_run.py`, 7 tests, in CI.
  - `make check` → exit 0, 4068 passed; offline integration 8 passed.
  - **Item 4 done (2026-11-12):** both stores are built on the main lake and read back
    (`READ-BACK OK`).
    - `factor_exposures`: 142 month-ends 2015-01-30 .. 2026-10-30, 497–504 names, every value
      finite; mean filled share 0.31 (2015) falling to 0.19 (2021 on).
    - `factor_returns`: 2,471 sessions 2017-01-03 .. 2026-10-30, 20 factors, no missing cell;
      mean R² 0.339, mean 392 names a day.
    - `risk_model_snapshots`: 115 month-ends 2017-04-28 .. 2026-10-30, 319–455 stocks, 30 ETFs
      from December 2018 on (three snapshots have 29), every covariance PSD.
    - Bias statistic, out of sample over the 21 sessions after each snapshot (1.00 =
      calibrated): cap-weighted 1.094, equal-weighted 1.076, random long/short 1.105, style
      tilts 0.960–1.067, single names 1.067. Risk is under-predicted by 6–10% for diversified
      portfolios.
    - On the way: the first ten-year risk-model build was killed by the system (exit 137);
      `load_cross_sections` kept a slice of the whole price panel alive per session. Fixed
      test-first in Stage 19 (commit `22fa7c1`); the re-run took 4.7 h.
    - Seen, not changed (Backlog): `BIL` is absent from three snapshots because the Stage 13
      stale-price rule blocked it (a T-bill ETF's close can stay unchanged for days), with no
      log line; the snapshot loop rebuilds its inputs at about 103 s per month-end.
- Tier 2 (owner's word 2026-11-12: "Start Tier 2"): items 1–6 done 2026-11-12; item 7 not started.
  - Item 1: `atlas research signal` refuses a window whose labels close after
    `backtest.in_sample_end` (exit 2, with the last `--to` that fits); `--allow-holdout` runs
    it and writes an `OVERRIDE` event to the run's audit log first; no record, no run. Every
    tear sheet says whether its labels read the hold-out. The default example
    `--from 2016 --to 2021` is now refused: its last labels close in March 2022.
  - Item 2: a session on which the book returns −100% or worse raises `BacktestError`.
  - Item 3: `ForwardReturns` hands out copies and keeps its own.
  - Item 4: the by-regime t-statistic keeps the gaps between a regime's dates;
    `--regimes vol|trend|quadrant` reads the stored `regime_history`.
  - Item 5: the header counts the dates with an IC.
  - Item 6: a model whose stated horizon differs from the config `predict` uses raises
    `ConfigError` (multi-factor, TSMOM and PEAD).
  - `make check` → exit 0, 4099 passed; offline integration 8 passed, 15 skipped (no lake in the sandbox run).
  - **Item 7 waits for the owner's decision.** It needs no download (the raw filings are
    kept), but it changes the leverage factor, so `factor_exposures`, `factor_returns` and
    `risk_model_snapshots` on the main lake would be rebuilt (about 9 h).
- Tier 3 (owner's word 2026-11-12: "Start Tier 3"): items 3, 4 and 5 done 2026-11-13.
  - Item 3: the per-company fundamentals pipeline works on plain rows instead of small
    pandas frames. Same results: every company of the main lake at three dates is equal
    (742 companies, 70,752 frames, 1,279,687 history rows) and that work is 87 times faster
    (1,148 s → 13.2 s). A real factor panel: 372.6 s → 15.5 s under the profiler.
  - Item 4: opt-in readers of the factor cache. `FactorStore.read_on` gives the panel of
    one exact day; `atlas risk-model build --cached-factors` reads its monthly anchors from
    it (same timing, factor returns within 3.2e-15); `MultiFactorModel(factor_store=…)`
    reads its style scores from it on a cached day (same numbers);
    `atlas research signal --cached-factors` uses the latest stored panel per date (an older
    signal, recorded in the tear sheet).
  - Item 5: `make test`, `make test-integration` and CI run the test files in parallel
    (`pytest-xdist`). Serial suite 518 s → 304 s, parallel 135 s, all measured under a heavy
    outside load. Still over the protocol's 60 s: the statarb acceptance tests remain.
  - A rebuild of both stores on the main lake now costs under an hour (it cost about 9 h):
    about 7 s per month-end for factors, 89 s per year for the risk model.
  - Item 1: done in code; applied to the main lake on 2026-11-13 (see "One pass" below). SIC codes alone would have been
    wrong for about one name in five, so each of the 180 former members with price bars has
    a reviewed line in `config/former_sectors.yaml` (134 agree with their SIC code, 46 do
    not; not from a data provider). With it, members with a sector rise from 324 to 447 of
    498 in mid-2015 and from 390 to 494 of 502 in mid-2019. Applying it needs the owner's
    review of the list, a universe sync and a rebuild of both stores.
  - Item 2: investigated here; implemented and applied on 2026-11-13 (see "One pass"). 34 members have no share count (mostly
    companies with several share classes); the raw filings hold weighted-average counts
    for 30 of them. Using an average as the count is an approximation and Berkshire's is
    in class-A equivalents: the owner's decision. It shares a fundamentals re-ingest with
    Tier 2 item 7.
- One pass, 2026-11-13 (owner: "Yes, one pass"): Tier 2 item 7 and Tier 3 items 1–2 on the
  main lake. Net debt / EBITDA (TTM operating income + TTM D&A; never EBIT alone). A company
  with no fresh cover-page share count gets its market cap from its weighted-average basic
  shares, only when TTM net income / EPS confirms it within a factor of 3 (about 1% of filed
  averages are in thousands or millions); Berkshire excluded. Reviewed sectors filled into the
  former members' instrument rows (without a universe sync, which would also bring in today's
  index changes). Re-parsed from the archived filings, both stores rebuilt on a scratch copy,
  checked, then swapped in (old tables backed up in `data/cache/`). Effects: members with a
  market cap at 2024-01-31 464 → 489 of 502; with a sector 390 → 494 of 502 in mid-2019; the
  risk model's daily cross-section 392 → 463 names on average; mean R² 0.339 → 0.317; bias
  of single names 1.067 → 1.076. Every other factor value unchanged.
- Tier 4: not started; waits for the owner's word, as in Phase 1.
