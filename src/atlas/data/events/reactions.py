"""Earnings reactions: a free surprise proxy measured from prices after each earnings event.

For an actual earnings event (an EDGAR filing, `source` `sec_*`) at `event_time`:

- the reaction session D0 is the first session whose close is after `event_time` (a filing
  after the close reacts the next session; one before the close, that same session);
- Dp is the session before D0 and Dn the session `reaction_days - 1` after D0;
- prices are the raw bars of [Dp, Dn], adjusted inside the window for splits and dividends
  with an ex-date in it (`atlas.data.prices.adjustment_factors`), so an action is never a
  reaction.

Measures (simple returns, fractions: 0.01 = 1%): `gap_return = open(D0) / close(Dp) - 1`,
`oc_return = close(D0) / open(D0) - 1`, `oc_abnormal = oc_return - oc_return(benchmark)` and
`car = (close(Dn) / close(Dp) - 1) - (same for the benchmark)`: the buy-and-hold abnormal return
over the window against the benchmark (a market-adjusted model, beta 1).

Point in time: a reaction is known only after its window closes, at `bar_available_at(Dn)`. An
event whose window lacks a bar of the symbol or the benchmark (including a window that has not
closed yet), or whose window holds a vendor action that cannot be applied (a dividend at or
above the prior close), gets no row: no price is guessed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

import pandas as pd

from atlas.core.calendar import TradingCalendar, market_date
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.pit import bar_available_at
from atlas.core.storage import AVAILABLE_AT, DEFAULT_REGISTRY, Column, ColumnType, Lake, TableSchema
from atlas.core.types.instruments import normalize_symbol
from atlas.data.events.base import EVENTS
from atlas.data.prices import CORPORATE_ACTIONS, PRICES_DAILY, adjustment_factors

_log = get_logger(__name__)
_S, _D, _F, _T = ColumnType.STRING, ColumnType.DATE, ColumnType.FLOAT64, ColumnType.TIMESTAMP

REACTION_COLUMNS: Final = (
    "symbol",
    "event_id",
    "event_time",
    "reaction_date",
    "window_end",
    "gap_return",
    "oc_return",
    "oc_abnormal",
    "car",
    "benchmark",
    AVAILABLE_AT,
)
"""Columns of `earnings_reactions`, in order."""

EARNINGS_REACTIONS = DEFAULT_REGISTRY.register(
    TableSchema(
        name="earnings_reactions",
        columns=(
            Column(name="symbol", dtype=_S),
            Column(name="event_id", dtype=_S),
            Column(name="event_time", dtype=_T),
            Column(name="reaction_date", dtype=_D),
            Column(name="window_end", dtype=_D),
            Column(name="gap_return", dtype=_F),
            Column(name="oc_return", dtype=_F),
            Column(name="oc_abnormal", dtype=_F),
            Column(name="car", dtype=_F),
            Column(name="benchmark", dtype=_S),
            Column(name=AVAILABLE_AT, dtype=_T),
        ),
        primary_key=("symbol", "event_id"),
        description="Price reaction after each earnings event (simple returns); Stage 12",
    )
)
"""The curated `earnings_reactions` table (registered in `DEFAULT_REGISTRY` on import)."""


@dataclass(frozen=True)
class ReactionsIngestReport:
    """Outcome of `ingest_earnings_reactions`: `rows` upserted, `skipped` events without a
    complete window (missing bars, or the window has not closed)."""

    rows: int
    skipped: int


def reaction_window(
    event_time: datetime, calendar: TradingCalendar, reaction_days: int
) -> tuple[date, date, date]:
    """`(Dp, D0, Dn)` of an event at the aware instant `event_time` (see the module
    docstring); `reaction_days` is the number of sessions from D0 to Dn inclusive."""
    day = market_date(event_time)
    if calendar.is_session(day) and event_time < calendar.session_close(day):
        first = day
    else:
        first = calendar.next_session(day)
    return calendar.prev_session(first), first, calendar.offset(first, reaction_days - 1)


def compute_reactions(
    events: pd.DataFrame,
    daily: pd.DataFrame,
    actions: pd.DataFrame,
    calendar: TradingCalendar,
    *,
    reaction_days: int,
    benchmark: str,
) -> tuple[pd.DataFrame, int]:
    """Reactions of the actual earnings events in `events` (event frame), from raw bars
    `daily` (`prices_daily` columns, the symbols and `benchmark`) and `actions`
    (`corporate_actions` columns): the `REACTION_COLUMNS` frame and the number of events
    skipped for an incomplete window."""
    bench = normalize_symbol(benchmark)
    actual = events.loc[
        (events["event_type"] == "earnings")
        & events["source"].str.startswith("sec_")
        & events["event_time"].notna()
    ].drop_duplicates(subset=["symbol", "event_id"], keep="first")
    by_symbol = {s: g.set_index("date") for s, g in daily.groupby("symbol", sort=False)}
    rows: list[dict[str, Any]] = []
    skipped = 0
    for event in actual.to_dict("records"):
        when = pd.Timestamp(event["event_time"]).to_pydatetime()
        try:
            dp, d0, dn = reaction_window(when, calendar, reaction_days)
            sessions = calendar.sessions(dp, dn)
        except ValueError:
            skipped += 1
            continue
        try:
            stock = _window(by_symbol, event["symbol"], sessions, actions)
            market = _window(by_symbol, bench, sessions, actions)
        except DataError as exc:  # a vendor action that cannot be applied: no guessed price
            _log.warning("events.reaction_skipped", event_id=event["event_id"], error=str(exc))
            skipped += 1
            continue
        if stock is None or market is None:
            skipped += 1
            continue
        oc = _px(stock, d0, "close") / _px(stock, d0, "open") - 1
        oc_bench = _px(market, d0, "close") / _px(market, d0, "open") - 1
        rows.append(
            {
                "symbol": event["symbol"],
                "event_id": event["event_id"],
                "event_time": when,
                "reaction_date": d0,
                "window_end": dn,
                "gap_return": _px(stock, d0, "open") / _px(stock, dp, "close") - 1,
                "oc_return": oc,
                "oc_abnormal": oc - oc_bench,
                "car": _hold(stock, dp, dn) - _hold(market, dp, dn),
                "benchmark": bench,
                AVAILABLE_AT: bar_available_at(dn, calendar),
            }
        )
    out = pd.DataFrame(rows, columns=list(REACTION_COLUMNS))
    for col in ("event_time", AVAILABLE_AT):
        out[col] = pd.to_datetime(out[col], utc=True).dt.as_unit("us")
    return out, skipped


def _window(
    by_symbol: dict[Any, pd.DataFrame],
    symbol: str,
    sessions: tuple[date, ...],
    actions: pd.DataFrame,
) -> pd.DataFrame | None:
    """Adjusted open/close of `symbol` on every session of `sessions`, indexed by date, or
    `None` when a bar is missing."""
    bars = by_symbol.get(symbol)
    if bars is None or any(d not in bars.index for d in sessions):
        return None
    window = bars.loc[list(sessions)].reset_index()
    own = actions.loc[
        (actions["symbol"] == symbol)
        & actions["ex_date"].map(lambda d: sessions[0] < d <= sessions[-1]).astype(bool)
    ]
    adjusted = adjustment_factors(window, own)
    return pd.DataFrame(
        {
            "open": adjusted["open"] * adjusted["total_factor"],
            "close": adjusted["close"] * adjusted["total_factor"],
        }
    ).set_axis(list(window["date"]))


def _px(prices: pd.DataFrame, day: date, column: str) -> float:
    return float(prices[column].loc[day])


def _hold(prices: pd.DataFrame, start: date, end: date) -> float:
    return _px(prices, end, "close") / _px(prices, start, "close") - 1


def ingest_earnings_reactions(
    lake: Lake,
    symbols: Sequence[str] | None = None,
    *,
    config: AtlasConfig | None = None,
    calendar: TradingCalendar | None = None,
) -> ReactionsIngestReport:
    """Compute the reactions of the stored actual earnings events of `symbols` (all symbols
    when `None`) from the lake's `prices_daily` and `corporate_actions`, and upsert them into
    `earnings_reactions` (idempotent). `reaction_days` and the benchmark come from
    `config.data.events` (`config` defaults to `AtlasConfig()`)."""
    if isinstance(symbols, str):
        raise ValueError("symbols must be a sequence of tickers, not one string")
    cfg = config if config is not None else AtlasConfig()
    cal = calendar if calendar is not None else TradingCalendar(config=cfg.calendar)
    settings = cfg.data.events
    if not lake.has_table(EVENTS.name):
        return ReactionsIngestReport(rows=0, skipped=0)
    wanted = None if symbols is None else sorted({normalize_symbol(s) for s in symbols})
    filters = None if wanted is None else [("symbol", "in", wanted)]
    events = lake.read_table(EVENTS.name, filters=filters)
    if events.empty:
        return ReactionsIngestReport(rows=0, skipped=0)
    names = sorted({*events["symbol"], settings.reaction_benchmark})
    daily = lake.read_table(PRICES_DAILY.name, filters=[("symbol", "in", names)])
    actions = lake.read_table(CORPORATE_ACTIONS.name, filters=[("symbol", "in", names)])
    rows, skipped = compute_reactions(
        events,
        daily,
        actions,
        cal,
        reaction_days=settings.reaction_days,
        benchmark=settings.reaction_benchmark,
    )
    if not rows.empty:
        lake.write_table(
            EARNINGS_REACTIONS.name,
            rows,
            mode="upsert",
            keys=list(EARNINGS_REACTIONS.primary_key),
        )
    _log.info("events.ingest_reactions", rows=len(rows), skipped=skipped)
    return ReactionsIngestReport(rows=len(rows), skipped=skipped)
