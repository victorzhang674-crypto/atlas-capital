"""Ingest news into the lake's `news` table, plus the provider chain the configuration asks for.

`ingest_news` asks one provider (often a `FallbackNewsProvider`) for a window and upserts the
articles by the table's key, so re-running a window, or ingesting overlapping windows, stores
each vendor article once and keeps its `article_id`. Cross-source duplicates are kept in the
table and hidden at query time (`atlas.data.news.pit`), so which copy survives never depends on
ingest order. A provider failure propagates and nothing is written (fail safe).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from atlas.core.clock import get_clock
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage import Lake
from atlas.core.types.base import FrozenDict
from atlas.data.news.alpaca_news import AlpacaNewsProvider
from atlas.data.news.base import NEWS, NewsProvider, check_news_window
from atlas.data.news.yahoo_news import YahooNewsProvider

_log = get_logger(__name__)


@dataclass(frozen=True)
class NewsIngestReport:
    """Outcome of `ingest_news`: `rows` upserted into `news`, and how many each provider
    (the `provider` column) served."""

    rows: int
    rows_by_provider: FrozenDict[str, int] = field(default_factory=lambda: FrozenDict({}))


class FallbackNewsProvider:
    """`primary` first; on `DataError`, `secondary` for the same request. Both failing raises
    `DataError`. An empty answer is not a failure (a quiet day has no news)."""

    name = "fallback"

    def __init__(self, primary: NewsProvider, secondary: NewsProvider) -> None:
        """A chain of two providers."""
        self.primary = primary
        self.secondary = secondary

    def fetch_news(
        self, symbols: Sequence[str] | None, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Articles from the primary, or from the secondary if the primary failed."""
        try:
            return self.primary.fetch_news(symbols, start, end)
        except DataError as exc:
            _log.warning("news.primary_failed", vendor=self.primary.name, error=str(exc))
            try:
                out = self.secondary.fetch_news(symbols, start, end)
            except DataError as exc2:
                raise DataError(
                    f"both news providers failed ({self.primary.name}: {exc}; "
                    f"{self.secondary.name}: {exc2})"
                ) from exc2
            _log.info("news.fallback_used", vendor=self.secondary.name, rows=len(out))
            return out


def build_news_provider(cfg: AtlasConfig) -> NewsProvider:
    """The news provider for `cfg`.

    `data.providers.news: alpaca` with both Alpaca keys set gives Alpaca backed by Yahoo;
    without the keys, Yahoo alone (recent news only) and a `news.alpaca_unavailable` warning.
    `news: yahoo` gives Yahoo alone.
    """
    yahoo = YahooNewsProvider(cfg.data.news)
    if cfg.data.providers.news == "yahoo":
        return yahoo
    alpaca = AlpacaNewsProvider.from_config(cfg)
    if alpaca is None:
        _log.warning(
            "news.alpaca_unavailable",
            reason="ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set; Yahoo: recent news only",
        )
        return yahoo
    return FallbackNewsProvider(alpaca, yahoo)


def ingest_news(
    lake: Lake,
    provider: NewsProvider,
    symbols: Sequence[str] | None,
    start: datetime,
    end: datetime,
) -> NewsIngestReport:
    """Fetch the articles tagged with `symbols` (`None`: market-wide) published in
    `[start, end)` (aware datetimes) and upsert them into `news`.

    The request is checked first (`ValueError` for a bare string, an invalid symbol or a bad
    window); the provider carries its own settings (`build_news_provider`). A provider
    `DataError` propagates and nothing is written. A repeat of an article already stored with
    the same headline, summary and tickers writes nothing. A change is a new row, known at the
    vendor's `updated_at` when that is after the stored copy, otherwise at `get_clock().now()`
    (which must be after the stored copy). The original row stays.
    """
    wanted = check_news_window(symbols, start, end)
    articles = provider.fetch_news(wanted, start, end)
    if articles.empty:
        _log.info("news.ingest", rows=0, provider=provider.name)
        return NewsIngestReport(rows=0)
    fresh = _revisions(lake, articles)
    if fresh.empty:
        _log.info("news.ingest", rows=0, provider=provider.name)
        return NewsIngestReport(rows=0)
    lake.write_table(NEWS.name, fresh, mode="upsert")
    counts = {str(k): int(v) for k, v in fresh["provider"].value_counts().sort_index().items()}
    _log.info("news.ingest", rows=len(fresh), provider=provider.name, by_provider=counts)
    return NewsIngestReport(rows=len(fresh), rows_by_provider=FrozenDict(counts))


def _revisions(lake: Lake, incoming: pd.DataFrame) -> pd.DataFrame:
    """The rows of `incoming` that are not already stored: first copies unchanged, and edits
    with `available_at` set to when the edit became known."""
    if not lake.has_table(NEWS.name):
        return incoming
    ids = list(dict.fromkeys(str(i) for i in incoming["article_id"]))
    stored = lake.read_table(NEWS.name, filters=[("article_id", "in", ids)])
    latest = _latest_stored(stored)
    out = incoming.copy()
    rows = out.to_dict(orient="records")
    stamps = [_ts(row["available_at"]) for row in rows]
    keep: list[int] = []
    for position, row in enumerate(rows):
        article_id = str(row["article_id"])
        key = (article_id, _ts(row["published_at"]))
        previous = latest.get(key)
        if previous is None:
            keep.append(position)
            continue
        if previous[0] == str(row["content_hash"]) and previous[1] == tuple(row["symbols"]):
            continue
        stamps[position] = _edit_known_at(row["updated_at"], previous[2], article_id)
        keep.append(position)
    if not keep:
        return incoming.iloc[0:0]
    out = out.iloc[keep].copy()
    out["available_at"] = pd.to_datetime([stamps[i] for i in keep], utc=True).as_unit("us")
    return out


_Stored = tuple[str, tuple[str, ...], pd.Timestamp]
"""`(content_hash, symbols, available_at)` of one stored news version."""


def _latest_stored(stored: pd.DataFrame) -> dict[tuple[str, pd.Timestamp], _Stored]:
    """`(article_id, published_at)` to `(content_hash, symbols, available_at)` of the latest
    stored version."""
    latest: dict[tuple[str, pd.Timestamp], _Stored] = {}
    if stored.empty:
        return latest
    ordered = stored.sort_values("available_at", kind="stable")
    for row in ordered.to_dict(orient="records"):
        latest[(str(row["article_id"]), _ts(row["published_at"]))] = (
            str(row["content_hash"]),
            tuple(row["symbols"]),
            _ts(row["available_at"]),
        )
    return latest


def _ts(value: Any) -> pd.Timestamp:
    return pd.Timestamp(value)


def _edit_known_at(updated_at: Any, stored_at: pd.Timestamp, article_id: str) -> pd.Timestamp:
    """When an edit of `article_id` is known: `updated_at` if it is after the stored copy,
    otherwise the clock. A clock that is not after the stored copy raises `DataError`."""
    if updated_at is not None and not pd.isna(updated_at):
        vendor = _ts(updated_at)
        if vendor.tzinfo is None:
            raise DataError(
                f"news: {article_id} updated_at must be timezone-aware, got {updated_at!r}"
            )
        vendor = vendor.tz_convert("UTC")
        if vendor > stored_at:
            return vendor
    now = pd.Timestamp(get_clock().now()).tz_convert("UTC")
    if now > stored_at:
        return now
    raise DataError(
        f"news: the edit of {article_id} is not after the stored copy "
        f"({stored_at.isoformat()}); the clock reads {now.isoformat()}"
    )
