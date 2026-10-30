"""Point-in-time event queries: what was scheduled ahead of `as_of`, what had happened by it.

Queries work on the *event view* (`EVENT_VIEW_COLUMNS`): stored company events, macro events
(`symbol` `None`, `time_of_day` from their time) and ex-dividend events derived from Stage 07's
`corporate_actions` (`dividend_events`; known from the ex-date bar, so never ahead of time). A
row is known at `as_of` iff `available_at < as_of.cutoff` (`pit_filter`, checked again with
`assert_no_lookahead`) and it was not withdrawn before the cutoff.

- `upcoming_events`: known events dated from `as_of.day` to `as_of.day + horizon_days`
  (calendar days, inclusive) that have not happened before the cutoff. Scheduled earnings and
  ex-dividend dates (`source` `yahoo`) come from the latest known snapshot of each symbol and
  event type only, and are dropped once an actual event of that type (an earnings filing, a
  seen ex-date) dated on or after `date - scheduled_match_days` is known. Optionally, a symbol
  without a current scheduled earnings date gets an estimated one (`estimated_earnings`:
  a past report date plus 52 weeks, from known events only; `source` `estimated`).
- `past_events`: the symbol's known events that happened before the cutoff, scheduled rows
  excluded, newest first.
- `known_reactions`: the symbol's earnings reactions known at `as_of`, newest first.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta

import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.pit import AsOf, assert_no_lookahead, pit_filter
from atlas.core.storage import AVAILABLE_AT, Lake
from atlas.core.types.instruments import normalize_symbol
from atlas.data.events.base import (
    EVENT_COLUMNS,
    EVENT_VIEW_COLUMNS,
    EVENTS,
    MACRO_EVENTS,
    classify_time_of_day,
)
from atlas.data.events.reactions import EARNINGS_REACTIONS, REACTION_COLUMNS
from atlas.data.prices import CORPORATE_ACTIONS

SCHEDULED_SOURCES = ("yahoo",)
"""Sources of scheduled (not yet happened) earnings and ex-dividend dates."""

ESTIMATED_SOURCE = "estimated"
"""`source` of an estimated next earnings date (computed at query time, never stored)."""


def _positive(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")


def _stamps(values: Sequence[object]) -> pd.Series:
    return pd.to_datetime(pd.Series(list(values), dtype="object"), utc=True).dt.as_unit("us")


def _empty_view() -> pd.DataFrame:
    out = pd.DataFrame({c: pd.Series([], dtype="object") for c in EVENT_VIEW_COLUMNS})
    for col in ("event_time", AVAILABLE_AT, "withdrawn_at"):
        out[col] = _stamps([])
    return out


def dividend_events(actions: pd.DataFrame) -> pd.DataFrame:
    """Each `dividend` row of `actions` (`corporate_actions` columns) as an event-view row:
    `event_type` `ex_dividend`, `event_id` `div-<SYMBOL>-<ex_date>`, no time, `source`
    `corporate_actions`, the action's `available_at` (its ex-date bar)."""
    paid = actions.loc[actions["type"] == "dividend"]
    if paid.empty:
        return _empty_view()
    out = pd.DataFrame(
        {
            "event_id": [
                f"div-{s}-{d.isoformat()}"
                for s, d in zip(paid["symbol"], paid["ex_date"], strict=True)
            ],
            "symbol": paid["symbol"].astype("str").to_numpy(),
            "event_type": "ex_dividend",
            "event_date": list(paid["ex_date"]),
            "event_time": _stamps([None] * len(paid)).to_numpy(),
            "time_of_day": "unknown",
            "source": "corporate_actions",
            AVAILABLE_AT: paid[AVAILABLE_AT].to_numpy(),
            "withdrawn_at": _stamps([None] * len(paid)).to_numpy(),
        }
    )
    for col in ("event_time", AVAILABLE_AT, "withdrawn_at"):
        out[col] = _stamps(list(out[col]))
    return out[list(EVENT_VIEW_COLUMNS)]


def event_view(
    events: pd.DataFrame,
    macro_events: pd.DataFrame,
    dividends: pd.DataFrame,
    calendar: TradingCalendar,
) -> pd.DataFrame:
    """The union of company `events`, `macro_events` and `dividends` (from
    `dividend_events`) as `EVENT_VIEW_COLUMNS`."""
    company = events.assign(withdrawn_at=_stamps([None] * len(events)).to_numpy())
    macro = macro_events.assign(
        symbol=None,
        time_of_day=[
            classify_time_of_day(None if pd.isna(t) else t.to_pydatetime(), calendar)
            for t in macro_events["event_time"]
        ],
    )
    parts = [p[list(EVENT_VIEW_COLUMNS)] for p in (company, macro, dividends) if not p.empty]
    if not parts:
        return _empty_view()
    out = pd.concat(parts, ignore_index=True)
    for col in ("event_time", AVAILABLE_AT, "withdrawn_at"):
        out[col] = _stamps(list(out[col]))
    out["symbol"] = pd.Series(list(out["symbol"]), dtype="object")
    return out


def _known(view: pd.DataFrame, as_of: AsOf) -> pd.DataFrame:
    known = pit_filter(view, as_of)
    assert_no_lookahead(known, as_of)
    withdrawn = known["withdrawn_at"].notna() & (known["withdrawn_at"] < pd.Timestamp(as_of.cutoff))
    known = known.loc[~withdrawn]
    first = known.sort_values([AVAILABLE_AT, "event_id"], kind="stable")
    return first.drop_duplicates(subset=["event_id"], keep="first")


def _is_actual(frame: pd.DataFrame) -> pd.Series:
    """Rows recording an event that happened: an earnings filing or a seen ex-dividend."""
    earnings = (frame["event_type"] == "earnings") & frame["source"].str.startswith("sec_")
    dividend = (frame["event_type"] == "ex_dividend") & (frame["source"] == "corporate_actions")
    return earnings | dividend


def _current_schedule(known: pd.DataFrame, scheduled_match_days: int) -> pd.DataFrame:
    """`known` with scheduled rows reduced to the latest snapshot of each symbol and event
    type, minus those an actual event of the same type already covers."""
    is_scheduled = known["source"].isin(SCHEDULED_SOURCES)
    scheduled = known.loc[is_scheduled]
    if scheduled.empty:
        return known
    latest = scheduled.groupby(["symbol", "event_type"])[AVAILABLE_AT].transform("max")
    scheduled = scheduled.loc[scheduled[AVAILABLE_AT] == latest]
    actual = known.loc[_is_actual(known)]
    last_actual = actual.groupby(["symbol", "event_type"])["event_date"].max().to_dict()
    window = timedelta(days=scheduled_match_days)
    covered = [
        (s, k) in last_actual and last_actual[(s, k)] >= d - window
        for s, k, d in zip(
            scheduled["symbol"], scheduled["event_type"], scheduled["event_date"], strict=True
        )
    ]
    scheduled = scheduled.loc[[not c for c in covered]]
    return pd.concat([known.loc[~is_scheduled], scheduled])


def estimated_earnings(known: pd.DataFrame, min_gap_days: int) -> pd.DataFrame:
    """Estimated next earnings dates (event-view rows, `source` `estimated`) from the known
    rows `known`: for each symbol without a current scheduled earnings date, the earliest
    known actual earnings date plus 52 weeks that falls more than `min_gap_days` calendar days
    after its latest known actual earnings event. The row takes the `time_of_day` of the event
    it is projected from and the `available_at` of the latest actual event (it is computed only
    from rows known by then)."""
    actual = known.loc[_is_actual(known) & (known["event_type"] == "earnings")]
    scheduled = set(
        known.loc[known["source"].isin(SCHEDULED_SOURCES) & (known["event_type"] == "earnings"),
                  "symbol"]
    )  # fmt: skip
    rows = []
    for symbol, own in actual.groupby("symbol", sort=True):
        if symbol in scheduled:
            continue
        latest = own.sort_values([AVAILABLE_AT, "event_id"]).iloc[-1]
        floor = latest["event_date"] + timedelta(days=min_gap_days)
        projected = own.assign(_next=[d + timedelta(weeks=52) for d in own["event_date"]])
        projected = projected.loc[[n > floor for n in projected["_next"]]]
        if projected.empty:
            continue
        source_row = projected.sort_values(["_next", "event_id"]).iloc[0]
        day = source_row["_next"]
        rows.append(
            {
                "event_id": f"est-{symbol}-{day.isoformat()}",
                "symbol": symbol,
                "event_type": "earnings",
                "event_date": day,
                "event_time": None,
                "time_of_day": source_row["time_of_day"],
                "source": ESTIMATED_SOURCE,
                AVAILABLE_AT: latest[AVAILABLE_AT],
                "withdrawn_at": None,
            }
        )
    if not rows:
        return _empty_view()
    out = pd.DataFrame(rows, columns=list(EVENT_VIEW_COLUMNS))
    for col in ("event_time", AVAILABLE_AT, "withdrawn_at"):
        out[col] = _stamps(list(out[col]))
    return out


def upcoming_events(
    view: pd.DataFrame,
    as_of: AsOf,
    horizon_days: int,
    *,
    scheduled_match_days: int,
    estimate_min_gap_days: int | None = None,
) -> pd.DataFrame:
    """Events expected from `as_of` to `horizon_days` calendar days after its day (see the
    module docstring), as `EVENT_COLUMNS`, sorted by date, time (none last), symbol (macro
    first) and id. `scheduled_match_days` is in calendar days. With `estimate_min_gap_days`,
    estimated earnings dates (`estimated_earnings`) are included."""
    _positive("horizon_days", horizon_days)
    known = _current_schedule(_known(view, as_of), scheduled_match_days)
    if estimate_min_gap_days is not None:
        _positive("estimate_min_gap_days", estimate_min_gap_days)
        estimates = estimated_earnings(known, estimate_min_gap_days)
        if not estimates.empty:
            known = pd.concat([known, estimates], ignore_index=True)
    end = as_of.day + timedelta(days=horizon_days)
    in_window = known["event_date"].map(lambda d: as_of.day <= d <= end).astype(bool)
    not_yet = known["event_time"].isna() | (known["event_time"] >= pd.Timestamp(as_of.cutoff))
    chosen = known.loc[in_window & not_yet].assign(_sym=lambda f: f["symbol"].fillna(""))
    chosen = chosen.sort_values(
        ["event_date", "event_time", "_sym", "event_id"], kind="stable", na_position="last"
    )
    return chosen[list(EVENT_COLUMNS)].reset_index(drop=True)


def past_events(view: pd.DataFrame, symbol: str, as_of: AsOf, n: int) -> pd.DataFrame:
    """The last `n` events of `symbol` that happened before `as_of.cutoff` (see the module
    docstring), newest first, as `EVENT_COLUMNS`."""
    _positive("n", n)
    known = _known(view, as_of)
    own = known.loc[
        (known["symbol"] == normalize_symbol(symbol)) & ~known["source"].isin(SCHEDULED_SOURCES)
    ]
    timed = own["event_time"].notna() & (own["event_time"] < pd.Timestamp(as_of.cutoff))
    dated = own["event_time"].isna() & own["event_date"].map(lambda d: d < as_of.day).astype(bool)
    chosen = own.loc[timed | dated].sort_values(
        ["event_date", "event_time", "event_id"],
        ascending=[False, False, True],
        kind="stable",
        na_position="last",
    )
    return chosen.head(n)[list(EVENT_COLUMNS)].reset_index(drop=True)


def known_reactions(reactions: pd.DataFrame, symbol: str, as_of: AsOf, n: int) -> pd.DataFrame:
    """The last `n` earnings reactions of `symbol` known at `as_of` (`REACTION_COLUMNS`),
    newest reaction session first."""
    _positive("n", n)
    known = pit_filter(reactions, as_of)
    assert_no_lookahead(known, as_of)
    own = known.loc[known["symbol"] == normalize_symbol(symbol)]
    own = own.sort_values(["reaction_date", "event_id"], ascending=[False, True], kind="stable")
    return own.head(n)[list(REACTION_COLUMNS)].reset_index(drop=True)


class Events:
    """Point-in-time events read from the lake (`events`, `macro_events`,
    `corporate_actions`, `earnings_reactions`; a table never written reads as empty).

    `config` defaults to `AtlasConfig()` (its calendar sets the decision time of a plain
    `date` as-of; `data.events` the defaults `horizon_days`, `past_n`, `scheduled_match_days`).
    """

    def __init__(
        self,
        lake: Lake,
        config: AtlasConfig | None = None,
        calendar: TradingCalendar | None = None,
    ) -> None:
        self._lake = lake
        self._cfg = config if config is not None else AtlasConfig()
        self._settings = self._cfg.data.events
        self._cal = calendar if calendar is not None else TradingCalendar(config=self._cfg.calendar)

    def _as_of(self, as_of: date | AsOf) -> AsOf:
        if isinstance(as_of, AsOf):
            return as_of
        if isinstance(as_of, datetime):
            raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
        return AsOf.for_session(as_of, config=self._cfg.calendar)

    def view(self, symbols: Sequence[str] | None = None) -> pd.DataFrame:
        """The event view of the stored tables; company events and dividends only of `symbols`
        when given (macro events always)."""
        wanted = None if symbols is None else sorted({normalize_symbol(s) for s in symbols})
        by_symbol = None if wanted is None else [("symbol", "in", wanted)]
        events = self._lake.read_table(EVENTS.name, filters=by_symbol)
        macro = self._lake.read_table(MACRO_EVENTS.name)
        actions = self._lake.read_table(
            CORPORATE_ACTIONS.name, filters=[("type", "=", "dividend"), *(by_symbol or [])]
        )
        return event_view(events, macro, dividend_events(actions), self._cal)

    def upcoming(
        self,
        as_of: date | AsOf,
        horizon_days: int | None = None,
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """`upcoming_events` at `as_of` (default horizon `data.events.horizon_days`, calendar
        days); company events only of `symbols` when given, macro events always."""
        point = self._as_of(as_of)
        horizon = self._settings.horizon_days if horizon_days is None else horizon_days
        _positive("horizon_days", horizon)
        return upcoming_events(
            self.view(symbols),
            point,
            horizon,
            scheduled_match_days=self._settings.scheduled_match_days,
            estimate_min_gap_days=(
                self._settings.estimate_min_gap_days if self._settings.estimate_earnings else None
            ),
        )

    def past(self, symbol: str, as_of: date | AsOf, n: int | None = None) -> pd.DataFrame:
        """`past_events` of `symbol` at `as_of` (default `n` = `data.events.past_n`)."""
        point = self._as_of(as_of)
        count = self._settings.past_n if n is None else n
        _positive("n", count)
        return past_events(self.view([symbol]), symbol, point, count)

    def reactions(self, symbol: str, as_of: date | AsOf, n: int | None = None) -> pd.DataFrame:
        """`known_reactions` of `symbol` at `as_of` (default `n` = `data.events.past_n`)."""
        point = self._as_of(as_of)
        count = self._settings.past_n if n is None else n
        _positive("n", count)
        stored = self._lake.read_table(
            EARNINGS_REACTIONS.name, filters=[("symbol", "=", normalize_symbol(symbol))]
        )
        return known_reactions(stored, symbol, point, count)
