"""Stage 12 Task 8: the decayed per-symbol sentiment and its daily series (deterministic)."""

import math
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sentiment_helpers import article, articles, sample_lexicon, utc

from atlas.core.errors import DataError, LookAheadError
from atlas.core.pit import AsOf
from atlas.core.storage import Lake
from atlas.data.news import NEWS
from atlas.data.sentiment import Lexicon
from atlas.data.sentiment.aggregate import Sentiment, SentimentAggregate, decayed_sentiment

AS_OF = AsOf.for_session(date(2024, 3, 8))  # Friday 08:00 EST = 13:00 UTC
CUT = AS_OF.cutoff


def detail(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    """A scored detail frame from (age in days before the cutoff, relevance, score) rows."""
    return pd.DataFrame(
        {
            "article_id": [f"a-{i}" for i in range(len(rows))],
            "available_at": pd.Series(
                [CUT - timedelta(days=age) for age, _, _ in rows], dtype="datetime64[us, UTC]"
            ),
            "relevance": [r for _, r, _ in rows],
            "score": [s for _, _, s in rows],
        }
    )


@pytest.fixture
def lexicon(tmp_path: Path) -> Lexicon:
    return sample_lexicon(tmp_path)


def test_decay_known_answer() -> None:
    got = decayed_sentiment(detail([(3.0, 1.0, 1.0), (6.0, 1.0, -1.0)]), AS_OF, half_life_days=3)
    assert got.score == pytest.approx((0.5 - 0.25) / 0.75)
    assert got.articles == 2
    assert got.weight == pytest.approx(0.75)


def test_relevance_weights() -> None:
    got = decayed_sentiment(detail([(1.0, 1.0, 1.0), (1.0, 0.5, -1.0)]), AS_OF, half_life_days=3)
    assert got.score == pytest.approx(1 / 3)


def test_no_articles_is_nan() -> None:
    got = decayed_sentiment(detail([]), AS_OF, half_life_days=3)
    assert math.isnan(got.score) and got.articles == 0 and got.weight == 0.0
    assert isinstance(got, SentimentAggregate)


def test_aggregate_independent_of_row_order() -> None:
    rng = np.random.default_rng(12)
    rows = [
        (float(a), float(r), float(s))
        for a, r, s in zip(
            rng.uniform(0.01, 14, 60), rng.choice([0.5, 1.0], 60), rng.uniform(-1, 1, 60),
            strict=True,
        )
    ]  # fmt: skip
    frame = detail(rows)
    first = decayed_sentiment(frame, AS_OF, half_life_days=3)
    for seed in range(5):
        shuffled = frame.sample(frac=1.0, random_state=seed)
        assert decayed_sentiment(shuffled, AS_OF, half_life_days=3) == first


def test_decay_rejects_rows_after_cutoff() -> None:
    with pytest.raises(LookAheadError):
        decayed_sentiment(detail([(0.0, 1.0, 1.0)]), AS_OF, half_life_days=3)


def _seed(lake: Lake, rows: list[dict[str, object]]) -> None:
    lake.write_table(NEWS.name, articles(rows), mode="upsert")


def test_sentiment_at_pit(tmp_lake: Lake, lexicon: Lexicon) -> None:
    _seed(
        tmp_lake,
        [
            article(article_id="alpaca-1", published_at=utc(2024, 3, 7, 15),
                    headline="Strong gains"),
            article(article_id="alpaca-2", published_at=utc(2024, 3, 8, 13, 30),
                    headline="Weak demand and a decline"),
        ],
    )  # fmt: skip
    got = Sentiment(tmp_lake, lexicon=lexicon).at("AAPL", AS_OF)
    assert (got.score, got.articles) == (1.0, 1)  # the 08:30 article is not known at 08:00
    later = Sentiment(tmp_lake, lexicon=lexicon).at("AAPL", date(2024, 3, 11))
    assert later.articles == 2 and later.score < 1.0


def test_duplicate_story_counted_once(tmp_lake: Lake, lexicon: Lexicon) -> None:
    _seed(
        tmp_lake,
        [
            article(article_id="alpaca-1", published_at=utc(2024, 3, 7, 15),
                    headline="Apple posts strong gains"),
            article(article_id="yahoo-x", provider="yahoo", source="Reuters",
                    published_at=utc(2024, 3, 7, 16), headline="Apple posts strong gains"),
        ],
    )  # fmt: skip
    assert Sentiment(tmp_lake, lexicon=lexicon).at("AAPL", AS_OF).articles == 1


def test_other_symbols_and_old_news_ignored(tmp_lake: Lake, lexicon: Lexicon) -> None:
    _seed(
        tmp_lake,
        [
            article(article_id="alpaca-1", symbols=["MSFT"], headline="Weak"),
            article(article_id="alpaca-2", published_at=utc(2024, 2, 1), headline="Weak"),
            article(article_id="alpaca-3", published_at=utc(2024, 3, 7), headline="Strong"),
        ],
    )  # fmt: skip
    assert Sentiment(tmp_lake, lexicon=lexicon).at("AAPL", AS_OF).articles == 1


def test_daily_series(tmp_lake: Lake, lexicon: Lexicon) -> None:
    _seed(
        tmp_lake,
        [
            article(article_id="alpaca-1", published_at=utc(2024, 3, 4, 15), headline="Strong"),
            article(article_id="alpaca-2", published_at=utc(2024, 3, 6, 15), headline="Weak"),
        ],
    )  # fmt: skip
    reader = Sentiment(tmp_lake, lexicon=lexicon)
    out = reader.daily("AAPL", AS_OF, days=5)
    assert list(out.columns) == ["date", "score", "articles"]
    assert out["date"].tolist() == [date(2024, 3, d) for d in (4, 5, 6, 7, 8)]
    assert math.isnan(out["score"].iloc[0])  # 03-04 08:00: nothing known yet
    assert out["score"].iloc[1] == 1.0 and out["articles"].iloc[1] == 1
    assert out["articles"].iloc[3] == 2
    expected = reader.at("AAPL", date(2024, 3, 7))
    assert out["score"].iloc[3] == expected.score
    pd.testing.assert_frame_equal(reader.daily("AAPL", AS_OF, days=5), out)


def test_daily_default_length_and_weekend(tmp_lake: Lake, lexicon: Lexicon) -> None:
    _seed(tmp_lake, [article()])
    out = Sentiment(tmp_lake, lexicon=lexicon).daily("AAPL", date(2024, 3, 9))  # a Saturday
    assert len(out) == 90 and out["date"].iloc[-1] == date(2024, 3, 8)


def test_sentiment_rejects_datetime_and_bad_days(tmp_lake: Lake, lexicon: Lexicon) -> None:
    reader = Sentiment(tmp_lake, lexicon=lexicon)
    with pytest.raises(TypeError):
        reader.at("AAPL", datetime(2024, 3, 8, 12))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="days"):
        reader.daily("AAPL", AS_OF, days=0)


def test_sentiment_without_news_raises(tmp_lake: Lake, lexicon: Lexicon) -> None:
    with pytest.raises(DataError, match="ingest_news"):
        Sentiment(tmp_lake, lexicon=lexicon).at("AAPL", AS_OF)


def test_sentiment_reader_guard_raises_if_filter_bypassed(
    tmp_lake: Lake, lexicon: Lexicon, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_lake, [article(published_at=utc(2024, 3, 8, 13, 30))])
    monkeypatch.setattr("atlas.data.news.pit.pit_filter", lambda df, as_of, *a: df.copy())
    monkeypatch.setattr(
        "atlas.data.news.pit.News.articles",
        lambda self, start, end: self._lake.read_table(NEWS.name),
    )
    with pytest.raises(LookAheadError):
        Sentiment(tmp_lake, lexicon=lexicon).at("AAPL", AS_OF)
