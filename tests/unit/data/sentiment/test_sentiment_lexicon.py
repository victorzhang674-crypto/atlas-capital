"""Stage 12 Task 7: the Loughran-McDonald lexicon, text scoring and `news_sentiment`."""

from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx
from sentiment_helpers import SAMPLE_MASTER, article, articles, sample_lexicon, utc

from atlas.core.config import AtlasConfig, SentimentConfig
from atlas.core.errors import DataError
from atlas.core.storage import DEFAULT_REGISTRY, YEAR, Lake
from atlas.data.news import NEWS
from atlas.data.sentiment import (
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


@pytest.fixture
def lexicon(tmp_path: Path) -> Lexicon:
    return sample_lexicon(tmp_path)


def test_build_lexicon_from_master() -> None:
    out = build_lm_lexicon(pd.read_csv(SAMPLE_MASTER))
    assert list(out.columns) == ["word", "category"]
    pairs = set(zip(out["word"], out["category"], strict=True))
    assert ("STRONG", "positive") in pairs and ("WEAK", "negative") in pairs
    assert ("MAY", "uncertainty") in pairs
    assert {("VOLATILITY", "negative"), ("VOLATILITY", "uncertainty")} <= pairs
    assert "BENEFIT" not in set(out["word"])  # removed from the positive list in 2020
    assert "LAWSUIT" not in set(out["word"])  # litigious only
    assert out.equals(out.sort_values(["category", "word"]).reset_index(drop=True))


def test_build_lexicon_rejects_bad_master() -> None:
    with pytest.raises(DataError, match="Positive"):
        build_lm_lexicon(pd.DataFrame({"Word": ["A"], "Negative": [0]}))


def test_load_lexicon(lexicon: Lexicon) -> None:
    assert {"STRONG", "GAINS", "PROFITABILITY"} <= lexicon.positive
    assert {"WEAK", "DECLINE"} <= lexicon.negative
    assert {"MAY", "UNCERTAIN"} <= lexicon.uncertainty
    assert len(lexicon.version) == 12


def test_load_lexicon_missing_file_names_download(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="download_lm_lexicon"):
        load_lexicon(tmp_path / "missing.csv")


def test_load_lexicon_rejects_unknown_category(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("word,category\nGOOD,happy\n", encoding="utf-8")
    with pytest.raises(DataError, match="happy"):
        load_lexicon(path)


def test_lexicon_version_stable(tmp_path: Path, lexicon: Lexicon) -> None:
    shuffled = tmp_path / "shuffled.csv"
    frame = pd.read_csv(tmp_path / "lm_lexicon.csv").iloc[::-1]
    frame.to_csv(shuffled, index=False)
    assert load_lexicon(shuffled).version == lexicon.version


def test_tokenize() -> None:
    assert tokenize("Apple's Q3: beats!") == ["APPLE", "S", "Q", "BEATS"]
    assert tokenize("") == []
    assert tokenize("Café 3.5% up-grade") == ["CAF", "UP", "GRADE"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Profit fell on weak demand, a decline and a lawsuit", TextSentiment(-1.0, 0, 2, 0, 10)),
        ("Strong gains and record profitability", TextSentiment(1.0, 3, 0, 0, 5)),
        ("Results may be uncertain", TextSentiment(0.0, 0, 0, 2, 4)),
        ("The company did not achieve gains", TextSentiment(0.0, 0, 0, 0, 6)),
        ("Strong gains, good results despite one downgrade", TextSentiment(0.5, 3, 1, 0, 7)),
        ("Gains came, though not strong", TextSentiment(1.0, 1, 0, 0, 5)),
        ("Volatility", TextSentiment(-1.0, 0, 1, 1, 1)),
        ("A clear benefit", TextSentiment(0.0, 0, 0, 0, 3)),
        ("", TextSentiment(0.0, 0, 0, 0, 0)),
    ],
)
def test_score_known_sentences(lexicon: Lexicon, text: str, expected: TextSentiment) -> None:
    assert score_text(text, lexicon) == expected


def test_negation_window(lexicon: Lexicon) -> None:
    text = "No doubt results were strong"  # NO is four words before STRONG
    assert score_text(text, lexicon, negation_window=3).pos == 1
    assert score_text(text, lexicon, negation_window=4).pos == 0
    assert score_text("not strong", lexicon, negation_window=0).pos == 1


def test_score_articles_columns_and_version(lexicon: Lexicon) -> None:
    frame = articles(
        [
            article(),
            article(article_id="alpaca-2", headline="Weak quarter", summary="A decline in sales"),
        ]
    )
    out = score_articles(frame, lexicon)
    assert tuple(out.columns) == SCORE_COLUMNS
    assert out["article_id"].tolist() == ["alpaca-1", "alpaca-2"]
    assert out["score"].tolist() == [1.0, -1.0]
    assert out["neg"].tolist() == [0, 2]  # headline and summary both scored
    assert set(out["lexicon_version"]) == {lexicon.version}
    assert (out["available_at"] == frame["available_at"]).all()


def test_score_articles_empty(lexicon: Lexicon) -> None:
    out = score_articles(articles([]), lexicon)
    assert out.empty and tuple(out.columns) == SCORE_COLUMNS


def test_news_sentiment_table_declaration() -> None:
    assert NEWS_SENTIMENT.name == "news_sentiment" and "news_sentiment" in DEFAULT_REGISTRY.names()
    assert NEWS_SENTIMENT.column_names == SCORE_COLUMNS
    assert NEWS_SENTIMENT.primary_key == ("article_id", "published_at")
    assert NEWS_SENTIMENT.partition_by == (YEAR,)


def test_ingest_news_sentiment_roundtrip(tmp_lake: Lake, lexicon: Lexicon) -> None:
    frame = articles(
        [
            article(),
            article(article_id="alpaca-2", headline="Weak quarter", published_at=utc(2024, 3, 2)),
            article(article_id="alpaca-3", headline="Strong", published_at=utc(2024, 5, 1)),
        ]
    )
    tmp_lake.write_table(NEWS.name, frame, mode="upsert")
    rows = ingest_news_sentiment(tmp_lake, lexicon, utc(2024, 3, 1), utc(2024, 4, 1))
    assert rows == 2
    stored = tmp_lake.read_table(NEWS_SENTIMENT.name)
    expected = score_articles(frame.iloc[:2].reset_index(drop=True), lexicon)
    pd.testing.assert_frame_equal(stored, expected, check_dtype=False)
    assert ingest_news_sentiment(tmp_lake, lexicon, utc(2024, 3, 1), utc(2024, 4, 1)) == 2
    assert len(tmp_lake.read_table(NEWS_SENTIMENT.name)) == 2  # idempotent


def test_download_lm_lexicon(tmp_path: Path) -> None:
    url = "https://example.test/lm_master.csv"
    settings = SentimentConfig(lexicon_file=tmp_path / "ref" / "lm.csv", lexicon_url=url)
    with respx.mock() as mock:
        mock.get(url).respond(200, content=SAMPLE_MASTER.read_bytes())
        with httpx.Client() as http:
            path = download_lm_lexicon(settings, http=http)
    assert path == tmp_path / "ref" / "lm.csv"
    assert "STRONG" in load_lexicon(path).positive


def test_download_lm_lexicon_http_error(tmp_path: Path) -> None:
    url = "https://example.test/lm_master.csv"
    settings = SentimentConfig(lexicon_file=tmp_path / "lm.csv", lexicon_url=url)
    with respx.mock() as mock:
        mock.get(url).respond(403)
        with httpx.Client() as http, pytest.raises(DataError, match="403"):
            download_lm_lexicon(settings, http=http)
    assert not (tmp_path / "lm.csv").exists()


def test_default_lexicon_path_is_gitignored() -> None:
    assert AtlasConfig().data.sentiment.lexicon_file.parts[0] == "data"
