"""Point-in-time macro queries: the latest vintage of each observation known at `as_of`.

A `macro_observations` row is known at `as_of` iff its `available_at` is strictly before
`as_of.cutoff` (`pit_filter`). Per series and observation date, the known row with the latest
`realtime_start` wins: an earlier `as_of` sees the first release, a later one the revision once
its vintage is public. An observation whose latest known vintage has no value (FRED's ".") is
left out. Values are in FRED's units for each series.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.pit import AsOf, assert_no_lookahead, pit_filter
from atlas.core.storage import Lake
from atlas.data.macro.series import (
    MACRO_OBSERVATIONS,
    MACRO_SERIES,
    OBS_COLUMNS,
    MacroCatalog,
    load_macro_series,
)

DETAIL_COLUMNS = OBS_COLUMNS
"""Columns of `macro_pit_detail`: the stored rows actually used."""


def known_observations(observations: pd.DataFrame, as_of: AsOf) -> pd.DataFrame:
    """The latest known vintage per series and observation date at `as_of` (rows of
    `macro_observations`, including missing values). The result is checked with
    `assert_no_lookahead` (`LookAheadError` if a later row slipped through)."""
    known = pit_filter(observations, as_of)
    latest = known.sort_values(
        ["series_id", "obs_date", "realtime_start"], kind="stable"
    ).drop_duplicates(subset=["series_id", "obs_date"], keep="last")
    assert_no_lookahead(latest, as_of)
    return latest.reset_index(drop=True)


def macro_pit_detail(
    observations: pd.DataFrame, series_ids: Sequence[str], as_of: AsOf, lookback: timedelta
) -> pd.DataFrame:
    """The rows behind `macro_pit`: per requested series (in request order), the latest known
    vintage of each observation dated from `as_of.day - lookback` to `as_of.day`, with a
    value. Columns `DETAIL_COLUMNS` (`available_at` and `realtime_start` say where each value
    came from). `lookback` must be positive (`ValueError`)."""
    if lookback <= timedelta(0):
        raise ValueError(f"lookback must be positive, got {lookback}")
    ids = list(series_ids)
    subset = observations[observations["series_id"].isin(ids)]
    latest = known_observations(subset, as_of)
    first = as_of.day - lookback
    inside = latest["obs_date"].map(lambda d: first <= d <= as_of.day).astype(bool)
    chosen = latest.loc[inside & latest["value"].notna()].copy()
    chosen["_order"] = chosen["series_id"].map({s: i for i, s in enumerate(ids)})
    chosen = chosen.sort_values(["_order", "obs_date"], kind="stable")
    return chosen[list(DETAIL_COLUMNS)].reset_index(drop=True)


def macro_pit(
    observations: pd.DataFrame, series_ids: Sequence[str], as_of: AsOf, lookback: timedelta
) -> pd.DataFrame:
    """The wide point-in-time view: index `obs_date` (tz-naive `DatetimeIndex`, ascending), one
    float column per requested series in request order (NaN where a series has no known value
    for that date; all NaN for a series with none), rows dated from `as_of.day - lookback` to
    `as_of.day`. See `macro_pit_detail` for the rows used."""
    ids = list(series_ids)
    detail = macro_pit_detail(observations, ids, as_of, lookback)
    wide = detail.pivot(index="obs_date", columns="series_id", values="value")
    wide = wide.reindex(columns=ids).astype("float64")
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index), name="obs_date")
    wide.columns.name = None
    return wide.sort_index()


class Macro:
    """Point-in-time macro data read from the lake's `macro_observations` table.

    `config` defaults to `AtlasConfig()` (pass the run's loaded config: its calendar sets the
    decision time of a plain `date` as-of); `catalog` to `config.data.macro.series_file`
    (relative to the working directory). Series ids must be in the catalog.
    """

    def __init__(
        self, lake: Lake, config: AtlasConfig | None = None, *, catalog: MacroCatalog | None = None
    ) -> None:
        self._lake = lake
        self._cfg = config if config is not None else AtlasConfig()
        self.catalog = (
            catalog if catalog is not None else load_macro_series(self._cfg.data.macro.series_file)
        )

    def _as_of(self, as_of: date | AsOf) -> AsOf:
        if isinstance(as_of, AsOf):
            return as_of
        if isinstance(as_of, datetime):
            raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
        return AsOf.for_session(as_of, config=self._cfg.calendar)

    def _ids(self, series_ids: Sequence[str]) -> list[str]:
        if isinstance(series_ids, str):
            raise ValueError("series_ids must be a sequence of FRED ids, not one string")
        ids = list(dict.fromkeys(s.strip().upper() for s in series_ids))
        unknown = [s for s in ids if s not in self.catalog]
        if unknown:
            raise ValueError(f"not in the macro series catalog: {unknown}")
        return ids

    def observations(self, series_ids: Sequence[str]) -> pd.DataFrame:
        """Every stored row of `series_ids` (all vintages, including ones after any as-of).

        Raises `DataError` when the table has never been written (run `ingest_macro`).
        """
        ids = self._ids(series_ids)
        if not self._lake.has_table(MACRO_OBSERVATIONS.name):
            raise DataError("macro_observations is empty: run ingest_macro first")
        return self._lake.read_table(MACRO_OBSERVATIONS.name, filters=[("series_id", "in", ids)])

    def pit(
        self, series_ids: Sequence[str], as_of: date | AsOf, lookback: timedelta
    ) -> pd.DataFrame:
        """`macro_pit` of `series_ids` at `as_of` (a date uses the configured decision time)."""
        ids, point = self._ids(series_ids), self._as_of(as_of)
        return macro_pit(self.observations(ids), ids, point, lookback)

    def pit_detail(
        self, series_ids: Sequence[str], as_of: date | AsOf, lookback: timedelta
    ) -> pd.DataFrame:
        """`macro_pit_detail` of `series_ids` at `as_of`."""
        ids, point = self._ids(series_ids), self._as_of(as_of)
        return macro_pit_detail(self.observations(ids), ids, point, lookback)

    def series_info(self) -> pd.DataFrame:
        """The stored `macro_series` rows (title, frequency, units, seasonal adjustment, kind)."""
        return self._lake.read_table(MACRO_SERIES.name)
