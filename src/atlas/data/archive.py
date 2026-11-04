"""Raw vendor archive: every price and news answer, kept as it arrived.

Curated tables keep one row per key, so a later fetch replaces an earlier one (an edited
headline, a vendor's restated adjusted close, a delisted name a vendor has since dropped).
The archive keeps each answer: `archive_frame` writes a frame to
`<raw_dir>/<vendor>/<dataset>/<UTC date>/<HHMMSS>-<sha256[:12]>.parquet`, one file per vendor
in the frame, written once (an identical answer in the same second is the same file). SEC and
FRED answers are archived by their own clients (`raw/sec/`, `raw/fred/`).

`ArchivingPriceProvider` and `ArchivingNewsProvider` wrap a provider: they return exactly what
it returns and archive it on the way (`daily`, `actions`, `news`). The sync uses them when
`data.sync.archive_raw` is on. Reads "now" from the installed clock.
"""

import hashlib
import io
from collections.abc import Sequence
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from atlas.core.clock import get_clock
from atlas.core.logging import get_logger
from atlas.core.storage.meta import atomic_write
from atlas.data.news.base import NewsProvider
from atlas.data.providers.base import PriceProvider, ServedRecorder

_log = get_logger(__name__)


def archive_frame(
    raw_dir: Path, dataset: str, frame: pd.DataFrame, *, vendor_column: str
) -> list[Path]:
    """Write `frame` under `raw_dir`, one file per value of `vendor_column`; returns the files
    written (none for an empty frame; an identical file already there is not rewritten)."""
    if frame.empty:
        return []
    if vendor_column not in frame.columns:
        raise KeyError(f"archive: the frame has no {vendor_column!r} column")
    now = get_clock().now()
    written = []
    for vendor, rows in frame.groupby(vendor_column, sort=True):
        buffer = io.BytesIO()
        rows.reset_index(drop=True).to_parquet(buffer, index=False)
        data = buffer.getvalue()
        digest = hashlib.sha256(data).hexdigest()[:12]
        folder = raw_dir / str(vendor) / dataset / now.date().isoformat()
        path = folder / f"{now:%H%M%S}-{digest}.parquet"
        if not path.exists():
            atomic_write(path, partial(_write_bytes, data))
            written.append(path)
    if written:
        _log.debug("archive.write", dataset=dataset, files=len(written), rows=len(frame))
    return written


def _write_bytes(data: bytes, fh: BinaryIO) -> None:
    fh.write(data)


class ArchivingPriceProvider:
    """A `PriceProvider` that archives the daily bars and actions `inner` returns (by the
    frames' `vendor` column), then returns them unchanged."""

    def __init__(self, inner: PriceProvider, raw_dir: Path) -> None:
        """Wrap `inner`, archiving under `raw_dir` (a lake's `raw_dir`)."""
        self.inner = inner
        self.name = inner.name
        self._raw_dir = raw_dir

    def fetch_daily(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """`inner.fetch_daily`, archived as `daily`."""
        out = self.inner.fetch_daily(symbols, start, end)
        archive_frame(self._raw_dir, "daily", out, vendor_column="vendor")
        return out

    def fetch_actions(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        """`inner.fetch_actions`, archived as `actions`."""
        out = self.inner.fetch_actions(symbols, start, end)
        archive_frame(self._raw_dir, "actions", out, vendor_column="vendor")
        return out

    def record_served(self, daily: pd.DataFrame) -> None:
        """Passed to `inner` when it records which vendor served each symbol."""
        if isinstance(self.inner, ServedRecorder):
            self.inner.record_served(daily)

    def close(self) -> None:
        """Close `inner` if it can be closed."""
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()


class ArchivingNewsProvider:
    """A `NewsProvider` that archives the articles `inner` returns (by their `provider`
    column), then returns them unchanged."""

    def __init__(self, inner: NewsProvider, raw_dir: Path) -> None:
        """Wrap `inner`, archiving under `raw_dir` (a lake's `raw_dir`)."""
        self.inner = inner
        self.name = inner.name
        self._raw_dir = raw_dir

    def fetch_news(
        self, symbols: Sequence[str] | None, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """`inner.fetch_news`, archived as `news`."""
        out = self.inner.fetch_news(symbols, start, end)
        archive_frame(self._raw_dir, "news", out, vendor_column="provider")
        return out

    def close(self) -> None:
        """Close `inner` if it can be closed."""
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()
