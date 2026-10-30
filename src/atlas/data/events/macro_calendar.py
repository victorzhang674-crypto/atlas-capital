"""Macro event calendar: FOMC decisions (static YAML) and data releases (FRED calendars).

`config/macro_calendar.yaml` lists the FOMC meetings per year (decision day; the statement at
`statement_time` New York), unscheduled meetings with their statement time, cancelled meetings
with the instant the cancellation became public, and the data releases (FRED release id,
headline series, release time). Release dates come from `fred/release/dates`; a date counts
only if the headline series first published a month's value that day (`output_type=4`), or if
it lies after the last such day (scheduled). FRED also lists revision-only days (CPI seasonal
factors each February until 2024, payroll benchmark previews), which are not market events.

Point in time: a scheduled date of year Y is known from 00:00 New York on 1 January Y
(`scheduled_available_at`); an unscheduled decision only at its statement time; a cancelled
meeting is `withdrawn_at` its cancellation. FRED keeps no history of its schedule, so release
dates are also kept as snapshots: on each ingest (at `known_at`) a future date not stored before
is known from `known_at`, and a stored future date that FRED no longer lists (moved or
cancelled) is `withdrawn_at` `known_at`; a stored row keeps its first `available_at`. Past dates
follow FRED's current list. A release dated inside a `schedule_disruptions` window (rescheduled
dates whose announcement time cannot be verified) is known only at its own release time.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Annotated, Any, Final, Self

import pandas as pd
from pydantic import AwareDatetime, Field, ValidationError, model_validator

from atlas.core.calendar import MARKET_TZ
from atlas.core.clock import get_clock
from atlas.core.config import AtlasConfig, read_config_file
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import get_logger
from atlas.core.storage import Lake
from atlas.core.types.base import AtlasModel, FrozenMap
from atlas.data.events.base import MACRO_EVENTS, empty_macro_events, normalize_macro_events
from atlas.data.macro.fred_client import FredClient

_log = get_logger(__name__)

FOMC_SOURCE: Final = "fomc_calendar"
"""`source` of FOMC events."""

RELEASE_SOURCE: Final = "fred_release"
"""`source` of data-release events."""


class UnscheduledMeeting(AtlasModel):
    """An unscheduled FOMC decision: statement `date` and `time` (naive, New York)."""

    date: date
    time: time


class CancelledMeeting(AtlasModel):
    """A scheduled meeting on `date` that did not take place; public from `cancelled_at`."""

    date: date
    cancelled_at: AwareDatetime


class FomcCalendar(AtlasModel):
    """FOMC decisions: `meetings` (scheduled decision days, ascending), `unscheduled`,
    `cancelled`; statements at `statement_time` (naive, New York) unless unscheduled."""

    statement_time: time
    meetings: tuple[date, ...]
    unscheduled: tuple[UnscheduledMeeting, ...] = ()
    cancelled: tuple[CancelledMeeting, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Self:
        days = [*self.meetings, *(u.date for u in self.unscheduled)]
        repeated = sorted({d for d in days if days.count(d) > 1})
        if repeated:
            raise ValueError(f"duplicate FOMC dates: {[d.isoformat() for d in repeated]}")
        for c in self.cancelled:
            if c.date not in self.meetings:
                raise ValueError(f"cancelled {c.date} is not a scheduled meeting")
        return self


class ReleaseDefinition(AtlasModel):
    """A data release from the FRED calendar: `fred_release_id`, the `headline_series` whose
    first releases mark the real release days, and `release_time` (naive, New York)."""

    fred_release_id: Annotated[int, Field(gt=0)]
    headline_series: Annotated[str, Field(pattern=r"^[A-Z0-9]+$")]
    release_time: time
    description: str = ""


class ScheduleDisruption(AtlasModel):
    """Release dates from `start` to `end` (inclusive) were rescheduled at unknown times."""

    start: date
    end: date
    reason: str = ""

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.start > self.end:
            raise ValueError(f"disruption start {self.start} is after its end {self.end}")
        return self

    def covers(self, day: date) -> bool:
        """Whether `day` lies in the window."""
        return self.start <= day <= self.end


class MacroCalendar(AtlasModel):
    """The contents of `config/macro_calendar.yaml` (see the module docstring)."""

    fomc: FomcCalendar
    releases: FrozenMap[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")], ReleaseDefinition]
    disruptions: tuple[ScheduleDisruption, ...] = ()


class _FomcFile(AtlasModel):
    statement_time: time
    meetings: dict[int, tuple[date, ...]]
    unscheduled: tuple[UnscheduledMeeting, ...] = ()
    cancelled: tuple[CancelledMeeting, ...] = ()

    @model_validator(mode="after")
    def _years(self) -> Self:
        for year, days in self.meetings.items():
            wrong = [d.isoformat() for d in days if d.year != year]
            if wrong:
                raise ValueError(f"meetings of {year} include {wrong}")
        return self


def load_macro_calendar(path: Path) -> MacroCalendar:
    """The calendar in `path`. A missing file or an invalid entry raises `ConfigError` naming
    the file and the entry at fault."""
    if not path.is_file():
        raise ConfigError(f"{path}: macro calendar not found")
    data = read_config_file(path)
    try:
        fomc = _FomcFile.model_validate(data.get("fomc"))
        return MacroCalendar(
            fomc=FomcCalendar(
                statement_time=fomc.statement_time,
                meetings=tuple(sorted(d for days in fomc.meetings.values() for d in days)),
                unscheduled=fomc.unscheduled,
                cancelled=fomc.cancelled,
            ),
            releases=data.get("releases"),
            disruptions=data.get("schedule_disruptions") or (),
        )
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{path}: {problems}") from None


def scheduled_available_at(day: date) -> datetime:
    """When a scheduled event on `day` is known: 00:00 New York on 1 January of its year, UTC."""
    return datetime(day.year, 1, 1, tzinfo=MARKET_TZ).astimezone(UTC)


def _at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=MARKET_TZ).astimezone(UTC)


def fomc_events(calendar: MacroCalendar) -> pd.DataFrame:
    """The FOMC decisions of `calendar` as `macro_events` rows (`event_type` `fomc`)."""
    fomc = calendar.fomc
    withdrawn = {c.date: c.cancelled_at.astimezone(UTC) for c in fomc.cancelled}
    rows: list[dict[str, Any]] = [
        _row("fomc", d, _at(d, fomc.statement_time), FOMC_SOURCE, scheduled_available_at(d))
        | {"withdrawn_at": withdrawn.get(d)}
        for d in fomc.meetings
    ]
    for meeting in fomc.unscheduled:
        when = _at(meeting.date, meeting.time)
        rows.append(_row("fomc", meeting.date, when, FOMC_SOURCE, when))
    return normalize_macro_events(pd.DataFrame(rows))


def release_events(
    name: str,
    definition: ReleaseDefinition,
    release_dates: Sequence[date],
    initial_dates: Sequence[date],
    *,
    disruptions: Sequence[ScheduleDisruption] = (),
) -> pd.DataFrame:
    """Release events `name` (`event_type`) from FRED's `release_dates` of the release and the
    `initial_dates` of its headline series (see the module docstring); a date inside one of
    `disruptions` is known only at its release time. No initial dates at all raises `DataError`
    (the revision-only days could not be told apart)."""
    if not initial_dates:
        raise DataError(f"{definition.headline_series}: no initial release dates")
    first = set(initial_dates)
    last = max(first)
    days = sorted({d for d in release_dates if d in first or d > last})
    if not days:
        return empty_macro_events()
    rows = []
    for d in days:
        when = _at(d, definition.release_time)
        disrupted = any(w.covers(d) for w in disruptions)
        known = when if disrupted else scheduled_available_at(d)
        rows.append(_row(name, d, when, RELEASE_SOURCE, known))
    return normalize_macro_events(pd.DataFrame(rows))


def merge_release_snapshot(
    stored: pd.DataFrame, fetched: pd.DataFrame, known_at: datetime
) -> pd.DataFrame:
    """Stored release rows updated with a fresh fetch made at `known_at` (see the module
    docstring): stored rows keep their first `available_at`; a fetched future date not stored
    before is known from `known_at` at the earliest; a stored future date missing from the fetch
    is `withdrawn_at` `known_at` (unless already withdrawn); past dates follow the fetch."""
    cutoff = pd.Timestamp(known_at)
    previous = stored.set_index("event_id")
    rows = []
    for row in fetched.to_dict("records"):
        old = previous.loc[row["event_id"]] if row["event_id"] in previous.index else None
        if old is not None and pd.isna(old["withdrawn_at"]):
            row["available_at"] = old["available_at"]
        elif row["event_time"] > cutoff:
            row["available_at"] = max(pd.Timestamp(row["available_at"]), cutoff)
        rows.append(row)
    fetched_ids = set(fetched["event_id"])
    for event_id, old in previous.iterrows():
        if event_id in fetched_ids or old["event_time"] <= cutoff:
            continue
        gone = old.to_dict() | {"event_id": event_id}
        if pd.isna(gone["withdrawn_at"]):
            gone["withdrawn_at"] = cutoff
        rows.append(gone)
    if not rows:
        return empty_macro_events()
    return normalize_macro_events(pd.DataFrame(rows))


def _row(kind: str, day: date, when: datetime, source: str, known: datetime) -> dict[str, Any]:
    return {
        "event_id": f"{kind}-{day.isoformat()}",
        "event_type": kind,
        "event_date": day,
        "event_time": when,
        "source": source,
        "available_at": known,
        "withdrawn_at": None,
    }


def ingest_macro_calendar(
    lake: Lake,
    client: FredClient | None,
    *,
    config: AtlasConfig | None = None,
    refresh: bool = True,
    known_at: datetime | None = None,
) -> int:
    """Rebuild the `macro_events` table; returns the number of rows rebuilt.

    FOMC rows always come from `config.data.events.macro_calendar_file` (relative to the
    working directory; `config` defaults to `AtlasConfig()`). With a `client`, each release's
    dates from `history_start` on are fetched, with the headline series' first releases from a
    year earlier (so January's release of December data is recognised); without one, the
    release rows already stored are kept and a warning is logged. FOMC rows replace the stored
    ones; release rows are merged with the stored ones as a snapshot taken at `known_at` (aware;
    default `get_clock().now()`, see `merge_release_snapshot`). A FRED `DataError` propagates
    before anything is written (fail safe).
    """
    when = known_at if known_at is not None else get_clock().now()
    if when.utcoffset() is None:
        raise ValueError(f"naive datetime {when!r}; pass a timezone-aware datetime")
    cfg = config if config is not None else AtlasConfig()
    settings = cfg.data.events
    calendar = load_macro_calendar(settings.macro_calendar_file)
    frames = [fomc_events(calendar)]
    sources = {FOMC_SOURCE}
    if client is None:
        _log.warning("events.releases_skipped", reason="no FRED client (FRED_API_KEY unset)")
    else:
        start = settings.history_start
        first_obs = date(start.year - 1, 1, 1)
        for name, definition in calendar.releases.items():
            dates = client.release_dates(
                definition.fred_release_id, realtime_start=start, refresh=refresh
            )
            initial = client.initial_release_dates(
                definition.headline_series, observation_start=first_obs, refresh=refresh
            )
            frames.append(
                release_events(name, definition, dates, initial, disruptions=calendar.disruptions)
            )
        sources.add(RELEASE_SOURCE)
    kept = (
        lake.read_table(MACRO_EVENTS.name)
        if lake.has_table(MACRO_EVENTS.name)
        else empty_macro_events()
    )
    if RELEASE_SOURCE in sources:
        fetched = normalize_macro_events(pd.concat(frames[1:], ignore_index=True))
        stored = kept.loc[kept["source"] == RELEASE_SOURCE]
        frames = [frames[0], merge_release_snapshot(stored, fetched, when)]
    rebuilt = normalize_macro_events(
        pd.concat([f for f in frames if not f.empty], ignore_index=True)
    )
    kept = kept.loc[~kept["source"].isin(sorted(sources))]
    table = pd.concat([df for df in (kept, rebuilt) if not df.empty], ignore_index=True)
    lake.write_table(MACRO_EVENTS.name, normalize_macro_events(table), mode="overwrite")
    _log.info("events.ingest_macro_calendar", rows=len(rebuilt), sources=sorted(sources))
    return len(rebuilt)


__all__ = [
    "FOMC_SOURCE",
    "RELEASE_SOURCE",
    "CancelledMeeting",
    "FomcCalendar",
    "MacroCalendar",
    "ReleaseDefinition",
    "ScheduleDisruption",
    "UnscheduledMeeting",
    "fomc_events",
    "ingest_macro_calendar",
    "load_macro_calendar",
    "merge_release_snapshot",
    "release_events",
    "scheduled_available_at",
]
