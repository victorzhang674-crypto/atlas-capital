"""yfinance adapter: free daily bars, dividends and splits from Yahoo Finance.

Yahoo serves Open/High/Low/Close, Volume and Dividends already adjusted for every split up
to today (AAPL's 2020-08-28 close shows as 124.81, not the 499.23 it traded at). This adapter
un-adjusts them with the symbol's full split history, so the contract's prices, volumes and
dividend amounts are raw: raw price = yahoo * F(d), raw volume = yahoo / F(d), raw dividend =
yahoo * F(d), where F(d) is the product of the ratios of all splits with ex-date after d.
`adj_close` is Yahoo's split- and dividend-adjusted close, unchanged.

Requests are batched (`batch_size` symbols per `yf.download`), throttled and retried (see
`PriceDataConfig`). Yahoo pads a symbol it has no data for (delisted, unknown) with NaN rows;
such rows are dropped, so the symbol is simply absent from the result. A symbol with bars but
no split history (`yf.Ticker(s).splits` is `None`) is also left out, with a
`prices.no_split_history` warning: its raw prices cannot be rebuilt, and the rest of the batch
is still served. Downloads and split histories are memoised per instance: `fetch_actions`
after `fetch_daily` on the same window makes no new request. The download memo keeps at most
`memo_size` (symbol, window) bar sets, dropping the least recently used; split histories are
small and kept for the instance's life. Symbols in `no_data` (Yahoo is known to have no bars
for them: `config/yahoo_no_data.yaml`, `load_yahoo_no_data`) are left out without a request,
exactly as if Yahoo had returned nothing, which saves ~25 s of yfinance retries per name and
lets a fallback provider serve them.

Error boundary: yfinance signals vendor trouble with many exception types (HTTP, rate-limit,
JSON, its own errors), so *any* exception raised inside `yf.download` / `Ticker.splits` is
retried and, when retries run out, raised as `DataError` chained to the original (`__cause__`).
That includes a programming error in the call itself (say a keyword yfinance renamed), which
therefore shows up as a failed fetch and a fallback to the secondary vendor, not a crash; read
the chained cause. Errors in this module's own parsing are not caught.
"""

import time
import warnings
from collections import OrderedDict
from collections.abc import Callable, Collection, Sequence
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from typing import Self

import numpy as np
import pandas as pd
import yfinance as yf
from pandas.errors import Pandas4Warning
from pydantic import ValidationError, model_validator

from atlas.core.calendar import MARKET_TZ
from atlas.core.config import PriceDataConfig, read_config_file
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import get_logger
from atlas.core.types.base import AtlasModel
from atlas.core.types.instruments import Symbol, normalize_symbol, vendor_symbol
from atlas.data.providers.base import (
    RetryPolicy,
    Throttle,
    call_with_retry,
    check_request,
    empty_actions,
    empty_daily,
    normalize_actions,
    normalize_daily,
)

DownloadFn = Callable[[list[str], date, date], pd.DataFrame]
"""`(vendor symbols, start, exclusive end) -> yf.download(..., group_by="ticker")` frame."""

SplitsFn = Callable[[str], "pd.Series | None"]
"""`vendor symbol -> yf.Ticker(symbol).splits` (ratios by ex-date timestamp, or `None`)."""

_BAR_FIELDS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
_log = get_logger(__name__)


def _yf_download(
    symbols: list[str], start: date, end_exclusive: date, *, timeout: float
) -> pd.DataFrame:
    # yfinance 1.7 trips pandas-4 deprecation notices inside its own code; only those are muted.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Pandas4Warning)
        frame = yf.download(
            symbols,
            start=start.isoformat(),
            end=end_exclusive.isoformat(),
            auto_adjust=False,
            actions=True,
            group_by="ticker",
            threads=False,
            progress=False,
            multi_level_index=True,
            timeout=timeout,
        )
    return pd.DataFrame() if frame is None else frame


def _yf_splits(symbol: str) -> pd.Series | None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Pandas4Warning)
        splits: pd.Series | None = yf.Ticker(symbol).splits
    return splits


class YFinanceProvider:
    """`PriceProvider` backed by yfinance. `download`/`splits`/`sleep` are injectable for tests;
    by default they call yfinance and `time.sleep`."""

    name = "yfinance"

    def __init__(
        self,
        config: PriceDataConfig | None = None,
        *,
        download: DownloadFn | None = None,
        splits: SplitsFn | None = None,
        sleep: Callable[[float], None] = time.sleep,
        no_data: Collection[str] = (),
    ) -> None:
        """A provider with `config` (default `PriceDataConfig()`; pass `cfg.data.prices`);
        `no_data` lists symbols Yahoo has no bars for (not requested)."""
        cfg = config if config is not None else PriceDataConfig()
        self._no_data = frozenset(normalize_symbol(s) for s in no_data)
        self._batch_size = cfg.batch_size
        self._policy = RetryPolicy.from_config(cfg)
        self._download = (
            download
            if download is not None
            else partial(_yf_download, timeout=cfg.request_timeout_seconds)
        )
        self._splits_fn = splits if splits is not None else _yf_splits
        self._sleep = sleep
        self._throttle = Throttle(self._policy.min_interval_seconds, sleep)
        self._memo_size = cfg.memo_size
        self._bars: OrderedDict[tuple[str, date, date], pd.DataFrame] = OrderedDict()
        self._splits: dict[str, tuple[tuple[date, float], ...] | None] = {}

    @property
    def no_data(self) -> frozenset[str]:
        """Canonical symbols this provider never asks Yahoo about (known to have no bars)."""
        return self._no_data

    def fetch_daily(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Raw daily bars of `symbols` for sessions in `[start, end]` (see module docstring)."""
        frames = []
        for symbol, rows in self._symbol_bars(symbols, start, end).items():
            bars = rows.dropna(how="all", subset=_BAR_FIELDS)  # dividend-only rows are no bar
            factor = self._split_factor(symbol, bars.index)
            frames.append(
                pd.DataFrame(
                    {
                        "symbol": symbol,
                        "date": bars.index,
                        "open": bars["Open"].to_numpy() * factor,
                        "high": bars["High"].to_numpy() * factor,
                        "low": bars["Low"].to_numpy() * factor,
                        "close": bars["Close"].to_numpy() * factor,
                        "adj_close": bars["Adj Close"].to_numpy(),
                        "volume": np.round(bars["Volume"].to_numpy() / factor),
                        "vendor": self.name,
                    }
                )
            )
        return normalize_daily(pd.concat(frames, ignore_index=True)) if frames else empty_daily()

    def fetch_actions(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Splits and raw-cash dividends of `symbols` with ex-dates in `[start, end]`."""
        rows: list[dict[str, object]] = []
        for symbol, bars in self._symbol_bars(symbols, start, end).items():
            if "Dividends" in bars.columns:
                paid = bars["Dividends"].fillna(0.0)
                paid = paid[paid > 0]
                factor = self._split_factor(symbol, paid.index)
                for day, amount, f in zip(paid.index, paid.to_numpy(), factor, strict=True):
                    rows.append(_action(symbol, day, "dividend", amount=float(amount * f)))
            for day, ratio in self._splits.get(symbol) or ():
                if start <= day <= end:
                    rows.append(_action(symbol, day, "split", ratio=float(ratio)))
        if not rows:
            return empty_actions()
        return normalize_actions(pd.DataFrame(rows))

    # --- vendor access ------------------------------------------------------------------------

    def _symbol_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        """Yahoo's rows per canonical symbol (date index). Left out: a symbol with no bar, and
        one with bars but no split history (its raw prices cannot be rebuilt; logged once)."""
        requested = check_request(symbols, start, end)
        wanted = [s for s in requested if s not in self._no_data]
        skipped = len(requested) - len(wanted)
        if skipped:
            _log.info("prices.known_no_data", vendor=self.name, symbols=skipped)
        got: dict[str, pd.DataFrame] = {}
        for symbol in wanted:
            key = (symbol, start, end)
            if key in self._bars:
                self._bars.move_to_end(key)
                got[symbol] = self._bars[key]
        missing = [s for s in wanted if s not in got]
        for i in range(0, len(missing), self._batch_size):
            got.update(self._download_batch(missing[i : i + self._batch_size], start, end))
        out = {}
        for symbol in wanted:
            bars = got[symbol]
            if bars.dropna(how="all", subset=_BAR_FIELDS).empty:
                _log.info("prices.no_data", vendor=self.name, symbol=symbol)
            elif self._split_history(symbol) is not None:
                out[symbol] = bars
        return out

    def _download_batch(
        self, symbols: list[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        """Yahoo's rows for `symbols` (one vendor request), also kept in the bounded memo."""
        vendor = {vendor_symbol(s, "-"): s for s in symbols}
        self._throttle.wait()
        frame = call_with_retry(
            lambda: self._download(list(vendor), start, end + timedelta(days=1)),
            self._policy,
            self._sleep,
            f"yfinance download of {list(vendor)}",
            (Exception,),
        )
        got = {}
        for vsym, symbol in vendor.items():
            got[symbol] = _bars_of(frame, vsym, len(vendor) == 1)
            self._bars[(symbol, start, end)] = got[symbol]
            self._bars.move_to_end((symbol, start, end))
            while len(self._bars) > self._memo_size:
                self._bars.popitem(last=False)
        return got

    def _split_history(self, symbol: str) -> tuple[tuple[date, float], ...] | None:
        """`(ex_date, ratio)` of every split in the symbol's whole history, in date order, or
        `None` when Yahoo has no history for it (logged as `prices.no_split_history`)."""
        if symbol not in self._splits:
            vsym = vendor_symbol(symbol, "-")
            self._throttle.wait()
            raw = call_with_retry(
                lambda: self._splits_fn(vsym),
                self._policy,
                self._sleep,
                f"yfinance split history of {vsym}",
                (Exception,),
            )
            if raw is None:
                _log.warning("prices.no_split_history", vendor=self.name, symbol=symbol)
                self._splits[symbol] = None
                return None
            ratios = raw[raw > 0].sort_index()
            self._splits[symbol] = tuple(
                zip(_to_dates(ratios.index), ratios.to_numpy(dtype="float64").tolist(), strict=True)
            )
        return self._splits[symbol]

    def _split_factor(self, symbol: str, days: pd.Index) -> np.ndarray:
        """F(d) for each day: product of the ratios of splits with ex-date after d."""
        factor = np.ones(len(days))
        dates = np.array(list(days), dtype="object")
        for split_day, ratio in self._splits.get(symbol) or ():
            factor[dates < split_day] *= ratio
        return factor


def _bars_of(frame: pd.DataFrame, vsym: str, only_symbol: bool) -> pd.DataFrame:
    """One symbol's rows from a `group_by="ticker"` frame, date index. Rows with neither bar
    data nor a dividend are dropped (Yahoo's padding); a dividend on a day without a bar is
    kept for `fetch_actions`."""
    if frame.empty:
        return pd.DataFrame(columns=_BAR_FIELDS)
    if isinstance(frame.columns, pd.MultiIndex):
        if vsym not in frame.columns.get_level_values(0):
            return pd.DataFrame(columns=_BAR_FIELDS)
        sub = pd.DataFrame(frame[vsym])
    elif only_symbol:
        sub = frame.copy()
    else:
        raise DataError(f"yfinance: unexpected flat columns for a batch: {list(frame.columns)}")
    missing = [c for c in _BAR_FIELDS if c not in sub.columns]
    if missing:
        raise DataError(f"yfinance: {vsym} frame lacks columns {missing}")
    has_bar = sub[_BAR_FIELDS].notna().any(axis=1)
    if "Dividends" in sub.columns:
        has_bar |= sub["Dividends"].fillna(0.0) > 0
    sub = sub[has_bar].copy()
    sub.index = pd.Index(_to_dates(sub.index), dtype="object")
    return sub


def _to_dates(index: pd.Index) -> list[date]:
    """A datetime index as New York `date`s (naive timestamps are taken as New York already)."""
    stamps = pd.DatetimeIndex(index)
    if stamps.tz is not None:
        stamps = stamps.tz_convert(MARKET_TZ)
    return [ts.date() for ts in stamps]


def _action(
    symbol: str,
    day: date,
    kind: str,
    *,
    ratio: float = float("nan"),
    amount: float = float("nan"),
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "ex_date": day,
        "type": kind,
        "ratio": ratio,
        "amount": amount,
        "vendor": YFinanceProvider.name,
    }


class _NoDataEntry(AtlasModel):
    symbol: Symbol
    checked: date
    note: str | None = None


class _NoDataFile(AtlasModel):
    """Body of `config/yahoo_no_data.yaml`."""

    symbols: tuple[_NoDataEntry, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> Self:
        names = [e.symbol for e in self.symbols]
        if len(set(names)) != len(names):
            raise ValueError("a symbol is listed twice")
        return self


def load_yahoo_no_data(path: Path) -> frozenset[str]:
    """The canonical symbols in `path` (`symbols: [{symbol, checked, note}]`; see
    `config/yahoo_no_data.yaml`). A missing file gives none; invalid content raises
    `ConfigError` naming the file."""
    data = read_config_file(path)
    try:
        body = _NoDataFile.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{path}: {problems}") from None
    return frozenset(e.symbol for e in body.symbols)
