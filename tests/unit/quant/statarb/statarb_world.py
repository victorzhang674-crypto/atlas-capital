"""Test worlds for the Stage 23 pairs engine (brief task 1: synthetic cointegrated pairs).

- `ou_spread`: an AR(1) / discrete Ornstein-Uhlenbeck spread with a given half-life.
- `planted_universe`: sectors of correlated random walks (market + sector factor + own random
  walk) with planted cointegrated pairs `y = a + b x + OU` inside sectors and one planted pair
  split across two sectors; simple daily returns.
- `random_walk_universe`: one sector of correlated random walks, no cointegration.
- `build_pairs_lake`: a planted universe written into a lake (bars, instruments, SP500
  membership) for the `DataView` loaders and the CLI.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.pit import bar_available_at
from atlas.core.storage import Lake
from atlas.core.types.instruments import AssetClass, Instrument
from atlas.data.prices import PRICES_DAILY
from atlas.data.reference import instruments_frame

CAL = TradingCalendar()
LAST = date(2024, 2, 29)
AS_OF = date(2024, 3, 1)


def ou_spread(n: int, half_life: float, sigma: float, rng: np.random.Generator) -> np.ndarray:
    """`n` values of `s_t = phi s_{t-1} + e_t`, `phi = 2^(-1/half_life)`, `e ~ N(0, sigma)`,
    started at its stationary distribution."""
    phi = 2.0 ** (-1.0 / half_life)
    out = np.empty(n)
    out[0] = rng.normal(0.0, sigma / np.sqrt(1.0 - phi**2))
    shocks = rng.normal(0.0, sigma, n)
    for t in range(1, n):
        out[t] = phi * out[t - 1] + shocks[t]
    return out


def _walk(n: int, sigma: float, rng: np.random.Generator) -> np.ndarray:
    return np.cumsum(rng.normal(0.0, sigma, n))


def _returns(log_prices: dict[str, np.ndarray], sessions: Sequence[date]) -> pd.DataFrame:
    """Simple daily returns of log price paths (the first session's return from the path's
    start, so the returns rebuild the path up to a constant)."""
    frame = pd.DataFrame(log_prices)
    rets = np.expm1(frame.diff().fillna(0.0))
    rets.index = pd.DatetimeIndex(pd.to_datetime(list(sessions)), name="date")
    return rets


@dataclass(frozen=True)
class Universe:
    """`returns` (sessions x symbols, simple daily), `sectors` (symbol → sector), the planted
    same-sector `planted` pair ids (`y~x`, alphabetical), the planted `cross` pair id, and the
    true log price paths `log_prices`."""

    returns: pd.DataFrame
    sectors: pd.Series
    planted: frozenset[str]
    cross: str | None
    log_prices: pd.DataFrame


def sessions(n: int, last: date = LAST) -> list[date]:
    """The last `n` sessions ending at `last`."""
    return list(CAL.sessions(CAL.offset(last, -(n - 1)), last))


def planted_universe(
    seed: int,
    *,
    n: int = 600,
    sectors: Sequence[str] = ("Energy", "Financials", "Utilities"),
    half_lives: Sequence[float] = (5.0, 10.0),
    noise_names: int = 2,
    cross: bool = True,
    spread_sigma: float = 0.008,
) -> Universe:
    """Per sector: `len(half_lives)` planted pairs (`y = a + b x + OU(hl)`, b in [0.7, 1.3])
    and `noise_names` factor-driven random walks; `cross` adds a planted pair whose legs are in
    the first two sectors. Daily vols: market 1%, sector 1%, own walk 0.4%."""
    rng = np.random.default_rng(seed)
    market = _walk(n, 0.01, rng)
    paths: dict[str, np.ndarray] = {}
    labels: dict[str, str] = {}
    planted: set[str] = set()
    factors = {s: _walk(n, 0.01, rng) for s in sectors}

    def name(sector: str, tag: str) -> str:
        return f"{sector[:3].upper()}{tag}"

    for sector in sectors:
        base = market + factors[sector]
        for k, hl in enumerate(half_lives):
            x = base + _walk(n, 0.004, rng)
            beta = rng.uniform(0.7, 1.3)
            y = rng.normal(0.0, 0.2) + beta * x + ou_spread(n, hl, spread_sigma, rng)
            ya, xb = name(sector, f"Y{k}"), name(sector, f"X{k}")
            paths[ya], paths[xb] = y + 3.0, x + 3.0
            labels[ya] = labels[xb] = sector
            planted.add(f"{min(ya, xb)}~{max(ya, xb)}")
        for k in range(noise_names):
            sym = name(sector, f"N{k}")
            paths[sym] = base + _walk(n, 0.004, rng) + 3.0
            labels[sym] = sector
    cross_id = None
    if cross and len(sectors) >= 2:
        x = market + factors[sectors[1]] + _walk(n, 0.004, rng)
        y = 0.9 * x + ou_spread(n, 5.0, spread_sigma, rng)
        ya, xb = name(sectors[0], "C0"), name(sectors[1], "C1")
        paths[ya], paths[xb] = y + 3.0, x + 3.0
        labels[ya], labels[xb] = sectors[0], sectors[1]
        cross_id = f"{min(ya, xb)}~{max(ya, xb)}"
    days = sessions(n)
    returns = _returns(paths, days)
    log_prices = pd.DataFrame(paths, index=returns.index)
    symbols = sorted(paths)
    return Universe(
        returns=returns[symbols],
        sectors=pd.Series(labels, dtype="object").reindex(symbols),
        planted=frozenset(planted),
        cross=cross_id,
        log_prices=log_prices[symbols],
    )


def random_walk_universe(seed: int, *, n_names: int = 6, n: int = 600) -> Universe:
    """`n_names` same-sector random walks driven by one market factor (daily return
    correlation about 0.86): no pair is cointegrated."""
    rng = np.random.default_rng(seed)
    market = _walk(n, 0.01, rng)
    paths = {f"RW{k:02d}": market + _walk(n, 0.004, rng) + 3.0 for k in range(n_names)}
    returns = _returns(paths, sessions(n))
    return Universe(
        returns=returns,
        sectors=pd.Series("Energy", index=list(paths), dtype="object"),
        planted=frozenset(),
        cross=None,
        log_prices=pd.DataFrame(paths, index=returns.index),
    )


def build_pairs_lake(lake: Lake, universe: Universe, *, end_date: date | None = None) -> None:
    """Write `universe` into `lake`: daily bars (close = exp(log price)), instruments with its
    sectors, SP500 membership of every name from 2010 (or until `end_date`)."""
    days = [d.date() for d in universe.log_prices.index]
    frames = []
    for sym in universe.log_prices.columns:
        closes = np.exp(universe.log_prices[sym].to_numpy())
        frame = pd.DataFrame(
            {
                "symbol": sym,
                "date": days,
                "open": closes,
                "high": closes * 1.01,
                "low": closes * 0.99,
                "close": closes,
                "adj_close": closes,
                "volume": 2_000_000,
                "vendor": "yfinance",
            }
        )
        frame["available_at"] = pd.Series(
            [bar_available_at(d, CAL) for d in days], dtype="object"
        ).astype("datetime64[us, UTC]")
        frames.append(frame[list(PRICES_DAILY.column_names)])
    lake.write_table("prices_daily", pd.concat(frames, ignore_index=True), mode="upsert")
    instruments = [
        Instrument(symbol=s, asset_class=AssetClass.EQUITY, name=f"{s} Inc", sector=sector)
        for s, sector in universe.sectors.items()
    ]
    lake.write_table("instruments", instruments_frame(instruments), mode="upsert")
    membership = pd.DataFrame(
        {
            "index_id": "SP500",
            "symbol": list(universe.sectors.index),
            "start_date": date(2010, 1, 4),
            "end_date": end_date,
        }
    )
    lake.write_table("index_membership", membership, mode="upsert")
