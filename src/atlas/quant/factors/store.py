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

`data_fingerprint` (schema version 2) is `input_fingerprint` of the data lake at the panel's
cutoff: how many facts, price bars and corporate actions were known then, and the instruments'
sectors and the index membership up to its day. A reader recomputes it; a panel whose data
changed since it was stored (a re-ingest, a sector review, a restated split), or one stored
without a fingerprint, is treated as not stored (`factors.cache_stale`), so the cache is safe
to read by default. Data known only after the cutoff (later filings, new bars) changes nothing.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
import pyarrow.dataset as ds

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
from atlas.data.fundamentals.parser import FUNDAMENTALS_FACTS
from atlas.data.prices import CORPORATE_ACTIONS, PRICES_DAILY
from atlas.data.reference import INSTRUMENTS
from atlas.data.universe import INDEX_MEMBERSHIP
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
            Column(name="data_fingerprint", dtype=ColumnType.STRING, nullable=True),
        ),
        primary_key=("date", "symbol", "factor"),
        partition_by=(YEAR,),
        year_column="date",
        description="Fundamental factor exposures per rebalance date (z-scores + raw); Stage 16",
        version=2,
    )
)
"""`factor_exposures`: the cached factor panels (module docstring)."""


def _add_fingerprint(frame: pd.DataFrame) -> pd.DataFrame:
    """Version 1 -> 2: the rows get an empty `data_fingerprint` (never read as current)."""
    return frame.assign(data_fingerprint=None)


DEFAULT_REGISTRY.add_migration(FACTOR_EXPOSURES.name, 1, _add_fingerprint)

_COUNTED = (FUNDAMENTALS_FACTS, PRICES_DAILY, CORPORATE_ACTIONS)


def input_fingerprint(lake: Lake, as_of: AsOf) -> str:
    """A 16-hex-char fingerprint of the data a factor panel at `as_of` is computed from, as
    known at its cutoff: the number of rows of `fundamentals_facts`, `prices_daily` and
    `corporate_actions` with `available_at` before the cutoff, the instruments' sectors and
    ETF flags, and the index membership as dated up to `as_of.day` (an end after the day
    counts as open)."""
    cutoff = pd.Timestamp(as_of.cutoff)
    payload: dict[str, object] = {}
    for schema in _COUNTED:
        files = lake.table_files(schema.name) if lake.has_table(schema.name) else ()
        if files:
            dataset = ds.dataset(
                [str(f) for f in files], format="parquet", schema=schema.arrow_schema()
            )
            payload[schema.name] = dataset.count_rows(filter=ds.field(AVAILABLE_AT) < cutoff)
        else:
            payload[schema.name] = 0
    if lake.has_table(INSTRUMENTS.name):
        inst = lake.read_table(INSTRUMENTS.name, columns=["symbol", "sector", "is_etf"])
        payload[INSTRUMENTS.name] = [
            [str(s), None if pd.isna(sec) else str(sec), bool(etf)]
            for s, sec, etf in zip(inst["symbol"], inst["sector"], inst["is_etf"], strict=True)
        ]
    if lake.has_table(INDEX_MEMBERSHIP.name):
        day = as_of.day
        rows = lake.read_table(INDEX_MEMBERSHIP.name)
        rows = rows[rows["start_date"] <= day]
        payload[INDEX_MEMBERSHIP.name] = sorted(
            [
                str(i),
                str(s),
                start.isoformat(),
                None if end is None or pd.isna(end) or end > day else end.isoformat(),
            ]
            for i, s, start, end in zip(
                rows["index_id"], rows["symbol"], rows["start_date"], rows["end_date"], strict=True
            )
        )
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


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
    panels computed with it are written (`ValueError` otherwise). `data_lake` (default `lake`)
    is the lake the panels are computed from: its `input_fingerprint` is stored with each
    panel and checked on every read (module docstring)."""

    def __init__(
        self, lake: Lake, config: AtlasConfig | None = None, *, data_lake: Lake | None = None
    ) -> None:
        self._lake = lake
        self._data = data_lake if data_lake is not None else lake
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
            data_fingerprint=input_fingerprint(self._data, panel.as_of),
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
        return self._stored(day, own) if self._fresh(day, own) else None

    def read_on(self, as_of: date | AsOf) -> StoredExposures | None:
        """The cached panel computed on `as_of.day` itself, if its rows are known at `as_of`
        (a `date` uses the configured decision time); `None` otherwise. It is what
        `factor_panel(DataView(lake, as_of))` gave when it was stored: a reader that wants the
        fresh panel of that day, not an older one, can take it instead of computing it."""
        point = _point(as_of, self._cfg)
        if not self._lake.has_table(FACTOR_EXPOSURES.name):
            return None
        rows = self._rows([("date", "==", point.day)])
        known = pit_filter(rows, point)
        assert_no_lookahead(known, point)
        if known.empty or not self._fresh(point.day, known):
            return None
        return self._stored(point.day, known)

    def _fresh(self, day: date, own: pd.DataFrame) -> bool:
        """Whether the panel of `day` (its rows `own`) was computed from the data lake as it
        is now (its stored `data_fingerprint` equals the current one); logged when not."""
        stored = set(own["data_fingerprint"].dropna())
        cutoff = pd.Timestamp(own["available_at"].iloc[0]) + _EPSILON
        current = input_fingerprint(self._data, AsOf(day=day, cutoff=cutoff.to_pydatetime()))
        if stored == {current} and not own["data_fingerprint"].isna().any():
            return True
        _log.warning(
            "factors.cache_stale",
            day=day.isoformat(),
            stored=sorted(stored),
            current=current,
            reason="data changed since the panel was stored" if stored else "no fingerprint",
        )
        return False

    def fresh_dates(self) -> tuple[date, ...]:
        """The stored rebalance dates with the current settings whose data has not changed
        since (`data_fingerprint` still current), ascending."""
        if not self._lake.has_table(FACTOR_EXPOSURES.name):
            return ()
        rows = self._rows([])
        out: list[date] = []
        for day in sorted(set(rows["date"])):
            own = rows[rows["date"] == day]
            if self._fresh(day, own):  # a stale date is logged (and recomputed by a build)
                out.append(day)
        return tuple(out)

    def _stored(self, day: date, own: pd.DataFrame) -> StoredExposures:
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
    one on another lake, with `data_lake=lake`, to leave `lake` read-only); dates already
    stored with the current settings and unchanged data are skipped unless `refresh`. Each
    date is logged (`factors.cache_written`). Returns the dates written."""
    cfg = config if config is not None else AtlasConfig()
    target = store if store is not None else FactorStore(lake, cfg)
    days = sorted(set(dates))
    stored = set() if refresh else set(target.fresh_dates())
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
