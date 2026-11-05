"""A synthetic lake for the Stage 21 loader tests: a calm market from 2017 that crashes on
2020-02-24 (about 20 sessions), then recovers, with SPY, HYG, IEF, 24 stocks (point-in-time
`sp500` membership with changes) and FRED rows with release-time availability:

- market series (`VIXCLS`, `T10Y2Y`, `BAMLH0A0HYM2` from 2019-10-31 only, like FRED's 3-year
  OAS) dated each session, available the next day 06:00 New York (Stage 10 `attach_available_at`);
- `NFCI` weekly (Friday obs, released the next Wednesday 08:30): -0.5 calm, +0.4 in the crash;
- `ICSA` weekly (Saturday obs, released the next Thursday 08:30), spiking in the crash;
- `INDPRO`, `PAYEMS`, `CPILFESL` monthly (obs dated the 1st, released on the 15th of the next
  month): steady growth, collapsing from the March 2020 observation; core CPI steady.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import (
    AtlasConfig,
    MacroConfig,
    QuantConfig,
    RegimeConfig,
    RegimeCreditConfig,
    RegimeHMMConfig,
    RegimeTrendConfig,
    RegimeVolConfig,
)
from atlas.core.pit import bar_available_at
from atlas.core.storage import Lake
from atlas.data.macro import attach_available_at, load_macro_series
from tiny_universe import bars_from_returns

FIRST = date(2017, 1, 3)
LAST = date(2020, 6, 30)
CRASH_START = date(2020, 2, 24)
CRASH_END = date(2020, 3, 20)
STOCKS = tuple(f"S{i:02d}" for i in range(24))
LEAVERS = ("S00", "S01")  # members until 2019-06-28
JOINERS = ("S22", "S23")  # members from 2019-07-01
LEAVE_DAY = date(2019, 6, 28)
JOIN_DAY = date(2019, 7, 1)
CATALOG = load_macro_series(Path("config/macro.yaml"))


def world_config() -> AtlasConfig:
    """Settings scaled to the short synthetic history (windows in sessions)."""
    regime = RegimeConfig(
        train_start=FIRST,
        vol=RegimeVolConfig(percentile_window=500, percentile_min_obs=120),
        trend=RegimeTrendConfig(breadth_min_names=10),
        hmm=RegimeHMMConfig(n_states=2, min_obs=250, n_init=2),
        credit=RegimeCreditConfig(zscore_window=126),
    )
    return AtlasConfig(quant=QuantConfig(regime=regime))


@dataclass(frozen=True)
class RegimeWorld:
    """The lake, its sessions and the scenario dates."""

    lake: Lake
    sessions: tuple[date, ...]
    config: AtlasConfig


def _returns(days: list[date], rng: np.random.Generator) -> dict[str, np.ndarray]:
    n = len(days) - 1
    stamp = np.array(days[1:])
    crash = (stamp >= CRASH_START) & (stamp <= CRASH_END)
    after = stamp > CRASH_END
    spy = rng.normal(0.0005, 0.010, n)
    calmer = (stamp >= date(2019, 1, 1)) & (stamp < CRASH_START)
    spy[calmer] = rng.normal(0.0006, 0.005, calmer.sum())
    spy[crash] = np.clip(rng.normal(-0.02, 0.025, crash.sum()), -0.055, 0.05)
    spy[after] = rng.normal(0.002, 0.015, after.sum())
    hyg = 0.35 * spy + rng.normal(0.0001, 0.002, n)
    hyg[crash] -= 0.006
    ief = rng.normal(0.0001, 0.003, n)
    ief[crash] += 0.002
    out = {"SPY": spy, "HYG": hyg, "IEF": ief}
    for i, s in enumerate(STOCKS):
        out[s] = 0.0002 + (0.6 + i * 0.04) * spy + rng.normal(0.0, 0.008, n)
    return {k: np.clip(v, -0.06, 0.06) for k, v in out.items()}


def _macro(
    series_id: str, obs: list[date], values: list[float], released: list[date]
) -> pd.DataFrame:
    rows = pd.DataFrame(
        {"series_id": series_id, "obs_date": obs, "value": values, "realtime_start": released}
    )
    out = attach_available_at(rows, CATALOG.get(series_id), MacroConfig())
    out["available_at"] = out["available_at"].astype("datetime64[us, UTC]")
    return out


def late_row(series_id: str, obs: date, value: float, published: date) -> pd.DataFrame:
    """One `macro_observations` row of a market series first available on `published` at
    06:00 New York (a late first release or a later vintage)."""
    stamp = pd.Timestamp(
        datetime(published.year, published.month, published.day, 6), tz="America/New_York"
    )
    return pd.DataFrame(
        {
            "series_id": [series_id],
            "obs_date": [obs],
            "value": [value],
            "realtime_start": [published],
            "available_at": pd.Series([stamp.tz_convert("UTC")]).astype("datetime64[us, UTC]"),
        }
    )


def _fridays(start: date, end: date, weekday: int) -> list[date]:
    d = start + timedelta(days=(weekday - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def macro_frames(days: list[date], spy: np.ndarray, rng: np.random.Generator) -> list[pd.DataFrame]:
    """Every FRED series of the scenario as `macro_observations` rows. VIX follows SPY's
    21-session realised vol (index points ≈ 100 x annualised vol + 3), 70 in the crash."""
    rv = pd.Series(np.concatenate([[np.nan], spy])).rolling(21, min_periods=5).std() * np.sqrt(252)
    vix = list((100 * rv.bfill() + 3 + rng.normal(0, 0.3, len(days))).to_numpy())
    for i, d in enumerate(days):
        if CRASH_START <= d <= CRASH_END:
            vix[i] = 70.0
    slope = list(0.6 + np.cumsum(rng.normal(0, 0.01, len(days))))
    frames = [
        _macro("VIXCLS", days, vix, days),
        _macro("T10Y2Y", days, slope, days),
    ]
    oas_days = [d for d in days if d >= date(2019, 10, 31)]
    oas = [8.0 if CRASH_START <= d <= CRASH_END else 3.5 + 0.05 * rng.random() for d in oas_days]
    frames.append(_macro("BAMLH0A0HYM2", oas_days, oas, oas_days))
    fridays = _fridays(date(2016, 1, 1), LAST, 4)
    nfci = [0.4 if CRASH_START - timedelta(days=4) <= f <= CRASH_END else -0.5 for f in fridays]
    frames.append(_macro("NFCI", fridays, nfci, [f + timedelta(days=5) for f in fridays]))
    saturdays = _fridays(date(2016, 1, 1), LAST, 5)
    claims = [
        900_000.0 if date(2020, 3, 14) <= s <= date(2020, 5, 30) else 260_000.0 - 150.0 * i
        for i, s in enumerate(saturdays)
    ]
    frames.append(_macro("ICSA", saturdays, claims, [s + timedelta(days=5) for s in saturdays]))
    months = list(pd.date_range("2015-01-01", "2020-05-01", freq="MS").date)
    released = [date(m.year + (m.month == 12), m.month % 12 + 1, 15) for m in months]
    grow, cpi = [100.0], [250.0]
    for i, m in enumerate(months[1:], start=1):  # growth accelerates, inflation slows
        rate = 0.93 if m >= date(2020, 3, 1) else 1.0005 + 0.00006 * i
        grow.append(grow[-1] * rate)
        cpi.append(cpi[-1] * (1.003 - 0.00003 * i))
    frames.append(_macro("INDPRO", months, grow, released))
    frames.append(_macro("PAYEMS", months, [150_000.0 * g / 100 for g in grow], released))
    frames.append(_macro("CPILFESL", months, cpi, released))
    return frames


def build_regime_world(lake: Lake, seed: int = 21, *, macro: bool = True) -> RegimeWorld:
    """Write the scenario into `lake` and describe it (`macro=False`: no FRED table at all)."""
    cal = TradingCalendar()
    days = list(cal.sessions(FIRST, LAST))
    rng = np.random.default_rng(seed)
    rets = _returns(days, rng)
    frames = [bars_from_returns(s, r, days, rng=rng, calendar=cal) for s, r in rets.items()]
    lake.write_table("prices_daily", pd.concat(frames, ignore_index=True), mode="upsert")
    members = [s for s in STOCKS if s not in JOINERS]
    membership = pd.DataFrame(
        {
            "index_id": "SP500",
            "symbol": [*members, *JOINERS],
            "start_date": [date(2010, 1, 4)] * len(members) + [JOIN_DAY] * len(JOINERS),
            "end_date": [LEAVE_DAY if s in LEAVERS else None for s in members]
            + [None] * len(JOINERS),
        }
    )
    lake.write_table("index_membership", membership, mode="upsert")
    if macro:
        frames = macro_frames(days, rets["SPY"], rng)
        lake.write_table("macro_observations", pd.concat(frames, ignore_index=True), mode="upsert")
    return RegimeWorld(lake=lake, sessions=tuple(days), config=world_config())


def add_future_rows(world: RegimeWorld) -> None:
    """Bars for July 2020 (after the scenario), a 4:1 split of S05 on 2020-07-08 and late
    macro rows: nothing about decision days up to 2020-06-30 may change."""
    cal = TradingCalendar()
    days = list(cal.sessions(date(2020, 7, 1), date(2020, 7, 31)))
    rng = np.random.default_rng(5)
    last = world.lake.read_table("prices_daily", filters=[("date", "=", LAST)])
    frames = []
    for symbol in last["symbol"]:
        start = float(last.loc[last["symbol"] == symbol, "close"].iloc[0])
        grid = [LAST, *days]
        frame = bars_from_returns(
            symbol, rng.normal(0, 0.05, len(days)), grid, start=start, rng=rng, calendar=cal
        ).iloc[1:]
        if symbol == "S05":  # raw prices quartered from the split day on
            split = frame["date"] >= date(2020, 7, 8)
            for col in ("open", "high", "low", "close", "adj_close"):
                frame.loc[split, col] = frame.loc[split, col] / 4
        frames.append(frame)
    world.lake.write_table("prices_daily", pd.concat(frames, ignore_index=True), mode="upsert")
    actions = pd.DataFrame(
        {
            "symbol": ["S05"],
            "ex_date": [date(2020, 7, 8)],
            "type": ["split"],
            "ratio": [4.0],
            "amount": [np.nan],
            "vendor": "yfinance",
            "available_at": [bar_available_at(date(2020, 7, 8), cal)],
        }
    )
    actions["available_at"] = pd.to_datetime(actions["available_at"], utc=True).astype(
        "datetime64[us, UTC]"
    )
    world.lake.write_table("corporate_actions", actions, mode="upsert")
    vix = _macro("VIXCLS", days, [90.0] * len(days), days)
    nfci_rev = _macro("NFCI", [date(2020, 6, 26)], [3.0], [date(2020, 7, 1)])  # a later vintage
    claims = _macro("ICSA", [date(2020, 6, 27)], [5_000_000.0], [date(2020, 7, 2)])
    world.lake.write_table(
        "macro_observations", pd.concat([vix, nfci_rev, claims], ignore_index=True), mode="upsert"
    )


def utc(y: int, m: int, d: int, h: int = 0) -> datetime:
    """An aware UTC instant."""
    return datetime(y, m, d, h, tzinfo=UTC)


def add_dividend(world: RegimeWorld, symbol: str, ex_date: date, amount: float) -> None:
    """A cash dividend of `symbol` (USD per share) with ex-date `ex_date`, known the evening
    before: every earlier adjusted close is scaled once it is known."""
    cal = TradingCalendar()
    prev = cal.offset(ex_date, -1)
    actions = pd.DataFrame(
        {
            "symbol": [symbol],
            "ex_date": [ex_date],
            "type": ["dividend"],
            "ratio": [np.nan],
            "amount": [amount],
            "vendor": "yfinance",
            "available_at": [bar_available_at(prev, cal)],
        }
    )
    actions["available_at"] = pd.to_datetime(actions["available_at"], utc=True).astype(
        "datetime64[us, UTC]"
    )
    world.lake.write_table("corporate_actions", actions, mode="upsert")
