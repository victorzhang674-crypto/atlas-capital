# Stage 23 — Statistical arbitrage (pairs) · Verification evidence

Filled only with output from commands run in this stage's chat (Project.md Rule 1). Raw outputs under `artifacts/`.

#### Verification evidence — Stage 23 — 2026-11-09
- Commands run (this chat; `UV_CACHE_DIR=$TMPDIR/uv-cache-s23 UV_NO_SYNC=1 LOKY_MAX_CPU_COUNT=4 MPLCONFIGDIR=$TMPDIR/mpl`):
  - Gate: `uv run pytest -q` → 3673 passed, 29 deselected, exit 0 · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
  - `make check` → exit 0 · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
    - `uv run ruff check .` → All checks passed!
    - `uv run ruff format --check .` → 348 files already formatted
    - `uv run mypy src/atlas` → Success: no issues found in 145 source files
    - `uv run pytest -q` → **3807 passed, 29 deselected** in 361 s
  - Brief's Verify `uv run pytest tests/unit/quant/statarb -q` → **126 passed**, exit 0 · raw: [artifacts/verify-statarb-tests.txt](artifacts/verify-statarb-tests.txt)
  - Mutation check `artifacts/mutate.py` (40 hand-written mutants of `selection.py`, `signals.py`, `backtest.py`) → **39/39 non-equivalent mutants killed; 1 equivalent mutant survived as expected** · raw: [artifacts/mutate.txt](artifacts/mutate.txt) (earlier runs: `mutate-3.txt` 36/40 → three test gaps closed; `mutate-1/2-stopped.txt`)
  - Recovery rates `artifacts/recovery_rates.py` → half-life 5: 119/120 (99.2%), half-life 10: 117/120 (97.5%), cross-sector planted pair 0/40 · raw: [artifacts/recovery-rates.txt](artifacts/recovery-rates.txt)
  - False-positive probes: `artifacts/fp_rate_probe.py` (3000 independent pairs) → pass every filter 3.90% (s.e. 0.35%), EG alone 4.00% · [artifacts/fp-rate-probe-1.txt](artifacts/fp-rate-probe-1.txt); `artifacts/fp_clustered_probe.py` (200 six-name universes, 3000 pairs) → pass every filter 3.70% (cluster s.e. 0.46%), selected after caps 3.53% (0.38%) · [artifacts/fp-clustered-probe.txt](artifacts/fp-clustered-probe.txt)
  - In-sample run `artifacts/insample_run.py` (real lake, read only, 2017-01-03 .. 2021-12-31) → exit 0 · raw: [artifacts/insample-run.txt](artifacts/insample-run.txt), outputs [artifacts/insample/](artifacts/insample/)
  - Secret scan of the stage's files against the main checkout's `.env` values (not printed) → 0 hits
- Acceptance criteria (brief):
  - [x] **Planted pairs are recovered** — evidence: `test_planted_pairs_are_recovered` (40 seeds, planted half-lives 5 and 10 sessions, each recovered on ≥ 95%: measured 99.2% / 97.5%; the cross-sector planted pair never selected); `test_cross_sector_pair_passes_the_filters_but_is_not_paired`; `test_select_pairs_at_reads_the_view_point_in_time` (planted pairs found through `DataView`); `test_planted_pairs_make_money_gross` (backtest selects every planted pair)
  - [x] **False positives are under 5% on random walks** — evidence: `test_false_positives_on_random_walks_under_five_percent` (3000 independent pairs of factor-correlated random walks, return correlation ≈ 0.86, > 95% pass the prefilter: rate 3.90% < 5% and rate + 2 s.e. < 5%); clustered design at scale 3.70% / 3.53% after caps; `test_selection_only_keeps_pairs_that_pass_every_filter`. The first test design (420 dependent pairs) measured 5.7% and was redesigned (threshold unchanged; worklog and Decisions log)
  - [x] **The signal state machine is fully tested** — evidence: `test_statarb_signals.py` (every transition of plan interpretation 10 on both sides with strict boundaries at entry / exit / stop, entry blocked beyond the stop, overshoot exit, priority exit → stop → breakdown → time stop, locks after stop / breakdown / time stop and not after exit, NaN z, integer and fractional time stops, scripted path, initial state); mutation check: all 21 non-equivalent `signals.py` mutants killed, the equivalent one (`if finite` → `if True`, NaN comparisons are always False) documented
  - [x] Brief tasks: (1) synthetic generator + selection — `statarb_world.py`, `test_statarb_selection.py`; (2) half-life / Hurst — `test_statarb_stats.py`; (3) state machine — `test_statarb_signals.py`; (4) pairs backtest + CLI — `test_statarb_backtest.py` (costs, Stage 17 engine, dollar-neutral, caps, no look-ahead in z and in the monthly selection, breakdown timing, deselection, parameters kept), `tests/unit/cli/test_research_cmd.py` (`atlas research pairs --as-of`); in-sample results below and in the PROGRESS handoff note
- In-sample results (real lake, 2017-01-03 .. 2021-12-31, every threshold as pre-registered, nothing tuned): 564 names, 60 formations (2017-01: 0 pairs, no complete 504-session window yet), mean 30.1 pairs selected, 503 distinct; **net Sharpe 0.10** (ann +0.21%, vol 2.10%, max DD 5.75%), **gross Sharpe 0.83**, total cost 7.71%, annual turnover 30.9, mean gross 0.49 of NAV, mean open pairs 10.1; 1502 trades, hit rate 60.1%, mean trade +0.14% per unit pair gross, mean held 8.4 sessions; exits: exit 1133, deselected 281, breakdown 66, stop loss 7, time stop 6
- Observations reported, not criteria: on the planted test universes, 3.6 non-planted same-sector pairs are selected per universe (≈ 7% of ~51 candidates; a stronger common factor, return correlation ≈ 0.93) — with the real-data reading, Backlog (multiple-testing control)
- Waivers: none

**Result:** ALL CRITERIA VERIFIED
