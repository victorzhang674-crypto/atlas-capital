# Stage 23 — Statistical arbitrage (pairs) · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** DONE (2026-11-09) — see [verification.md](verification.md)
- **Gate check:** Stage 22 is DONE (its [verification.md](../stage-22-systematic-alpha/verification.md) says **ALL CRITERIA VERIFIED**); `uv run pytest -q` → 3673 passed, 29 deselected, exit 0 (2026-11-09, fresh worktree after `uv sync --group dev`; raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt))
- **Execution:** written and executed in one go, inline TDD, no subagents (kick-off: "plan, execute and audit Stage 23 according to the existing plans"; the owner's standing preference: plan.md first, then execute, no review pause). `worklog.md` is the ledger.

**Goal:** a market-neutral pairs engine: monthly point-in-time selection of cointegrated same-sector pairs, a mean-reversion signal state machine, `PairSignal` output translated to dollar-neutral per-symbol weights, a vectorised pairs backtest with costs, and `atlas research pairs --as-of …`. Completes M2.

**Architecture:** new package `atlas.quant.statarb` (the brief's three files plus `__init__.py`):
- `selection.py` — pure statistics `half_life`, `hurst_exponent`, `hedge_ratio`, `coint_pvalue`; `Pair` (frozen dataclass); `evaluate_pair` (all filters on one candidate, returns the stats and the first failed filter); `select_pairs(log_prices, sectors, *, config)` (pure: prefilter, tests, ranking, caps); `log_price_panel(returns)`; `select_pairs_at(view)` (the `DataView` loader: PIT members, sectors, prices).
- `signals.py` — `SpreadState`, `Event`, `PairState`, `spread`, `zscore`, `step` (the state machine for one session), `run_pair` (the state machine over a series), `rolling_coint_pvalue`, `expected_return`, `confidence`, `PairSignal` (pydantic v2: crosses into Stage 39), `to_signal`, `target_weights`, `current_signals(view)` (a stateless snapshot for the CLI).
- `backtest.py` — `pairs_backtest(returns, sectors, members, *, start, end, config)` → `PairsBacktestResult` (Stage 17 `BacktestResult` + trade log + monthly pair sets).
- Stage 17 refactor (behaviour-preserving): the drift / turnover / cost loop of `vector_backtest` is moved into a public `weights_backtest(targets, daily, *, cost_bps, sessions_per_year)`, which `vector_backtest` now calls; Stage 17's tests must stay green unchanged.
- CLI `atlas research pairs --as-of YYYY-MM-DD [--universe] [--out]` in `src/atlas/cli/research_cmd.py`.
- Config `cfg.quant.statarb` (`StatArbConfig`; YAML `config/fund.yaml › quant.statarb`).

**Tech stack:** numpy / pandas; statsmodels `coint` (Engle-Granger with MacKinnon p-values; already installed via `arch`); Stage 13 `DataView` (`returns`, `members_on`, `universe`, `instrument`); Stage 17 `rebalance_dates`, `BacktestResult`, the new `weights_backtest`; Stage 20 metrics (via the Stage 17 summary).

## Collisions with the overall plan
None with the roadmap (row 23: size M, depends on 17; Phase 2, completes M2 "… run vectorised factor and pairs backtests"; consumer Stage 39 `StatArbPod`: "active pair signals → dollar-neutral per-pair target weights, daily rebalance, pair stop logic") or the spec (§3 quant "pairs cointegration", §8 Stat-Arb pod "S&P 500 pairs within sector, cointegration mean reversion, days–weeks, daily", §11 statsmodels for cointegration, §12 `quant/ … statarb`, A7 in-sample end 2021-12-31). Kalman β and baskets / Johansen go to the Backlog (brief: out of scope; "OLS (or Kalman)" → OLS chosen).

Interpretations and design choices (also logged in `PROGRESS.md › Decisions log`):

1. **Prices.** Log total-return price index per name, `log P = cumsum(log(1 + r))` from `DataView.returns` (split- and dividend-adjusted, simple daily). A name needs a finite return on **every** session of the formation window to be a candidate (no fills; logged).
2. **Formation window.** The brief fixes the correlation window (252 sessions) but not the cointegration window. Engle-Granger power on one year is too low to find genuine pairs (probe, [artifacts/eg_power_probe.py](artifacts/eg_power_probe.py): planted half-life 10 recovered 40% of the time at 252 sessions, 96% at 504; random-walk false positives about 4% at both). So: **correlation of daily log returns over the last `corr_window` = 252 sessions** (prefilter > `min_corr` 0.7, strict); **Engle-Granger test, OLS hedge ratio, half-life and Hurst over the last `formation` = 504 sessions** (2 years). Both windows end at the latest session known at the as-of (the session before the decision day).
3. **Engle-Granger test, order-free.** `coint_pvalue(a, b)` = max of statsmodels `coint(a, b, trend="c", autolag="aic")` and `coint(b, a, …)` p-values: a pair passes only if it is cointegrated in both orientations (`< max_pvalue` 0.05, strict). Order-free and conservative (one test per orientation would double the false positives).
4. **Orientation and hedge ratio.** `y` = the alphabetically first symbol, `x` = the other; `pair_id = "y~x"`. Hedge ratio β and intercept α by OLS of log y on log x over the formation window; β must be > 0 (a negative β is no spread trade). Spread `s = log y − β log x` (α is irrelevant to z, which is demeaned).
5. **Half-life.** AR(1) on the spread: `Δs_t = a + b s_{t−1} + e`, φ = 1 + b, `half_life = −ln 2 / ln φ` sessions for 0 < φ < 1, else `inf` (no mean reversion). Kept if `min_half_life` 2 ≤ HL ≤ `max_half_life` 60.
6. **Hurst exponent.** Lagged-difference estimator: slope of log std(s_{t+τ} − s_t) on log τ for τ = 2 … `hurst_max_lag` (50). Kept if H < `max_hurst` 0.5. (Probe: planted OU spreads 0.17–0.44, random walk OLS residuals ≈ 0.45 at 504 sessions; the filter is weak against spurious pairs, the EG test does the work.)
7. **Ranking and caps.** Candidates passing every filter are ranked by EG p-value (then half-life, then `pair_id`) and accepted greedily while each name is in ≤ `max_pairs_per_name` 2 accepted pairs and the total is ≤ `max_pairs` 40. Same sector only (GICS sector from the instruments table; a name without a sector is not paired, logged). Sectors are today's values (the instruments table is static, Stage 13): a known limitation, as in Stage 22.
8. **Monthly PIT selection.** Formation dates = the first session of each month (Stage 17 `rebalance_dates(…, "monthly")`); candidates are the members of `universe` on that date (`members_on`); windows end at the previous session.
9. **z-score.** `z_t = (s_t − mean) / std` (ddof 1) over the last `L = max(round(lookback_mult × HL), min_lookback)` spread values including `s_t` (`lookback_mult` 2, `min_lookback` 5). The decision on session t uses closes through t−1 (what a `DataView` at t sees), trades at the close of t and earns from t+1 (Stage 17 timing): one session of lag, no look-ahead.
10. **State machine** (one transition per session, evaluated on `z_t`; NaN z → no entry and no z-based exit, the time stop and breakdown still apply):
    - `FLAT` → `LONG_SPREAD` (long y, short x) if `−stop_z ≤ z < −entry_z`; → `SHORT_SPREAD` if `entry_z < z ≤ stop_z`; a pair `locked` this formation never enters.
    - In a position, checked in this order: **exit** (`LONG`: z > −exit_z; `SHORT`: z < exit_z — this includes |z| < 0.5 and an overshoot through zero) → **stop loss** (|z| > stop_z, strict) → **breakdown** (latest rolling p-value > `breakdown_pvalue`) → **time stop** (sessions held ≥ `time_stop_mult` × HL). Exit returns to `FLAT` (re-entry allowed from the next session); stop loss, breakdown and time stop return to `FLAT` and **lock** the pair until the next formation (fail safe: no new risk on a pair that just failed).
    - Breakdown also locks a flat pair (no entry on a broken pair).
    - Thresholds: `entry_z` 2, `exit_z` 0.5, `stop_z` 4, `time_stop_mult` 3.
11. **Breakdown check.** `rolling_coint_pvalue` = the order-free EG p-value (3) over the last `formation` sessions, re-evaluated every `breakdown_every` 5 sessions (carried forward in between; the first value is on the formation date), `breakdown_pvalue` 0.20 (lenient: on a 2-year window a genuine pair's p drifts above 0.05 often).
12. **Reselection.** At each formation date the new pair set is selected. An open pair that is reselected keeps its entry parameters (β, HL, lookback) until it is flat, then takes the new ones; an open pair **not** reselected is closed at that formation (event `deselected`). Locks reset at each formation. So the open set is always a subset of the current selection and the caps hold.
13. **`PairSignal`** (pydantic v2, frozen): `pair_id`, `long`, `short` (symbols), `hedge_ratio` (β, > 0), `z` (finite), `exp_return` (annualised fraction of the pair's gross notional, ≥ 0), `confidence` ∈ [0, 1]. Emitted for pairs **in a position** (or, in the CLI snapshot, that would enter now). `LONG_SPREAD` → long y / short x; `SHORT_SPREAD` → long x / short y.
    - `exp_return` = OU reversion to the exit band: move `(|z| − exit_z) × σ_s` (log spread units, σ_s = the z window's std) per `(1 + β)` of gross, over the expected time `HL × log2(|z| / exit_z)` sessions, annualised × `P / time` (P = `quant.features.sessions_per_year`); 0 if |z| ≤ exit_z.
    - `confidence = clip(1 − p / breakdown_pvalue, 0, 1)` with p the latest rolling p-value (formation p-value in the snapshot).
14. **Weights: dollar-neutral per pair** (brief). Each active pair gets gross `pair_gross` 0.05 of NAV: +0.025 on the long leg, −0.025 on the short leg (default: 40 pairs × 0.05 = 2.0 gross at full use). A name in two pairs gets the sum. Note: dollar-neutral, not β-neutral; β is used for the spread / z and reported for risk.
15. **Pairs backtest.** Daily targets (pod rebalance `daily`) through `weights_backtest`: weights drift with returns between sessions, every session rebalances back to target, turnover cost `cost_bps` 5 bps per unit of turnover (Stage 17 convention; includes the daily re-hedge, conservative); no borrow / financing (Backlog). Summary from Stage 20 metrics + trade stats (number of trades, hit rate, mean holding period, events by type, mean pairs open).
16. **In-sample run (Task 4).** Real lake, read only, one `DataView` at 2021-12-31 (no 2022 bar read; hold-out untouched), S&P 500 point-in-time members per formation date. With the lake starting 2015 and 504-session formation, the first formation date is the first month with 504 sessions of history (about 2017-01); the window runs to 2021-12-31. Results are recorded as they come (no tuning of thresholds on this window: everything above is fixed before the run). The brief asks for results in the handoff note, not for a Sharpe threshold.
17. **Acceptance criteria, defined before the tests are written:**
    - "Planted pairs are recovered": a synthetic universe (3 sectors, correlated random walks plus planted cointegrated pairs with HL 5 and 10 sessions, 504-session formation): every planted pair is selected on ≥ 95% of 40 seeds; a planted pair split across sectors is never selected.
    - "False positives under 5% on random walks": universes of same-sector random walks with a common factor (daily return correlation ≈ 0.85, so they pass the 0.7 prefilter): the share of tested pairs that pass every filter is < 5% over ≥ 400 pairs, and the share selected after caps also < 5%.
    - "Signal state machine fully tested": every transition and boundary of interpretation 10 has a test, plus a hand-written mutation check of `signals.py` with every mutant killed (as Stages 20–22).
18. **Config** `cfg.quant.statarb` holds every number above (`universe`, `corr_window`, `formation`, `min_corr`, `max_pvalue`, `min_half_life`, `max_half_life`, `max_hurst`, `hurst_max_lag`, `max_pairs_per_name`, `max_pairs`, `entry_z`, `exit_z`, `stop_z`, `lookback_mult`, `min_lookback`, `time_stop_mult`, `breakdown_pvalue`, `breakdown_every`, `pair_gross`, `cost_bps`), with validators (`exit_z < entry_z < stop_z`, `min_half_life < max_half_life`, `corr_window ≤ formation`, `hurst_max_lag < formation / 2`).

## Design decisions
- **D1 Errors.** Bad arguments / config → `ValueError` (pydantic `ValidationError` for config); a name without data or sector → not a candidate (logged); no candidates → empty selection (logged), never a guess. The CLI: 0 ok, 1 run failed, 2 bad arguments (Stage 17 convention).
- **D2 Types.** `PairSignal` pydantic v2 (crosses to Stage 39); `Pair`, `PairState` frozen dataclasses; `SpreadState` / `Event` `StrEnum`s; frames for panels.
- **D3 PIT.** Only `DataView` reads (`select_pairs_at`, `current_signals`); the backtest is pure on a returns panel and computes everything at t from data through t−1; windows end at the session before the decision; `guard_not_label` on panel inputs.
- **D4 Determinism.** No randomness in the engine (statsmodels' `coint` is deterministic); test generators take `seed`.
- **D5 Logging.** structlog `get_logger(__name__)`: candidates dropped (coverage, sector), selection counts per formation, breakdowns.

## Changes to the plan during execution (all recorded in worklog.md)
- `corr_window < formation` strictly; `max_pvalue < breakdown_pvalue`; `exit_z > 0` (config validators).
- The false-positive acceptance test measures 3000 independent pairs (the first, clustered design was too noisy: 5.7% on 420 dependent pairs; at scale the clustered design gives 3.70%); threshold unchanged.
- `pairs_backtest` gained an optional `progress(day, n_selected)` callback; `latest_zscore` (one z-score helper for the backtest and the snapshot); `rolling_coint_pvalue` is the backtest's breakdown source.
- Three tests added from the mutation check (half-life lower bound, Hurst lag set, no look-ahead in the monthly selection).

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

Commands run as `UV_CACHE_DIR=$TMPDIR/uv-cache-s23 UV_NO_SYNC=1 LOKY_MAX_CPU_COUNT=4 uv run …`. Tests in `tests/unit/quant/statarb/` with unique basenames `test_statarb_*.py`.

### Task 0 — Config
- Files: modify `src/atlas/core/config.py` (`StatArbConfig`, `QuantConfig.statarb`), `config/fund.yaml` · test `test_statarb_config.py`
- [x] Failing tests: defaults equal the YAML and interpretation 18; validators reject `exit_z ≥ entry_z`, `entry_z ≥ stop_z`, `min_half_life ≥ max_half_life`, `corr_window > formation`, a correlation outside (0, 1), non-positive gross / caps
- [x] Implement → PASS

### Task 1 — Synthetic generator; selection recovers planted pairs, rejects random walks (brief task 1)
- Files: `src/atlas/quant/statarb/{__init__.py,selection.py}` · world `tests/unit/quant/statarb/statarb_world.py` (`ou_spread`, `planted_universe`, `random_walk_universe`, lake world) · test `test_statarb_selection.py`
- [x] Failing tests: generator (OU half-life, factor correlation); `hedge_ratio` recovers β; `coint_pvalue` small for a planted pair, symmetric in its arguments; `evaluate_pair` reports the first failed filter (correlation, EG, β sign, half-life, Hurst); `select_pairs`: recovery ≥ 95% of 40 seeds for HL 5/10, cross-sector planted pair never selected, sector missing → not paired, incomplete window → not a candidate, caps (≤ 2 per name, ≤ `max_pairs`) and deterministic ranking; random-walk false positive rates < 5% (interpretation 17); `log_price_panel`; `select_pairs_at` on a lake world uses PIT members and windows ending before the as-of
- [x] Implement → PASS

### Task 2 — Half-life and Hurst (brief task 2)
- Files: `selection.py` · test `test_statarb_stats.py`
- [x] Failing tests: `half_life` of an exact AR(1) (known φ → −ln 2 / ln φ), of OU paths ≈ planted (within tolerance), `inf` for a random walk / explosive series, short input → ValueError; `hurst_exponent` ≈ 0.5 for a random walk, < 0.4 for a strong OU spread, > 0.5 for a persistent (trending-increment) series, constant series → NaN, lag validation
- [x] Implement → PASS (these functions may already exist from Task 1's needs; their tests are written here first if not)

### Task 3 — Signal state machine (brief task 3)
- Files: `src/atlas/quant/statarb/signals.py` · test `test_statarb_signals.py`
- [x] Failing tests: `zscore` window and ddof; every transition of interpretation 10 (both sides, strict boundaries at exactly 2 / 0.5 / 4, entry blocked beyond the stop, overshoot exit, priority order, locks after stop / breakdown / time stop, no lock after exit, NaN z, time stop counted in sessions held); `run_pair` on a scripted z path gives the expected events; `rolling_coint_pvalue` cadence; `expected_return` / `confidence` known values; `PairSignal` validation; `to_signal` leg orientation; `target_weights` dollar-neutral per pair and summing across pairs; `current_signals` on the lake world
- [x] Implement → PASS

### Task 4 — Pairs backtest + CLI; in-sample results (brief task 4)
- Files: modify `src/atlas/quant/research/vector_backtest.py` (`weights_backtest`), create `src/atlas/quant/statarb/backtest.py`, modify `src/atlas/cli/research_cmd.py` · tests `test_statarb_backtest.py`, `tests/unit/cli/test_research_cmd.py` (pairs), Stage 17 tests unchanged · artifact `artifacts/insample_run.py`
- [x] Failing tests: `weights_backtest` matches the old `vector_backtest` result exactly (Stage 17 tests green); pairs backtest on a planted universe earns a positive gross return, costs reduce it by `turnover × bps`, one-session timing (a z computed at t trades at t's close, earns t+1; shifting the data proves no look-ahead), deselection closes positions, reselected open pairs keep their parameters, trade log events; CLI help, table output, `--out` writes `pairs.csv` / `pairs.json`, bad date exit 2, empty lake exit 1
- [x] Implement → PASS
- [x] Run `artifacts/insample_run.py` on the real lake (background, read only; 2017-01 .. 2021-12); record summary, trade stats and pairs per month in the handoff note

### Task 5 — Exports, audit, full verification
- [x] `atlas.quant.statarb.__init__` exports; package test (exports importable, no `datetime.now`, no network)
- [x] Self-audit of the diff (PIT, units, errors, config, timing) and a mutation check `artifacts/mutate.py` over `selection.py` / `signals.py` / `backtest.py`; fixes test-first
- [x] `make check` + `uv run pytest tests/unit/quant/statarb -q`; `verification.md`, PROGRESS (DONE only if every criterion is evidenced)
- [ ] Commit `stage-23: Statistical arbitrage (pairs)`; fast-forward `main` (owner's terminal panel, Rule 6A / sandbox note); merged-main `uv run pytest -q` + `make check`; push; CI; records commit
