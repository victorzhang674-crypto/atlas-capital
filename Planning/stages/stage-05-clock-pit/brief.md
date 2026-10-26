# Stage 05 — Clock, trading calendar & point-in-time guard
**Phase:** 0 · **Size:** M · **Depends on:** 04 · **Spec refs:** §3.2

## Goal
One source of "now" for the whole system, real trading-day arithmetic, and the primitives that make look-ahead bias impossible by construction.

## In scope
- `Clock` protocol; `SimClock` (settable, advances by trading session) and `LiveClock` (wall time, America/New_York aware); `get_clock()` and `use_clock(clock)` context manager
- `TradingCalendar` wrapping `exchange_calendars` XNYS: `is_session`, `sessions(start, end)`, `next_session`, `prev_session`, `offset(date, n)`, `session_open/close` timestamps, early closes
- Point-in-time primitives: `AsOf` value (date + cutoff timestamp: the decision is made at a configurable time, default 08:00 ET pre-market, using data **known** strictly before the cutoff); `knowledge_time` convention, meaning every stored record has `available_at` (when the market could know it), as distinct from `event_date`
- `pit_filter(df, as_of, time_col="available_at")` and `assert_no_lookahead(df, as_of)` raising `LookAheadError`
- Rule table, documented in code: a daily bar for date D is available at D 16:00 ET + 15 min; SEC filings at their `accepted` timestamp; FRED observations at their release time (vintage); news at its publish timestamp

## Out of scope
Data storage (Stage 06); vendor specifics.

## Files
`src/atlas/core/clock.py`, `src/atlas/core/calendar.py`, `src/atlas/core/pit.py`, tests

## Key interfaces
```python
clock = SimClock(start=date(2020,1,2)); clock.advance(); clock.now() -> datetime
cal = TradingCalendar(); cal.offset(d, -21)
as_of = AsOf.for_session(date(2024,3,1), decision_time=time(8,0))  # cutoff tz-aware
pit_filter(df, as_of) ; assert_no_lookahead(df, as_of)
```

## Tasks
1. Calendar wrapper + tests on known holidays (2024-07-04, Good Friday) and early closes (day after Thanksgiving).
2. SimClock / LiveClock + contextvar; tests (no `datetime.now()` outside this module: add a ruff banned-API rule).
3. AsOf + cutoff logic; boundary tests (a bar for D is not visible at D 08:00; it is visible at D+1 08:00).
4. pit_filter / assert_no_lookahead with tz handling tests.

## Tests
Holiday sessions; offsets across weekends; the bar-visibility boundary; tz-naive input rejected; ruff rule bans `datetime.now`/`date.today` outside `core/clock.py`.

## Acceptance criteria
- A grep or ruff check shows no direct wall-clock calls outside `clock.py`; PIT boundary tests pass.

## Verify
`uv run pytest tests/unit/core -q && uv run ruff check .`
