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

import pandas as pd

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
    `DataError` propagates and nothing is written.
    """
    wanted = check_news_window(symbols, start, end)
    articles = provider.fetch_news(wanted, start, end)
    if articles.empty:
        _log.info("news.ingest", rows=0, provider=provider.name)
        return NewsIngestReport(rows=0)
    lake.write_table(NEWS.name, articles, mode="upsert")
    counts = {str(k): int(v) for k, v in articles["provider"].value_counts().sort_index().items()}
    _log.info("news.ingest", rows=len(articles), provider=provider.name, by_provider=counts)
    return NewsIngestReport(rows=len(articles), rows_by_provider=FrozenDict(counts))
