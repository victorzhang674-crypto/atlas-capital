# Stage 05 — Clock, trading calendar & point-in-time guard · Work log

Append entries in order. Record failures and dead ends as honestly as successes.

## 2026-10-26 — Planning and Task 0 (kick-off)
- Did: read `Project.md`, `PROGRESS.md`, the brief, spec §3/§11/§12/§15/§16, stage protocol §2–§5 and the briefs of Stages 06/07/09–13/55/56/61/66 that consume this stage. Wrote the broken-down plan (owner approved in chat; execution method "Native (Recommended)"; D6 "Raise an error (Recommended)").
- Ran: `UV_PROJECT_ENVIRONMENT=.venv.nosync uv sync --group dev` + `ln -s .venv.nosync .venv` (new worktree) → exit 0
- Ran (gate check): Stage 04 `verification.md` contains "ALL CRITERIA VERIFIED"; `uv run pytest -q` → 426 passed, exit 0 · raw: artifacts/gate-check-pytest.txt
- Did: copied `_template/` (`worklog.md`, `verification.md`, `artifacts/`) into this folder; `plan.md` = the approved plan with the gate check filled in; PROGRESS.md current stage → `IN PROGRESS`.
- Next: Task 1 (dependencies and `calendar` config section).

## 2026-10-26 — Task 1: dependencies and `calendar` config section
- Did: `uv add "exchange-calendars>=4.5" "pandas>=2.2"` (resolved exchange-calendars 4.13.2, pandas 3.1.0, numpy 2.5.4); `uv add --group dev "pandas-stubs>=2.2"` (3.0.5.260914); suite still 426 passed.
- RED: added `test_calendar_defaults`, `test_calendar_env_override`, three `test_invalid_config_raises` cases (start ≥ end, negative lag, tz-aware decision time) and `cfg.calendar` in `test_repo_backtest_profile_fully_populated` → `ImportError: cannot import name 'CalendarConfig'`.
- GREEN: `CalendarConfig` (`start`, `end`, `decision_time`, `bar_publish_lag_minutes`) + `AtlasConfig.calendar` + `"calendar"` in `_SECTIONS` → only `test_every_config_key_is_written_in_yaml` failed (keys missing from YAML, as expected); added the commented `calendar:` section to `config/fund.yaml` (`decision_time: "08:00"` quoted) → 97 passed.
- Ran: `uv run pytest -q` → 431 passed; `uv run mypy src/atlas` → Success (32 files); `ruff check` → All checks passed!; `ruff format --check` → 55 files already formatted.
- Files: `pyproject.toml`, `uv.lock`, `src/atlas/core/config.py`, `config/fund.yaml`, `tests/unit/core/test_config.py`.

## 2026-10-26 — Task 2: `TradingCalendar`
- RED: `tests/unit/core/test_calendar.py` (23 cases: `market_date`, holidays incl. 2024-07-04 and Good Friday 2024-03-29, inclusive ranges, next/prev, offsets across weekends/holidays incl. `offset(2024-03-01, -21) == 2024-01-31`, regular/DST/early closes, non-session and out-of-bounds errors, config default bounds) → `ImportError` (no `atlas.core.calendar`).
- GREEN: `src/atlas/core/calendar.py` — `MARKET_TZ`, `market_date`, `TradingCalendar`. Session arithmetic uses `searchsorted` on the XNYS session index; open/close/early closes come from `exchange_calendars`; the XNYS object is cached per `(start, end)` → 23 passed.
- Problems / fixes: (1) mypy: `exchange_calendars` has no `py.typed` → added the plan's `ignore_missing_imports` override in `pyproject.toml`; `get_loc` returns a union type → `offset` uses `searchsorted` on a checked session. (2) The test run printed 8 `Pandas4Warning`s raised inside `exchange_calendars` 4.13 while it builds the calendar (pandas 3.1 deprecation of `Series.values` on tz-aware data). Ruling R1: silence only that category, only around the build, in `_xnys` (comment in code).
- Ran: `uv run pytest -q` → 454 passed (no warnings); `mypy src/atlas` → Success (33 files); `ruff check` → All checks passed!; `ruff format --check` → 57 files already formatted.
- Files: `src/atlas/core/calendar.py`, `tests/unit/core/test_calendar.py`, `pyproject.toml`.

## 2026-10-26 — Task 3: clocks, contextvar and the wall-clock ban
- RED (1): `tests/unit/core/test_clock.py` (23 cases) → `ModuleNotFoundError: atlas.core.clock`.
- RED (2): with `clock.py` written but no ruff rule yet → the 5 `test_ruff_bans_wall_clock_outside_clock_module` cases FAILED (`ruff` returned 0, "All checks passed!") and `test_sim_clock_fixture` ERRORED (fixture missing); 17 passed. This proves the ban tests detect a missing rule.
- GREEN: `src/atlas/core/clock.py` — `Clock` protocol, `SimClock` (session start at the decision time, `advance(n)`, monotone `set`), `LiveClock` (the only `datetime.now` in Atlas), `get_clock()` (raises `AtlasError` with no clock — owner-confirmed D6), `use_clock()`. `pyproject.toml`: `"TID"` added to ruff `select`, nine `banned-api` entries (`datetime.now/utcnow/today`, `date.today`, `time.time/time_ns`, `pandas.Timestamp.now/today/utcnow`), `per-file-ignores` for `src/atlas/core/clock.py`. `tests/conftest.py`: shared `sim_clock` fixture (stage protocol §5) → 23 passed.
- Beyond the plan's list (additions, same behaviour): `test_simclock_advance_after_set_moves_to_next_session`, `test_simclock_accepts_aware_datetime_start`, `test_clocks_satisfy_protocol`; the ban test checks a `tests/` path as well as a `src/` path.
- Problems / fixes: `ruff check .` then reported only two E501 long lines (one in each new file); no existing file trips TID251. Shortened both.
- Ran: `uv run pytest -q` → 477 passed; `ruff check .` → All checks passed!; `ruff format` → 59 files unchanged; `mypy src/atlas` → Success (34 files).
- Files: `src/atlas/core/clock.py`, `tests/unit/core/test_clock.py`, `tests/conftest.py`, `pyproject.toml`.

## 2026-10-26 — Task 4: `AsOf` and the availability rules
- RED: `tests/unit/core/test_pit.py` (12 cases: default/custom/DST cutoffs, validation incl. a cutoff on the wrong New-York day, aware-timezone input stored as UTC, frozen + hashable, strict `knows`, bar not visible at D 08:00 / visible at D+1 08:00, bar availability on a regular day and an early close, `to_available_at`, rule table keys) → `ModuleNotFoundError: atlas.core.pit`.
- GREEN: `src/atlas/core/pit.py` — module docstring rule table, `AVAILABILITY_RULES` (`FrozenDict`), `AsOf(day, cutoff)` (`AtlasModel`, cutoff must fall on `day` in New York), `AsOf.for_session`, `AsOf.knows`, `bar_available_at` (session close + lag, collision C1), `to_available_at` → 12 passed.
- Problems / fixes: I first saved `to_available_at` with a stray placeholder line at the end of the file; caught it on re-read and fixed it before the first run (`astimezone(UTC)`). ruff E501 on a docstring table row and the test-module docstring → shortened. mypy noted the `exchange_calendars.*` override pattern as unused → narrowed it to `exchange_calendars`.
- Ran: `uv run pytest -q` → 489 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 61 files already formatted; `mypy src/atlas` → Success (35 files).
- Files: `src/atlas/core/pit.py`, `tests/unit/core/test_pit.py`, `pyproject.toml`.

## 2026-10-26 — Task 5: `pit_filter` / `assert_no_lookahead`
- RED: 11 new cases in `tests/unit/core/test_pit.py` (known rows only incl. the exact-cutoff row, empty frame, New-York-tz column, custom `time_col`, naive and non-datetime/object columns, missing column, `NaT` rows, assert passes on filtered data, assert raises with count + latest timestamp, injected future bar per stage protocol §5) → `ImportError: cannot import name 'assert_no_lookahead'`.
- GREEN: `pit_filter`, `assert_no_lookahead` and the shared `_known` mask in `src/atlas/core/pit.py` (`DataError` for a missing / non-`DatetimeTZDtype` column; strict `<` against the cutoff; `NaT` never known).
- Problems / fixes: the first GREEN run errored in test setup: pandas 3 infers one string format from the first element, and the fixture mixed `"%H:%M"` and `"%H:%M:%S"` stamps. Test-data bug, not a code bug: wrote every fixture stamp with seconds; assertions unchanged → 23 passed.
- Ran: `uv run pytest tests/unit/core -q` → 475 passed; `uv run pytest -q` → 500 passed; `ruff check .` → All checks passed!; `ruff format` → 61 files unchanged; `mypy src/atlas` → Success (35 files).
- Files: `src/atlas/core/pit.py`, `tests/unit/core/test_pit.py`.

## 2026-10-26 — Task 6: whole-branch review and fix pass
- Did: built a review package from the working-tree diff (the stage commits once at the end, so the skill's commit-range script did not apply) and had a fresh reviewer (Opus) review it against the plan, brief, spec and the five Review-focus items. Verdict "With fixes": Critical none; Important 3; Minor 5; all five focus items confirmed by tests and probes.
- Re-grade: Minor 2 (`bar_available_at` accepted a negative `lag`, so the same-day bar could become visible before the 08:00 cutoff = silent look-ahead) → Important.
- Fixes (each test written first and seen failing: 10 failed, 48 passed; then 58 passed):
  - I1, the `calendar` config had no effect (all defaults came from `CalendarConfig()`, never from the loaded config): `TradingCalendar(config=cfg.calendar)` now carries the run's config; `SimClock`'s decision time and `bar_available_at`'s lag default to `calendar.config`; `AsOf.for_session(day, config=cfg.calendar)`. Test: `test_loaded_calendar_config_changes_behaviour` (env lag 60, decision 09:30 → bar 22:00Z, cutoff 14:30Z, SimClock 14:30Z).
  - I2, ban gaps: 7 more `banned-api` entries (`time.localtime/gmtime/ctime/asctime/strftime/clock_gettime/clock_gettime_ns`) + AST scan of `src/` and `tests/` for `"now"`/`"today"` passed to `Timestamp/to_datetime/datetime64/date_range/bdate_range` (`test_no_string_clock_reads_outside_clock_module`; detector proven by `test_string_clock_detector_flags_samples`); `clock.py` docstring no longer claims a complete ban.
  - I3, `SimClock.advance()` skipped today's 08:00 decision when the clock was set before it (Stage 61 pattern): `advance(n)` = n-th session decision instant strictly after `now()`. Test: `test_simclock_advance_before_decision_runs_todays_decision`.
  - Re-graded Minor 2: `bar_available_at` raises `ValueError` on a negative lag (`test_bar_available_at_rejects_negative_lag`).
- Deferred minors → PROGRESS Backlog: pandas floor `>=2.2` vs the pandas-3-only `Pandas4Warning` import; `datetime` passed to calendar methods gives a confusing `TypeError`; `SimClock` accepts a start instant outside the calendar bounds; untested-but-working cases (Nov DST fall-back advance, early-close decision between 13:15 and 16:15 ET, duplicate index labels).
- Rulings on "declined to judge" (stand as is): ULID/structlog wall-clock reads (D11, Backlog); `AsOf.for_session` on non-session days (not required; Stage 61); vintage selection (Stages 09/10/13); `ArrowDtype` columns rejected (fails safe; Stage 06 handoff note); async `use_clock`; `SimClock` across threads; `catch_warnings` thread safety (once per bounds under `lru_cache`).
- Ran: `uv run pytest -q` → 512 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 61 files already formatted; `mypy src/atlas` → Success (35 files).
- Files changed in the fix pass: `src/atlas/core/{calendar,clock,pit}.py`, `pyproject.toml`, `tests/unit/core/test_{clock,pit}.py`.

## 2026-10-26 — Task 6: verification and records
- Ran (final working tree, raw output in `artifacts/`): `uv run pytest -q` → 512 passed (pytest.txt); brief's Verify `uv run pytest tests/unit/core -q && uv run ruff check .` → 487 passed, All checks passed! (verify.txt); `ruff check .` / `ruff format --check .` / `mypy src/atlas` → clean, 61 files, 35 source files (lint-type.txt); `make check` → exit 0 (make-check.txt); stage tests verbose → 178 passed (stage-tests-verbose.txt); wall-clock grep → only `clock.py:106` executes a wall-clock call (wallclock-grep.txt); docstring check → 34 public names, none missing (docstrings.txt).
- Did: wrote `verification.md` (ALL CRITERIA VERIFIED); PROGRESS.md: Stage 05 ticked and `DONE`, 5/66, current stage 06 `NOT STARTED`, Decisions log rows (C1, D1–D11 with review amendments, R1, review fix pass), four Backlog minors, handoff note + evidence summary; `plan.md` status `DONE`, step boxes ticked, post-review amendments table appended.
- Code and test files created or changed in this stage: `src/atlas/core/calendar.py`, `src/atlas/core/clock.py`, `src/atlas/core/pit.py`, `src/atlas/core/config.py`, `config/fund.yaml`, `pyproject.toml`, `uv.lock`, `tests/conftest.py`, `tests/unit/core/test_calendar.py`, `tests/unit/core/test_clock.py`, `tests/unit/core/test_pit.py`, `tests/unit/core/test_config.py`.
- Next: commit `stage-05: Clock, trading calendar & point-in-time guard`.

## 2026-10-26 — Commit, rebase onto main, fast-forward and merged-main check
- Did: committed `stage-05: Clock, trading calendar & point-in-time guard` (`64ae655`) after a final `make check` (exit 0, 512 passed); deleted the scratch review workspace (`.superpowers/sdd/plan`, git-ignored).
- Owner asked: "Fast-forward main to the stage-05 commit, like Stages 02–04, and record the merged-main check."
- Problem: `main` had moved to `5979a34 stage-04: correct model count in records` (records-only) after this branch was cut, so a fast-forward was impossible. The repo has no `origin` remote, so the app's sync tool (fetches from `origin`) could not be used. Rebased the unpushed stage commit onto `main`: one conflict in `PROGRESS.md` › Decisions log (each side appended rows at the same place) → kept both, Stage 04 row first. New stage commit `47e5ddd`; `make check` on the rebased branch → exit 0, 512 passed.
- Ran (owner's Terminal panel; the sandbox cannot write the main checkout): `artifacts/merged-main-check.sh` → `git merge --ff-only` 5979a34..47e5ddd, `uv sync --group dev` (installs exchange-calendars/pandas there), then on merged `main`: `uv run pytest -q` → 512 passed; `ruff check` → All checks passed!; `ruff format --check` → 61 files already formatted; `mypy src/atlas` → Success (35 files); brief's Verify → 487 passed, All checks passed!; `make check` → exit=0; `git status` clean before and after · raw: artifacts/merged-main-check.txt
- Did: records-only follow-up commit `stage-05: follow-up records` (verification.md and PROGRESS.md evidence lines, Decisions log row, this entry), then fast-forward `main` to it.

## 2026-10-26 — Post-DONE audit: plan test names vs code (records only)
- Owner asked: "According to the plans, please verify whether all the work for Stage 5 has been completed or not." Re-ran on `206366e` (= `main`): `uv run pytest -q` → 512 passed; `ruff check .` → All checks passed!; `ruff format --check .` → 61 files already formatted; `mypy src/atlas` → Success (35 files); brief's Verify → 487 passed, All checks passed!; `make check` → exit 0. The brief's Key-interfaces examples run as written. Result: Stage 05 complete; `DONE` stands.
- Finding (records only, no behaviour gap): of the 55 test names in `plan.md`, 52 exist verbatim; three are covered under other names:
  - `test_calendar_config_validation` (plan Task 1) → three parametrized cases of `test_invalid_config_raises` in `tests/unit/core/test_config.py` (start ≥ end, negative `bar_publish_lag_minutes`, tz-aware `decision_time`), following the plan's "match the existing pattern" note; already mentioned in the Task 1 entry.
  - `test_simclock_rejects_bad_input` (plan Task 3) → split into `test_simclock_rejects_bad_start` (non-session start date, naive start datetime) and `test_simclock_rejects_bad_moves` (`advance(0)`, `set` backwards, `set` naive, clock unchanged afterwards) in `tests/unit/core/test_clock.py`; same cases, not logged in the Task 3 entry.
  - `test_bar_available_at_early_close` (plan Review focus 2) → `test_bar_available_at_regular_and_early_close` in `tests/unit/core/test_pit.py` (the name plan Task 4 itself uses; asserts 2024-11-29 → 18:15 UTC); the plan used two names for one test.
- Changes: this entry only; no code, test or other record changed.

## 2026-11-02 — Phase 0 checkpoint follow-up (Tier 1): pandas floor raised to 3.0
- Owner's words, after the Phase 0 checkpoint report: "Start with Tier 1". Backlog item "(Stage 05 review, minor) `pyproject.toml` declares `pandas>=2.2`, but `atlas.core.calendar` imports `pandas.errors.Pandas4Warning` (pandas 3 only)".
- Red: new `tests/unit/test_dependency_floors.py::test_pandas_floor_is_pandas_3` FAILED: `assert (2, 2) >= (3, 0)`.
- Green: `pyproject.toml` now says `pandas>=3.0` and (dev group) `pandas-stubs>=3.0`; `uv lock` changed only those two specifiers in `uv.lock` (pandas 3.1.0 and pandas-stubs 3.0.5.260914 stay locked; no package version moved). The test passes.
- Brief's Verify `uv run pytest tests/unit/core -q && uv run ruff check .` → 704 passed; All checks passed! (`artifacts/checkpoint-2026-11-02-verify.txt`).
- Final runs (raw: `artifacts/checkpoint-2026-11-02-*.txt`): `uv run pytest -q` → 1678 passed, 0 failed, 11 deselected; `ruff check .` → All checks passed!; `ruff format --check .` → 191 files already formatted; `mypy src/atlas` → Success (84 files); `make check` → exit 0. The full-suite runs end with the known `nanobind: leaked …` lines (Backlog); exit codes 0.
- Base commit `473629f`, branch `claude/phase-one-checkpoint-28fcd7`; one commit for all five Tier 1 items (Stages 02, 03, 05, 06, 14), each recorded in its own stage folder.
- Files changed: `pyproject.toml`, `uv.lock`; tests: `tests/unit/test_dependency_floors.py` (new).
