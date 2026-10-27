"""On-disk cache in front of a price provider (design spec §16: provider + cache + fallback).

Layout: `<cache_dir>/<provider name>/{daily,actions}/<SYMBOL>_<start>_<end>.parquet`, one file
per symbol and window, written atomically. What is cached, so a cache can never hide data:

- daily bars of a symbol that came back with rows (a symbol with no rows is asked again
  next time: it may be a transient vendor gap, not a delisting);
- actions of a symbol whose bars for the same window are cached (an empty actions answer
  from a source that served bars means "no actions"), including an empty result;
- nothing from a call that raised, and nothing for a window ending today or later
  (`get_clock().today()`), since the latest bar may still change.

Pass a lake's `cache_dir` (`<lake>/cache/prices`) as `cache_dir`. Delete the directory to
force a refetch.

Needs an installed clock: every fetch reads `get_clock().today()`, which raises `AtlasError`
("no clock installed") outside `use_clock(...)`. Run it under `use_clock(LiveClock())` for
live work (the Stage 14 CLI) or `use_clock(SimClock(...))` in a backtest or test.

When `inner` keeps a record of which vendor served each symbol (`ServedRecorder`, e.g.
`FallbackPriceProvider`), bars served from the cache are passed to its `record_served`, so
actions fetched afterwards still come from the vendor whose bars are cached.
"""

from collections.abc import Callable, Sequence
from datetime import date
from functools import partial
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from atlas.core.clock import get_clock
from atlas.core.logging import get_logger
from atlas.core.storage.meta import atomic_write
from atlas.data.providers.base import (
    PriceProvider,
    ServedRecorder,
    check_request,
    empty_actions,
    empty_daily,
    normalize_actions,
    normalize_daily,
)

_log = get_logger(__name__)


class CachedPriceProvider:
    """`inner` with finished windows memoised under `cache_dir` (see module docstring)."""

    def __init__(self, inner: PriceProvider, cache_dir: Path) -> None:
        """Cache `inner`'s answers under `cache_dir/<inner.name>/`."""
        self.inner = inner
        self.name = inner.name
        self._root = Path(cache_dir) / inner.name

    def fetch_daily(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Daily bars, from the cache where possible."""
        return self._fetch("daily", symbols, start, end)

    def fetch_actions(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """Corporate actions, from the cache where possible."""
        return self._fetch("actions", symbols, start, end)

    def _fetch(self, kind: str, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        wanted = check_request(symbols, start, end)
        cacheable = end < get_clock().today()
        normalize, empty = (
            (normalize_daily, empty_daily)
            if kind == "daily"
            else (normalize_actions, empty_actions)
        )
        frames: list[pd.DataFrame] = []
        missing = []
        for symbol in wanted:
            path = self._path(kind, symbol, start, end)
            if cacheable and path.exists():
                frames.append(normalize(pd.read_parquet(path)))
            else:
                missing.append(symbol)
        if kind == "daily" and frames and isinstance(self.inner, ServedRecorder):
            self.inner.record_served(pd.concat(frames, ignore_index=True))
        if missing:
            fetch: Callable[[Sequence[str], date, date], pd.DataFrame] = (
                self.inner.fetch_daily if kind == "daily" else self.inner.fetch_actions
            )
            fresh = fetch(missing, start, end)
            frames.append(fresh)
            if cacheable:
                self._store(kind, missing, fresh, start, end)
        _log.debug(
            "prices.cache",
            vendor=self.name,
            kind=kind,
            hits=len(wanted) - len(missing),
            misses=len(missing),
        )
        frames = [f for f in frames if not f.empty]
        if not frames:
            return empty()
        sort = ["symbol", "date"] if kind == "daily" else ["symbol", "ex_date", "type"]
        out = pd.concat(frames, ignore_index=True)
        return out.sort_values(sort, kind="stable").reset_index(drop=True)

    def _store(
        self, kind: str, symbols: list[str], fresh: pd.DataFrame, start: date, end: date
    ) -> None:
        for symbol in symbols:
            rows = fresh[fresh["symbol"] == symbol].reset_index(drop=True)
            if kind == "daily" and rows.empty:
                continue
            if kind == "actions" and not self._path("daily", symbol, start, end).exists():
                continue
            atomic_write(self._path(kind, symbol, start, end), partial(_write_parquet, rows))

    def _path(self, kind: str, symbol: str, start: date, end: date) -> Path:
        return self._root / kind / f"{symbol}_{start.isoformat()}_{end.isoformat()}.parquet"


def _write_parquet(frame: pd.DataFrame, fh: BinaryIO) -> None:
    frame.to_parquet(fh, index=False)
