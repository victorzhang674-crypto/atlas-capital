"""Stage 11 Task 9: guard branches of the news package (malformed input, defaults)."""

from typing import Any

import httpx
import pandas as pd
import pytest
import respx
from news_helpers import article, articles_frame, raw_frame, utc
from pydantic import SecretStr

from atlas.core.errors import DataError
from atlas.data.news import normalize_news
from atlas.data.news import yahoo_news as yahoo_module
from atlas.data.news.alpaca_news import AlpacaNewsProvider


def test_normalize_rejects_non_frame() -> None:
    with pytest.raises(DataError, match="DataFrame"):
        normalize_news([article()])  # type: ignore[arg-type]


def test_normalize_all_headlines_empty() -> None:
    out = normalize_news(raw_frame([article(headline=""), article(headline=None)]))
    assert out.empty


def test_normalize_missing_published_at() -> None:
    with pytest.raises(DataError, match="published_at is missing"):
        normalize_news(raw_frame([article(published_at=None)]))


def test_normalize_bare_string_and_missing_tags() -> None:
    out = articles_frame(
        [article(article_id="a-1", symbols="aapl"), article(article_id="a-2", symbols=None)]
    )
    assert list(out["symbols"]) == [("AAPL",), ()]


def test_normalize_is_idempotent() -> None:
    once = articles_frame([article(), article(article_id="a-2", updated_at=utc(2020, 3, 3))])
    pd.testing.assert_frame_equal(normalize_news(once), once)


def test_alpaca_bad_timestamp_is_data_error() -> None:
    body: dict[str, Any] = {
        "news": [{"id": 1, "headline": "x", "created_at": "yesterday", "symbols": ["AAPL"]}],
        "next_page_token": None,
    }
    with respx.mock() as router:
        router.get("https://data.alpaca.markets/v1beta1/news").mock(
            return_value=httpx.Response(200, json=body)
        )
        provider = AlpacaNewsProvider(SecretStr("k"), SecretStr("s"))
        with pytest.raises(DataError, match="bad timestamp"):
            provider.fetch_news(["AAPL"], utc(2020, 3, 2), utc(2020, 3, 3))
        provider.close()


def test_yahoo_default_fetch_uses_search(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, int, int]] = []

    class FakeSearch:
        def __init__(self, query: str, news_count: int, max_results: int) -> None:
            seen.append((query, news_count, max_results))
            self.news = [
                {
                    "uuid": "u-1",
                    "title": "Apple up",
                    "publisher": "Reuters",
                    "link": "https://x",
                    "providerPublishTime": 1583161200,  # 2020-03-02 15:00 UTC
                    "relatedTickers": ["AAPL"],
                }
            ]

    monkeypatch.setattr(yahoo_module.yf, "Search", FakeSearch)
    out = yahoo_module.YahooNewsProvider().fetch_news(["AAPL"], utc(2020, 3, 2), utc(2020, 3, 3))
    assert seen == [("AAPL", 20, 1)]
    assert list(out["article_id"]) == ["yahoo-u-1"]
