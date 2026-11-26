"""The Stage 30 test world: 24 S&P 500 members and SPY (the benchmark, not a member), 600
sessions of daily bars to 2024-02-29 with realistic OHLC, varying volume and distinct shapes:

- `UPP` rises steadily, `DWN` falls steadily;
- `SPL` splits 2:1 on `SPLIT_DAY` (inside the last 252 sessions): its raw prices halve, so
  every level must be read split-adjusted;
- `NEW` listed `NEW_SESSIONS` sessions before the as-of (too short for a 52-week range);
- `M01`..`M20` random walks (the cross-section behind the universe percentiles).

A dirty copy adds rows known only after `AS_OF`: bars to 2024-03-15 and a 4:1 UPP split on
2024-03-08. They must never show.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.pit import bar_available_at
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.core.types.instruments import AssetClass, Instrument
from atlas.data.prices import CORPORATE_ACTIONS, PRICES_DAILY
from atlas.data.reference import instruments_frame
from atlas.data.view import DataView

CAL = TradingCalendar()
AS_OF = date(2024, 3, 1)
"""The world's as-of (08:00 New York: last known bar 2024-02-29)."""
LAST = date(2024, 2, 29)
SESSIONS = 600
RANDOM = tuple(f"M{i:02d}" for i in range(1, 21))
MEMBERS = ("UPP", "DWN", "SPL", "NEW", *RANDOM)
BENCHMARK = "SPY"
SPLIT_DAY = date(2023, 11, 15)
SPLIT_RATIO = 2.0
NEW_SESSIONS = 80
FUTURE_SPLIT = ("UPP", date(2024, 3, 8), 4.0)

CONFIG = AtlasConfig.model_validate({"quant": {"features": {"snapshot": {"min_universe": 5}}}})
"""Settings for a cross-section of 25 names."""


def _drift(symbol: str) -> float:
    return {"UPP": 0.002, "DWN": -0.0015, BENCHMARK: 0.0004}.get(symbol, 0.0)


def bars_for(symbol: str, days: list[date], rng: np.random.Generator, start: float) -> pd.DataFrame:
    """Daily bars of `symbol` on `days`: a random walk with its drift; open near the previous
    close, high and low around the open and close; volume around 2M shares."""
    n = len(days)
    noise = 0.004 if symbol in ("UPP", "DWN") else 0.012
    closes = start * np.exp(np.cumsum(rng.normal(_drift(symbol), noise, n)))
    opens = np.r_[start, closes[:-1]] * np.exp(rng.normal(0, 0.003, n))
    top = np.maximum(opens, closes) * (1 + rng.uniform(0.001, 0.015, n))
    bottom = np.minimum(opens, closes) * (1 - rng.uniform(0.001, 0.015, n))
    frame = pd.DataFrame(
        {
            "symbol": symbol,
            "date": days,
            "open": opens,
            "high": top,
            "low": bottom,
            "close": closes,
            "adj_close": closes,
            "volume": rng.integers(1_000_000, 3_000_000, n),
            "vendor": "yfinance",
        }
    )
    frame["available_at"] = pd.Series(
        [bar_available_at(d, CAL) for d in days], dtype="object"
    ).astype("datetime64[us, UTC]")
    return frame[list(PRICES_DAILY.column_names)]


def split(bars: pd.DataFrame, symbol: str, ex: date, ratio: float) -> pd.DataFrame:
    """`bars` with `symbol`'s prices from `ex` on divided by `ratio` (a raw split)."""
    out = bars.copy()
    hit = (out["symbol"] == symbol) & (out["date"] >= ex)
    for col in ("open", "high", "low", "close", "adj_close"):
        out.loc[hit, col] = out.loc[hit, col] / ratio
    return out


def actions(rows: list[tuple[str, date, float]]) -> pd.DataFrame:
    """Split rows `(symbol, ex_date, ratio)`, known from the bar before the ex-date."""
    frame = pd.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "ex_date": [r[1] for r in rows],
            "type": "split",
            "ratio": [r[2] for r in rows],
            "amount": [None] * len(rows),
            "vendor": "yfinance",
            "available_at": [bar_available_at(CAL.prev_session(r[1]), CAL) for r in rows],
        }
    )
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True).astype(
        "datetime64[us, UTC]"
    )
    frame["amount"] = pd.to_numeric(frame["amount"]).astype("float64")
    return frame[list(CORPORATE_ACTIONS.column_names)]


def world_bars(seed: int = 30) -> pd.DataFrame:
    """The clean world's bars (module docstring)."""
    rng = np.random.default_rng(seed)
    days = list(CAL.sessions(CAL.offset(LAST, -(SESSIONS - 1)), LAST))
    frames = []
    for i, symbol in enumerate((*MEMBERS, BENCHMARK)):
        listed = days[-NEW_SESSIONS:] if symbol == "NEW" else days
        frames.append(bars_for(symbol, listed, rng, 20.0 + 7.0 * i))
    return split(pd.concat(frames, ignore_index=True), "SPL", SPLIT_DAY, SPLIT_RATIO)


def write_world(lake: Lake, bars: pd.DataFrame) -> None:
    """Write `bars` and the world's reference tables (split, instruments, membership)."""
    lake.write_table("prices_daily", bars, mode="upsert")
    lake.write_table("corporate_actions", actions([("SPL", SPLIT_DAY, SPLIT_RATIO)]), mode="upsert")
    instruments = [
        Instrument(symbol=s, asset_class=AssetClass.EQUITY, name=f"{s} Inc", sector="Industrials")
        for s in MEMBERS
    ] + [Instrument(symbol=BENCHMARK, asset_class=AssetClass.ETF, name="SPDR S&P 500")]
    lake.write_table("instruments", instruments_frame(instruments), mode="upsert")
    start = {s: date(2010, 1, 4) for s in MEMBERS}
    start["NEW"] = CAL.offset(LAST, -(NEW_SESSIONS - 1))
    membership = pd.DataFrame(
        {
            "index_id": "SP500",
            "symbol": list(MEMBERS),
            "start_date": [start[s] for s in MEMBERS],
            "end_date": None,
        }
    )
    lake.write_table("index_membership", membership, mode="upsert")


def future_rows(lake: Lake, bars: pd.DataFrame) -> None:
    """Rows known only after `AS_OF` (module docstring): each symbol's walk continues from its
    last close."""
    rng = np.random.default_rng(99)
    later = list(CAL.sessions(date(2024, 3, 1), date(2024, 3, 15)))
    last = bars.sort_values("date").groupby("symbol")["close"].last()
    future = pd.concat(
        [bars_for(s, later, rng, float(last[s])) for s in (*MEMBERS, BENCHMARK)], ignore_index=True
    )
    symbol, ex, ratio = FUTURE_SPLIT
    lake.write_table("prices_daily", split(future, symbol, ex, ratio), mode="upsert")
    lake.write_table("corporate_actions", actions([FUTURE_SPLIT]), mode="upsert")


@dataclass(frozen=True)
class TechnicalWorld:
    clean: Lake
    dirty: Lake
    bars: pd.DataFrame

    def view(
        self, which: str = "clean", day: date = AS_OF, config: AtlasConfig = CONFIG
    ) -> DataView:
        return DataView(self.clean if which == "clean" else self.dirty, day, config)


def build_technical_world(root: Path) -> TechnicalWorld:
    """The clean and the dirty lake under `root`."""
    bars = world_bars()
    clean = Lake(root / "clean", registry=DEFAULT_REGISTRY.copy())
    write_world(clean, bars)
    dirty = Lake(root / "dirty", registry=DEFAULT_REGISTRY.copy())
    write_world(dirty, bars)
    future_rows(dirty, bars)
    return TechnicalWorld(clean=clean, dirty=dirty, bars=bars)
