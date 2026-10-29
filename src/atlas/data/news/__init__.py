"""Timestamped, de-duplicated, ticker-tagged news with stable evidence ids (Stage 11).

`base` holds the `NewsProvider` contract, the article frame and the `news` table (registered on
import); `dedup` the text normalisation, content hash and point-in-time duplicate marking;
`relevance` the per-ticker relevance score and roundup filter; `alpaca_news` and `yahoo_news`
are the vendor adapters; `ingest` fills the table (`ingest_news`, `build_news_provider`); `pit`
answers what was known at `as_of` as citable `Evidence` (`News(lake).pit(symbol, as_of)`, ids
`news:<article_id>`).
"""

from atlas.data.news.base import (
    ARTICLE_COLUMNS,
    NEWS,
    RAW_COLUMNS,
    NewsProvider,
    check_news_window,
    empty_news,
    normalize_news,
)
from atlas.data.news.dedup import DUPLICATE_OF, content_hash, mark_duplicates, normalize_text
from atlas.data.news.ingest import (
    FallbackNewsProvider,
    NewsIngestReport,
    build_news_provider,
    ingest_news,
)
from atlas.data.news.pit import (
    DETAIL_COLUMNS,
    EVIDENCE_PREFIX,
    News,
    market_news_pit,
    news_pit,
    news_pit_detail,
    to_evidence,
)
from atlas.data.news.relevance import headline_mentions, score_relevance

__all__ = [
    "ARTICLE_COLUMNS",
    "DETAIL_COLUMNS",
    "DUPLICATE_OF",
    "EVIDENCE_PREFIX",
    "NEWS",
    "RAW_COLUMNS",
    "FallbackNewsProvider",
    "News",
    "NewsIngestReport",
    "NewsProvider",
    "build_news_provider",
    "check_news_window",
    "content_hash",
    "empty_news",
    "headline_mentions",
    "ingest_news",
    "mark_duplicates",
    "market_news_pit",
    "news_pit",
    "news_pit_detail",
    "normalize_news",
    "normalize_text",
    "score_relevance",
    "to_evidence",
]
