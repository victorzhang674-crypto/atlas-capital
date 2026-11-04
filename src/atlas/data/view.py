"""`DataView`: the one door to all data, bound to a point-in-time `as_of`.

Strategy and agent code reads data only through a `DataView` (design spec §3 principle 2).
A view is read-only and immutable: `advance(new_as_of)` returns a new view. Every method

- returns only data known at `as_of` (each delegate applies `pit_filter`; the view checks the
  result again and raises `LookAheadError` if anything later slipped through);
- leaves out the symbols (and macro series) with a `critical` issue in the day's quality
  report (`atlas.data.quality.quality_report`); single-symbol methods raise `DataError` for
  a blocked symbol instead of returning nothing;
- caches its result in an LRU shared with the views `advance` creates, keyed on the cutoff,
  the lake's write generation, the method and its arguments (hits return copies).

Fundamentals also keep a second LRU across as-of days (`data.view.fundamentals_cache_size`):
a symbol's concept values (`concept_values`, the costly part) are reused while the facts it
knows are the same (keyed on the symbol, the lake's write generation and `known_fact_count`);
the staleness rule and the ratios are applied for each day (`assemble_detail`). Like the main
cache, it sees writes made through this view's `Lake` object only.

Units: prices in USD (raw, plus `adj_close`, our total-return adjustment with the actions known
at `as_of`, relative to each symbol's last bar in the window); returns are simple daily total
returns; ADV in USD and in split-adjusted shares; `prices` / `returns` / `adv` lookbacks are
sessions, `news` / `social` lookbacks calendar days, `macro` a `timedelta` or calendar days.
"""

from collections import OrderedDict
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Self

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError, LookAheadError
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf, assert_no_lookahead, bar_available_at, pit_filter
from atlas.core.storage import AVAILABLE_AT, Lake
from atlas.core.types.instruments import Instrument, normalize_symbol
from atlas.core.types.research import Evidence
from atlas.data.events import Events
from atlas.data.fundamentals import (
    FUNDAMENTALS_FACTS,
    HISTORY_COLUMNS,
    ConceptMap,
    Fundamentals,
    assemble_detail,
    concept_history,
    concept_values,
    known_fact_count,
    known_facts,
)
from atlas.data.macro import Macro, MacroCatalog
from atlas.data.news import News
from atlas.data.prices import CORPORATE_ACTIONS, PRICES_DAILY, adjustment_factors
from atlas.data.quality import (
    blocked_symbols,
    latest_known_session,
    quality_report,
    usable_actions,
)
from atlas.data.reference import read_instruments
from atlas.data.sentiment import Lexicon, Sentiment, SentimentAggregate, Social
from atlas.data.universe import Universe

_log = get_logger(__name__)

PRICE_COLUMNS = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
    "vendor",
    AVAILABLE_AT,
)
"""Columns of `DataView.prices` (raw OHLC in USD, raw volume in shares, `adj_close` ours)."""

ADV_COLUMNS = ("adv_usd", "adv_shares", "sessions")
"""Columns of `DataView.adv`."""


def _positive(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return value


def _symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    """Canonical symbols in request order, without repeats."""
    if isinstance(symbols, str):
        raise ValueError("symbols must be a sequence of tickers, not one string")
    return tuple(dict.fromkeys(normalize_symbol(s) for s in symbols))


@dataclass(frozen=True)
class CacheInfo:
    """Counters of the LRU cache a view shares with the views `advance` creates."""

    hits: int
    misses: int
    size: int
    maxsize: int


class _LRU:
    """A least-recently-used cache of at most `size` results."""

    def __init__(self, size: int) -> None:
        self._data: OrderedDict[Hashable, Any] = OrderedDict()
        self._size = size
        self.hits = 0
        self.misses = 0

    def info(self) -> CacheInfo:
        """The current counters."""
        return CacheInfo(self.hits, self.misses, len(self._data), self._size)

    def get(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        """The cached value of `key`, computing and storing it on a miss."""
        if key in self._data:
            self._data.move_to_end(key)
            self.hits += 1
            return self._data[key]
        self.misses += 1
        value = compute()
        self._data[key] = value
        if len(self._data) > self._size:
            self._data.popitem(last=False)
        return value


def _copy(value: Any) -> Any:
    """A copy a caller may change without changing the cached value."""
    if isinstance(value, pd.DataFrame | pd.Series):
        return value.copy(deep=True)
    if isinstance(value, list):
        return list(value)
    return value


def guard_frame(frame: pd.DataFrame, as_of: AsOf) -> pd.DataFrame:
    """`frame` unchanged if every row with a known `available_at` is known at `as_of`;
    otherwise `LookAheadError`. Frames without `available_at` pass."""
    if AVAILABLE_AT in frame.columns:
        assert_no_lookahead(frame[frame[AVAILABLE_AT].notna()], as_of)
    return frame


def guard_evidence(items: list[Evidence], as_of: AsOf) -> list[Evidence]:
    """`items` unchanged if each became public (`Evidence.as_of`) before the cutoff;
    otherwise `LookAheadError`."""
    late = [e.id for e in items if not as_of.knows(e.as_of)]
    if late:
        raise LookAheadError(
            f"{len(late)} evidence item(s) published at or after the cutoff "
            f"{as_of.cutoff.isoformat()}: {late[:5]}"
        )
    return items


class _Shared:
    """What a view and the views `advance` creates share: lake, config, calendar, the
    optional reference inputs, the lazily built readers and the cache."""

    def __init__(
        self,
        lake: Lake,
        config: AtlasConfig,
        calendar: TradingCalendar,
        concept_map: ConceptMap | None,
        catalog: MacroCatalog | None,
        lexicon: Lexicon | None,
    ) -> None:
        self.lake = lake
        self.config = config
        self.calendar = calendar
        self.concept_map = concept_map
        self.catalog = catalog
        self.lexicon = lexicon
        self.cache = _LRU(config.data.view.cache_size)
        self.fundamentals_cache = _LRU(config.data.view.fundamentals_cache_size)
        self.readers: dict[str, Any] = {}


class DataView:
    """Read-only, point-in-time access to every dataset of the lake at `as_of`.

    `as_of` is an `AsOf` or a trading day (a `date` uses `config.calendar.decision_time`).
    `config` defaults to `AtlasConfig()`; `calendar` to `TradingCalendar(config=config.calendar)`;
    `concept_map`, `catalog` and `lexicon` default to the files named in `config` (loaded on
    first use, relative to the working directory).
    """

    __slots__ = ("_as_of", "_shared")

    _as_of: AsOf
    _shared: _Shared

    def __init__(
        self,
        lake: Lake,
        as_of: date | AsOf,
        config: AtlasConfig | None = None,
        *,
        calendar: TradingCalendar | None = None,
        concept_map: ConceptMap | None = None,
        catalog: MacroCatalog | None = None,
        lexicon: Lexicon | None = None,
    ) -> None:
        cfg = config if config is not None else AtlasConfig()
        cal = calendar if calendar is not None else TradingCalendar(config=cfg.calendar)
        shared = _Shared(lake, cfg, cal, concept_map, catalog, lexicon)
        object.__setattr__(self, "_shared", shared)
        object.__setattr__(self, "_as_of", self._point(as_of))

    @classmethod
    def _create(cls, shared: _Shared, point: AsOf) -> Self:
        view = object.__new__(cls)
        object.__setattr__(view, "_shared", shared)
        object.__setattr__(view, "_as_of", point)
        return view

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"DataView is immutable (cannot set {name!r}); use advance()")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"DataView is immutable (cannot delete {name!r})")

    def __repr__(self) -> str:
        return f"DataView(as_of={self._as_of.cutoff.isoformat()})"

    def _point(self, as_of: date | AsOf) -> AsOf:
        if isinstance(as_of, AsOf):
            return as_of
        if isinstance(as_of, datetime):
            raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
        return AsOf.for_session(as_of, config=self._shared.config.calendar)

    # --- identity -------------------------------------------------------------------------------

    @property
    def as_of(self) -> AsOf:
        """The decision moment this view is bound to."""
        return self._as_of

    @property
    def day(self) -> date:
        """The trading day of `as_of`."""
        return self._as_of.day

    @property
    def config(self) -> AtlasConfig:
        """The configuration the view was built with."""
        return self._shared.config

    def advance(self, new_as_of: date | AsOf) -> "DataView":
        """A new view at `new_as_of`, sharing this view's readers and cache. Moving back in
        time raises `ValueError` (a backtest only moves forward)."""
        point = self._point(new_as_of)
        if point.cutoff < self._as_of.cutoff:
            raise ValueError(
                f"cannot advance to {point.cutoff.isoformat()}: it is before this view's "
                f"cutoff {self._as_of.cutoff.isoformat()}"
            )
        return self._create(self._shared, point)

    # --- caching and quality --------------------------------------------------------------------

    def cache_info(self) -> CacheInfo:
        """Hits, misses and size of the cache shared with the views `advance` creates."""
        return self._shared.cache.info()

    def _cached(self, method: str, args: tuple[Hashable, ...], compute: Callable[[], Any]) -> Any:
        key = (self._as_of.cutoff, self._shared.lake.generation, method, args)
        return _copy(self._shared.cache.get(key, compute))

    def quality_issues(self) -> pd.DataFrame:
        """The day's quality report (`quality_report` at `as_of`, not persisted)."""
        return self._cached("quality_issues", (), self._quality)

    def _quality(self) -> pd.DataFrame:
        shared = self._shared
        return guard_frame(
            quality_report(
                shared.lake,
                self._as_of,
                config=shared.config,
                calendar=shared.calendar,
                concept_map=shared.concept_map,
            ),
            self._as_of,
        )

    @property
    def blocked(self) -> frozenset[str]:
        """Symbols and macro series ids with a `critical` issue in the day's quality report."""
        return self._cached("blocked", (), lambda: blocked_symbols(self.quality_issues()))

    def _require_unblocked(self, symbol: str) -> None:
        if symbol in self.blocked:
            report = self.quality_issues()
            rows = report[(report["symbol"] == symbol) & (report["severity"] == "critical")]
            reasons = "; ".join(
                f"{c} on {d}" for c, d in zip(rows["check"], rows["date"], strict=True)
            )
            raise DataError(
                f"{symbol} is blocked on {self.day.isoformat()} by data quality issues: {reasons}"
            )

    def _allowed(self, symbols: Sequence[str]) -> tuple[str, ...]:
        blocked = self.blocked
        return tuple(s for s in _symbols(symbols) if s not in blocked)

    def _known(self, table: str, filters: list[tuple[str, str, object]]) -> pd.DataFrame:
        frame = self._shared.lake.read_table(table, filters=filters)
        known = pit_filter(frame, self._as_of)
        assert_no_lookahead(known, self._as_of)
        return known

    def _sessions(self, n: int) -> tuple[date, ...]:
        """The last `n` sessions whose bars are known at `as_of`, oldest first."""
        cal = self._shared.calendar
        last = latest_known_session(self._as_of, cal)
        return cal.sessions(cal.offset(last, -(n - 1)), last)

    # --- prices ---------------------------------------------------------------------------------

    def sessions(self, lookback: int) -> tuple[date, ...]:
        """The last `lookback` trading sessions whose daily bars are known at `as_of`, oldest
        first: the session grid of `prices`, `returns` and `adv` (the last one is the latest
        session whose bar is published before the cutoff). Raises `LookAheadError` if that
        grid ever ends at a bar published after the cutoff."""
        n = _positive("lookback", lookback)

        def compute() -> tuple[date, ...]:
            days = self._sessions(n)
            published = bar_available_at(days[-1], self._shared.calendar)
            if not self._as_of.knows(published):
                raise LookAheadError(
                    f"session {days[-1].isoformat()} is published at {published.isoformat()}, "
                    f"not before the cutoff {self._as_of.cutoff.isoformat()}"
                )
            return days

        return self._cached("sessions", (n,), compute)

    def _bars_and_actions(
        self, symbols: tuple[str, ...], n: int
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Known bars of `symbols` over the last `n` sessions (sorted by symbol and date) and
        the usable corporate actions known at `as_of` that go ex inside that window."""
        sessions = self._sessions(n)
        first, last = sessions[0], sessions[-1]
        bars = self._known(
            PRICES_DAILY.name,
            [("symbol", "in", list(symbols)), ("date", ">=", first), ("date", "<=", last)],
        )
        actions = self._known(
            CORPORATE_ACTIONS.name,
            [("symbol", "in", list(symbols)), ("ex_date", ">", first), ("ex_date", "<=", last)],
        )
        bars = bars.sort_values(["symbol", "date"], kind="stable", ignore_index=True)
        kept = []
        for symbol, own in actions.groupby("symbol", sort=True):
            usable, rejected = usable_actions(bars[bars["symbol"] == symbol], own)
            for day, reason in rejected:
                _log.warning(
                    "view.action_skipped", symbol=symbol, day=day.isoformat(), reason=reason
                )
            kept.append(usable)
        usable_all = pd.concat(kept, ignore_index=True) if kept else actions
        return bars, usable_all

    def _bars(self, symbols: tuple[str, ...], n: int) -> pd.DataFrame:
        """Known bars of `symbols` over the last `n` sessions plus our adjustment columns
        (`split_factor`, `total_factor`, `calc_adj_close`; actions known at `as_of`)."""
        bars, actions = self._bars_and_actions(symbols, n)
        return guard_frame(adjustment_factors(bars, actions), self._as_of)

    def prices(self, symbols: Sequence[str], lookback: int) -> pd.DataFrame:
        """Daily bars of `symbols` over the last `lookback` sessions whose bars are known at
        `as_of` (`PRICE_COLUMNS`, sorted by symbol and date; blocked symbols left out).

        OHLC are raw USD prices and `volume` raw shares; `adj_close` is the close adjusted for
        splits and cash dividends (CRSP style) with the actions known at `as_of`, relative to
        each symbol's last bar in the window (not the vendor's adjusted close).
        """
        wanted, n = self._allowed(symbols), _positive("lookback", lookback)

        def compute() -> pd.DataFrame:
            bars = self._bars(wanted, n).rename(columns={"adj_close": "vendor_adj_close"})
            bars["adj_close"] = bars["calc_adj_close"]
            return bars[list(PRICE_COLUMNS)].reset_index(drop=True)

        return self._cached("prices", (wanted, n), compute)

    def returns(self, symbols: Sequence[str], lookback: int) -> pd.DataFrame:
        """Simple daily total returns (from `adj_close`) of `symbols` for the last `lookback`
        sessions whose bars are known: index `date` (tz-naive `DatetimeIndex`, one row per
        session), one float column per unblocked symbol in request order. A missing bar gives
        NaN on its session and the next; a symbol with no bars is all NaN."""
        wanted, n = self._allowed(symbols), _positive("lookback", lookback)

        def compute() -> pd.DataFrame:
            sessions = self._sessions(n + 1)
            bars = self.prices(wanted, n + 1)
            wide = bars.pivot(index="date", columns="symbol", values="adj_close")
            wide = wide.reindex(index=list(sessions), columns=list(wanted)).astype("float64")
            out = wide.pct_change(fill_method=None).iloc[1:]
            out.index = pd.DatetimeIndex(pd.to_datetime(list(sessions[1:])), name="date")
            out.columns.name = None
            return out

        return self._cached("returns", (wanted, n), compute)

    def adv(self, symbols: Sequence[str], lookback: int | None = None) -> pd.DataFrame:
        """Average daily volume of `symbols` over the last `lookback` sessions whose bars are
        known (default `limits.adv_window_days`): index `symbol` (unblocked, request order),
        `adv_usd` = mean of raw close x volume (USD), `adv_shares` = mean volume in today's
        shares (split-adjusted), `sessions` = bars used (NaN averages with none)."""
        wanted = self._allowed(symbols)
        n = _positive(
            "lookback", self._shared.config.limits.adv_window_days if lookback is None else lookback
        )

        def compute() -> pd.DataFrame:
            bars = self._bars(wanted, n)
            bars = bars.assign(
                dollars=bars["close"] * bars["volume"],
                shares=bars["volume"] / bars["split_factor"],
            )
            grouped = bars.groupby("symbol", sort=False)
            out = pd.DataFrame(
                {
                    "adv_usd": grouped["dollars"].mean(),
                    "adv_shares": grouped["shares"].mean(),
                    "sessions": grouped.size(),
                }
            ).reindex(list(wanted))
            out["sessions"] = out["sessions"].fillna(0).astype("int64")
            out.index.name = "symbol"
            return out[list(ADV_COLUMNS)].astype({"adv_usd": "float64", "adv_shares": "float64"})

        return self._cached("adv", (wanted, n), compute)

    def split_factors(self, symbols: Sequence[str], lookback: int) -> pd.DataFrame:
        """The split factor of each of the last `lookback` sessions whose bars are known:
        index `date` (tz-naive `DatetimeIndex`, the `sessions(lookback)` grid), one float column
        per unblocked symbol in request order. A session's factor is the product of
        `1 / ratio` over the usable splits known at `as_of` that go ex after it, inside the
        window (1.0 when there are none), as in `prices`' adjustment: a share count dated on
        a session, divided by its factor, is in today's shares."""
        wanted, n = self._allowed(symbols), _positive("lookback", lookback)

        def compute() -> pd.DataFrame:
            sessions = self._sessions(n)
            _, actions = self._bars_and_actions(wanted, n)
            splits = actions[actions["type"] == "split"]
            days = np.array(sessions, dtype="datetime64[D]")
            out = pd.DataFrame(
                1.0,
                index=pd.DatetimeIndex(pd.to_datetime(list(sessions)), name="date"),
                columns=list(wanted),
            )
            for symbol, ex_date, ratio in zip(
                splits["symbol"], splits["ex_date"], splits["ratio"], strict=True
            ):
                before = days < np.datetime64(ex_date, "D")
                out.loc[before, symbol] = out.loc[before, symbol] / float(ratio)
            out.columns.name = None
            return out.astype("float64")

        return self._cached("split_factors", (wanted, n), compute)

    # --- readers --------------------------------------------------------------------------------

    def _reader(self, name: str, build: Callable[[], Any]) -> Any:
        readers = self._shared.readers
        if name not in readers:
            readers[name] = build()
        return readers[name]

    @property
    def _universe(self) -> Universe:
        sh = self._shared
        return self._reader("universe", lambda: Universe(sh.lake, sh.config, calendar=sh.calendar))

    @property
    def _fundamentals(self) -> Fundamentals:
        sh = self._shared
        return self._reader(
            "fundamentals", lambda: Fundamentals(sh.lake, sh.config, concept_map=sh.concept_map)
        )

    @property
    def _macro(self) -> Macro:
        sh = self._shared
        return self._reader("macro", lambda: Macro(sh.lake, sh.config, catalog=sh.catalog))

    @property
    def _news(self) -> News:
        sh = self._shared
        return self._reader("news", lambda: News(sh.lake, sh.config))

    @property
    def _events(self) -> Events:
        sh = self._shared
        return self._reader("events", lambda: Events(sh.lake, sh.config, sh.calendar))

    @property
    def _sentiment(self) -> Sentiment:
        sh = self._shared
        return self._reader("sentiment", lambda: Sentiment(sh.lake, sh.config, sh.lexicon))

    @property
    def _social(self) -> Social:
        sh = self._shared
        return self._reader("social", lambda: Social(sh.lake, sh.config))

    def _one(self, symbol: str) -> str:
        """`symbol` in canonical form, if it is not blocked (`DataError` otherwise)."""
        canonical = normalize_symbol(symbol)
        self._require_unblocked(canonical)
        return canonical

    # --- fundamentals ---------------------------------------------------------------------------

    def fundamentals(self, symbol: str) -> pd.Series:
        """The point-in-time fundamentals of `symbol` (`Fundamentals.pit`): a float Series
        indexed by metric (USD, USD per share, shares; ratios as fractions; NaN where
        unavailable), named after the symbol. A blocked symbol raises `DataError`."""
        sym = self._one(symbol)

        def compute() -> pd.Series:
            detail = self._fundamentals_detail(sym, self._fundamentals.facts(sym))
            return pd.Series(
                detail["value"].to_numpy(dtype="float64"), index=list(detail["metric"]), name=sym
            )

        return self._cached("fundamentals", (sym,), compute)

    def fundamentals_panel(self, symbols: Sequence[str]) -> pd.DataFrame:
        """`fundamentals` of each unblocked symbol of `symbols`: index `symbol` (request
        order), one float column per metric (all NaN for a symbol with no stored facts).
        Raises `DataError` when `fundamentals_facts` was never written."""
        wanted = self._allowed(symbols)

        def compute() -> pd.DataFrame:
            lake = self._shared.lake
            if not lake.has_table(FUNDAMENTALS_FACTS.name):
                raise DataError("fundamentals_facts is empty: run ingest_fundamentals first")
            facts = lake.read_table(
                FUNDAMENTALS_FACTS.name, filters=[("symbol", "in", list(wanted))]
            )
            own = dict(tuple(facts.groupby("symbol", sort=False)))
            empty = facts.iloc[0:0]
            rows = {}
            for sym in wanted:
                detail = self._fundamentals_detail(sym, own.get(sym, empty))
                rows[sym] = pd.Series(
                    detail["value"].to_numpy(dtype="float64"), index=list(detail["metric"])
                )
            if not rows:
                return pd.DataFrame(index=pd.Index([], name="symbol", dtype="object"))
            panel = pd.DataFrame(rows).T.astype("float64")
            panel.index.name = "symbol"
            return panel

        return self._cached("fundamentals_panel", (wanted,), compute)

    def fundamentals_history(self, symbols: Sequence[str], concepts: Sequence[str]) -> pd.DataFrame:
        """Every fiscal period of `concepts` (concept-map names such as `revenue`,
        `shares_outstanding`) known at `as_of`, for each unblocked symbol of `symbols`:
        `symbol` + `HISTORY_COLUMNS` (`concept_history`), symbols in request order, then
        concept and `period_end`. Flows give the discrete quarter (`value`) and the TTM
        (`ttm`) at each period end, stocks each balance; units are the concept's. A restatement
        is used once it is public; no staleness rule is applied (callers compare `period_end`
        with `as_of`). Raises `ValueError` for an unknown concept and `DataError` when
        `fundamentals_facts` was never written."""
        if isinstance(concepts, str):
            raise ValueError("concepts must be a sequence of concept names, not one string")
        names = tuple(dict.fromkeys(c.strip() for c in concepts))
        cmap = self._fundamentals.concept_map
        unknown = [c for c in names if c not in cmap]
        if unknown:
            raise ValueError(f"unknown concept(s) {unknown}; known: {list(cmap)}")
        wanted = self._allowed(symbols)

        def compute() -> pd.DataFrame:
            lake = self._shared.lake
            if not lake.has_table(FUNDAMENTALS_FACTS.name):
                raise DataError("fundamentals_facts is empty: run ingest_fundamentals first")
            facts = lake.read_table(
                FUNDAMENTALS_FACTS.name, filters=[("symbol", "in", list(wanted))]
            )
            own = dict(tuple(facts.groupby("symbol", sort=False)))
            empty = facts.iloc[0:0]
            frames = []
            for sym in wanted:
                part = self._history_of(sym, own.get(sym, empty), names)
                if not part.empty:
                    frames.append(part.assign(symbol=sym))
            if not frames:
                return concept_history(
                    empty, cmap, names, self._shared.config.data.fundamentals
                ).assign(symbol=pd.Series(dtype="str"))[["symbol", *HISTORY_COLUMNS]]
            out = pd.concat(frames, ignore_index=True)
            return guard_frame(out[["symbol", *HISTORY_COLUMNS]], self._as_of)

        return self._cached("fundamentals_history", (wanted, names), compute)

    def _history_of(self, symbol: str, facts: pd.DataFrame, names: tuple[str, ...]) -> pd.DataFrame:
        """`concept_history` of one symbol's stored `facts` at this view's as-of, from the
        cross-day cache while the known facts are the same."""
        shared, point = self._shared, self._as_of
        key = ("history", symbol, shared.lake.generation, known_fact_count(facts, point), names)
        return shared.fundamentals_cache.get(
            key,
            lambda: concept_history(
                known_facts(facts, point),
                self._fundamentals.concept_map,
                names,
                shared.config.data.fundamentals,
            ),
        )

    def _fundamentals_detail(self, symbol: str, facts: pd.DataFrame) -> pd.DataFrame:
        """`fundamentals_pit_detail` of one symbol's stored `facts` at this view's as-of, with
        the concept values taken from the cross-day cache when the known facts are the same."""
        shared, point = self._shared, self._as_of
        settings = shared.config.data.fundamentals
        concepts = self._fundamentals.concept_map
        key = (symbol, shared.lake.generation, known_fact_count(facts, point))
        values = shared.fundamentals_cache.get(
            key, lambda: concept_values(known_facts(facts, point), concepts, settings)
        )
        return guard_frame(assemble_detail(values, point, settings), point)

    # --- macro ----------------------------------------------------------------------------------

    def macro(self, series: Sequence[str], lookback: timedelta | int) -> pd.DataFrame:
        """The point-in-time macro view (`Macro.pit`): index `obs_date` (tz-naive), one float
        column per requested series id in request order, observations dated within `lookback`
        (a `timedelta`, or calendar days) before `as_of.day`. Series ids must be in the macro
        catalog; a series with a `critical` quality issue is left out."""
        if isinstance(series, str):
            raise ValueError("series must be a sequence of FRED ids, not one string")
        span = (
            timedelta(days=_positive("lookback", lookback))
            if isinstance(lookback, int)
            else lookback
        )
        if span <= timedelta(0):
            raise ValueError(f"lookback must be positive, got {lookback!r}")
        blocked = self.blocked
        ids = tuple(s for s in dict.fromkeys(x.strip().upper() for x in series) if s not in blocked)

        def compute() -> pd.DataFrame:
            detail = guard_frame(self._macro.pit_detail(list(ids), self._as_of, span), self._as_of)
            wide = detail.pivot(index="obs_date", columns="series_id", values="value")
            wide = wide.reindex(columns=list(ids)).astype("float64")
            wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index), name="obs_date")
            wide.columns.name = None
            return wide.sort_index()

        return self._cached("macro", (ids, span), compute)

    # --- news, sentiment, social ----------------------------------------------------------------

    def news(
        self, symbol: str, lookback: int | None = None, limit: int | None = None
    ) -> list[Evidence]:
        """News about `symbol` known at `as_of` (`News.pit`), newest first, as `Evidence`;
        `lookback` in calendar days and `limit` default to `data.news`."""
        sym = self._one(symbol)
        return self._cached(
            "news",
            (sym, lookback, limit),
            lambda: guard_evidence(self._news.pit(sym, self._as_of, lookback, limit), self._as_of),
        )

    def market_news(self, lookback: int | None = None, limit: int | None = None) -> list[Evidence]:
        """Market-wide news known at `as_of` (`News.market`), as `Evidence`."""
        return self._cached(
            "market_news",
            (lookback, limit),
            lambda: guard_evidence(self._news.market(self._as_of, lookback, limit), self._as_of),
        )

    def sentiment(self, symbol: str) -> SentimentAggregate:
        """The lexicon news sentiment of `symbol` at `as_of` (`Sentiment.at`: score in
        [-1, 1], NaN without articles)."""
        sym = self._one(symbol)
        return self._cached("sentiment", (sym,), lambda: self._sentiment.at(sym, self._as_of))

    def sentiment_daily(self, symbol: str, days: int | None = None) -> pd.DataFrame:
        """The daily sentiment series of `symbol` up to `as_of` (`Sentiment.daily`; `days` in
        sessions, default `data.sentiment.daily_days`)."""
        sym = self._one(symbol)
        return self._cached(
            "sentiment_daily", (sym, days), lambda: self._sentiment.daily(sym, self._as_of, days)
        )

    def social(
        self, symbol: str, lookback: int | None = None, limit: int | None = None
    ) -> list[Evidence]:
        """Stored social posts about `symbol` known at `as_of` (`Social.evidence`), newest
        first; `lookback` in calendar days."""
        sym = self._one(symbol)
        return self._cached(
            "social",
            (sym, lookback, limit),
            lambda: guard_evidence(
                self._social.evidence(sym, self._as_of, lookback, limit), self._as_of
            ),
        )

    # --- events ---------------------------------------------------------------------------------

    def events(
        self, symbols: Sequence[str] | None = None, horizon_days: int | None = None
    ) -> pd.DataFrame:
        """Upcoming events known at `as_of` (`Events.upcoming`): macro events always, company
        events of `symbols` (all when `None`) without blocked symbols; `horizon_days` in
        calendar days (default `data.events.horizon_days`)."""
        wanted = None if symbols is None else self._allowed(symbols)

        def compute() -> pd.DataFrame:
            frame = self._events.upcoming(
                self._as_of, horizon_days, None if wanted is None else list(wanted)
            )
            blocked = self.blocked
            frame = frame[~frame["symbol"].isin(blocked)].reset_index(drop=True)
            return guard_frame(frame, self._as_of)

        return self._cached("events", (wanted, horizon_days), compute)

    def past_events(self, symbol: str, n: int | None = None) -> pd.DataFrame:
        """The last `n` events of `symbol` known at `as_of` (`Events.past`), newest first."""
        sym = self._one(symbol)
        return self._cached(
            "past_events",
            (sym, n),
            lambda: guard_frame(self._events.past(sym, self._as_of, n), self._as_of),
        )

    def earnings_reactions(self, symbol: str, n: int | None = None) -> pd.DataFrame:
        """The last `n` earnings reactions of `symbol` known at `as_of` (`Events.reactions`;
        simple returns)."""
        sym = self._one(symbol)
        return self._cached(
            "earnings_reactions",
            (sym, n),
            lambda: guard_frame(self._events.reactions(sym, self._as_of, n), self._as_of),
        )

    # --- universe and reference -----------------------------------------------------------------

    def universe(self, name: str = "sp500") -> list[str]:
        """Sorted members of universe `name` on `as_of.day` (`Universe.members`), without
        blocked symbols."""
        blocked = self.blocked
        return self._cached(
            "universe",
            (name,),
            lambda: [s for s in self._universe.members(self._as_of, name) if s not in blocked],
        )

    def tradable(self, name: str = "sp500") -> list[str]:
        """Sorted members of `name` that pass the liquidity filters at `as_of`
        (`Universe.tradable`, `data.tradable`), without blocked symbols."""
        blocked = self.blocked
        return self._cached(
            "tradable",
            (name,),
            lambda: [s for s in self._universe.tradable(self._as_of, name) if s not in blocked],
        )

    def instrument(self, symbol: str) -> Instrument:
        """The reference data of `symbol` (static: names, sectors and exchanges are today's
        values). An unknown or blocked symbol raises `DataError`."""
        sym = self._one(symbol)
        found = self._cached(
            "instruments", (), lambda: {i.symbol: i for i in read_instruments(self._shared.lake)}
        )
        if sym not in found:
            raise DataError(f"no instrument {sym!r} in the instruments table")
        return found[sym]
