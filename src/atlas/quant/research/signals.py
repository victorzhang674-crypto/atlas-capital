"""Point-in-time signal panels for research (Stage 17).

`evaluation_dates(start, end, frequency)` gives the decision days a signal is evaluated on:
the first trading session of each day, week or month. `signal_panel(name, view_factory,
dates)` computes signal `name` on each of them with a `DataView` at that date (08:00 New
York, so bars up to the close of the previous session), for the members of the research
universe **at that date** (point-in-time membership: later index leavers are included, so no
survivorship bias). A name is a Stage 15 technical feature (its latest value) or a Stage 16
fundamental factor (its cleaned cross-sectional z-score exposure).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Literal, get_args

import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.pit import guard_not_label
from atlas.data.view import DataView
from atlas.quant.factors import FACTOR_NAMES, factor_panel
from atlas.quant.features import REGISTRY, FeatureRegistry, compute_features
from atlas.quant.research.labels import ViewFactory, check_dates

_log = get_logger(__name__)

Frequency = Literal["daily", "weekly", "monthly"]
SignalKind = Literal["technical", "fundamental"]
FREQUENCIES: tuple[str, ...] = get_args(Frequency)


def evaluation_dates(
    start: date,
    end: date,
    frequency: Frequency = "monthly",
    calendar: TradingCalendar | None = None,
) -> tuple[date, ...]:
    """The first trading session of each `frequency` period (`daily`: every session; `weekly`:
    ISO week; `monthly`: calendar month) among the sessions in [`start`, `end`]."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"frequency must be one of {FREQUENCIES}, got {frequency!r}")
    if start > end:
        raise ValueError(f"start ({start}) must not be after end ({end})")
    cal = calendar if calendar is not None else TradingCalendar()
    sessions = cal.sessions(start, end)
    if frequency == "daily":
        return tuple(sessions)

    def period(d: date) -> tuple[int, int]:
        if frequency == "weekly":
            iso = d.isocalendar()
            return iso.year, iso.week
        return d.year, d.month

    out: list[date] = []
    seen: set[tuple[int, int]] = set()
    for d in sessions:
        key = period(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return tuple(out)


def signal_kind(name: str, registry: FeatureRegistry = REGISTRY) -> SignalKind:
    """`technical` for a feature in `registry`, `fundamental` for a Stage 16 factor;
    `ValueError` naming the known signals otherwise."""
    if name in registry:
        return "technical"
    if name in FACTOR_NAMES:
        return "fundamental"
    raise ValueError(
        f"unknown signal {name!r}; technical: {', '.join(registry.names())}; "
        f"fundamental: {', '.join(FACTOR_NAMES)}"
    )


@dataclass(frozen=True, eq=False)
class SignalPanel:
    """Signal `name` (`kind`) on evaluation dates: `values` (index `date`, tz-naive
    `DatetimeIndex`; one column per symbol that was a member on some date, sorted; NaN where
    the name was not a member, is blocked or has no value), `members` (date -> the members
    evaluated that day) and `sectors` (symbol -> GICS sector, `None` when unknown; today's
    sectors, not point-in-time). Units: those of the feature (`FeatureSpec.unit`) or z-scores
    for a fundamental factor."""

    name: str
    kind: SignalKind
    values: pd.DataFrame
    members: Mapping[date, tuple[str, ...]]
    sectors: pd.Series


def _sectors(view: DataView, symbols: Sequence[str]) -> pd.Series:
    out: dict[str, str | None] = {}
    for symbol in symbols:
        try:
            out[symbol] = view.instrument(symbol).sector
        except DataError as exc:  # no instruments row (or blocked today): sector unknown, logged
            _log.info("research.no_sector", symbol=symbol, error=str(exc))
            out[symbol] = None
    return pd.Series(out, dtype="object", index=pd.Index(list(symbols), name="symbol"))


def _row(
    view: DataView, name: str, kind: SignalKind, members: list[str], registry: FeatureRegistry
) -> pd.Series:
    if not members:
        return pd.Series(dtype="float64")
    if kind == "technical":
        frame = compute_features(view, members, [name], history=1, registry=registry)[name]
        return frame.iloc[-1].astype("float64")
    return factor_panel(view, members).exposures[name].astype("float64")


def signal_panel(
    name: str,
    view_factory: ViewFactory,
    dates: Sequence[date],
    *,
    universe: str | None = None,
    registry: FeatureRegistry = REGISTRY,
    calendar: TradingCalendar | None = None,
    progress: Callable[[date], None] | None = None,
) -> SignalPanel:
    """Signal `name` on each of `dates` (ascending sessions), each from
    `view_factory(date)`, for the members of `universe` (default
    `quant.research.universe` of the view's config) at that date. `calendar` (default XNYS
    with the default calendar settings) checks the dates; `progress(date)` is called after
    each date. Unknown names raise `ValueError` before any data is read."""
    kind = signal_kind(name, registry)
    days = check_dates(dates, calendar if calendar is not None else TradingCalendar())
    rows: dict[date, pd.Series] = {}
    members: dict[date, tuple[str, ...]] = {}
    view: DataView | None = None
    for day in days:
        view = view_factory(day)
        wanted = view.universe(
            universe if universe is not None else view.config.quant.research.universe
        )
        row = _row(view, name, kind, wanted, registry)
        guard_not_label(row, "signal")
        rows[day] = row
        members[day] = tuple(wanted)
        if progress is not None:
            progress(day)
    columns = sorted({s for row in rows.values() for s in row.index})
    index = pd.DatetimeIndex(pd.to_datetime(list(days)), name="date")
    values = pd.DataFrame(
        [rows[d].reindex(columns).to_numpy(dtype="float64") for d in days],
        index=index,
        columns=pd.Index(columns),
        dtype="float64",
    )
    assert view is not None  # days is never empty (check_dates)
    return SignalPanel(
        name=name,
        kind=kind,
        values=values,
        members=MappingProxyType(members),
        sectors=_sectors(view, columns),
    )
