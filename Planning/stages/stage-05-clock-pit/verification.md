# Stage 05 — Clock, trading calendar & point-in-time guard · Verification evidence

Fill this in **only** with output from commands actually run in this stage's final chat (Project.md Rule 1). Save full raw outputs under `artifacts/` and quote the key lines here.

#### Verification evidence — Stage 05 — 2026-10-26
- Commands run (this chat, after the review fix pass, on the final working tree):
  - `uv run pytest -q` → 512 passed, 0 failed, 0 deselected, exit 0 · raw: [artifacts/pytest.txt](artifacts/pytest.txt)
  - `uv run ruff check .` → All checks passed! (with the TID251 wall-clock ban active) · raw: [artifacts/lint-type.txt](artifacts/lint-type.txt)
  - `uv run ruff format --check .` → 61 files already formatted · raw: [artifacts/lint-type.txt](artifacts/lint-type.txt)
  - `uv run mypy src/atlas` → Success: no issues found in 35 source files · raw: [artifacts/lint-type.txt](artifacts/lint-type.txt)
  - `uv run pytest tests/unit/core -q && uv run ruff check .` (brief's Verify) → 487 passed; All checks passed!; exit 0 · raw: [artifacts/verify.txt](artifacts/verify.txt)
  - `make check` → lint, format check, mypy clean; 512 passed; exit 0 · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
  - Stage tests verbose (`test_calendar.py`, `test_clock.py`, `test_pit.py`, `test_config.py`) → 178 passed, exit 0 · raw: [artifacts/stage-tests-verbose.txt](artifacts/stage-tests-verbose.txt)
  - Wall-clock grep over `src/` and `tests/` → the only executable call is `src/atlas/core/clock.py:106` (`datetime.now(UTC)` in `LiveClock.now`); every other hit is a docstring, a comment or a string snippet inside `test_clock.py` · raw: [artifacts/wallclock-grep.txt](artifacts/wallclock-grep.txt)
  - Docstring check → 34 public names checked (calendar, clock, pit, `CalendarConfig`); missing: none · raw: [artifacts/docstrings.txt](artifacts/docstrings.txt)
  - Follow-up on merged `main` (`47e5ddd`, fast-forward from `5979a34`): `uv run pytest -q` → 512 passed; `ruff check` → All checks passed!; `ruff format --check` → 61 files already formatted; `mypy src/atlas` → Success (35 files); `uv run pytest tests/unit/core -q && uv run ruff check .` → 487 passed, All checks passed!; `make check` → exit=0; checkout clean before and after · raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt)
  - Gate check (start of stage) → `uv run pytest -q` 426 passed · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- Acceptance criteria (brief.md):
  - [x] **A grep or ruff check shows no direct wall-clock calls outside `clock.py`.** Evidence:
    - `uv run ruff check .` → All checks passed!, with TID251 banning 16 APIs (`datetime.now/utcnow/today`, `date.today`, `time.time/time_ns/localtime/gmtime/ctime/asctime/strftime/clock_gettime/clock_gettime_ns`, `pandas.Timestamp.now/today/utcnow`) everywhere except `src/atlas/core/clock.py`.
    - `test_ruff_bans_wall_clock_outside_clock_module` → 12/12 PASSED; each case checks a `src/` path and a `tests/` path. These tests were seen failing before the rule existed (worklog, Task 3).
    - `test_ruff_allows_wall_clock_in_clock_module` PASSED.
    - `test_no_string_clock_reads_outside_clock_module` PASSED; its detector is proven by `test_string_clock_detector_flags_samples`.
    - `artifacts/wallclock-grep.txt`: the only executable call is in `clock.py`.
  - [x] **PIT boundary tests pass.** Evidence (all PASSED, `artifacts/stage-tests-verbose.txt`):
    - `test_bar_not_visible_same_morning`: the bar for D is not visible at D 08:00.
    - `test_bar_visible_next_session_morning`: the bar for D is visible at D+1 08:00.
    - `test_knows_is_strict`: data available exactly at the cutoff is not known.
    - `test_pit_filter_keeps_only_known_rows`: a row at the exact cutoff and a future row are dropped.
    - `test_injected_future_bar_is_caught`: `LookAheadError`.
    - `test_nat_rows_are_never_visible`.
    - `test_cutoff_tracks_dst`.
    - `test_bar_available_at_regular_and_early_close`.
- Brief's Tests list:
  - Holiday sessions: `test_holidays_are_not_sessions` (5 cases: 2024-07-04, Good Friday 2024-03-29, a Saturday, two sessions) and `test_early_closes` (2024-11-29, 2024-07-03, 2024-12-24).
  - Offsets across weekends: `test_offset_across_weekends_and_holidays` (6 cases, including `offset(2024-03-01, -21) == 2024-01-31`).
  - Bar-visibility boundary: as above.
  - tz-naive input rejected: `test_pit_filter_rejects_naive_column`, `test_market_date`, `test_asof_validation`, `test_simclock_rejects_bad_start`.
  - Ruff rule: as above.
- Definition of Done extras:
  - Every new config key (`calendar.start`, `calendar.end`, `calendar.decision_time`, `calendar.bar_publish_lag_minutes`) is in `config/fund.yaml` with a comment and has a default in `CalendarConfig`. Evidence: `test_every_config_key_is_written_in_yaml`, `test_every_yaml_key_has_a_comment`, `test_calendar_defaults`.
  - The loaded config drives behaviour: `test_loaded_calendar_config_changes_behaviour`.
- Waivers: none

**Result:** ALL CRITERIA VERIFIED

#### Phase 0 checkpoint follow-up (Tier 1) — 2026-11-02
Owner-requested follow-up (see worklog, 2026-11-02): the declared pandas floor now matches the pandas 3 API the code imports. Re-run in this chat on the final working tree (base commit `473629f`):
- Brief's Verify `uv run pytest tests/unit/core -q && uv run ruff check .` → 704 passed; All checks passed!; exit 0 · raw: [artifacts/checkpoint-2026-11-02-verify.txt](artifacts/checkpoint-2026-11-02-verify.txt)
- New `test_pandas_floor_is_pandas_3` PASSED (it FAILED before the fix)
- `uv run pytest -q` → 1678 passed, 0 failed, 11 deselected (live/llm/integration), exit 0 · raw: [artifacts/checkpoint-2026-11-02-pytest.txt](artifacts/checkpoint-2026-11-02-pytest.txt)
- `uv run ruff check .` → All checks passed!; `uv run ruff format --check .` → 191 files already formatted; `uv run mypy src/atlas` → Success: no issues found in 84 source files · raw: [artifacts/checkpoint-2026-11-02-lint-type.txt](artifacts/checkpoint-2026-11-02-lint-type.txt)
- `make check` → lint, format check, mypy clean; 1678 passed; exit 0 · raw: [artifacts/checkpoint-2026-11-02-make-check.txt](artifacts/checkpoint-2026-11-02-make-check.txt)
- Machine load during these runs: 5.07 → 5.33 (`uptime`) · raw: [artifacts/checkpoint-2026-11-02-load.txt](artifacts/checkpoint-2026-11-02-load.txt)
- The acceptance criterion still holds: ruff's banned-api rule finds no wall-clock call outside `clock.py`; point-in-time boundary tests pass.
- On merged `main` (`d9cffb6`, fast-forward from `473629f`; run in the owner's Terminal panel, the sandbox cannot write the main checkout): `uv run pytest -q` → 1678 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 191 files already formatted; `mypy src/atlas` → Success (84 files); 1M-row upsert → 0.73 s PASSED; `make check` → exit 0; `git branch --merged main` lists `claude/phase-one-checkpoint-28fcd7`; checkout clean before and after; all 11 commands exit 0 · raw: [stage-06 artifacts/checkpoint-2026-11-02-merged-main-check.txt](../stage-06-storage/artifacts/checkpoint-2026-11-02-merged-main-check.txt) (script: same name, `.sh`)

**Result:** ALL CRITERIA VERIFIED
