"""Index membership history and the investable universe on each date.

`index_membership` holds one row per membership interval: `start_date` inclusive,
`end_date` exclusive (the removal's effective date), `end_date` null while still a member.
An index change takes effect before the open of its date, so a name added on day D is a
member on D and a name removed on D is not. Changes are announced days before they take
effect, so membership on D is known at D's decision time: the dates themselves are the
point-in-time rule, and the table is static (no `available_at`).

The intervals are rebuilt backwards from today's constituents and the change log (see
`build_membership`). Symbols are identities: an instrument's latest ticker (META for FB).

`Universe.members` gives the names of a configured universe on a day; `Universe.tradable`
keeps the members that pass the liquidity filters (`cfg.data.tradable`) on raw daily bars
known strictly before the decision cutoff. Residual biases: `docs/data-limitations.md`.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Self

import pandas as pd
from pydantic import ValidationError, model_validator

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, UniverseConfig, read_config_file
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf, assert_no_lookahead, bar_available_at, pit_filter
from atlas.core.storage import DEFAULT_REGISTRY, Column, ColumnType, Lake, TableSchema
from atlas.core.types.base import AtlasModel, NonEmptyStr
from atlas.core.types.instruments import normalize_symbol
from atlas.data.prices import PRICES_DAILY
from atlas.data.reference import (
    INSTRUMENTS,
    TickerMap,
    build_instruments,
    instruments_frame,
    load_former_sectors,
    load_ticker_map,
    parse_changes,
    parse_constituents,
    trade_date_ranges,
)

SP500_INCEPTION = date(1957, 3, 4)
"""First day of the S&P 500; the `start_date` of an interval the source cannot date."""

INDEX_MEMBERSHIP = DEFAULT_REGISTRY.register(
    TableSchema(
        name="index_membership",
        columns=(
            Column(name="index_id", dtype=ColumnType.STRING),
            Column(name="symbol", dtype=ColumnType.STRING),
            Column(name="start_date", dtype=ColumnType.DATE),
            Column(name="end_date", dtype=ColumnType.DATE, nullable=True),
        ),
        primary_key=("index_id", "symbol", "start_date"),
        static=True,
        description="Index membership intervals [start_date, end_date); Stage 08",
    )
)
"""`index_membership`: one row per membership interval of a symbol in an index."""

AnomalyKind = Literal["added_not_member", "removed_while_member", "bad_symbol"]

_log = get_logger(__name__)


@dataclass(frozen=True)
class MembershipAnomaly:
    """A change-log row the reconstruction could not apply as written.

    `added_not_member`: the added name is not a member just after the change (it never shows
    up later, or a later row uses another ticker); `removed_while_member`: the removed name is
    already a member just before the change (usually a reused ticker); `bad_symbol`: the cell
    is not a valid ticker. `symbol` is the canonical ticker (raw text for `bad_symbol`);
    `detail` is the security name from the row.
    """

    date: date
    symbol: str
    kind: AnomalyKind
    detail: str | None


@dataclass(frozen=True)
class MembershipBuild:
    """Result of `build_membership`: `intervals` in `index_membership` column order (sorted
    by symbol and start), the number of rename rows skipped, and the anomalies (newest first).
    """

    intervals: pd.DataFrame
    renames: int
    anomalies: tuple[MembershipAnomaly, ...]


KnownAnomaly = tuple[date, str, AnomalyKind]
"""`(date, symbol, kind)` of a reviewed change-log anomaly (see `load_known_anomalies`)."""


class _KnownAnomalyEntry(AtlasModel):
    date: date
    symbol: NonEmptyStr
    kind: AnomalyKind
    note: str | None = None


class _KnownAnomaliesFile(AtlasModel):
    """Body of `config/membership_anomalies.yaml`."""

    known: tuple[_KnownAnomalyEntry, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> Self:
        keys = [(e.date, e.symbol, e.kind) for e in self.known]
        if len(set(keys)) != len(keys):
            raise ValueError("an anomaly is listed twice")
        return self


def load_known_anomalies(path: Path) -> frozenset[KnownAnomaly]:
    """The reviewed anomalies in `path` (`known: [{date, symbol, kind, note}]`; see
    `config/membership_anomalies.yaml`). A missing file gives none; invalid content raises
    `ConfigError` naming the file."""
    data = read_config_file(path)
    try:
        body = _KnownAnomaliesFile.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{path}: {problems}") from None
    return frozenset((e.date, e.symbol, e.kind) for e in body.known)


def build_membership(
    constituents: pd.DataFrame,
    changes: pd.DataFrame,
    *,
    index_id: str = "SP500",
    ticker_map: TickerMap | None = None,
    known_anomalies: Collection[KnownAnomaly] = frozenset(),
) -> MembershipBuild:
    """Rebuild membership intervals from today's `constituents` and the `changes` log
    (frames shaped like `parse_constituents` / `parse_changes`).

    Walks the log newest → oldest, starting from today's members. Per row, the added ticker
    is resolved through `ticker_map` as of the row's date and the removed ticker as of the day
    before (its last day as a member). A row whose two sides resolve to one instrument is a
    rename and is skipped. Otherwise an added name's interval starts on the date and a removed
    name's interval ends on it. Rows that contradict the state become `MembershipAnomaly`s
    and leave it unchanged; each is logged as a warning, or at info when it is in
    `known_anomalies` (reviewed, `load_known_anomalies`). An interval with no dated start begins at
    `SP500_INCEPTION`, or, for a current member that no row names, at the constituents
    table's `date_added`.
    """
    tmap = ticker_map if ticker_map is not None else TickerMap()
    anomalies: list[MembershipAnomaly] = []
    open_end: dict[str, date | None] = {
        normalize_symbol(s): None for s in constituents["symbol"]
    }  # members just after the row being read → end of their interval
    rows: list[tuple[str, date, date | None]] = []
    mentioned: set[str] = set()  # canonical symbols named by any row, renames included
    renames = 0

    def resolve(raw: object, on: date, row_day: date, name: object) -> str | None:
        """Canonical symbol of a ticker cell seen on `on`; `None` for an empty cell or a
        `bad_symbol` (recorded)."""
        if not isinstance(raw, str):
            return None
        try:
            return tmap.canonical(raw, on)
        except ValueError:
            anomalies.append(MembershipAnomaly(row_day, raw, "bad_symbol", _opt(name)))
            return None

    for day, added_raw, added_name, removed_raw, removed_name in changes[
        ["date", "added", "added_name", "removed", "removed_name"]
    ].itertuples(index=False):
        added = resolve(added_raw, day, day, added_name)
        removed = resolve(removed_raw, day - timedelta(days=1), day, removed_name)
        mentioned.update(s for s in (added, removed) if s is not None)
        if added is not None and added == removed:
            renames += 1
            continue
        if added is not None:
            if added in open_end:
                rows.append((added, day, open_end.pop(added)))
            else:
                anomalies.append(
                    MembershipAnomaly(day, added, "added_not_member", _opt(added_name))
                )
        if removed is not None:
            if removed in open_end:
                anomalies.append(
                    MembershipAnomaly(day, removed, "removed_while_member", _opt(removed_name))
                )
            else:
                open_end[removed] = day

    # The table's `date_added` restarts at renames (DuPont: 2019-06-03, when DowDuPont, a
    # member since 2017-09-01, took the DD ticker), so it dates only names the log never names.
    added_on = {
        normalize_symbol(s): d
        for s, d in zip(constituents["symbol"], constituents["date_added"], strict=True)
        if isinstance(d, date) and normalize_symbol(s) not in mentioned
    }
    for symbol, end in open_end.items():
        start = added_on.get(symbol, SP500_INCEPTION) if end is None else SP500_INCEPTION
        rows.append((symbol, start, end))

    known = set(known_anomalies)
    for a in anomalies:
        reviewed = (a.date, a.symbol, a.kind) in known
        (_log.info if reviewed else _log.warning)(
            "universe.membership_anomaly",
            index_id=index_id,
            day=a.date.isoformat(),
            symbol=a.symbol,
            kind=a.kind,
            detail=a.detail,
            known=reviewed,
        )
    intervals = pd.DataFrame(
        {
            "index_id": index_id,
            "symbol": [r[0] for r in rows],
            "start_date": [r[1] for r in rows],
            "end_date": pd.Series([r[2] for r in rows], dtype=object),
        },
        columns=list(INDEX_MEMBERSHIP.column_names),
    )
    intervals = intervals.sort_values(["symbol", "start_date"], ignore_index=True)
    return MembershipBuild(intervals=intervals, renames=renames, anomalies=tuple(anomalies))


def members_on(intervals: pd.DataFrame, day: date) -> list[str]:
    """Sorted symbols whose interval in `intervals` contains `day` (start ≤ day < end)."""
    if intervals.empty:
        return []
    ends = intervals["end_date"]
    open_ended = ends.isna()
    started = intervals["start_date"] <= day
    not_ended = open_ended | (ends.where(~open_ended, date.max) > day)
    return sorted(set(intervals.loc[started & not_ended, "symbol"]))


@dataclass(frozen=True)
class ReferenceReport:
    """Outcome of `refresh_universe_reference`: rows written to `instruments` and
    `index_membership`, rename rows skipped and anomalies found in the change log."""

    instruments: int
    intervals: int
    renames: int
    anomalies: tuple[MembershipAnomaly, ...]


def refresh_universe_reference(
    lake: Lake,
    constituents_html: str,
    changes_html: str,
    *,
    config: AtlasConfig | None = None,
    ticker_map: TickerMap | None = None,
    known_anomalies: Collection[KnownAnomaly] | None = None,
) -> ReferenceReport:
    """Rebuild `index_membership` (index `SP500`) and `instruments` from the two source pages
    (idempotent). The `SP500` rows of `index_membership` are replaced and rows of other
    indices kept; `instruments` rows are upserted by symbol, so instruments of other indices
    stay.

    `ticker_map` defaults to `load_ticker_map(config.data.reference.ticker_changes_file)`,
    `known_anomalies` to `load_known_anomalies(config.data.reference.known_anomalies_file)`;
    ETFs are the tickers of every ticker-list universe in `config.data.universes`; first and
    last trade dates come from `prices_daily` when the lake has prices; former members get
    the reviewed sector of `config.data.reference.former_sectors_file` when it lists them.
    """
    cfg = config if config is not None else AtlasConfig()
    tmap = (
        ticker_map
        if ticker_map is not None
        else load_ticker_map(cfg.data.reference.ticker_changes_file)
    )
    known = (
        known_anomalies
        if known_anomalies is not None
        else load_known_anomalies(cfg.data.reference.known_anomalies_file)
    )
    constituents = parse_constituents(constituents_html)
    changes = parse_changes(changes_html)
    build = build_membership(constituents, changes, ticker_map=tmap, known_anomalies=known)
    etfs = sorted({t for u in cfg.data.universes.values() for t in u.tickers})
    instruments = build_instruments(
        constituents,
        changes,
        build.intervals,
        etf_symbols=etfs,
        ticker_map=tmap,
        trade_dates=trade_date_ranges(lake),
        former_sectors=load_former_sectors(cfg.data.reference.former_sectors_file),
    )
    lake.write_table(
        INDEX_MEMBERSHIP.name, _replace_index(lake, build.intervals, "SP500"), mode="overwrite"
    )
    lake.write_table(INSTRUMENTS.name, instruments_frame(instruments), mode="upsert")
    report = ReferenceReport(
        instruments=len(instruments),
        intervals=len(build.intervals),
        renames=build.renames,
        anomalies=build.anomalies,
    )
    _log.info(
        "universe.reference_refreshed",
        instruments=report.instruments,
        intervals=report.intervals,
        renames=report.renames,
        anomalies=len(report.anomalies),
    )
    return report


def apply_former_sectors(lake: Lake, *, config: AtlasConfig | None = None) -> tuple[str, ...]:
    """Fill the reviewed sectors of `config.data.reference.former_sectors_file` into the
    `instruments` rows of `lake` that a `refresh_universe_reference` with that file would
    fill: no sector yet, not an ETF, not a current member of an index. Returns the symbols
    filled, sorted (none on a second call).

    It needs no source pages, so it changes nothing else: a refresh from today's pages would
    also bring in today's index changes. Raises `DataError` when `instruments` was never
    written.
    """
    cfg = config if config is not None else AtlasConfig()
    if not lake.has_table(INSTRUMENTS.name):
        raise DataError("instruments is empty: run refresh_universe_reference first")
    reviewed = load_former_sectors(cfg.data.reference.former_sectors_file)
    table = lake.read_table(INSTRUMENTS.name)
    current: set[str] = set()
    if lake.has_table(INDEX_MEMBERSHIP.name):
        membership = lake.read_table(INDEX_MEMBERSHIP.name)
        current = set(membership.loc[membership["end_date"].isna(), "symbol"])
    wanted = [
        symbol
        for symbol, sector, is_etf in zip(
            table["symbol"], table["sector"], table["is_etf"], strict=True
        )
        if pd.isna(sector) and not is_etf and symbol in reviewed and symbol not in current
    ]
    if not wanted:
        return ()
    rows = table[table["symbol"].isin(wanted)].copy()
    rows["sector"] = [reviewed[symbol] for symbol in rows["symbol"]]
    lake.write_table(INSTRUMENTS.name, rows, mode="upsert")
    _log.info("universe.former_sectors_applied", instruments=len(rows))
    return tuple(sorted(wanted))


class Universe:
    """The investable universe on each date, from the lake's reference tables and prices.

    `config` defaults to `AtlasConfig()` (pass the run's loaded config); `calendar` to
    `TradingCalendar(config=config.calendar)`. Membership read from `index_membership` is
    cached and re-read whenever the table's files change on disk, whichever `Lake` object or
    process wrote them (each write replaces a file atomically, so inode, mtime or size move).
    """

    def __init__(
        self,
        lake: Lake,
        config: AtlasConfig | None = None,
        *,
        calendar: TradingCalendar | None = None,
    ) -> None:
        self._lake = lake
        self._cfg = config if config is not None else AtlasConfig()
        self._calendar = (
            calendar if calendar is not None else TradingCalendar(config=self._cfg.calendar)
        )
        self._intervals: dict[str, pd.DataFrame] = {}
        self._files_state: tuple[tuple[str, int, int, int], ...] | None = None

    def _as_of(self, as_of: date | AsOf) -> AsOf:
        if isinstance(as_of, AsOf):
            return as_of
        if isinstance(as_of, datetime):
            raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
        return AsOf.for_session(as_of, config=self._cfg.calendar)

    def _universe(self, name: str) -> UniverseConfig:
        universes = self._cfg.data.universes
        if name not in universes:
            known = ", ".join(sorted(universes))
            raise ValueError(f"unknown universe {name!r}; known: {known}")
        return universes[name]

    def _membership_files_state(self) -> tuple[tuple[str, int, int, int], ...]:
        """Path, inode, mtime (ns) and size of every `index_membership` file."""
        state = []
        for path in self._lake.table_files(INDEX_MEMBERSHIP.name):
            st = path.stat()
            state.append((str(path), st.st_ino, st.st_mtime_ns, st.st_size))
        return tuple(state)

    def _index_intervals(self, index_id: str) -> pd.DataFrame:
        state = self._membership_files_state()
        if state != self._files_state:
            self._intervals.clear()
            self._files_state = state
        if index_id not in self._intervals:
            frame = self._lake.read_table(
                INDEX_MEMBERSHIP.name, filters=[("index_id", "=", index_id)]
            )
            if frame.empty:
                raise DataError(
                    f"no index_membership rows for {index_id}; load the reference data with "
                    "refresh_universe_reference first"
                )
            self._intervals[index_id] = frame
        return self._intervals[index_id]

    def members(self, as_of: date | AsOf, name: str = "sp500") -> list[str]:
        """Sorted members of universe `name` on trading day `as_of` (a `date` or the `day` of
        an `AsOf`). An index universe uses the membership intervals (changes effective on
        `as_of` count); a ticker-list universe returns its configured tickers."""
        day = self._as_of(as_of).day
        universe = self._universe(name)
        if universe.index is None:
            return sorted({normalize_symbol(t) for t in universe.tickers})
        return members_on(self._index_intervals(universe.index), day)

    def tradable(
        self,
        as_of: date | AsOf,
        name: str = "sp500",
        *,
        min_price: float | None = None,
        min_adv_usd: float | None = None,
        min_history_days: int | None = None,
    ) -> list[str]:
        """Sorted members of `name` at `as_of` that pass the liquidity filters.

        A `date` means that session's decision cutoff (`cfg.calendar.decision_time`). Only
        `prices_daily` bars with `available_at` strictly before the cutoff are used (the bar
        of the day itself is unknown at 08:00). A member is tradable when it has at least
        `min_history_days` bars, its last raw close is at least `min_price` USD, the mean of
        raw close x volume over its last `adv_window_days` bars is at least `min_adv_usd`
        USD, and its last bar is at most `max_stale_sessions` sessions older than the latest
        session whose bar is known. Unset filters come from `cfg.data.tradable`. Members with
        no bars are never tradable.
        """
        point = self._as_of(as_of)
        names = self.members(point, name)
        if not names:
            return []
        rules = self._cfg.data.tradable
        price_floor = rules.min_price if min_price is None else min_price
        adv_floor = rules.min_adv_usd if min_adv_usd is None else min_adv_usd
        history = rules.min_history_days if min_history_days is None else min_history_days
        window = rules.adv_window_days

        bars = self._lake.read_table(
            PRICES_DAILY.name,
            filters=[("symbol", "in", names), ("date", "<=", point.day)],
            columns=["symbol", "date", "close", "volume", "available_at"],
        )
        known = pit_filter(bars, point)
        assert_no_lookahead(known, point)
        known = known.sort_values(["symbol", "date"], ignore_index=True)
        known["dollar_volume"] = known["close"] * known["volume"]
        by_symbol = known.groupby("symbol", sort=True)
        stats = pd.DataFrame(
            {
                "bars": by_symbol.size(),
                "last_close": by_symbol["close"].last(),
                "last_date": by_symbol["date"].last(),
            }
        )
        recent = known.groupby("symbol", sort=True).tail(window).groupby("symbol", sort=True)
        stats["adv_bars"] = recent.size()
        stats["adv"] = recent["dollar_volume"].mean()
        ok = (
            (stats["bars"] >= history)
            & (stats["last_close"] >= price_floor)
            & (stats["adv_bars"] >= window)
            & (stats["adv"] >= adv_floor)
            & (stats["last_date"] >= self._stale_floor(point))
        )
        result = sorted(str(s) for s in stats.index[ok])
        _log.info(
            "universe.tradable",
            universe=name,
            day=point.day.isoformat(),
            members=len(names),
            with_bars=len(stats),
            tradable=len(result),
        )
        return result

    def _stale_floor(self, point: AsOf) -> date:
        """Oldest acceptable last-bar date: `max_stale_sessions` sessions before the latest
        session whose bar is known at `point`."""
        cal = self._calendar
        latest = point.day if cal.is_session(point.day) else cal.prev_session(point.day)
        while not point.knows(bar_available_at(latest, cal)):
            latest = cal.prev_session(latest)
        return cal.offset(latest, -self._cfg.data.tradable.max_stale_sessions)


def _replace_index(lake: Lake, intervals: pd.DataFrame, index_id: str) -> pd.DataFrame:
    """The whole `index_membership` table with the rows of `index_id` replaced by
    `intervals`, sorted by primary key."""
    if not lake.has_table(INDEX_MEMBERSHIP.name):
        return intervals
    stored = lake.read_table(INDEX_MEMBERSHIP.name)
    kept = stored[stored["index_id"] != index_id]
    frame = pd.concat([kept, intervals], ignore_index=True) if len(kept) else intervals
    return frame.sort_values(list(INDEX_MEMBERSHIP.primary_key), ignore_index=True)


def _opt(value: object) -> str | None:
    return value if isinstance(value, str) else None
