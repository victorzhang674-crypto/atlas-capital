"""The shared `tiny_universe` test world (protocol §5): 5 tickers, 300 sessions of synthetic
daily bars with known properties, written to a lake and read through `DataView`.

SPY's daily simple returns have sample std (ddof 1) exactly `MARKET_VOL`; each stock's daily
returns are `alpha + beta * spy + noise`, where the noise is exactly orthogonal to a constant
and to SPY's returns over the 299 returns and has sample std exactly `IDIO_VOL`. So an OLS fit
over the whole sample recovers each beta and the residual std exactly (to float precision).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import MappingProxyType

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.pit import bar_available_at
from atlas.core.storage import Lake
from atlas.data.prices import PRICES_DAILY
from atlas.data.view import DataView

AS_OF = date(2024, 3, 1)
LAST_SESSION = date(2024, 2, 29)
N_SESSIONS = 300
BETAS = MappingProxyType({"AAA": 0.5, "BBB": 1.0, "CCC": 1.3, "DDD": 1.8})
MARKET_VOL = 0.01
IDIO_VOL = 0.015
BENCHMARK = "SPY"


@dataclass(frozen=True)
class TinyUniverse:
    """A lake with 300 sessions (to `LAST_SESSION`) of bars for `symbols`, the stocks in the
    `sp500` membership. Units: `betas` against SPY daily returns; `market_vol` and `idio_vol`
    are daily return standard deviations (ddof 1)."""

    lake: Lake
    as_of: date
    sessions: tuple[date, ...]
    symbols: tuple[str, ...]
    stocks: tuple[str, ...]
    betas: Mapping[str, float]
    market_vol: float
    idio_vol: float

    def view(self, config: AtlasConfig | None = None) -> DataView:
        """A `DataView` of the lake at `as_of` (08:00 New York: last known bar `sessions[-1]`)."""
        return DataView(self.lake, self.as_of, config)


def _standardised(x: np.ndarray, basis: np.ndarray) -> np.ndarray:
    """`x` with its projection on the columns of `basis` removed, scaled to sample std 1."""
    coef, *_ = np.linalg.lstsq(basis, x, rcond=None)
    resid = x - basis @ coef
    return resid / resid.std(ddof=1)


def tiny_returns(seed: int = 15) -> pd.DataFrame:
    """The 299 daily simple returns of every symbol (columns AAA..DDD, SPY), seeded."""
    rng = np.random.default_rng(seed)
    n = N_SESSIONS - 1
    ones = np.ones((n, 1))
    spy = 0.0004 + MARKET_VOL * _standardised(rng.standard_normal(n), ones)
    basis = np.column_stack([ones, spy])
    out = {
        symbol: 0.0002 + beta * spy + IDIO_VOL * _standardised(rng.standard_normal(n), basis)
        for symbol, beta in BETAS.items()
    }
    out[BENCHMARK] = spy
    return pd.DataFrame(out)


def bars_from_returns(
    symbol: str,
    returns: np.ndarray,
    days: list[date],
    *,
    start: float = 100.0,
    rng: np.random.Generator,
    calendar: TradingCalendar,
) -> pd.DataFrame:
    """`prices_daily` rows of `symbol` whose closes follow `returns` from `start`: open =
    previous close times a small gap, high/low a random range around open and close, volume
    log-normal around 2M shares, `adj_close` = close (no actions)."""
    closes = start * np.concatenate([[1.0], np.cumprod(1.0 + returns)])
    n = len(closes)
    prev = np.concatenate([[start], closes[:-1]])
    opens = prev * (1.0 + rng.normal(0.0, 0.003, n))
    highs = np.maximum(opens, closes) * (1.0 + np.abs(rng.normal(0.0, 0.005, n)))
    lows = np.minimum(opens, closes) * (1.0 - np.abs(rng.normal(0.0, 0.005, n)))
    volume = np.round(2_000_000 * np.exp(rng.normal(0.0, 0.3, n))).astype("int64")
    frame = pd.DataFrame(
        {
            "symbol": symbol,
            "date": days,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "adj_close": closes,
            "volume": volume,
            "vendor": "yfinance",
        }
    )
    stamps = [bar_available_at(d, calendar) for d in days]
    frame["available_at"] = pd.to_datetime(pd.Series(stamps, dtype="object"), utc=True).astype(
        "datetime64[us, UTC]"
    )
    return frame[list(PRICES_DAILY.column_names)]


def build_tiny_universe(lake: Lake, seed: int = 15) -> TinyUniverse:
    """Write the tiny world into `lake` (prices and `SP500` membership) and describe it."""
    cal = TradingCalendar()
    days = list(cal.sessions(cal.offset(LAST_SESSION, -(N_SESSIONS - 1)), LAST_SESSION))
    rets = tiny_returns(seed)
    rng = np.random.default_rng(seed + 1)
    symbols = (*BETAS, BENCHMARK)
    frames = [
        bars_from_returns(s, rets[s].to_numpy(), days, rng=rng, calendar=cal) for s in symbols
    ]
    lake.write_table("prices_daily", pd.concat(frames, ignore_index=True), mode="upsert")
    membership = pd.DataFrame(
        {
            "index_id": "SP500",
            "symbol": list(BETAS),
            "start_date": date(2010, 1, 4),
            "end_date": None,
        }
    )
    lake.write_table("index_membership", membership, mode="upsert")
    return TinyUniverse(
        lake=lake,
        as_of=AS_OF,
        sessions=tuple(days),
        symbols=symbols,
        stocks=tuple(BETAS),
        betas=BETAS,
        market_vol=MARKET_VOL,
        idio_vol=IDIO_VOL,
    )


def add_future_rows(tu: TinyUniverse) -> None:
    """Bars for 2024-03-01..15 (published after the 08:00 cutoff of 03-01), a 3:1 split of CCC
    on 03-05 and a dividend of AAA with a past ex-date recorded only on 03-04."""
    cal = TradingCalendar()
    days = list(cal.sessions(date(2024, 3, 1), date(2024, 3, 15)))
    rng = np.random.default_rng(99)
    last = tu.lake.read_table("prices_daily", filters=[("date", "=", date(2024, 2, 29))])
    frames = []
    for symbol in tu.symbols:
        start = float(last.loc[last["symbol"] == symbol, "close"].iloc[0])
        rets = rng.normal(0.0, 0.03, len(days))
        grid = [date(2024, 2, 29), *days]  # the first row restates 02-29 and is dropped
        frame = bars_from_returns(symbol, rets, grid, start=start, rng=rng, calendar=cal).iloc[1:]
        frames.append(frame)
    tu.lake.write_table("prices_daily", pd.concat(frames, ignore_index=True), mode="upsert")
    actions = pd.DataFrame(
        {
            "symbol": ["CCC", "AAA"],
            "ex_date": [date(2024, 3, 5), date(2024, 2, 20)],
            "type": ["split", "dividend"],
            "ratio": [3.0, None],
            "amount": [None, 1.0],
            "vendor": "yfinance",
            "available_at": [
                bar_available_at(date(2024, 3, 5), cal),
                datetime(2024, 3, 4, 22, tzinfo=UTC),
            ],
        }
    )
    actions["available_at"] = pd.to_datetime(actions["available_at"], utc=True).astype(
        "datetime64[us, UTC]"
    )
    tu.lake.write_table("corporate_actions", actions, mode="upsert")
