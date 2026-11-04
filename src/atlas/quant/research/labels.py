"""Forward-return labels for signal research (Stage 17).

Timing (t -> t+h): a signal dated on decision day `t` is what a `DataView` at `t` (08:00 New
York) computes, so it uses bars up to the close of `t-1`. Its label for horizon `h` sessions
is the total return from the close of `t` to the close of the session `h` sessions later,

    label_h(t) = prod_{k=1..h} (1 + r_{t+k}) - 1,

with `r_s` the simple daily total return of session `s` (adjusted close). The night and day of
`t` are skipped: positions decided on `t` can trade at the close of `t` at the earliest.

Labels are read through **one** `DataView` at the session after the last label session (the
first decision moment at which every label is realised). A missing bar anywhere in a window
gives NaN (no guess); a window that ends after the lake's last bar gives NaN (not realised);
a name blocked by that day's quality report has no labels (NaN, listed in `blocked`). Names
that delist drop out of the windows they do not finish.

Labels are for evaluation only: every frame here is tagged (`atlas.core.pit.tag_label`) and
`ForwardReturns` is marked as a label, so `guard_not_label` refuses them as a signal.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from types import MappingProxyType
from typing import ClassVar

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.errors import LookAheadError
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf, tag_label
from atlas.data.view import DataView
from atlas.quant.features import normalize_symbols

_log = get_logger(__name__)

ViewFactory = Callable[[date], DataView]
"""`view_factory(day)` returns a `DataView` at trading day `day` (08:00 New York)."""


def _date_index(days: Sequence[date]) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(list(days)), name="date")


def check_horizons(horizons: Sequence[int]) -> tuple[int, ...]:
    """`horizons` as a tuple if non-empty, positive ints, strictly ascending (`ValueError`)."""
    out = tuple(horizons)
    if (
        not out
        or any(isinstance(h, bool) or not isinstance(h, int) or h < 1 for h in out)
        or any(b <= a for a, b in pairwise(out))
    ):
        raise ValueError(f"horizons must be positive ints, strictly ascending, got {horizons!r}")
    return out


def compound_forward(
    daily: pd.DataFrame, dates: Sequence[date | pd.Timestamp], horizon: int
) -> pd.DataFrame:
    """The `horizon`-session forward returns of `dates` from daily returns (pure).

    `daily`: index = a session grid (oldest first) containing every date, one column per
    symbol, the simple daily return of each session. Row `t` of the result is
    `prod(1 + daily[t+1 .. t+horizon]) - 1` per column: NaN when a return in the window is
    NaN or the window passes the end of the grid. Index = `dates` (`DatetimeIndex` `date`).
    """
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError(f"horizon must be a positive int, got {horizon!r}")
    grid = pd.DatetimeIndex(daily.index)
    wanted = _date_index([pd.Timestamp(d).date() for d in dates])
    positions = grid.get_indexer(wanted)
    if (positions < 0).any():
        missing = [str(d.date()) for d, p in zip(wanted, positions, strict=True) if p < 0]
        raise ValueError(f"dates {missing} are not in the daily grid")
    growth = 1.0 + daily.to_numpy(dtype="float64")
    n_rows, n_cols = growth.shape
    out = np.full((len(positions), n_cols), np.nan)
    for row, p in enumerate(positions):
        if p + horizon < n_rows:
            out[row] = growth[p + 1 : p + 1 + horizon].prod(axis=0) - 1.0
    return pd.DataFrame(out, index=wanted, columns=daily.columns)


@dataclass(frozen=True, eq=False)
class ForwardReturns:
    """Realised forward returns of `symbols` on decision `dates`, for evaluation only.

    `daily` (tagged): the simple daily total returns on the session grid from the first date
    to the last label session (first row NaN); `horizon(h)` (tagged copy): dates x symbols,
    the `h`-session label of each date (module docstring); `known_at`: the `AsOf` of the
    view that read them; `blocked`: names left NaN because that day's quality report blocks
    them. Units: simple returns as fractions (0.05 = 5%).
    """

    __atlas_label__: ClassVar[bool] = True

    dates: tuple[date, ...]
    symbols: tuple[str, ...]
    horizons: tuple[int, ...]
    daily: pd.DataFrame
    known_at: AsOf
    blocked: tuple[str, ...]
    labels: Mapping[int, pd.DataFrame]

    def horizon(self, h: int) -> pd.DataFrame:
        """The labels of horizon `h` (a tagged copy; `ValueError` if `h` was not computed)."""
        if h not in self.labels:
            raise ValueError(f"horizon {h} was not computed; horizons: {self.horizons}")
        return tag_label(self.labels[h])


def check_dates(dates: Sequence[date], calendar: TradingCalendar) -> tuple[date, ...]:
    """`dates` as a tuple of `date`s: non-empty, strictly ascending trading sessions
    (`ValueError` otherwise)."""
    days = tuple(pd.Timestamp(d).date() for d in dates)
    if not days:
        raise ValueError("dates must not be empty")
    if any(b <= a for a, b in pairwise(days)):
        raise ValueError("dates must be strictly ascending without repeats")
    off = [d.isoformat() for d in days if not calendar.is_session(d)]
    if off:
        raise ValueError(f"dates {off} are not a trading session")
    return days


def forward_returns(
    view_factory: ViewFactory,
    dates: Sequence[date],
    symbols: Sequence[str],
    horizons: Sequence[int] | None = None,
    *,
    config: AtlasConfig | None = None,
) -> ForwardReturns:
    """The forward-return labels of `symbols` on decision days `dates` for `horizons`
    sessions (default `quant.research.horizons` of `config`, default `AtlasConfig()`).

    Reads one view, `view_factory(next_session(offset(max(dates), max(horizons))))`, and its
    `returns` over the sessions from the first date to the last label session (module
    docstring). `dates` must be ascending trading sessions; symbols are normalised.
    """
    cfg = config if config is not None else AtlasConfig()
    hs = check_horizons(cfg.quant.research.horizons if horizons is None else horizons)
    calendar = TradingCalendar(config=cfg.calendar)
    days = check_dates(dates, calendar)
    wanted = normalize_symbols(symbols)
    if not wanted:
        raise ValueError("symbols must not be empty")
    last = calendar.offset(days[-1], hs[-1])
    view = view_factory(calendar.next_session(last))
    grid = calendar.sessions(days[0], last)  # at least two sessions: every horizon is >= 1
    rets = view.returns(wanted, len(grid) - 1)
    index = _date_index(grid)
    if not pd.DatetimeIndex(rets.index).equals(index[1:]):
        raise LookAheadError(
            f"the label view at {view.day} returned sessions {rets.index[0]}..{rets.index[-1]}, "
            f"expected {grid[1]}..{grid[-1]}"
        )
    daily = pd.DataFrame(np.nan, index=index, columns=wanted, dtype="float64")
    daily.iloc[1:] = rets.reindex(columns=wanted).to_numpy(dtype="float64")
    blocked = tuple(s for s in wanted if s not in rets.columns)
    if blocked:
        _log.warning("research.labels_blocked", day=view.day.isoformat(), symbols=list(blocked))
    labels = {h: tag_label(compound_forward(daily, days, h)) for h in hs}
    return ForwardReturns(
        dates=days,
        symbols=tuple(wanted),
        horizons=hs,
        daily=tag_label(daily),
        known_at=view.as_of,
        blocked=blocked,
        labels=MappingProxyType(labels),
    )
