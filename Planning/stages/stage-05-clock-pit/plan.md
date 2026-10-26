# Stage 05 — Clock, trading calendar & point-in-time guard · Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking; tick only after the step's tests have run and passed in this chat.

- **Brief:** [brief.md](brief.md) (`Planning/stages/stage-05-clock-pit/brief.md`)
- **Status:** DONE (2026-10-26; see [verification.md](verification.md))
- **Gate check:** Stage 04 is DONE (its [verification.md](../stage-04-domain-model/verification.md) says ALL CRITERIA VERIFIED; evidence block also in `PROGRESS.md`); `uv run pytest -q` → 426 passed, exit 0 (2026-10-26) · raw: [artifacts/gate-check-pytest.txt](artifacts/gate-check-pytest.txt)
- **Execution method:** executing-plans (native, inline TDD), chosen by the owner in chat 2026-10-26 ("Native (Recommended)"); one whole-branch review by a fresh reviewer before DONE.
- **Owner decisions in chat (2026-10-26):** plan approved; D6 confirmed — `get_clock()` raises when no clock is installed ("Raise an error (Recommended)").

## Context

The owner asked for a fully broken-down Stage 05 plan that the assistant then executes. Stage 04 is `DONE` (4/66). Stage 05 creates the single source of "now" (`atlas.core.clock`), real NYSE trading-day arithmetic (`atlas.core.calendar`) and the point-in-time primitives (`atlas.core.pit`) that make look-ahead bias impossible by construction (spec §3 principle 2). Later consumers checked: Stage 06 (required `available_at` column), 07 (`available_at` = session close + 15 min, "calendar from Stage 05"), 09/10/11 (filing / vintage / publish timestamps), 13 (`DataView` applies `pit_filter`, LRU keyed by `as_of` → must be hashable), 55/56 (`SimClock` loops sessions), 61 (08:00 ET decision, holidays, early closes, SimClock-driven tests), 66 (clock-jump chaos test).

**Goal:** `Clock`/`SimClock`/`LiveClock` + contextvar, an XNYS `TradingCalendar`, `AsOf`, the availability rule table, `pit_filter` and `assert_no_lookahead`, plus a ruff rule that bans wall-clock calls outside `core/clock.py`.

**Architecture:** `calendar.py` wraps `exchange_calendars` XNYS behind `date`-in / UTC-`datetime`-out methods (cached per bounds). `clock.py` holds the `Clock` protocol, `SimClock` (moves session to session at the decision time) and `LiveClock` (the only wall-clock call in Atlas), installed per context with `use_clock()`. `pit.py` holds `AsOf` (frozen `AtlasModel`), the availability rules, and the DataFrame filter/guard. Dependency direction: `config` ← `calendar` ← `clock`, `pit` (pit never imports clock).

**Tech stack:** `exchange-calendars` (XNYS), `pandas` (+ `pandas-stubs` for mypy strict), stdlib `zoneinfo`, `contextvars`; ruff `flake8-tidy-imports` banned-api (TID251).

**Spec:** brief above; design spec §3 principles 2 (point-in-time) and 6 (fail safe), §11 (exchange_calendars), §12 (`core/` holds clock, calendar), §16 (look-ahead mitigation); stage protocol §4 (time rule, errors, config), §5 (`sim_clock` shared fixture, look-ahead tests).

## Global constraints

- Internal datetimes are tz-aware **UTC**; trading days are `date`; the market timezone is `America/New_York` (`MARKET_TZ`). Docstrings state units/timezones.
- mypy strict on `atlas.core.*` (already configured). `AsOf` crosses packages → frozen Pydantic (`AtlasModel`); internal values may be frozen dataclasses.
- Nothing hard-coded: decision time, bar publish lag and calendar bounds come from a new `calendar` config section (defaults in code, restated with comments in `config/fund.yaml`).
- `datetime.now`/`utcnow`/`today`, `date.today`, `time.time`/`time_ns`, `pandas.Timestamp.now/today/utcnow` appear **only** in `src/atlas/core/clock.py`.
- Errors: `LookAheadError` for visibility violations, `DataError` for malformed PIT input (missing column, naive/non-datetime column), `AtlasError` for "no clock installed", `ValueError` for programming errors (non-session date, out-of-bounds date, clock moved backwards, naive datetime argument). Never swallow.
- No network in unit tests (`exchange_calendars` is offline/rule-based).
- Test basenames unique (no `__init__.py`): `test_calendar.py`, `test_clock.py`, `test_pit.py`.
- **One commit for the stage:** `stage-05: Clock, trading calendar & point-in-time guard` (+ attribution trailer), after DONE. No per-task commits.

## Collisions with the overall plan

- **C1 (brief vs brief, same rank):** Stage 05's rule says "a daily bar for D is available at D 16:00 ET + 15 min"; Stage 07 says "`available_at` = session close + 15 min (calendar from Stage 05)". **Resolution:** `bar_available_at(D) = session_close(D) + lag` — identical (16:15 ET) on every regular session, and correct on early closes (13:15 ET), which is what Stage 07 consumes. Log in Decisions log.
- None with the roadmap or design spec.

## Design decisions (also logged in PROGRESS.md › Decisions log)

| # | Decision | Why |
|---|---|---|
| D1 | New config section `calendar` (`CalendarConfig`): `start: date = 2000-01-01`, `end: date = 2030-12-31`, `decision_time: time = 08:00` (naive, read as ET), `bar_publish_lag_minutes: int = 15` (≥0). Validator: `start < end`, `decision_time.tzinfo is None`. YAML value quoted (`"08:00"`) so YAML never reads it as base-60. | Stage protocol: no hard-coded thresholds; pinned bounds keep `exchange_calendars` from using its wall-clock default (today − 20 y). |
| D2 | `TradingCalendar` takes and returns `date`; timestamps are UTC-aware `datetime`. `next_session`/`prev_session` are strictly after/before `d` (d need not be a session). `offset(d, n)` requires `d` to be a session (else `ValueError`); `offset(d, 0) == d`. Dates outside `[start, end]` → `ValueError`. Underlying XNYS object cached per `(start, end)`. | One unambiguous convention; non-session offsets are ambiguous, so they fail loudly. |
| D3 | `Clock` protocol: `now() -> datetime` (UTC-aware) and `today() -> date` (market date in New York). `market_date(ts)` lives in `calendar.py` and is the shared helper. | Callers never think about time zones. |
| D4 | `SimClock(start: date \| datetime, *, calendar=None, decision_time=None)`: a `date` must be a session (else `ValueError`) and puts `now()` at that session's decision time (ET → UTC); an aware `datetime` is used as is (naive → `ValueError`). `advance(n=1)` (n ≥ 1) jumps to the n-th next session at the decision time and returns the new `now()`. `set(ts)` requires aware, non-decreasing `ts` (backwards → `ValueError`). | A backtest clock that moves backwards is a look-ahead hazard; Stage 61 needs intra-day `set`. |
| D5 | `LiveClock.now()` = `datetime.now(UTC)` — the only wall-clock call in Atlas. | Brief + ban rule. |
| D6 | `get_clock()` raises `AtlasError` when no clock is installed (no silent `LiveClock` default; owner-confirmed 2026-10-26); `use_clock(clock)` is a nesting context manager that restores the previous clock even on exception. | Fail safe: a backtest that forgot `use_clock` must crash, not read wall time. |
| D7 | `AsOf(day: date, cutoff: UtcDatetime)` frozen `AtlasModel`, hashable; validator: `market_date(cutoff) == day`. `for_session(day, decision_time=None)` builds cutoff = `day` at decision time ET (default from `CalendarConfig()`). `knows(ts)` ⇔ `ts < cutoff` (strict; naive `ts` → `ValueError`). Field is `day`, not `date`, to avoid shadowing the type. | Brief: data known **strictly before** the cutoff; Stage 13 caches by `as_of`. |
| D8 | Availability rule table in `pit.py`: `AVAILABILITY_RULES` (`daily_bar`, `sec_filing`, `fred_observation`, `news` → one-line rule each) + module docstring table; `bar_available_at(day, calendar, lag=None)` (C1); `to_available_at(ts)` = tz-aware → UTC (naive → `ValueError`) for the timestamp-based sources (filing `accepted`, FRED release time, news publish). | Brief: "documented in code"; later stages own vendor details. |
| D9 | `pit_filter(df, as_of, time_col="available_at")` returns a **copy** of the rows with `time_col < as_of.cutoff` (NaT rows dropped), index and columns preserved. `assert_no_lookahead(df, as_of, time_col=...)` raises `LookAheadError` (naming count and the latest offending timestamp) iff `pit_filter` would drop any row (NaT counts as a violation). Missing column or a column that is not `DatetimeTZDtype` → `DataError`. | One definition of "known"; unknown availability is never treated as known. |
| D10 | Ban via ruff TID251 `banned-api` + `per-file-ignores` for `src/atlas/core/clock.py`; DTZ not enabled (it would flag intentional naive datetimes in existing tests). | Brief: "ruff banned-API rule". |
| D11 | Out of scope, stays in Backlog: routing ULID ids / `AuditEvent.ts` / structlog timestamps through the clock (Stage 03 D2, Stage 04 backlog row). | Not in the brief's In-scope list (Rule 2.4). |

## File structure

| File | Responsibility |
|---|---|
| `src/atlas/core/config.py` (modify) | `CalendarConfig`; `AtlasConfig.calendar`; `"calendar"` in `_SECTIONS` |
| `config/fund.yaml` (modify) | `calendar:` section with comments |
| `src/atlas/core/calendar.py` (create) | `MARKET_TZ`, `market_date`, `TradingCalendar` |
| `src/atlas/core/clock.py` (create) | `Clock`, `SimClock`, `LiveClock`, `get_clock`, `use_clock` |
| `src/atlas/core/pit.py` (create) | `AsOf`, `AVAILABILITY_RULES`, `bar_available_at`, `to_available_at`, `pit_filter`, `assert_no_lookahead` |
| `tests/unit/core/test_{calendar,clock,pit}.py` (create); `tests/unit/core/test_config.py` (modify) | Tests |
| `tests/conftest.py` (modify) | `sim_clock` shared fixture (stage protocol §5) |
| `pyproject.toml`, `uv.lock` (modify) | deps `exchange-calendars>=4.5`, `pandas>=2.2`; dev `pandas-stubs>=2.2`; ruff TID251 config |

## Review focus

1. **DST:** the same 08:00 ET cutoff / 16:00 ET close is a different UTC hour either side of 2024-03-10 → `test_cutoff_tracks_dst` (Task 4), `test_session_close_tracks_dst` (Task 2).
2. **Early close:** a bar on 2024-11-29 is available at 13:15 ET, not 16:15 → `test_bar_available_at_early_close` (Task 4).
3. **Mixed time zones in data:** an `available_at` column in `America/New_York` must compare correctly against a UTC cutoff; naive or object columns rejected → `test_pit_filter_mixed_timezones`, `test_pit_filter_rejects_naive_column`, `test_pit_filter_rejects_non_datetime_column` (Task 5).
4. **Unknown availability:** `NaT` rows are hidden by `pit_filter` and make `assert_no_lookahead` raise → `test_nat_rows_are_never_visible` (Task 5).
5. **Clock context isolation:** a new thread sees no clock (raises), `copy_context()` propagates it, nested `use_clock` restores after an exception → `test_new_thread_has_no_clock`, `test_copy_context_propagates_clock`, `test_use_clock_restores_after_exception` (Task 3).

---

### Task 0: Kick-off (gate check, stage folder, plan) — records only

- [x] **Step 1:** Environment for this worktree: `UV_CACHE_DIR="$TMPDIR/uv-cache" UV_PROJECT_ENVIRONMENT=.venv.nosync uv sync --group dev` then `ln -s .venv.nosync .venv` (PROGRESS handoff note; needs PyPI).
- [x] **Step 2: Gate check.** Confirm PROGRESS.md shows Stage 04 `DONE` and `stage-04-domain-model/verification.md` says ALL CRITERIA VERIFIED. Run `uv run pytest -q 2>&1 | tee Planning/stages/stage-05-clock-pit/artifacts/gate-check-pytest.txt` → expect `426 passed`. If red: stop, report, do not start Stage 05.
- [x] **Step 3:** Copy `Planning/stages/_template/{plan.md,worklog.md,verification.md,artifacts/}` into `Planning/stages/stage-05-clock-pit/`; replace `plan.md` with this plan (gate-check line filled in). Set Stage 05 `IN PROGRESS` in PROGRESS.md (current-stage line). Log the kick-off in `worklog.md`.

### Task 1: Dependencies and `calendar` config section

**Files:** modify `pyproject.toml`, `uv.lock`, `src/atlas/core/config.py`, `config/fund.yaml`, `tests/unit/core/test_config.py`.

**Interfaces — Produces:** `CalendarConfig(start: date, end: date, decision_time: time, bar_publish_lag_minutes: int)` (frozen `_Frozen`), `AtlasConfig.calendar: CalendarConfig`.

- [x] **Step 1:** `uv add "exchange-calendars>=4.5" "pandas>=2.2"` and `uv add --group dev "pandas-stubs>=2.2"`; `uv run pytest -q` still 426 passed.
- [x] **Step 2: Failing tests** in `test_config.py`:
  - `test_calendar_defaults` — `AtlasConfig().calendar == CalendarConfig(start=date(2000,1,1), end=date(2030,12,31), decision_time=time(8,0), bar_publish_lag_minutes=15)`.
  - `test_calendar_config_validation` — parametrized: `start >= end`, `bar_publish_lag_minutes=-1`, `decision_time` with `tzinfo=UTC` → `ConfigError` via `load_config(overrides=...)` (match the existing `test_invalid_config_raises` pattern).
  - `test_calendar_env_override` — `ATLAS__CALENDAR__BAR_PUBLISH_LAG_MINUTES=20` → `20` (section accepted by the env guard).
  - Extend `test_repo_backtest_profile_fully_populated` to also compare `cfg.calendar == defaults.calendar` (pins the quoted `"08:00"` → `time(8,0)`).
- [x] **Step 3: Run** `uv run pytest tests/unit/core/test_config.py -q` → FAIL (`ImportError: CalendarConfig`).
- [x] **Step 4: Implement** `CalendarConfig` + field + `_SECTIONS`; add to `config/fund.yaml`, every key commented:
  ```yaml
  # Trading calendar and point-in-time timing (Stage 05).
  calendar:
    start: 2000-01-01  # first date the XNYS calendar covers
    end: 2030-12-31  # last date the XNYS calendar covers
    decision_time: "08:00"  # daily decision cutoff, America/New_York (pre-market)
    bar_publish_lag_minutes: 15  # a daily bar is known this long after the session close
  ```
- [x] **Step 5: Run** test_config → PASS (incl. `test_every_config_key_is_written_in_yaml`, `test_every_yaml_key_has_a_comment`); full suite green; `uv run mypy src/atlas` → Success.

### Task 2: `TradingCalendar` (brief task 1)

**Files:** create `src/atlas/core/calendar.py`, `tests/unit/core/test_calendar.py`.

**Interfaces:**
- Consumes: `CalendarConfig` (bounds defaults).
- Produces: `MARKET_TZ: ZoneInfo = ZoneInfo("America/New_York")`; `market_date(ts: datetime) -> date` (New-York calendar date of an aware ts; naive → `ValueError`); `class TradingCalendar` with `__init__(self, start: date | None = None, end: date | None = None)`, `is_session(d: date) -> bool`, `sessions(start: date, end: date) -> tuple[date, ...]` (inclusive), `next_session(d) -> date`, `prev_session(d) -> date`, `offset(d: date, n: int) -> date`, `session_open(d) -> datetime`, `session_close(d) -> datetime` (UTC-aware), `is_early_close(d) -> bool`.

- [x] **Step 1: Failing tests** (exact values):
  - `test_market_date` — `market_date(datetime(2024,3,1,3,0,tzinfo=UTC)) == date(2024,2,29)`; naive → `ValueError`.
  - `test_holidays_are_not_sessions` — `2024-07-04`, `2024-03-29` (Good Friday), `2024-03-30` (Sat) → `False`; `2024-07-03`, `2024-03-28` → `True`.
  - `test_sessions_inclusive_range` — `sessions(2024-03-28, 2024-04-02) == (3-28, 4-01, 4-02)`; `sessions(2024-03-30, 2024-03-31) == ()`.
  - `test_next_prev_session` — `next_session(2024-03-28) == 2024-04-01`, `next_session(2024-03-30) == 2024-04-01`, `prev_session(2024-04-01) == 2024-03-28`, `prev_session(2024-03-30) == 2024-03-28`.
  - `test_offset_across_weekends_and_holidays` — `offset(2024-03-01, 1) == 2024-03-04`; `offset(2024-03-04, -1) == 2024-03-01`; `offset(2024-04-01, -1) == 2024-03-28`; `offset(2024-07-03, 1) == 2024-07-05`; `offset(2024-03-01, -21) == 2024-01-31`; `offset(d, 0) == d`.
  - `test_offset_rejects_non_session` — `offset(2024-03-30, 1)` → `ValueError`.
  - `test_regular_open_close` — `session_open(2024-03-01) == datetime(2024,3,1,14,30,tzinfo=UTC)`, `session_close(2024-03-01) == datetime(2024,3,1,21,0,tzinfo=UTC)`; `is_early_close(2024-03-01) is False`.
  - `test_session_close_tracks_dst` — `session_close(2024-03-11) == datetime(2024,3,11,20,0,tzinfo=UTC)`.
  - `test_early_closes` — `session_close(2024-11-29) == datetime(2024,11,29,18,0,tzinfo=UTC)` and `is_early_close` True; same for `2024-07-03` (`17:00 UTC`) and `2024-12-24` (`18:00 UTC`).
  - `test_open_close_reject_non_session` — `session_close(2024-07-04)` → `ValueError`.
  - `test_out_of_bounds_raises` — `TradingCalendar(start=date(2020,1,1), end=date(2020,12,31)).is_session(date(2021,1,4))` → `ValueError`.
  - `test_default_bounds_from_config` — `TradingCalendar().is_session(date(2000,1,3))` True; `.is_session(date(2030,12,31))` True.
- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_calendar.py -q` → FAIL (`ModuleNotFoundError`).
- [x] **Step 3: Implement** with `exchange_calendars.get_calendar("XNYS", start=..., end=...)` behind a module `functools.lru_cache` keyed by `(start, end)`; convert `pd.Timestamp` → `date` / `.to_pydatetime()` (UTC). Bound check before every query. If mypy reports missing stubs / py.typed for `exchange_calendars`, add a `[[tool.mypy.overrides]] module = ["exchange_calendars.*"] ignore_missing_imports = true` block.
- [x] **Step 4: Run** → PASS; `uv run mypy src/atlas` → Success.

### Task 3: Clocks, contextvar and the wall-clock ban (brief task 2)

**Files:** create `src/atlas/core/clock.py`, `tests/unit/core/test_clock.py`; modify `pyproject.toml` (ruff), `tests/conftest.py`.

**Interfaces:**
- Consumes: `TradingCalendar`, `MARKET_TZ`, `market_date`, `CalendarConfig`, `AtlasError`.
- Produces: `class Clock(Protocol)`: `now() -> datetime`, `today() -> date`; `SimClock(start: date | datetime, *, calendar: TradingCalendar | None = None, decision_time: time | None = None)` with `now()`, `today()`, `advance(n: int = 1) -> datetime`, `set(ts: datetime) -> None`; `LiveClock()`; `get_clock() -> Clock`; `use_clock(clock: Clock) -> AbstractContextManager[Clock]`; fixture `sim_clock` (SimClock at `date(2024,3,1)`, installed via `use_clock`).

- [x] **Step 1: Failing tests:**
  - `test_simclock_starts_at_decision_time` — `SimClock(start=date(2020,1,2)).now() == datetime(2020,1,2,13,0,tzinfo=UTC)`; `.today() == date(2020,1,2)`.
  - `test_simclock_advance_skips_weekend_and_holiday` — start `2024-03-28`; `advance()` returns `datetime(2024,4,1,12,0,tzinfo=UTC)` (Good Friday skipped, EDT); `advance(2)` from `2024-03-01` → `2024-03-05 13:00 UTC`.
  - `test_simclock_custom_decision_time` — `decision_time=time(17,30)` on `2024-03-01` → `22:30 UTC`.
  - `test_simclock_rejects_bad_input` — start `2024-03-30` → `ValueError`; naive datetime start → `ValueError`; `advance(0)` → `ValueError`; `set(earlier)` → `ValueError`; `set(naive)` → `ValueError`.
  - `test_simclock_set_moves_forward` — `set(datetime(2024,3,1,21,15,tzinfo=UTC))` then `now()` equals it.
  - `test_liveclock_is_utc_and_monotone` — two `LiveClock().now()` calls: `tzinfo == UTC`, first ≤ second; `today() == market_date(now())` checked via a fixed ts through `market_date` only.
  - `test_get_clock_without_clock_raises` — `AtlasError`.
  - `test_use_clock_installs_and_nests` — outer/inner `use_clock`; `get_clock()` is inner inside, outer after.
  - `test_use_clock_restores_after_exception`, `test_new_thread_has_no_clock` (thread calls `get_clock()` → raises), `test_copy_context_propagates_clock`.
  - `test_sim_clock_fixture` — the fixture's clock `is get_clock()` and `.today() == date(2024,3,1)`.
  - `test_ruff_bans_wall_clock_outside_clock_module` — parametrized snippets: `from datetime import datetime\ndatetime.now()`, `from datetime import date\ndate.today()`, `import time\ntime.time()`, `import datetime as dt\ndt.datetime.utcnow()`, `import pandas as pd\npd.Timestamp.now()`; run `[sys.executable, "-m", "ruff", "check", "--no-cache", "--select", "TID251", "--stdin-filename", "src/atlas/data/example.py", "-"]` with `cwd=repo_root`, `input=snippet` → returncode 1 and `"TID251"` in stdout.
  - `test_ruff_allows_wall_clock_in_clock_module` — same command with `--stdin-filename src/atlas/core/clock.py` and the first snippet → returncode 0.
- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_clock.py -q` → FAIL (`ModuleNotFoundError`; ban tests fail because TID251 is not configured).
- [x] **Step 3: Implement** `clock.py` per D3–D6 (`_CLOCK: ContextVar[Clock | None]`, set/reset with token in `finally`). Add to `pyproject.toml`: `"TID"` in `select`; `[tool.ruff.lint.flake8-tidy-imports.banned-api]` entries for `datetime.datetime.now`, `datetime.datetime.utcnow`, `datetime.datetime.today`, `datetime.date.today`, `time.time`, `time.time_ns`, `pandas.Timestamp.now`, `pandas.Timestamp.today`, `pandas.Timestamp.utcnow`, each `msg = "Use atlas.core.clock.get_clock() instead"`; `[tool.ruff.lint.per-file-ignores] "src/atlas/core/clock.py" = ["TID251"]`. Add `sim_clock` fixture to `tests/conftest.py`.
- [x] **Step 4: Run** test_clock → PASS; `uv run ruff check .` → All checks passed (whole repo, proves no existing violations); mypy → Success.

### Task 4: `AsOf` and the availability rules (brief task 3)

**Files:** create `src/atlas/core/pit.py`, `tests/unit/core/test_pit.py`.

**Interfaces:**
- Consumes: `AtlasModel`, `UtcDatetime` (`atlas.core.types.base`), `TradingCalendar`, `MARKET_TZ`, `market_date` (all from `calendar.py`; pit never imports clock), `CalendarConfig`.
- Produces: `AsOf(day: date, cutoff: UtcDatetime)` with `for_session(day: date, decision_time: time | None = None) -> AsOf` (classmethod) and `knows(ts: datetime) -> bool`; `AVAILABILITY_RULES: Final[Mapping[str, str]]`; `bar_available_at(day: date, calendar: TradingCalendar, lag: timedelta | None = None) -> datetime`; `to_available_at(ts: datetime) -> datetime`.

- [x] **Step 1: Failing tests:**
  - `test_for_session_default_cutoff` — `AsOf.for_session(date(2024,3,1)).cutoff == datetime(2024,3,1,13,0,tzinfo=UTC)`; `.day == date(2024,3,1)`.
  - `test_cutoff_tracks_dst` — `for_session(date(2024,3,11)).cutoff == datetime(2024,3,11,12,0,tzinfo=UTC)`.
  - `test_asof_validation` — naive cutoff → `ValidationError`; cutoff whose New-York date ≠ `day` → `ValidationError`.
  - `test_asof_is_frozen_and_hashable` — assignment raises; equal objects hash equal; usable as a dict key.
  - `test_knows_is_strict` — `as_of.knows(as_of.cutoff) is False`; `knows(cutoff - 1µs) is True`; naive → `ValueError`.
  - `test_bar_not_visible_same_morning` — `as_of = for_session(2024-03-01)`; `as_of.knows(bar_available_at(2024-03-01, cal)) is False`.
  - `test_bar_visible_next_session_morning` — `for_session(2024-03-04).knows(bar_available_at(2024-03-01, cal)) is True`.
  - `test_bar_available_at_regular_and_early_close` — regular `2024-03-01` → `21:15 UTC`; `2024-11-29` → `18:15 UTC`; `lag=timedelta(0)` → session close.
  - `test_to_available_at` — NY-aware ts → same instant in UTC (`tzinfo == UTC`); naive → `ValueError`.
  - `test_availability_rules_cover_all_sources` — `set(AVAILABILITY_RULES) == {"daily_bar", "sec_filing", "fred_observation", "news"}`; each value non-empty.
- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_pit.py -q` → FAIL (`ModuleNotFoundError`).
- [x] **Step 3: Implement** per D7–D8 and C1; module docstring carries the rule table (source → `available_at` rule).
- [x] **Step 4: Run** → PASS; mypy → Success.

### Task 5: `pit_filter` / `assert_no_lookahead` (brief task 4)

**Files:** modify `src/atlas/core/pit.py`, `tests/unit/core/test_pit.py`.

**Interfaces — Produces:** `pit_filter(df: pd.DataFrame, as_of: AsOf, time_col: str = "available_at") -> pd.DataFrame`; `assert_no_lookahead(df: pd.DataFrame, as_of: AsOf, time_col: str = "available_at") -> None`.

Fixture: a 4-row frame for `as_of = AsOf.for_session(2024-03-04)` (cutoff `13:00 UTC`) with `available_at` = `[2024-03-01 21:15, 2024-03-04 12:59:59, 2024-03-04 13:00 (== cutoff), 2024-03-04 21:15]` UTC, index `["a","b","c","d"]`.

- [x] **Step 1: Failing tests:**
  - `test_pit_filter_keeps_only_known_rows` — result index `["a","b"]`, columns unchanged, input frame unchanged, result is not the same object.
  - `test_pit_filter_empty_frame` — empty frame with a UTC `available_at` column → empty, same columns.
  - `test_pit_filter_mixed_timezones` — same instants stored in `America/New_York` → same rows kept.
  - `test_pit_filter_custom_time_col` — `time_col="accepted_at"` works.
  - `test_pit_filter_rejects_naive_column` — naive datetime64 column → `DataError` mentioning the column.
  - `test_pit_filter_rejects_non_datetime_column` — strings / object column → `DataError`.
  - `test_missing_time_col_raises` — `DataError` naming `available_at`.
  - `test_nat_rows_are_never_visible` — a `NaT` row is dropped by `pit_filter` and makes `assert_no_lookahead` raise `LookAheadError`.
  - `test_assert_no_lookahead_passes_on_filtered` — `assert_no_lookahead(pit_filter(df, as_of), as_of)` returns `None`.
  - `test_assert_no_lookahead_raises_on_future_row` — full fixture → `LookAheadError` whose message contains `"2"` (count) and the latest offending timestamp `2024-03-04 21:15`.
  - `test_injected_future_bar_is_caught` — stage-protocol §5 look-ahead test: append a bar for `2024-03-04` (`available_at = bar_available_at(...)`) to past bars → `assert_no_lookahead(df, for_session(2024-03-04))` raises; `pit_filter` drops it.
- [x] **Step 2: Run** `uv run pytest tests/unit/core/test_pit.py -q` → FAIL (`ImportError: pit_filter`).
- [x] **Step 3: Implement** per D9 (vectorised boolean mask; `isinstance(df[col].dtype, pd.DatetimeTZDtype)` check).
- [x] **Step 4: Run** → PASS; `uv run pytest tests/unit/core -q` → PASS; mypy → Success.

### Task 6: Review, verification and records

- [x] **Step 1:** Whole-branch review by a fresh reviewer (superpowers:requesting-code-review); fix findings test-first; log rulings in `worklog.md`.
- [x] **Step 2: Verify** (save each raw output under `artifacts/`):
  - `uv run pytest tests/unit/core -q && uv run ruff check .` (brief Verify) → `verify.txt`
  - `uv run pytest -q` → `pytest.txt` (expect ≈ 426 + new tests, 0 failed)
  - `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/atlas` → `lint-type.txt`
  - `make check` → `make-check.txt` (exit 0)
  - Wall-clock grep: `grep -rnE "datetime\.now|date\.today|\.utcnow|time\.time\(|Timestamp\.(now|today)" src tests --include=*.py` → only `src/atlas/core/clock.py` and the ruff-ban snippets in `tests/unit/core/test_clock.py` → `wallclock-grep.txt`
  - `uv run pytest tests/unit/core/test_pit.py tests/unit/core/test_calendar.py tests/unit/core/test_clock.py -v` → `stage-tests-verbose.txt` (boundary-test names for evidence)
  - Docstring check for new public names → `docstrings.txt`
- [x] **Step 3:** Write `verification.md` (Project.md §1.5 block, **ALL CRITERIA VERIFIED** only if true). Acceptance criteria evidence: (a) "no direct wall-clock calls outside `clock.py`" → `ruff check .` clean with TID251 + `test_ruff_bans_wall_clock_outside_clock_module` + `wallclock-grep.txt`; (b) "PIT boundary tests pass" → `test_bar_not_visible_same_morning`, `test_bar_visible_next_session_morning`, `test_knows_is_strict`, `test_pit_filter_keeps_only_known_rows`.
- [x] **Step 4:** PROGRESS.md: tick 05, status `DONE`, stages done 5/66, handoff note (built, key interfaces, decisions, collisions C1, backlog, next-stage notes for Stage 06: `available_at` must be `DatetimeTZDtype`, use `bar_available_at`/`to_available_at`), evidence summary + link; Decisions log rows C1, D1–D11; Backlog: D11 item stays.
- [x] **Step 5:** Commit `stage-05: Clock, trading calendar & point-in-time guard` with the attribution trailer; record the commit in `worklog.md`.

## Verification (end-to-end)

Stage is DONE only when, in this chat: the brief's Verify command passes; full `pytest`, `ruff check`, `ruff format --check`, `mypy src/atlas` and `make check` are clean; both acceptance criteria have named evidence in `verification.md` and PROGRESS.md.

## Post-review amendments (2026-10-26, whole-branch review fix pass; see worklog)

| # | Amendment | Why |
|---|---|---|
| D1a | `TradingCalendar(start=None, end=None, *, config=None)` stores the run's `CalendarConfig` as `.config`; `SimClock` decision time and `bar_available_at` lag default to `calendar.config`; `AsOf.for_session(day, decision_time=None, *, config=None)`. Callers pass `cfg.calendar`. | Review I1: the loaded config never reached behaviour. |
| D4a | `SimClock.advance(n)` moves to the n-th session decision instant strictly after `now()` (today's decision counts if today is a session and the clock is before it). | Review I3: a clock set to 06:30 ET skipped that day's 08:00 decision. |
| D8a | `bar_available_at` rejects a negative `lag` (`ValueError`). | Review Minor 2, re-graded Important: a negative lag is silent look-ahead. |
| D10a | Ban widened with `time.localtime/gmtime/ctime/asctime/strftime/clock_gettime/clock_gettime_ns`; string forms (`"now"`/`"today"` passed to `Timestamp/to_datetime/datetime64/date_range/bdate_range`) are caught by an AST scan test over `src/` and `tests/`. | Review I2. |
