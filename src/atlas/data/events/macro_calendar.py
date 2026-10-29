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
meeting is `withdrawn_at` its cancellation. FRED shows actual dates, so a release moved later
(a government shutdown) looks known from 1 January: a documented limitation.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Annotated, Any, Final, Self

import pandas as pd
from pydantic import AwareDatetime, Field, ValidationError, model_validator

from atlas.core.calendar import MARKET_TZ
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


class MacroCalendar(AtlasModel):
    """The contents of `config/macro_calendar.yaml` (see the module docstring)."""

    fomc: FomcCalendar
    releases: FrozenMap[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")], ReleaseDefinition]


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
) -> pd.DataFrame:
    """Release events `name` (`event_type`) from FRED's `release_dates` of the release and the
    `initial_dates` of its headline series (see the module docstring). No initial dates at all
    raises `DataError` (the revision-only days could not be told apart)."""
    if not initial_dates:
        raise DataError(f"{definition.headline_series}: no initial release dates")
    first = set(initial_dates)
    last = max(first)
    days = sorted({d for d in release_dates if d in first or d > last})
    if not days:
        return empty_macro_events()
    rows = [
        _row(name, d, _at(d, definition.release_time), RELEASE_SOURCE, scheduled_available_at(d))
        for d in days
    ]
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
) -> int:
    """Rebuild the `macro_events` table; returns the number of rows rebuilt.

    FOMC rows always come from `config.data.events.macro_calendar_file` (relative to the
    working directory; `config` defaults to `AtlasConfig()`). With a `client`, each release's
    dates from `history_start` on are fetched, with the headline series' first releases from a
    year earlier (so January's release of December data is recognised); without one, the
    release rows already stored are kept and a warning is logged. The rows of every source that
    was rebuilt replace that source's stored rows, so a moved date does not linger. A FRED
    `DataError` propagates before anything is written (fail safe).
    """
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
            frames.append(release_events(name, definition, dates, initial))
        sources.add(RELEASE_SOURCE)
    rebuilt = normalize_macro_events(pd.concat(frames, ignore_index=True))
    kept = (
        lake.read_table(MACRO_EVENTS.name)
        if lake.has_table(MACRO_EVENTS.name)
        else empty_macro_events()
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
    "UnscheduledMeeting",
    "fomc_events",
    "ingest_macro_calendar",
    "load_macro_calendar",
    "release_events",
    "scheduled_available_at",
]
