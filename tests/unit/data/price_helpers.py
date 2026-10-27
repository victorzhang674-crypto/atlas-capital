"""Test doubles and fixture loaders for the Stage 07 price tests (no network)."""

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd

from atlas.core.errors import DataError
from atlas.data.providers.base import (
    check_request,
    empty_actions,
    empty_daily,
    normalize_actions,
    normalize_daily,
)


def daily_rows(
    symbol: str, rows: Sequence[tuple[date, float, int]], vendor: str = "fake"
) -> pd.DataFrame:
    """A contract-shaped daily frame from `(date, close, volume)` rows (open = high = low =
    close, adj_close = close)."""
    frame = pd.DataFrame(
        {
            "symbol": [symbol] * len(rows),
            "date": [r[0] for r in rows],
            "open": [r[1] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[1] for r in rows],
            "close": [r[1] for r in rows],
            "adj_close": [r[1] for r in rows],
            "volume": [r[2] for r in rows],
            "vendor": [vendor] * len(rows),
        }
    )
    return normalize_daily(frame)


class FakePriceProvider:
    """In-memory `PriceProvider`: serves slices of the frames it was given.

    `fail_symbols` raise `DataError` whenever they are part of a request (so a batch holding
    one of them fails as a whole, like a vendor error); `fail_all` makes every call fail;
    `fail_actions` does the same for `fetch_actions` only. Every call is recorded in `calls`.
    """

    def __init__(
        self,
        daily: pd.DataFrame | None = None,
        actions: pd.DataFrame | None = None,
        *,
        name: str = "fake",
        fail_symbols: Sequence[str] = (),
        fail_all: bool = False,
        fail_actions: Sequence[str] = (),
    ) -> None:
        self.name = name
        self.daily = normalize_daily(daily) if daily is not None else empty_daily()
        self.actions = normalize_actions(actions) if actions is not None else empty_actions()
        self.fail_symbols = set(fail_symbols)
        self.fail_all = fail_all
        self.fail_actions = set(fail_actions)
        self.calls: list[tuple[str, tuple[str, ...], date, date]] = []

    def fetch_daily(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        syms = check_request(symbols, start, end)
        self.calls.append(("daily", syms, start, end))
        if self.fail_all or self.fail_symbols & set(syms):
            raise DataError(f"{self.name}: simulated vendor failure for {list(syms)}")
        d = self.daily
        mask = d["symbol"].isin(syms) & (d["date"] >= start) & (d["date"] <= end)
        return d[mask].reset_index(drop=True)

    def fetch_actions(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        syms = check_request(symbols, start, end)
        self.calls.append(("actions", syms, start, end))
        if self.fail_all or (self.fail_symbols | self.fail_actions) & set(syms):
            raise DataError(f"{self.name}: simulated actions failure for {list(syms)}")
        a = self.actions
        mask = a["symbol"].isin(syms) & (a["ex_date"] >= start) & (a["ex_date"] <= end)
        return a[mask].reset_index(drop=True)


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "prices"
"""`tests/fixtures/prices/`: recorded vendor payloads (see its README.md)."""

_NY = "America/New_York"


def load_yf_symbol(symbol: str) -> pd.DataFrame:
    """The recorded yfinance frame of one vendor symbol (yfinance's own column names)."""
    frame = pd.read_csv(FIXTURES / f"yfinance_{symbol}.csv", index_col="Date", parse_dates=True)
    frame.index.name = "Date"
    return frame


def load_yf_splits() -> dict[str, pd.Series]:
    """Recorded `yf.Ticker(s).splits` per symbol, indexed like yfinance (09:30 New York)."""
    rows = pd.read_csv(FIXTURES / "yfinance_splits.csv")
    out: dict[str, pd.Series] = {}
    for sym, group in rows.groupby("symbol"):
        index = pd.DatetimeIndex(pd.to_datetime(group["date"]) + pd.Timedelta(hours=9, minutes=30))
        out[str(sym)] = pd.Series(group["ratio"].to_numpy(), index=index.tz_localize(_NY))
    return out


class FakeYFinance:
    """Stand-ins for `yf.download` and `yf.Ticker(s).splits`, served from recorded fixtures.

    `frames` maps vendor symbol → yfinance per-symbol frame; unknown symbols come back as
    all-NaN columns (as Yahoo pads a delisted ticker). `splits` maps vendor symbol → split
    series; symbols in `no_splits` return `None` (as yfinance does for a delisted name), other
    unknown symbols an empty series. `fail_times` makes the next N download calls raise.
    """

    def __init__(
        self,
        frames: dict[str, pd.DataFrame] | None = None,
        splits: dict[str, pd.Series] | None = None,
        *,
        no_splits: Sequence[str] = ("SIVB",),
        fail_times: int = 0,
    ) -> None:
        self.frames = (
            frames if frames is not None else {s: load_yf_symbol(s) for s in ("AAPL", "SPY")}
        )
        self.split_series = splits if splits is not None else load_yf_splits()
        self.no_splits = set(no_splits)
        self.fail_times = fail_times
        self.download_calls: list[tuple[list[str], date, date]] = []
        self.split_calls: list[str] = []

    def download(self, symbols: list[str], start: date, end_exclusive: date) -> pd.DataFrame:
        self.download_calls.append((list(symbols), start, end_exclusive))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("simulated Yahoo outage")
        parts = {}
        some = next(iter(self.frames.values()))
        for sym in symbols:
            frame = self.frames.get(sym)
            if frame is None:
                cols = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
                frame = pd.DataFrame(float("nan"), index=some.index, columns=cols)
            idx = frame.index
            naive = idx.tz_localize(None) if idx.tz is not None else idx
            keep = (naive >= pd.Timestamp(start)) & (naive < pd.Timestamp(end_exclusive))
            parts[sym] = frame[keep]
        return pd.concat(parts, axis=1)

    def splits(self, symbol: str) -> pd.Series | None:
        self.split_calls.append(symbol)
        if symbol in self.no_splits:
            return None
        return self.split_series.get(symbol, pd.Series(dtype="float64"))
