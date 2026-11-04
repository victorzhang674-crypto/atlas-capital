"""The Stage 17 research test world: a lake with a planted, known signal.

`build_research_world(lake)` writes `n_sessions` sessions of daily bars (to `LAST_SESSION`) for
`n_names` stocks, the `SP500` membership (one name, `LATE`, joins half-way) and instruments
with sectors (every fourth name has none). For each decision day `t = sessions[i]` the world
draws a signal `s_t` (one value per name) and the return of the next session is

    r_{t+1} = m_{t+1} + sigma * (rho * z(s_t) + sqrt(1 - rho^2) * z(e_{t+1}))

where `z` standardises across names (mean 0, population std 1) and `m` is a common market
return. With `exact=True` the noise `e` is made orthogonal to `s_t` across names, so the
cross-sectional Pearson correlation of `s_t` and `r_{t+1}` is exactly `rho` on every date: the
planted IC. Units: returns are simple daily fractions; prices USD (start 100).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.storage import Lake
from atlas.core.types.instruments import AssetClass, Instrument
from atlas.data.reference import instruments_frame
from atlas.data.view import DataView
from tiny_universe import bars_from_returns

CAL = TradingCalendar()
LAST_SESSION = date(2024, 2, 29)
SECTOR_CYCLE: tuple[str | None, ...] = ("Information Technology", "Health Care", "Energy", None)


@dataclass(frozen=True)
class ResearchWorld:
    """What `build_research_world` wrote. `signal`: decision days (`sessions[:-1]`) x names,
    the planted signal; `returns`: sessions[1:] x names, the simple daily returns behind the
    closes; `closes`: sessions x names (raw = adjusted, no corporate actions)."""

    lake: Lake
    sessions: tuple[date, ...]
    symbols: tuple[str, ...]
    signal: pd.DataFrame
    returns: pd.DataFrame
    closes: pd.DataFrame
    rho: float
    late: str
    late_start: date
    sectors: Mapping[str, str | None] = field(default_factory=dict)

    def view(self, day: date) -> DataView:
        """A `DataView` of the lake at `day` (08:00 New York)."""
        return DataView(self.lake, day)

    @property
    def factory(self) -> Callable[[date], DataView]:
        """`view_factory` for the research toolkit."""
        return self.view

    def day(self, i: int) -> date:
        """`sessions[i]`."""
        return self.sessions[i]


def _z(x: np.ndarray) -> np.ndarray:
    return (x - x.mean()) / x.std()


def planted_returns(
    n_names: int, n_sessions: int, *, rho: float, sigma: float, seed: int, exact: bool
) -> tuple[np.ndarray, np.ndarray]:
    """(signal, returns): `signal[i]` is drawn for decision day i (0..n_sessions-2) and
    `returns[i]` is the return of session i+1 (see the module docstring)."""
    rng = np.random.default_rng(seed)
    n = n_sessions - 1
    signal = rng.standard_normal((n, n_names))
    returns = np.empty((n, n_names))
    for i in range(n):
        s = _z(signal[i])
        e = rng.standard_normal(n_names)
        e = e - e.mean()
        if exact:
            e = e - (e @ s / n_names) * s
        e = e / e.std()
        market = rng.normal(0.0003, 0.006)
        returns[i] = market + sigma * (rho * s + np.sqrt(1.0 - rho**2) * e)
    return signal, returns


def build_research_world(
    lake: Lake,
    *,
    n_names: int = 40,
    n_sessions: int = 120,
    rho: float = 0.05,
    sigma: float = 0.02,
    seed: int = 17,
    exact: bool = True,
    drop: Sequence[tuple[str, int]] = (),
    stale: str | None = None,
) -> ResearchWorld:
    """Write the world into `lake` and describe it. `drop` removes the bars `(symbol, i)`
    (session index); `stale` freezes that symbol's last 6 closes (a critical stale-price
    issue, so it is blocked at the end of the world)."""
    days = list(CAL.sessions(CAL.offset(LAST_SESSION, -(n_sessions - 1)), LAST_SESSION))
    symbols = tuple(f"S{i:03d}" for i in range(n_names))
    late = symbols[-1]
    late_start = days[n_sessions // 2]
    signal, returns = planted_returns(
        n_names, n_sessions, rho=rho, sigma=sigma, seed=seed, exact=exact
    )
    rng = np.random.default_rng(seed + 1)
    frames = []
    for j, symbol in enumerate(symbols):
        frame = bars_from_returns(symbol, returns[:, j], days, rng=rng, calendar=CAL)
        if symbol == stale:
            last_ok = len(days) - 7
            for col in ("open", "high", "low", "close", "adj_close"):
                frame.loc[frame.index[last_ok + 1 :], col] = frame["close"].iloc[last_ok]
        frames.append(frame)
    bars = pd.concat(frames, ignore_index=True)
    for symbol, i in drop:
        bars = bars[~((bars["symbol"] == symbol) & (bars["date"] == days[i]))]
    lake.write_table("prices_daily", bars.reset_index(drop=True), mode="upsert")
    membership = pd.DataFrame(
        {
            "index_id": "SP500",
            "symbol": list(symbols),
            "start_date": [date(2010, 1, 4)] * (n_names - 1) + [late_start],
            "end_date": None,
        }
    )
    lake.write_table("index_membership", membership, mode="upsert")
    sectors = {s: SECTOR_CYCLE[j % len(SECTOR_CYCLE)] for j, s in enumerate(symbols)}
    instruments = [
        Instrument(symbol=s, asset_class=AssetClass.EQUITY, name=f"{s} Inc", sector=sector)
        for s, sector in sectors.items()
    ]
    lake.write_table("instruments", instruments_frame(instruments), mode="upsert")
    index = pd.DatetimeIndex(pd.to_datetime(days), name="date")
    closes = pd.DataFrame(
        100.0 * np.vstack([np.ones(n_names), np.cumprod(1.0 + returns, axis=0)]),
        index=index,
        columns=list(symbols),
    )
    return ResearchWorld(
        lake=lake,
        sessions=tuple(days),
        symbols=symbols,
        signal=pd.DataFrame(signal, index=index[:-1], columns=list(symbols)),
        returns=pd.DataFrame(returns, index=index[1:], columns=list(symbols)),
        closes=closes,
        rho=rho,
        late=late,
        late_start=late_start,
        sectors=MappingProxyType(sectors),
    )
