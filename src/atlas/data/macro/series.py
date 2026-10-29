"""Macro series: the catalog (`config/macro.yaml`), the two lake tables, FRED JSON → rows, the
real-time windows of long vintage histories, and the point-in-time `available_at` rules.

`macro_observations` (registered in `DEFAULT_REGISTRY` on import) holds one row per series,
observation date and **vintage** (ALFRED real-time period): a revised value is another row with
a later `realtime_start`; the first row of an observation is its first release. `value` is in
FRED's units for the series (NaN where FRED shows "."). `macro_series` (static) describes each
series (title, frequency D/W/M/Q, units, seasonal adjustment, kind).

Each series is a **market** quote (Treasury yields, credit spreads, VIX) or a **release** (a
published statistic). `available_at` (UTC):

* release: the vintage's `realtime_start` date at the series' `release_time` (New York);
* market: an observation's first vintage is available `market_lag_days` calendar days after
  its date at `market_available_time` (New York); a revision, at the later of that and its
  `realtime_start` date at `market_available_time`.
"""

import math
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any, Literal, Self

import pandas as pd
from pydantic import Field, ValidationError, model_validator

from atlas.core.calendar import MARKET_TZ
from atlas.core.config import MacroConfig, read_config_file
from atlas.core.errors import ConfigError, DataError
from atlas.core.storage import AVAILABLE_AT, DEFAULT_REGISTRY, Column, ColumnType, TableSchema
from atlas.core.types.base import AtlasModel, NonEmptyStr

SeriesKind = Literal["market", "release"]
"""How a series' `available_at` is set (see the module docstring)."""

_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]*$")


class MacroSeries(AtlasModel):
    """One catalog entry: FRED series `id` (upper case), its `kind`, the release time of a
    release series (naive, America/New_York; `None` for a market series) and a description."""

    id: str
    kind: SeriesKind
    release_time: time | None = None
    description: NonEmptyStr

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not _ID_RE.match(self.id):
            raise ValueError(f"series id {self.id!r} must be an upper-case FRED id")
        if self.kind == "release" and self.release_time is None:
            raise ValueError(f"{self.id}: a release series needs release_time")
        if self.kind == "market" and self.release_time is not None:
            raise ValueError(f"{self.id}: a market series takes no release_time")
        if self.release_time is not None and self.release_time.tzinfo is not None:
            raise ValueError(f"{self.id}: release_time must be naive (read in New York)")
        return self


class _Entry(AtlasModel):
    kind: SeriesKind
    release_time: time | None = None
    description: NonEmptyStr


class _CatalogFile(AtlasModel):
    series: dict[str, _Entry] = Field(min_length=1)


class MacroCatalog:
    """The series set, in file order: series id → `MacroSeries` (read-only)."""

    def __init__(self, series: Mapping[str, MacroSeries]) -> None:
        """A catalog of `series` (id → entry)."""
        self._series = dict(series)

    @property
    def ids(self) -> tuple[str, ...]:
        """Series ids in catalog order."""
        return tuple(self._series)

    def get(self, series_id: str) -> MacroSeries:
        """The entry of `series_id`; `KeyError` naming it when it is not in the catalog."""
        try:
            return self._series[series_id]
        except KeyError:
            raise KeyError(f"{series_id!r} is not in the macro series catalog") from None

    def __contains__(self, series_id: object) -> bool:
        return series_id in self._series

    def __iter__(self) -> Iterator[str]:
        return iter(self._series)

    def __len__(self) -> int:
        return len(self._series)


def load_macro_series(path: Path) -> MacroCatalog:
    """The catalog in `path` (`series: {ID: {kind, release_time?, description}}`).

    A missing file, an empty catalog or an invalid entry raises `ConfigError` naming the file
    and the entry at fault.
    """
    if not path.is_file():
        raise ConfigError(f"{path}: macro series catalog not found")
    data = read_config_file(path)
    try:
        body = _CatalogFile.model_validate(data)
        series = {
            sid: MacroSeries(id=sid, **entry.model_dump()) for sid, entry in body.series.items()
        }
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{path}: {problems}") from None
    return MacroCatalog(series)


# --- tables ---------------------------------------------------------------------------------------

_S, _F, _D, _T = ColumnType.STRING, ColumnType.FLOAT64, ColumnType.DATE, ColumnType.TIMESTAMP

MACRO_OBSERVATIONS = DEFAULT_REGISTRY.register(
    TableSchema(
        name="macro_observations",
        columns=(
            Column(name="series_id", dtype=_S),
            Column(name="obs_date", dtype=_D),
            Column(name="value", dtype=_F, nullable=True),
            Column(name="realtime_start", dtype=_D),
            Column(name=AVAILABLE_AT, dtype=_T),
        ),
        primary_key=("series_id", "obs_date", "realtime_start"),
        partition_by=("series_id",),
        description="FRED/ALFRED observations, one row per vintage; Stage 10",
    )
)
"""`macro_observations`: one row per series, observation date and vintage."""

OBS_COLUMNS = MACRO_OBSERVATIONS.column_names
"""Columns of `macro_observations`, in order."""

ROW_COLUMNS = ("series_id", "obs_date", "value", "realtime_start")
"""Columns of `parse_observations` (before `attach_available_at`)."""

MACRO_SERIES = DEFAULT_REGISTRY.register(
    TableSchema(
        name="macro_series",
        columns=(
            Column(name="series_id", dtype=_S),
            Column(name="title", dtype=_S),
            Column(name="frequency", dtype=_S),
            Column(name="units", dtype=_S),
            Column(name="seasonal_adjustment", dtype=_S),
            Column(name="kind", dtype=_S),
        ),
        primary_key=("series_id",),
        static=True,
        description="FRED series descriptions (title, frequency, units); Stage 10",
    )
)
"""`macro_series`: descriptive metadata per series (static reference data)."""

_KEY = ["series_id", "obs_date", "realtime_start"]


# --- FRED JSON → rows -----------------------------------------------------------------------------


def _iso(value: object, what: str) -> date:
    if not isinstance(value, str):
        raise DataError(f"{what}: expected an ISO date, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataError(f"{what}: expected an ISO date, got {value!r}") from exc


def _value(raw: object, what: str) -> float:
    if raw == ".":
        return math.nan
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DataError(f"{what}: value is not a number: {raw!r}") from exc
    if not math.isfinite(value):
        raise DataError(f"{what}: value is not finite: {raw!r}")
    return value


def parse_observations(payload: Mapping[str, Any], series_id: str) -> pd.DataFrame:
    """The rows of an `output_type=1` observations payload as `ROW_COLUMNS`, sorted by
    observation date then `realtime_start`.

    `value` is float (FRED's "." → NaN). A payload without an `observations` list, or a row
    without an ISO `date` / `realtime_start` or with a non-numeric value, raises `DataError`.
    """
    items = payload.get("observations")
    if not isinstance(items, list):
        raise DataError(f"FRED observations {series_id}: no `observations` list")
    rows = []
    for item in items:
        if not isinstance(item, Mapping):
            raise DataError(f"FRED observations {series_id}: row is not an object: {item!r}")
        what = f"FRED observations {series_id} {item.get('date')!r}"
        rows.append(
            (
                _iso(item.get("date"), f"{what} date"),
                _value(item.get("value"), what),
                _iso(item.get("realtime_start"), f"{what} realtime_start"),
            )
        )
    frame = pd.DataFrame.from_records(rows, columns=["obs_date", "value", "realtime_start"])
    frame.insert(0, "series_id", pd.Series([series_id] * len(frame), dtype="str"))
    frame["value"] = frame["value"].astype("float64")
    return frame.sort_values(["obs_date", "realtime_start"], kind="stable").reset_index(drop=True)


def parse_series_metadata(payload: Mapping[str, Any], series: MacroSeries) -> pd.DataFrame:
    """One `macro_series` row from a `fred/series` payload (`seriess[0]`), with the catalog's
    `kind`. A missing entry, a different id or a missing field raises `DataError`."""
    entries = payload.get("seriess")
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], Mapping):
        raise DataError(f"FRED series {series.id}: no `seriess` entry")
    entry = entries[0]
    if entry.get("id") != series.id:
        raise DataError(f"FRED series {series.id}: payload describes {entry.get('id')!r}")
    fields = ("title", "frequency_short", "units", "seasonal_adjustment_short")
    missing = [f for f in fields if not isinstance(entry.get(f), str)]
    if missing:
        raise DataError(f"FRED series {series.id}: missing {missing}")
    return pd.DataFrame(
        {
            "series_id": [series.id],
            "title": [entry["title"]],
            "frequency": [entry["frequency_short"]],
            "units": [entry["units"]],
            "seasonal_adjustment": [entry["seasonal_adjustment_short"]],
            "kind": [series.kind],
        },
        dtype="str",
    )


# --- real-time windows and vintage merging --------------------------------------------------------


def vintage_windows(
    dates: Sequence[date], realtime_start: date, realtime_end: date, max_dates: int
) -> list[tuple[date, date]]:
    """Real-time windows `[(start, end), ...]` covering `[realtime_start, realtime_end]` so
    that each holds at most `max_dates` vintages, counting the one in effect at its start.

    The vintage `dates` inside the range go `max_dates - 1` to a window; window 0 starts at
    `realtime_start`, window k at its first vintage, and each ends the day before the next
    starts (the last at `realtime_end`). No vintages → one window. `max_dates < 2` raises
    `ValueError`.
    """
    if max_dates < 2:
        raise ValueError(f"max_dates must be at least 2, got {max_dates}")
    inside = sorted(d for d in set(dates) if realtime_start < d <= realtime_end)
    size = max_dates - 1
    starts = [realtime_start] + [inside[i] for i in range(size, len(inside), size)]
    ends = [s - timedelta(days=1) for s in starts[1:]] + [realtime_end]
    return list(zip(starts, ends, strict=True))


def collapse_vintages(rows: pd.DataFrame) -> pd.DataFrame:
    """`rows` (`ROW_COLUMNS`, any order) with consecutive vintages of the same value merged.

    Per series and observation date, in `realtime_start` order, a row whose value equals the
    previous row's (NaN equals NaN) is dropped, so the earliest `realtime_start` of each value
    is kept. This undoes the clipping of real-time periods at window starts.
    """
    ordered = rows.sort_values(_KEY, kind="stable").reset_index(drop=True)
    group = ordered.groupby(["series_id", "obs_date"], sort=False)["value"]
    previous = group.shift(1)
    first = group.cumcount() == 0
    same = (ordered["value"] == previous) | (ordered["value"].isna() & previous.isna())
    return ordered.loc[first | ~same].reset_index(drop=True)


# --- availability ---------------------------------------------------------------------------------


def _at(days: pd.Series, clock: time, *, plus_days: int = 0) -> pd.Series:
    """Each date in `days`, moved `plus_days` calendar days, at the New York wall-clock time
    `clock`, as UTC timestamps."""
    local = pd.to_datetime(pd.Series(days, dtype="object")) + pd.Timedelta(
        days=plus_days, hours=clock.hour, minutes=clock.minute, seconds=clock.second
    )
    return local.dt.tz_localize(MARKET_TZ).dt.tz_convert("UTC").astype("datetime64[us, UTC]")


def attach_available_at(
    rows: pd.DataFrame, series: MacroSeries, config: MacroConfig
) -> pd.DataFrame:
    """`rows` (`ROW_COLUMNS` of one series, merged by `collapse_vintages`) with `available_at`
    (UTC) by the series' kind (see the module docstring), as `OBS_COLUMNS`."""
    out = rows.sort_values(_KEY, kind="stable").reset_index(drop=True)
    if series.kind == "release":
        assert series.release_time is not None  # guaranteed by MacroSeries
        out[AVAILABLE_AT] = _at(out["realtime_start"], series.release_time)
    else:
        clock = config.market_available_time
        after_obs = _at(out["obs_date"], clock, plus_days=config.market_lag_days)
        at_vintage = _at(out["realtime_start"], clock)
        first = out.groupby(["series_id", "obs_date"], sort=False).cumcount() == 0
        out[AVAILABLE_AT] = after_obs.where(first | (after_obs >= at_vintage), at_vintage)
    return out[list(OBS_COLUMNS)]
