"""Numeric sentiment sources (Stage 12).

`lexicon` holds the Loughran-McDonald word lists (`download_lm_lexicon`, `load_lexicon`), text
scoring (`score_text`, `score_articles`) and the `news_sentiment` table (registered on import);
`aggregate` the decayed per-symbol score and its daily series (`Sentiment(lake).at(...)`);
`social` the optional StockTwits / Reddit providers (off unless enabled) and their `Evidence`.
"""

from atlas.data.sentiment.aggregate import Sentiment, SentimentAggregate, decayed_sentiment
from atlas.data.sentiment.lexicon import (
    CATEGORIES,
    NEGATIONS,
    NEWS_SENTIMENT,
    SCORE_COLUMNS,
    Lexicon,
    TextSentiment,
    build_lm_lexicon,
    download_lm_lexicon,
    ingest_news_sentiment,
    load_lexicon,
    score_articles,
    score_text,
    tokenize,
)
from atlas.data.sentiment.social import (
    POST_COLUMNS,
    RedditProvider,
    SocialProvider,
    StockTwitsProvider,
    build_social_providers,
    social_evidence,
)

__all__ = [
    "CATEGORIES",
    "NEGATIONS",
    "NEWS_SENTIMENT",
    "POST_COLUMNS",
    "SCORE_COLUMNS",
    "Lexicon",
    "RedditProvider",
    "Sentiment",
    "SentimentAggregate",
    "SocialProvider",
    "StockTwitsProvider",
    "TextSentiment",
    "build_lm_lexicon",
    "build_social_providers",
    "decayed_sentiment",
    "download_lm_lexicon",
    "ingest_news_sentiment",
    "load_lexicon",
    "score_articles",
    "score_text",
    "social_evidence",
    "tokenize",
]
