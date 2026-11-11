"""The factor panel cache: `factor_exposures` in the lake, for monthly rebalance dates.

One row per (rebalance `date`, `symbol`, `factor`): `value` = the cleaned exposure
(z-score; NaN for a factor left uncleaned), `raw` = the raw factor value (units per
`FACTORS`), `filled` = the raw value was missing and the exposure is its sector median,
`config_hash` = `factor_config_hash` of the settings that produced it, `available_at` = the
computing as-of's cutoff minus 1 µs (every input was known strictly before the cutoff, so a
reader at that as-of, and later, may use the row; one before it may not).

`FactorStore.read(as_of)` returns the latest stored rebalance date on or before the as-of
day whose rows are known at its cutoff and carry the current settings' hash; the rows are
checked with `assert_no_lookahead`. Writing a date replaces all of its rows.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf, assert_no_lookahead, pit_filter
from atlas.core.storage import (
    AVAILABLE_AT,
    DEFAULT_REGISTRY,
    YEAR,
    Column,
    ColumnType,
    Lake,
    TableSchema,
)
from atlas.data.view import DataView
from atlas.quant.factors.fundamental import (
    FACTOR_NAMES,
    FactorPanel,
    factor_config_hash,
    factor_panel,
)

_log = get_logger(__name__)

FACTOR_EXPOSURES = DEFAULT_REGISTRY.register(
    TableSchema(
        name="factor_exposures",
        columns=(
            Column(name="date", dtype=ColumnType.DATE),
            Column(name="symbol", dtype=ColumnType.STRING),
            Column(name="factor", dtype=ColumnType.STRING),
            Column(name="value", dtype=ColumnType.FLOAT64, nullable=True),
            Column(name="raw", dtype=ColumnType.FLOAT64, nullable=True),
            Column(name="filled", dtype=ColumnType.BOOL),
            Column(name="config_hash", dtype=ColumnType.STRING),
            Column(name=AVAILABLE_AT, dtype=ColumnType.TIMESTAMP),
        ),
        primary_key=("date", "symbol", "factor"),
        partition_by=(YEAR,),
        year_column="date",
        description="Fundamental factor exposures per rebalance date (z-scores + raw); Stage 16",
    )
)
"""`factor_exposures`: the cached factor panels (module docstring)."""

_EPSILON = pd.Timedelta(microseconds=1)


@dataclass(frozen=True)
class StoredExposures:
    """A cached panel: its rebalance `date`, `exposures` and `raw` (index symbol, sorted;
    `FACTOR_NAMES` columns), `filled` (bool) and the `config_hash` it was computed with."""

    date: date
    exposures: pd.DataFrame
    raw: pd.DataFrame
    filled: pd.DataFrame
    config_hash: str


def _melt(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    """`frame` (index symbol, one column per factor) as `symbol, factor, <name>` rows."""
    wide = frame.rename_axis(index="symbol", columns=None).reset_index()
    return wide.melt(id_vars="symbol", var_name="factor", value_name=name)


def _point(as_of: date | AsOf, config: AtlasConfig) -> AsOf:
    if isinstance(as_of, AsOf):
        return as_of
    if isinstance(as_of, datetime):
        raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
    return AsOf.for_session(as_of, config=config.calendar)


class FactorStore:
    """Read and write cached factor panels in `lake` for the factor settings of `config`
    (default `AtlasConfig()`): only rows with `factor_config_hash(config)` are read, and only
    panels computed with it are written (`ValueError` otherwise)."""

    def __init__(self, lake: Lake, config: AtlasConfig | None = None) -> None:
        self._lake = lake
        self._cfg = config if config is not None else AtlasConfig()
        self.config_hash = factor_config_hash(self._cfg)

    @property
    def lake(self) -> Lake:
        """The lake this store reads and writes."""
        return self._lake

    def write(self, panel: FactorPanel) -> int:
        """Store `panel` under its as-of day, replacing every stored row of that day; return
        the number of rows written."""
        if panel.config_hash != self.config_hash:
            raise ValueError(
                f"panel config_hash {panel.config_hash!r} differs from the store's "
                f"{self.config_hash!r}: it was computed with other factor settings"
            )
        day = panel.as_of.day
        long = _melt(panel.exposures, "value")
        long["raw"] = _melt(panel.raw.reindex_like(panel.exposures), "raw")["raw"].to_numpy()
        flags = panel.filled.reindex_like(panel.exposures).fillna(False).astype("bool")
        long["filled"] = _melt(flags, "filled")["filled"].to_numpy(dtype="bool")
        frame = long.assign(
            date=day,
            config_hash=self.config_hash,
            available_at=pd.Timestamp(panel.as_of.cutoff) - _EPSILON,
        )
        frame["symbol"] = frame["symbol"].astype("str")
        frame["factor"] = frame["factor"].astype("str")
        frame["available_at"] = frame["available_at"].astype("datetime64[us, UTC]")
        frame = frame[list(FACTOR_EXPOSURES.column_names)]
        if self._lake.has_table(FACTOR_EXPOSURES.name):
            self._lake.delete_rows(FACTOR_EXPOSURES.name, [("date", "=", day)])
            if not frame.empty:
                self._lake.write_table(FACTOR_EXPOSURES.name, frame, mode="upsert")
        else:
            self._lake.write_table(FACTOR_EXPOSURES.name, frame, mode="overwrite")
        _log.info("factors.stored", day=day.isoformat(), rows=len(frame))
        return len(frame)

    def _rows(self, filters: list[tuple[str, str, object]]) -> pd.DataFrame:
        rows = self._lake.read_table(FACTOR_EXPOSURES.name, filters=filters)
        return rows[rows["config_hash"] == self.config_hash]

    def dates(self) -> tuple[date, ...]:
        """The stored rebalance dates with the current settings, ascending."""
        if not self._lake.has_table(FACTOR_EXPOSURES.name):
            return ()
        return tuple(sorted(set(self._rows([])["date"])))

    def read(self, as_of: date | AsOf) -> StoredExposures | None:
        """The latest cached panel dated on or before `as_of.day` whose rows are known at
        `as_of` (a `date` uses the configured decision time), or `None`."""
        point = _point(as_of, self._cfg)
        if not self._lake.has_table(FACTOR_EXPOSURES.name):
            return None
        rows = self._rows([("date", "<=", point.day)])
        known = pit_filter(rows, point)
        assert_no_lookahead(known, point)
        if known.empty:
            return None
        day = max(known["date"])
        own = known[known["date"] == day]

        def wide(column: str) -> pd.DataFrame:
            table = own.pivot(index="symbol", columns="factor", values=column)
            table = table.reindex(columns=list(FACTOR_NAMES)).sort_index()
            table.columns.name = None
            return table

        return StoredExposures(
            date=day,
            exposures=wide("value").astype("float64"),
            raw=wide("raw").astype("float64"),
            filled=wide("filled").astype("bool"),
            config_hash=self.config_hash,
        )


def monthly_rebalance_dates(
    start: date, end: date, calendar: TradingCalendar | None = None
) -> tuple[date, ...]:
    """The last trading session of each month that ends inside [`start`, `end`]."""
    if start > end:
        raise ValueError(f"start ({start}) must not be after end ({end})")
    cal = calendar if calendar is not None else TradingCalendar()
    sessions = cal.sessions(start, end)
    out = []
    for this, nxt in zip(sessions, (*sessions[1:], None), strict=True):
        if nxt is None:
            if cal.next_session(this).month != this.month:
                out.append(this)
        elif nxt.month != this.month:
            out.append(this)
    return tuple(out)


def build_factor_cache(
    lake: Lake,
    dates: Iterable[date],
    *,
    config: AtlasConfig | None = None,
    symbols: Sequence[str] | None = None,
    refresh: bool = False,
    store: FactorStore | None = None,
) -> tuple[date, ...]:
    """Compute `factor_panel` at each of `dates` (ascending; one `DataView` of `lake` advanced
    date by date) and store it in `store` (default: a `FactorStore` on `lake` itself; pass
    one on another lake to leave `lake` read-only); dates already stored with the current
    settings are skipped unless `refresh`. Each date is logged (`factors.cache_written`).
    Returns the dates written."""
    cfg = config if config is not None else AtlasConfig()
    target = store if store is not None else FactorStore(lake, cfg)
    days = sorted(set(dates))
    stored = set() if refresh else set(target.dates())
    todo = [d for d in days if d not in stored]
    written: list[date] = []
    view: DataView | None = None
    for day in todo:
        view = DataView(lake, day, cfg) if view is None else view.advance(day)
        panel = factor_panel(view, symbols)
        rows = target.write(panel)
        written.append(day)
        _log.info(
            "factors.cache_written",
            date=day.isoformat(),
            names=len(panel.exposures),
            rows=rows,
            done=len(written),
            todo=len(todo),
        )
    return tuple(written)
