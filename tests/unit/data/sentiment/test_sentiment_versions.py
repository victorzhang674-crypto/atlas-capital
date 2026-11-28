"""An edited article keeps its original sentiment score until the edit is known."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from sentiment_helpers import article, articles, sample_lexicon, utc

from atlas.core.pit import AsOf
from atlas.core.storage import Lake
from atlas.data.news import NEWS
from atlas.data.news.dedup import content_hash
from atlas.data.sentiment import NEWS_SENTIMENT, ingest_news_sentiment
from atlas.data.sentiment.aggregate import Sentiment
from atlas.data.sentiment.lexicon import pit_scores


def test_an_edit_keeps_the_original_score_until_it_is_known(tmp_lake: Lake, tmp_path: Path) -> None:
    lexicon = sample_lexicon(tmp_path)
    published = utc(2024, 3, 1, 15)
    edited = utc(2024, 3, 1, 18)
    original = articles([article(headline="Apple posts strong gains", published_at=published)])
    revision = original.copy()
    revision["headline"] = "Apple posts weak decline"
    revision["content_hash"] = content_hash("Apple posts weak decline", "")
    revision["available_at"] = pd.to_datetime([edited], utc=True).as_unit("us")
    tmp_lake.write_table(
        NEWS.name, pd.concat([original, revision], ignore_index=True), mode="upsert"
    )
    written = ingest_news_sentiment(tmp_lake, lexicon, utc(2024, 3, 1), utc(2024, 3, 2))
    stored = tmp_lake.read_table(NEWS_SENTIMENT.name)
    assert written == 2
    assert len(stored) == 2
    assert set(stored["score"]) == {1.0, -1.0}
    early = AsOf(day=date(2024, 3, 1), cutoff=utc(2024, 3, 1, 16))
    late = AsOf(day=date(2024, 3, 1), cutoff=utc(2024, 3, 1, 20))
    assert float(pit_scores(stored, early)["score"].iloc[0]) == 1.0
    assert float(pit_scores(stored, late)["score"].iloc[0]) == -1.0
    view = Sentiment(tmp_lake, lexicon=lexicon)
    assert view.at("AAPL", early).score == pytest.approx(1.0)
    assert view.at("AAPL", late).score == pytest.approx(-1.0)
    assert ingest_news_sentiment(tmp_lake, lexicon, utc(2024, 3, 1), utc(2024, 3, 2)) == 0
    assert len(tmp_lake.read_table(NEWS_SENTIMENT.name)) == 2
