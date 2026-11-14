# Stage 21 — Regime detection · Verification evidence

Output from commands run in this stage's chat (Project.md Rule 1); raw outputs under `artifacts/`.

#### Verification evidence — Stage 21 — 2026-11-05
- Commands run (this chat, worktree `claude/stage-21-completion-c11c2d`, `UV_CACHE_DIR=$TMPDIR/uv-cache-s21 UV_NO_SYNC=1 LOKY_MAX_CPU_COUNT=4`):
  - `make check` → exit 0 · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
    - `uv run ruff check .` → All checks passed!
    - `uv run ruff format --check .` → 320 files already formatted
    - `uv run mypy src/atlas` → Success: no issues found in 134 source files
    - `uv run pytest -q` → 3598 passed, 0 failed, 29 deselected (live/llm/integration)
  - `uv run pytest tests/unit/quant/regime -q` (brief's Verify) → 674 passed, exit 0 · raw: [artifacts/verify-brief.txt](artifacts/verify-brief.txt)
  - same with `-W error` → 674 passed · raw: [artifacts/werror.txt](artifacts/werror.txt)
  - coverage (`--cov=src/atlas/quant/regime --cov=src/atlas/data/macro`): every `atlas.quant.regime` module 100%, `atlas/data/macro/pit.py` 100% · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
  - mutation checks `artifacts/mutate.py` → **50/50 caught** on the final code (first run 42/50: 6 test gaps fixed, 1 dead code removed, 1 equivalent mutant replaced; see worklog) · raw: [artifacts/mutation-checks-final.txt](artifacts/mutation-checks-final.txt)
  - real lake, read only (`~/Projects/atlas-capital/data`): `artifacts/real_lake_check.py` 2017-01-03 .. 2022-12-30 → 1,510 decision days in 55.6 s through one view, stored and read back equal, exit 0 · raw: [artifacts/real-lake-check.txt](artifacts/real-lake-check.txt)
  - `ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data uv run pytest tests/integration/test_regime_real_lake.py -m integration -v` → 6 passed in 68 s · raw: [artifacts/integration-real-lake.txt](artifacts/integration-real-lake.txt)
  - On merged `main` (`fe260a3`, fast-forward of `6209064` via the owner's Terminal panel; tree = branch tree): `make check` → exit 0, 3598 passed; brief's Verify → 674 passed; pushed `main` and the stage branch · raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt), [artifacts/push.txt](artifacts/push.txt)
  - GitHub Actions CI on `fe260a3`: success (main 42114690550, branch 42114690323) · raw: [artifacts/ci-runs.txt](artifacts/ci-runs.txt)
  - gate: `uv run pytest -q` before the stage → 2915 passed, exit 0 · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
- Acceptance criteria (brief):
  - [x] **PIT-safe** — evidence: HMM `test_one_fit_per_month_on_rows_before_the_month` (every fit's `fit_end` < its month), `test_future_rows_change_nothing` (noise after t changes no probability ≤ t), `test_truncated_history_gives_the_same_probabilities`, `test_filtered_last_row_equals_hmmlearn_predict_proba` (filtered, never smoothed); loader `test_future_rows_and_a_later_split_change_nothing`, `test_shorter_history_gives_the_same_states`, `test_nfci_is_used_only_after_its_release`, `test_monthly_release_is_used_only_after_its_release`, `test_a_late_market_row_is_not_used_before_it_is_published`, `test_a_revision_is_used_only_after_it_is_published`, `test_hmm_fit_uses_the_slope_known_at_the_month_start`, `test_history_uses_each_days_membership`, `test_history_equals_regime_at_day_by_day`; rule-based `test_trailing_percentile_is_point_in_time`, `test_trailing_zscore` (truncation); store `test_point_in_time_read`, `test_rows_not_yet_known_are_invisible`; DataView leak suite `test_guard_raises_if_filter_bypassed[macro_vintages]`, `test_future_rows_never_leak[macro_vintages|members_on]`; 8 look-ahead mutants caught; real lake: every `data_date < as_of`
  - [x] **Stable labels** — evidence: `test_label_stability_across_expanding_refits` (5 expanding refits, each true regime keeps its label, accuracy > 85% each), `test_order_does_not_depend_on_hmmlearn_internal_order` (6 seeds), `test_states_are_ordered_by_volatility`, `test_transition_matrix_follows_the_state_order`, `test_fit_is_deterministic_and_records_its_window`, `test_fits_run_single_threaded` (bitwise reproducible), `test_two_state_recovery_above_85_percent`, `test_three_state_recovery_above_85_percent` (brief task 2: > 85%)
  - [x] **The 2020 and 2022 episodes are flagged on real data** — evidence (thresholds fixed in plan interpretation 12 before the run): 2020-03-09 .. 2020-04-09 (24 days): CRISIS 100%, stress 100%, DOWN 100%, P(turbulent) ≥ 0.5 100%, scalar ≤ 0.5 100% (min 0.25); 2022-01-03 .. 2022-10-31 (209 days): DOWN 77.5%, HIGH/CRISIS 79.4%, stress 23.4%, scalar < 1 99.5%; `test_march_2020_is_crisis_and_stress`, `test_2022_bear_market_is_flagged` PASSED on the real lake
- Brief tasks: 1 rule-based on synthetic series (`test_regime_vol_trend.py`, 34 tests); 2 HMM > 85% and label stability (above); 3 expanding-window PIT (`test_regime_hmm_pit.py`); 4 quadrant, stress flag and the risk_scalar table (`test_regime_macro_quadrant.py`; `test_risk_scalar_full_table` 480 combinations, `test_worse_labels_never_raise_the_scalar` hypothesis); 5 real-data sanity check (integration test above). In scope "persist the daily regime history": `regime_history` table, `test_regime_store.py`, real history stored and read back equal.
- Reported, not hidden:
  - The plan's own **calm control** (2017: scalar < 1 on ≤ 25% of days) **failed**: 35.9%, all from the macro-quadrant multiplier (0.0% without it; vol, trend, stress and HMM never cut in 2017). Not a brief criterion; the multipliers were not retuned after seeing the data. Owner decision (2026-11-05): keep today's table as the default; Stage 43 runs a walk-forward comparison of four rules (fully invested, current table, table without the quadrant, learned model) and adopts the out-of-sample winner after costs (Decisions log, Backlog).
  - `regime_history` and a one-day `regime_at` agree on labels, stress and scalar but not bit-for-bit on real data: dividends known later back-adjust earlier prices (~1e-15 relative) and the history uses the reading view's quality block list (2020-03-16 breadth 0.1189 vs 0.1195; EOG, HP blocked that day only). Documented in `loader.py`; tested on synthetic data (`test_a_dividend_inside_the_range_changes_only_rounding`).
  - Gap fix (2026-11-05): `atlas regime build` persists the history; see the section below for the main-lake run.
- Waivers: none

**Result:** ALL CRITERIA VERIFIED

#### Gap fix — persisting the history (2026-11-05)
- `tests/unit/cli/test_regime_cmd.py` → 7 passed (`-W error`); CLI mutants 2/2 caught
- Dry run (sandbox, main lake read only → scratch store): 2,724 decision days 2016-01-04 .. 2026-11-02, exit 0 · raw: [artifacts/cli-dry-run-scratch.txt](artifacts/cli-dry-run-scratch.txt)
- CI on records commit `9c349a6`: success (main 42142157780, branch 42142157168) · raw: [artifacts/ci-runs-records.txt](artifacts/ci-runs-records.txt)
- Commit `2b59c61`: `make check` exit 0, 3605 passed (before the commit and on merged `main`); pushed · raw: [artifacts/make-check-gapfix.txt](artifacts/make-check-gapfix.txt), [artifacts/persist-main-lake.txt](artifacts/persist-main-lake.txt)
- **Persisted in the main lake:** `atlas regime build --from 2016-01-04 --to 2026-11-05` → 2,724 rows of `regime_history` (2016-01-04 .. 2026-11-02); read back: PIT reads correct, equal to the dry run · raw: [artifacts/persist-readback.txt](artifacts/persist-readback.txt)
- CI on `2b59c61`: success (main 42148293255, branch 42148293175) · raw: [artifacts/ci-runs-gapfix.txt](artifacts/ci-runs-gapfix.txt), [artifacts/ci-runs-gapfix-main.txt](artifacts/ci-runs-gapfix-main.txt)

#### Phase 2 checkpoint follow-up (Tier 2: `RegimeStore.labels`) — 2026-11-12
- Red first, then `test_labels_by_kind` passes; brief's Verify `uv run pytest tests/unit/quant/regime -q` → 675 passed · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-verify.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-verify.txt); `make check` → exit 0 (ruff clean, 381 files formatted, mypy clean on 157 files, 4099 passed, 38 deselected) · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-make-check.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier2-make-check.txt)
- Main lake (read only): 1,448 labelled decision days per kind, 2016-01-04 .. 2021-10-01. Details in [worklog.md](worklog.md).
- Acceptance criteria unchanged and still evidenced (in the brief's Verify).

#### Phase 2 checkpoint follow-up (Tier 4: hmmlearn loads in the fit) — 2026-11-14
- Commands run (this chat): `make check` → exit 0 (ruff clean, 393 files formatted, mypy clean on 157 files, 4241 passed in parallel and 3 timing tests alone); `uv run pytest -q` → 4244 passed, 71 deselected; integration on the main lake → 55 passed, 1 skipped (the flag-gated 2018–2024 calibration) · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-final-checks.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-final-checks.txt); red first: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-red.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-red.txt); mutants: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-mutate.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-mutate.txt)
- `tests/unit/quant/regime/` passes.
- Acceptance criteria unchanged and still evidenced.

**Result:** ALL CRITERIA VERIFIED
