"""Point-in-time primitives: what the market could know, and when.

A decision for trading day D is made at a cutoff (default 08:00 America/New_York, before the
open) using only data known **strictly before** that cutoff. `AsOf` carries the day and the
cutoff.

Every stored record has an `available_at` column: the UTC instant from which the market could
know it. This is *knowledge time*, distinct from the record's `event_date` (the period or day it
describes). The availability rules, by source:

| Source | `available_at` |
|---|---|
| Daily bar, session D | close of D + lag (16:15 ET; 13:15 ET on an early close) |
| SEC filing | the filing's EDGAR `accepted` timestamp |
| FRED / ALFRED observation | the release time of the vintage that first carried the value |
| News article | its publish timestamp |
| Earnings event (EDGAR 8-K 2.02, 10-Q/10-K) | the filing's EDGAR acceptance timestamp |
| Scheduled event (FOMC, CPI, NFP; snapshot) | 00:00 New York on 1 January; snapshot: when taken |
| Earnings reaction | the bar availability of the last session of its window |

Forward-return labels (Stage 17) are the opposite of point-in-time data: the return from a
decision day to a later session, known only after that session. They exist to evaluate
signals and must never feed one. `tag_label` marks a label frame (a pandas `attrs` entry,
which pandas carries through arithmetic, ranks, shifts, reindexing and slicing) and
`guard_not_label` raises `LookAheadError` when a tagged object is passed where a signal or a
feature is expected. The guard catches accidental use; rebuilding a frame from raw numpy
values drops the tag and is not detected.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Self

import pandas as pd
from pydantic import model_validator

from atlas.core.calendar import MARKET_TZ, TradingCalendar, market_date
from atlas.core.config import CalendarConfig
from atlas.core.errors import DataError, LookAheadError
from atlas.core.types.base import AtlasModel, FrozenDict, UtcDatetime

AVAILABILITY_RULES: Final = FrozenDict(
    {
        "daily_bar": "session close of day D + bar_publish_lag_minutes (default 15)",
        "sec_filing": "the filing's EDGAR acceptance timestamp (`accepted`)",
        "fred_observation": "release time of the first vintage that carried the value",
        "news": "the article's publish timestamp",
        "earnings_event": "the EDGAR acceptance timestamp of the 8-K (item 2.02) or 10-Q/10-K",
        "scheduled_event": (
            "macro schedule: 00:00 New York on 1 January of the event's year, or the ingest "
            "that first saw a future date if later (unscheduled or in a disruption window: its "
            "own time); provider snapshot: the time it was taken"
        ),
        "earnings_reaction": "bar availability of the last session of the reaction window",
    }
)
"""`available_at` rule per data source (see the module docstring's table)."""


def _require_aware(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"naive datetime {ts!r}; pass a timezone-aware datetime")
    return ts


class AsOf(AtlasModel):
    """A point-in-time decision moment: trading day `day` with knowledge cutoff `cutoff`.

    `cutoff` is a timezone-aware instant stored in UTC and must fall on `day` in New York.
    Data is known at this `AsOf` only if its `available_at` is strictly before `cutoff`.
    """

    day: date
    cutoff: UtcDatetime

    @model_validator(mode="after")
    def _cutoff_on_day(self) -> Self:
        if market_date(self.cutoff) != self.day:
            raise ValueError(
                f"cutoff {self.cutoff.isoformat()} is on {market_date(self.cutoff)} in "
                f"New York, not on day {self.day}"
            )
        return self

    @classmethod
    def for_session(
        cls,
        day: date,
        decision_time: time | None = None,
        *,
        config: CalendarConfig | None = None,
    ) -> Self:
        """The `AsOf` for `day` at `decision_time` (naive, New York time).

        Without `decision_time`, `config.decision_time` is used (pass the run's `cfg.calendar`);
        without either, the built-in default 08:00.
        """
        settings = config if config is not None else CalendarConfig()
        decision = decision_time if decision_time is not None else settings.decision_time
        if decision.tzinfo is not None:
            raise ValueError("decision_time must be naive (it is read in America/New_York)")
        return cls(day=day, cutoff=datetime.combine(day, decision, tzinfo=MARKET_TZ))

    def knows(self, ts: datetime) -> bool:
        """Whether something available at the aware instant `ts` is known at this `AsOf`."""
        return _require_aware(ts) < self.cutoff


def bar_available_at(
    day: date, calendar: TradingCalendar, lag: timedelta | None = None
) -> datetime:
    """When the daily bar of session `day` becomes known: session close + `lag`, UTC.

    `lag` defaults to `calendar.config.bar_publish_lag_minutes` (15 minutes unless configured)
    and must not be negative: a bar is never known before its session closes.
    """
    if lag is None:
        lag = timedelta(minutes=calendar.config.bar_publish_lag_minutes)
    if lag < timedelta(0):
        raise ValueError(f"lag must not be negative, got {lag}")
    return calendar.session_close(day) + lag


def to_available_at(ts: datetime) -> datetime:
    """A source timestamp (filing acceptance, release, publish time) as `available_at` in UTC.

    Naive timestamps raise `ValueError`: an instant without a time zone is not a known moment.
    """
    return _require_aware(ts).astimezone(UTC)


def _known(df: pd.DataFrame, as_of: AsOf, time_col: str) -> pd.Series:
    """Row mask: `time_col` strictly before the cutoff. `NaT` (unknown) is never known."""
    if time_col not in df.columns:
        raise DataError(f"column {time_col!r} is missing; point-in-time data needs it")
    col = df[time_col]
    if not isinstance(col, pd.Series) or not isinstance(col.dtype, pd.DatetimeTZDtype):
        raise DataError(
            f"column {time_col!r} must hold timezone-aware datetimes (datetime64[ns, tz]); "
            f"got {getattr(col, 'dtype', 'several columns')}"
        )
    return col < pd.Timestamp(as_of.cutoff)


def pit_filter(df: pd.DataFrame, as_of: AsOf, time_col: str = "available_at") -> pd.DataFrame:
    """A copy of the rows of `df` known at `as_of`: `time_col` strictly before `as_of.cutoff`.

    Rows whose `time_col` is `NaT` (availability unknown) are dropped. Index and column order
    are kept. A missing or non-timezone-aware `time_col` raises `DataError`.
    """
    return df.loc[_known(df, as_of, time_col)].copy()


def assert_no_lookahead(df: pd.DataFrame, as_of: AsOf, time_col: str = "available_at") -> None:
    """Raise `LookAheadError` if any row of `df` is not known at `as_of` (`NaT` counts).

    Passes exactly when `pit_filter(df, as_of, time_col)` would drop nothing. A missing or
    non-timezone-aware `time_col` raises `DataError`.
    """
    known = _known(df, as_of, time_col)
    if bool(known.all()):
        return
    late = df.loc[~known, time_col]
    latest = late.max()
    latest_text = "unknown" if pd.isna(latest) else str(latest.tz_convert(UTC))
    raise LookAheadError(
        f"{len(late)} row(s) have {time_col} not strictly before the cutoff "
        f"{as_of.cutoff.isoformat()} (latest: {latest_text}; unknown: {int(late.isna().sum())})"
    )


LABEL_ATTR: Final = "atlas_label"
"""Key of the pandas `attrs` entry that marks a forward-return label frame or series."""


def tag_label[T: (pd.DataFrame, pd.Series)](frame: T) -> T:
    """A copy of `frame` (a DataFrame or Series of forward-return labels) tagged as a label."""
    out = frame.copy()
    out.attrs[LABEL_ATTR] = True
    return out


def is_label(obj: object) -> bool:
    """Whether `obj` is a forward-return label: a DataFrame or Series tagged by `tag_label`
    (or derived from one), or an object whose class sets `__atlas_label__ = True`."""
    if isinstance(obj, pd.DataFrame | pd.Series):
        return bool(obj.attrs.get(LABEL_ATTR, False))
    return getattr(obj, "__atlas_label__", False) is True


def guard_not_label(obj: object, what: str = "signal") -> None:
    """Raise `LookAheadError` if `obj`, passed as a `what` (a signal, features), is a
    forward-return label: labels are known only after their horizon and are for evaluation
    only."""
    if is_label(obj):
        raise LookAheadError(
            f"the {what} is a forward-return label (or derived from one): labels are known "
            "only after their horizon and may be used to evaluate a signal, never to build one"
        )
