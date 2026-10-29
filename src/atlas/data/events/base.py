"""Event contracts and tables: `events` (per symbol) and `macro_events` (market-wide).

Event frame (`EVENT_COLUMNS`, the columns of `events`), one row per event and knowledge time:

| Column | Type | Meaning |
|---|---|---|
| `event_id` | `str` | stable id: `sec-<accession>`, `yahoo-<SYMBOL>-<YYYY-MM-DD>` |
| `symbol` | `str` | canonical ticker |
| `event_type` | `str` | `earnings` (dividends are derived from `corporate_actions` at query time) |
| `event_date` | `date` | New York calendar date of the event |
| `event_time` | UTC timestamp or NaT | exact instant when known; NaT for a date-only event |
| `time_of_day` | `str` | `BMO`, `AMC` or `unknown` (`classify_time_of_day`) |
| `source` | `str` | `sec_8k`, `sec_10q`, `sec_10k` (actual events); `yahoo` (scheduled) |
| `available_at` | UTC timestamp | when the market could know the row (acceptance; snapshot time) |

`available_at` is part of the key, so every snapshot of a scheduled date is kept: a later fetch
never moves the first knowledge of a date. `macro_events` (`MACRO_EVENT_COLUMNS`) has no
symbol and adds `withdrawn_at`: from that instant the event is no longer known to happen (a
cancelled meeting). Queries work on the *event view* (`EVENT_VIEW_COLUMNS`), the union of both
with `symbol` `None` for macro events.
"""

from datetime import datetime
from typing import Any, Final, Literal

import pandas as pd

from atlas.core.calendar import TradingCalendar, market_date
from atlas.core.errors import DataError
from atlas.core.storage import AVAILABLE_AT, DEFAULT_REGISTRY, Column, ColumnType, TableSchema
from atlas.core.types.instruments import normalize_symbol

_S, _D, _T = ColumnType.STRING, ColumnType.DATE, ColumnType.TIMESTAMP

TimeOfDay = Literal["BMO", "AMC", "unknown"]
"""Before the open, after the close, or not known (in session, a non-session day, no time)."""

TIMES_OF_DAY: Final = ("BMO", "AMC", "unknown")

EVENT_COLUMNS: Final = (
    "event_id",
    "symbol",
    "event_type",
    "event_date",
    "event_time",
    "time_of_day",
    "source",
    AVAILABLE_AT,
)
"""Columns of an event frame and of the `events` table, in order."""

MACRO_EVENT_COLUMNS: Final = (
    "event_id",
    "event_type",
    "event_date",
    "event_time",
    "source",
    AVAILABLE_AT,
    "withdrawn_at",
)
"""Columns of the `macro_events` table, in order."""

EVENT_VIEW_COLUMNS: Final = (*EVENT_COLUMNS, "withdrawn_at")
"""Columns of the event view the queries work on (company and macro events together)."""

EVENTS = DEFAULT_REGISTRY.register(
    TableSchema(
        name="events",
        columns=(
            Column(name="event_id", dtype=_S),
            Column(name="symbol", dtype=_S),
            Column(name="event_type", dtype=_S),
            Column(name="event_date", dtype=_D),
            Column(name="event_time", dtype=_T, nullable=True),
            Column(name="time_of_day", dtype=_S),
            Column(name="source", dtype=_S),
            Column(name=AVAILABLE_AT, dtype=_T),
        ),
        primary_key=("symbol", "event_id", AVAILABLE_AT),
        partition_by=("symbol",),
        description="Corporate events (earnings from EDGAR filings, scheduled dates); Stage 12",
    )
)
"""The curated `events` table (registered in `DEFAULT_REGISTRY` on import)."""

MACRO_EVENTS = DEFAULT_REGISTRY.register(
    TableSchema(
        name="macro_events",
        columns=(
            Column(name="event_id", dtype=_S),
            Column(name="event_type", dtype=_S),
            Column(name="event_date", dtype=_D),
            Column(name="event_time", dtype=_T, nullable=True),
            Column(name="source", dtype=_S),
            Column(name=AVAILABLE_AT, dtype=_T),
            Column(name="withdrawn_at", dtype=_T, nullable=True),
        ),
        primary_key=("event_id",),
        description="Macro events (FOMC decisions, CPI and payrolls releases); Stage 12",
    )
)
"""The curated `macro_events` table (registered in `DEFAULT_REGISTRY` on import)."""


def classify_time_of_day(event_time: datetime | None, calendar: TradingCalendar) -> TimeOfDay:
    """`BMO` if `event_time` is before the open of its New York day's session, `AMC` if at or
    after that session's close (early closes included), else `unknown` (during the session, on
    a day without a session, or no time: `None`/NaT). A naive instant raises `ValueError`."""
    if event_time is None or pd.isna(event_time):
        return "unknown"
    day = market_date(event_time)
    if not calendar.is_session(day):
        return "unknown"
    if event_time < calendar.session_open(day):
        return "BMO"
    if event_time >= calendar.session_close(day):
        return "AMC"
    return "unknown"


def empty_events() -> pd.DataFrame:
    """An event frame with no rows."""
    return _typed(pd.DataFrame({c: pd.Series([], dtype="object") for c in EVENT_COLUMNS}))


def empty_macro_events() -> pd.DataFrame:
    """A macro event frame with no rows."""
    return _typed_macro(
        pd.DataFrame({c: pd.Series([], dtype="object") for c in MACRO_EVENT_COLUMNS})
    )


def normalize_events(df: pd.DataFrame) -> pd.DataFrame:
    """`df` in the event contract: exactly `EVENT_COLUMNS`, canonical symbols, UTC timestamps,
    sorted by `symbol, event_date, event_id, available_at`, fresh index.

    A missing column, an empty `event_id`, an unknown `time_of_day`, a naive timestamp or a
    missing `available_at` raises `DataError`.
    """
    _require(df, EVENT_COLUMNS, "events")
    if df.empty:
        return empty_events()
    out = pd.DataFrame(
        {
            "event_id": _ids(df["event_id"], "events"),
            "symbol": [normalize_symbol(str(s)) for s in df["symbol"]],
            "event_type": [str(v) for v in df["event_type"]],
            "event_date": list(df["event_date"]),
            "event_time": _instants(df["event_time"], "event_time", nullable=True),
            "time_of_day": _times_of_day(df["time_of_day"]),
            "source": [str(v) for v in df["source"]],
            AVAILABLE_AT: _instants(df[AVAILABLE_AT], AVAILABLE_AT, nullable=False),
        }
    )
    out = out.sort_values(["symbol", "event_date", "event_id", AVAILABLE_AT], kind="stable")
    return _typed(out.reset_index(drop=True))


def normalize_macro_events(df: pd.DataFrame) -> pd.DataFrame:
    """`df` in the macro event contract (`MACRO_EVENT_COLUMNS`), UTC timestamps, sorted by
    `event_date, event_id`, fresh index. Errors as `normalize_events`."""
    _require(df, MACRO_EVENT_COLUMNS, "macro_events")
    if df.empty:
        return empty_macro_events()
    out = pd.DataFrame(
        {
            "event_id": _ids(df["event_id"], "macro_events"),
            "event_type": [str(v) for v in df["event_type"]],
            "event_date": list(df["event_date"]),
            "event_time": _instants(df["event_time"], "event_time", nullable=True),
            "source": [str(v) for v in df["source"]],
            AVAILABLE_AT: _instants(df[AVAILABLE_AT], AVAILABLE_AT, nullable=False),
            "withdrawn_at": _instants(df["withdrawn_at"], "withdrawn_at", nullable=True),
        }
    )
    out = out.sort_values(["event_date", "event_id"], kind="stable").reset_index(drop=True)
    return _typed_macro(out)


def _require(df: pd.DataFrame, columns: tuple[str, ...], what: str) -> None:
    if not isinstance(df, pd.DataFrame):
        raise DataError(f"{what}: expected a pandas DataFrame, got {type(df).__name__}")
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise DataError(f"{what}: missing columns {missing}")


def _ids(series: pd.Series, what: str) -> list[str]:
    ids = [str(v) if v is not None else "" for v in series]
    if any(not i.strip() for i in ids):
        raise DataError(f"{what}: event_id must not be empty")
    return ids


def _times_of_day(series: pd.Series) -> list[str]:
    values = [str(v) for v in series]
    bad = sorted({v for v in values if v not in TIMES_OF_DAY})
    if bad:
        raise DataError(f"events: time_of_day must be one of {TIMES_OF_DAY}, got {bad}")
    return values


def _instants(series: pd.Series, name: str, *, nullable: bool) -> list[pd.Timestamp | None]:
    out: list[pd.Timestamp | None] = []
    for value in series:
        if value is None or value is pd.NaT or (not isinstance(value, datetime) and pd.isna(value)):
            if not nullable:
                raise DataError(f"events: {name} is missing")
            out.append(None)
            continue
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise DataError(f"events: {name} must be a timezone-aware datetime, got {value!r}")
        out.append(pd.Timestamp(value).tz_convert("UTC"))
    return out


def _timestamps(values: Any) -> pd.Series:
    return pd.to_datetime(pd.Series(values, dtype="object"), utc=True).dt.as_unit("us")


def _typed(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in ("event_time", AVAILABLE_AT):
        out[col] = _timestamps(list(out[col])).set_axis(out.index)
    for col in ("event_id", "symbol", "event_type", "time_of_day", "source"):
        out[col] = out[col].astype("str")
    out["event_date"] = pd.Series(list(out["event_date"]), index=out.index, dtype="object")
    return out[list(EVENT_COLUMNS)]


def _typed_macro(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in ("event_time", AVAILABLE_AT, "withdrawn_at"):
        out[col] = _timestamps(list(out[col])).set_axis(out.index)
    for col in ("event_id", "event_type", "source"):
        out[col] = out[col].astype("str")
    out["event_date"] = pd.Series(list(out["event_date"]), index=out.index, dtype="object")
    return out[list(MACRO_EVENT_COLUMNS)]
