# Stage 15 — Technical feature library · Verification evidence

Filled in only with output from commands run in this stage's chat (Project.md Rule 1). Raw outputs are under `artifacts/`. Commands were run as `UV_CACHE_DIR=$TMPDIR/uv-cache-s15 UV_NO_SYNC=1 uv run …` (sandbox).

#### Verification evidence — Stage 15 — 2026-11-02
- Commands run (this chat):
  - `uv run pytest -q` → 2027 passed, 0 failed, 11 deselected (live) in 35.46 s · raw: [artifacts/pytest.txt](artifacts/pytest.txt)
  - `uv run ruff check .` → All checks passed! · raw: [artifacts/ruff-check.txt](artifacts/ruff-check.txt)
  - `uv run ruff format --check .` → 204 files already formatted · raw: [artifacts/ruff-format.txt](artifacts/ruff-format.txt)
  - `uv run mypy src/atlas` → Success: no issues found in 88 source files · raw: [artifacts/mypy.txt](artifacts/mypy.txt)
  - `uv run pytest tests/unit/quant/features -q` (brief's Verify) → 339 passed in 6.27 s · raw: [artifacts/verify-features.txt](artifacts/verify-features.txt)
  - `make check` → exit 0 (ruff, format, mypy, 2027 passed) · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
  - Stage tests with `-W error` → 339 passed · raw: [artifacts/stage-tests-W-error.txt](artifacts/stage-tests-W-error.txt)
  - Coverage `--cov=atlas.quant.features` (feature tests + DataView tests) → 100% of 445 statements · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
  - Performance (`-s`) → "40 features x 500 symbols x 2000 sessions in 2.52 s (limit 20 s)" (4.71 s on the first run) · raw: [artifacts/performance.txt](artifacts/performance.txt)
  - Mutation checks (16 deliberate bugs, 3 of them look-ahead) → 16 caught, 0 missed · raw: [artifacts/mutation-checks.txt](artifacts/mutation-checks.txt), script [artifacts/mutate.py](artifacts/mutate.py)
  - Docstrings of the new public API → 49 public names checked, none missing; 40/40 features have a description · raw: [artifacts/docstrings.txt](artifacts/docstrings.txt)
  - Gate check (start of stage) → 1673 passed, 11 deselected · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
  - `main` merged into the branch (`7dea6a4`, main had moved to `4edc709`): brief's Verify → 339 passed; `make check` → exit 0, 2032 passed · raw: [artifacts/merge-main-into-branch-check.txt](artifacts/merge-main-into-branch-check.txt)
  - On merged `main` (`7dea6a4`, fast-forward from `4edc709`, via the owner's Terminal panel: the sandbox cannot write the main checkout): `uv run pytest -q` → 2032 passed, 11 deselected; brief's Verify → 339 passed; `make check` → exit 0; `git branch --merged main` lists the stage branch; clean tree · raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt)
- Acceptance criteria (brief):
  - [x] Every feature matches reference values within 1e-6 (or a documented tolerance) — evidence: `test_technical_features.py::test_matches_reference[group-name]` × 40 (one per feature; `rtol 1e-6`, `atol 1e-9`, identical NaN pattern, at least 60 finite reference values each) against `tests/fixtures/features/expected.csv`, written by the independent plain-Python implementation `tests/unit/quant/features/feature_reference.py` (`test_expected_csv_is_current` re-runs it: equal to 1e-10); hand-computed cases `test_*_hand_values` × 6; known beta 1.3 `test_market_known_beta_exact` (beta and idio vol to 1e-9) and `test_market_known_beta_noisy` (±0.05). Tolerance: relative 1e-6 with an absolute floor of 1e-9 for values near zero (stricter than an absolute 1e-6).
  - [x] No look-ahead (features at t use data ≤ t only, enforced by a shift test) — evidence: `test_feature_lookahead.py::test_shift_future_data_changes_nothing[name-cut]` × 120 (every feature × 3 cut dates: all later bars scrambled → every value ≤ t identical; not vacuous: later values do change), `test_truncating_the_future_changes_nothing[name]` × 40, `test_view_future_rows_never_leak` (later bars, a later split and a late-recorded dividend in the lake change nothing at the as-of; they do change the answer at a later as-of), `test_feature_snapshot.py::test_snapshot_no_lookahead`, Stage 13 leak suite on the new `DataView.sessions` (`test_future_rows_never_leak[sessions]`, `test_guard_raises_if_filter_bypassed[sessions]`); mutation checks: a gap using the next close, a centred SMA and a forward-shifted beta window are each caught.
- Brief tasks and In scope:
  - [x] Registry `@feature(name, lookback, kind="ts"|"xs")` and `compute_features(view, symbols, names)` wide or long — `test_feature_registry.py` (13), `test_feature_compute.py` (14) on `tiny_universe` (`test_tiny_universe.py`, 3)
  - [x] The brief's features (40 registered) — `test_feature_specs_match_the_plan`, `test_forty_features_registered`
  - [x] `TechnicalSnapshot` (latest values + percentile vs own history + vs universe) — `test_feature_snapshot.py` (11)
  - [x] Lookback-aware: the view loads exactly `lookback + buffer` sessions — `test_compute_features_loads_lookback_plus_buffer` (spy on `DataView.prices` / `sessions`: `max lookback + buffer + history − 1`), `test_first_value_at_lookback[name]` × 40
  - [x] Performance: 500 symbols × 2,000 days, all features, under 20 s — `test_all_features_500x2000_under_20s` (2.52 s, view load included)
- Waivers: none

**Result:** ALL CRITERIA VERIFIED

#### Phase 2 checkpoint follow-up (Tier 4: split-adjusted share volume) — 2026-11-14
- Commands run (this chat): `make check` → exit 0 (ruff clean, 393 files formatted, mypy clean on 157 files, 4241 passed in parallel and 3 timing tests alone); `uv run pytest -q` → 4244 passed, 71 deselected; integration on the main lake → 55 passed, 1 skipped (the flag-gated 2018–2024 calibration) · raw: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-final-checks.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-final-checks.txt); red first: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-red.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-red.txt); mutants: [../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-mutate.txt](../stage-23-stat-arb/artifacts/phase2-checkpoint-2026-11-11-tier4-mutate.txt)
- `tests/unit/quant/features/` passes (353).
- Acceptance criteria unchanged and still evidenced.

**Result:** ALL CRITERIA VERIFIED
