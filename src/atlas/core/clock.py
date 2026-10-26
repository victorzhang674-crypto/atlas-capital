"""The single source of "now" for Atlas.

Code asks `get_clock().now()` for the time and never reads the wall clock itself. A ruff rule
(TID251) bans the known wall-clock APIs (`datetime.now`, `date.today`, `time.time`,
`time.localtime`, `pandas.Timestamp.now`, ...) everywhere except this module, and a test scans
`src/` and `tests/` for the string forms (`pd.Timestamp("now")`, `np.datetime64("today")`).
A backtest installs a `SimClock` with `use_clock(...)`; paper trading installs a `LiveClock`.
With no clock installed,
`get_clock()` raises instead of falling back to wall time (fail safe: a backtest that forgot
its clock must not quietly see today's date).

`now()` is always timezone-aware UTC; `today()` is the market (America/New_York) date.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, time
from typing import Protocol

from atlas.core.calendar import MARKET_TZ, TradingCalendar, market_date
from atlas.core.errors import AtlasError


class Clock(Protocol):
    """Anything that can tell the time."""

    def now(self) -> datetime:
        """The current instant, timezone-aware UTC."""
        ...

    def today(self) -> date:
        """The current market date (America/New_York)."""
        ...


class SimClock:
    """A settable clock for backtests and tests that moves from session to session.

    `start` is either a trading session (the clock starts at that session's decision time) or
    a timezone-aware instant. `decision_time` is a naive time read in America/New_York
    (default: `calendar.config.decision_time`, 08:00 unless configured). The clock never
    moves backwards.
    """

    def __init__(
        self,
        start: date | datetime,
        *,
        calendar: TradingCalendar | None = None,
        decision_time: time | None = None,
    ) -> None:
        self._calendar = calendar if calendar is not None else TradingCalendar()
        self._decision_time = (
            decision_time if decision_time is not None else self._calendar.config.decision_time
        )
        if self._decision_time.tzinfo is not None:
            raise ValueError("decision_time must be naive (it is read in America/New_York)")
        if isinstance(start, datetime):
            self._now = _require_aware(start).astimezone(UTC)
        elif self._calendar.is_session(start):
            self._now = self._decision_instant(start)
        else:
            raise ValueError(f"{start} is not a trading session")

    def _decision_instant(self, session: date) -> datetime:
        return datetime.combine(session, self._decision_time, tzinfo=MARKET_TZ).astimezone(UTC)

    def now(self) -> datetime:
        """The simulated instant, timezone-aware UTC."""
        return self._now

    def today(self) -> date:
        """The market date of the simulated instant."""
        return market_date(self._now)

    def advance(self, n: int = 1) -> datetime:
        """Jump to the `n`-th session decision time strictly after `now()`; return the new now.

        If today is a session whose decision time is still ahead, that decision counts as the
        first, so a clock set to 06:30 ET never skips the 08:00 decision.
        """
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")
        today = self.today()
        if self._calendar.is_session(today) and self._now < self._decision_instant(today):
            first = today
        else:
            first = self._calendar.next_session(today)
        self._now = self._decision_instant(self._calendar.offset(first, n - 1))
        return self._now

    def set(self, ts: datetime) -> None:
        """Move the clock to the aware instant `ts`, which must not be earlier than `now()`."""
        ts = _require_aware(ts).astimezone(UTC)
        if ts < self._now:
            raise ValueError(f"cannot move the clock backwards from {self._now} to {ts}")
        self._now = ts


class LiveClock:
    """Wall-clock time. The only place in Atlas that reads the system clock."""

    def now(self) -> datetime:
        """The current wall-clock instant, timezone-aware UTC."""
        return datetime.now(UTC)

    def today(self) -> date:
        """Today's market date (America/New_York)."""
        return market_date(self.now())


def _require_aware(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"naive datetime {ts!r}; pass a timezone-aware datetime")
    return ts


_CLOCK: ContextVar[Clock | None] = ContextVar("atlas_clock", default=None)


def get_clock() -> Clock:
    """The clock installed by the innermost `use_clock`; raises `AtlasError` if there is none."""
    clock = _CLOCK.get()
    if clock is None:
        raise AtlasError(
            "no clock installed; wrap the code in use_clock(SimClock(...)) "
            "or use_clock(LiveClock())"
        )
    return clock


@contextmanager
def use_clock(clock: Clock) -> Iterator[Clock]:
    """Install `clock` for the current context (and contexts copied from it) until exit."""
    token = _CLOCK.set(clock)
    try:
        yield clock
    finally:
        _CLOCK.reset(token)
