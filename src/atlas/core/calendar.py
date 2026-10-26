"""NYSE (XNYS) trading calendar: sessions, session offsets, open/close times, early closes.

Trading days go in and come out as `date`s. Open and close times come out as timezone-aware
UTC `datetime`s. The market's own time zone is `MARKET_TZ` (America/New_York). The calendar
covers `[start, end]` (from `CalendarConfig`); a query outside that range raises `ValueError`
rather than guessing.
"""

import warnings
from datetime import UTC, date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
from exchange_calendars import ExchangeCalendar
from pandas.errors import Pandas4Warning

from atlas.core.config import CalendarConfig

MARKET_TZ = ZoneInfo("America/New_York")
"""Time zone of the US equity market; decision times and session dates are read in it."""


def market_date(ts: datetime) -> date:
    """The New York calendar date of the aware instant `ts` (naive `ts` raises `ValueError`)."""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"naive datetime {ts!r}; pass a timezone-aware datetime")
    return ts.astimezone(MARKET_TZ).date()


@lru_cache(maxsize=8)
def _xnys(start: date, end: date) -> ExchangeCalendar:
    """The underlying `exchange_calendars` XNYS calendar for `[start, end]`, built once."""
    # exchange_calendars 4.13 trips a pandas-4 deprecation notice while it builds; that notice
    # is about the library's internals, not our use, so only that category is silenced here.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Pandas4Warning)
        return xcals.get_calendar("XNYS", start=start.isoformat(), end=end.isoformat())


def _utc(ts: pd.Timestamp) -> datetime:
    return ts.to_pydatetime().astimezone(UTC)


class TradingCalendar:
    """XNYS trading calendar over `[start, end]`, carrying the run's `CalendarConfig`.

    Pass the loaded config (`TradingCalendar(config=cfg.calendar)`); `start`/`end` default to
    its bounds and may be narrowed. `config` also feeds the decision time (`SimClock`) and the
    bar publish lag (`bar_available_at`). Without `config`, the built-in defaults apply.

    `next_session`/`prev_session` are strictly after/before the given date, which need not be
    a session. `offset(d, n)` moves `n` sessions from the session `d` (negative = back).
    """

    def __init__(
        self,
        start: date | None = None,
        end: date | None = None,
        *,
        config: CalendarConfig | None = None,
    ) -> None:
        self.config = config if config is not None else CalendarConfig()
        self.start = start if start is not None else self.config.start
        self.end = end if end is not None else self.config.end
        if self.start >= self.end:
            raise ValueError(f"start ({self.start}) must be before end ({self.end})")
        self._cal = _xnys(self.start, self.end)
        self._sessions: pd.DatetimeIndex = self._cal.sessions

    def _check(self, d: date) -> pd.Timestamp:
        if not self.start <= d <= self.end:
            raise ValueError(f"{d} is outside the calendar [{self.start}, {self.end}]")
        return pd.Timestamp(d)

    def _session_at(self, i: int) -> date:
        if not 0 <= i < len(self._sessions):
            raise ValueError(
                f"the requested session is outside the calendar [{self.start}, {self.end}]"
            )
        return self._sessions[i].date()

    def _require_session(self, d: date) -> pd.Timestamp:
        ts = self._check(d)
        if ts not in self._sessions:
            raise ValueError(f"{d} is not a trading session")
        return ts

    def is_session(self, d: date) -> bool:
        """Whether the exchange is open on `d`."""
        return self._check(d) in self._sessions

    def sessions(self, start: date, end: date) -> tuple[date, ...]:
        """Every session from `start` to `end`, both inclusive, in order."""
        lo = int(self._sessions.searchsorted(self._check(start), side="left"))
        hi = int(self._sessions.searchsorted(self._check(end), side="right"))
        return tuple(ts.date() for ts in self._sessions[lo:hi])

    def next_session(self, d: date) -> date:
        """The first session strictly after `d`."""
        return self._session_at(int(self._sessions.searchsorted(self._check(d), side="right")))

    def prev_session(self, d: date) -> date:
        """The last session strictly before `d`."""
        return self._session_at(int(self._sessions.searchsorted(self._check(d), side="left")) - 1)

    def offset(self, d: date, n: int) -> date:
        """The session `n` sessions after the session `d` (`n < 0` moves back; 0 returns `d`)."""
        i = int(self._sessions.searchsorted(self._require_session(d), side="left"))
        return self._session_at(i + n)

    def session_open(self, d: date) -> datetime:
        """Opening time of session `d`, timezone-aware UTC."""
        return _utc(self._cal.session_open(self._require_session(d)))

    def session_close(self, d: date) -> datetime:
        """Closing time of session `d` (earlier on early-close days), timezone-aware UTC."""
        return _utc(self._cal.session_close(self._require_session(d)))

    def is_early_close(self, d: date) -> bool:
        """Whether session `d` closes early (for example 13:00 ET the day after Thanksgiving)."""
        return bool(self._require_session(d) in self._cal.early_closes)
