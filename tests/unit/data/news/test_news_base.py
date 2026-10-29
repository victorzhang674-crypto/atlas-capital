"""Stage 11 Task 2: the article contract, the `news` table and the content hash."""

from datetime import datetime

import pandas as pd
import pytest
from news_helpers import FakeNewsProvider, article, articles_frame, raw_frame, utc

from atlas.core.config import NewsConfig
from atlas.core.errors import DataError
from atlas.core.storage import DEFAULT_REGISTRY, YEAR, ColumnType
from atlas.data.news import (
    ARTICLE_COLUMNS,
    NEWS,
    NewsProvider,
    check_news_window,
    content_hash,
    empty_news,
    normalize_news,
    normalize_text,
)


def test_news_table_declaration() -> None:
    assert NEWS.name == "news"
    assert NEWS.column_names == ARTICLE_COLUMNS
    assert ARTICLE_COLUMNS == (
        "article_id",
        "published_at",
        "available_at",
        "updated_at",
        "provider",
        "source",
        "headline",
        "summary",
        "url",
        "symbols",
        "content_hash",
    )
    assert NEWS.primary_key == ("article_id", "published_at")
    assert NEWS.partition_by == (YEAR,)
    assert NEWS.year_column == "published_at"
    assert NEWS.column("symbols").dtype is ColumnType.STRING_LIST
    assert NEWS.column("updated_at").nullable and NEWS.column("url").nullable
    assert "content" not in NEWS.column_names


def test_import_registers_news_table() -> None:
    assert DEFAULT_REGISTRY.get("news") == NEWS


def test_normalize_news_contract() -> None:
    out = articles_frame(
        [
            article(article_id="alpaca-2", published_at=utc(2020, 3, 2, 16)),
            article(article_id="alpaca-1", published_at=utc(2020, 3, 2, 15), url="https://x"),
        ]
    )
    assert tuple(out.columns) == ARTICLE_COLUMNS
    assert list(out["article_id"]) == ["alpaca-1", "alpaca-2"]
    assert (out["available_at"] == out["published_at"]).all()
    assert str(out["published_at"].dt.tz) == "UTC"
    assert list(out["symbols"]) == [("AAPL",), ("AAPL",)]
    assert out["updated_at"].isna().all()
    assert list(out["url"]) == ["https://x", None]
    NEWS.check_partitioning(NEWS.partition_by)
    from atlas.core.storage import validate_frame

    validate_frame(NEWS, out)


def test_normalize_converts_other_zones_to_utc() -> None:
    eastern = pd.Timestamp("2020-03-02 10:00", tz="America/New_York").to_pydatetime()
    out = articles_frame([article(published_at=eastern)])
    assert out.loc[0, "published_at"] == pd.Timestamp("2020-03-02 15:00", tz="UTC")


def test_normalize_drops_empty_headline_and_cuts_summary() -> None:
    out = normalize_news(
        raw_frame(
            [
                article(article_id="alpaca-1", headline="   "),
                article(article_id="alpaca-2", headline="  Apple up  ", summary="x" * 1500),
                article(article_id="alpaca-3", summary=None),
            ]
        ),
        NewsConfig(),
    )
    assert list(out["article_id"]) == ["alpaca-2", "alpaca-3"]
    assert out.loc[0, "headline"] == "Apple up"
    assert len(out.loc[0, "summary"]) == 1000
    assert out.loc[1, "summary"] == ""


def test_normalize_drops_invalid_symbols() -> None:
    out = articles_frame(
        [article(symbols=["aapl", "^GSPC", "", "1ABC", "TOOLONGNAME1", "AAPL", "brk-b"])]
    )
    assert out.loc[0, "symbols"] == ("AAPL", "BRK.B")


def test_normalize_one_row_per_article_id() -> None:
    out = articles_frame([article(headline="first"), article(headline="second")])
    assert list(out["headline"]) == ["first"]


def test_normalize_rejects_naive_published_at() -> None:
    with pytest.raises(DataError, match="published_at"):
        articles_frame([article(published_at=datetime(2020, 3, 2, 15))])


def test_normalize_rejects_missing_columns() -> None:
    with pytest.raises(DataError, match="missing"):
        normalize_news(raw_frame([article()]).drop(columns=["symbols"]))


def test_empty_news_has_contract_columns() -> None:
    out = empty_news()
    assert tuple(out.columns) == ARTICLE_COLUMNS and out.empty
    assert normalize_news(raw_frame([])).empty


def test_check_news_window() -> None:
    start, end = utc(2020, 3, 2), utc(2020, 3, 3)
    assert check_news_window(["aapl", "AAPL", "brk-b"], start, end) == ("AAPL", "BRK.B")
    assert check_news_window(None, start, end) is None
    with pytest.raises(ValueError, match="aware"):
        check_news_window(None, datetime(2020, 3, 2), end)
    with pytest.raises(ValueError, match="before"):
        check_news_window(None, end, start)
    with pytest.raises(ValueError, match="before"):
        check_news_window(None, start, start)
    with pytest.raises(ValueError, match="one string"):
        check_news_window("AAPL", start, end)
    with pytest.raises(ValueError, match="invalid symbol"):
        check_news_window(["^GSPC"], start, end)


def test_normalize_text_examples() -> None:
    assert normalize_text("Apple\u2019s Q3:  Beats!") == "apple s q3 beats"
    assert normalize_text("  \uff21\uff30\uff30\uff2c\tUp  ") == "appl up"
    assert normalize_text("") == ""


def test_content_hash_ignores_case_and_punctuation() -> None:
    a = content_hash("Apple Beats Estimates!", "Revenue up 5%.")
    b = content_hash("apple beats estimates", "revenue up 5 %")
    assert a == b and len(a) == 64
    assert content_hash("Apple Beats Estimates", "") != a
    assert content_hash("a b", "") != content_hash("a", "b")


def test_content_hash_column_matches_function() -> None:
    out = articles_frame([article(headline="Apple up", summary="Shares rose")])
    assert out.loc[0, "content_hash"] == content_hash("Apple up", "Shares rose")


def test_fake_provider_is_a_news_provider() -> None:
    assert isinstance(FakeNewsProvider("fake", empty_news()), NewsProvider)
