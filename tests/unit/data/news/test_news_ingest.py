"""Stage 11 Task 5: ingesting news into the lake, the fallback chain and the provider factory."""

from pathlib import Path

import pytest
from news_helpers import FakeNewsProvider, article, articles_frame, logged_events, utc
from pydantic import SecretStr

from atlas.core.config import AtlasConfig, DataConfig, DataProviders, Secrets
from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.news import NEWS
from atlas.data.news.alpaca_news import AlpacaNewsProvider
from atlas.data.news.ingest import (
    FallbackNewsProvider,
    NewsIngestReport,
    build_news_provider,
    ingest_news,
)
from atlas.data.news.yahoo_news import YahooNewsProvider

START, END = utc(2020, 3, 2), utc(2020, 3, 4)
ALPACA_ROWS = articles_frame(
    [
        article(article_id="alpaca-1", published_at=utc(2020, 3, 2, 13), symbols=["AAPL"]),
        article(
            article_id="alpaca-2",
            published_at=utc(2020, 3, 2, 15),
            symbols=["AAPL", "MSFT"],
            headline="Big Tech slips",
        ),
        article(article_id="alpaca-3", published_at=utc(2020, 3, 3, 14), symbols=["MSFT"]),
    ]
)
YAHOO_ROWS = articles_frame(
    [
        article(
            article_id="yahoo-a",
            provider="yahoo",
            source="Reuters",
            published_at=utc(2020, 3, 2, 16),
            symbols=["AAPL"],
            headline="Apple shares rise",
        )
    ]
)


def _keys() -> Secrets:
    return Secrets(alpaca_api_key_id=SecretStr("PKX"), alpaca_api_secret_key=SecretStr("sx"))


def test_ingest_writes_news_table(tmp_lake: Lake) -> None:
    report = ingest_news(tmp_lake, FakeNewsProvider("alpaca", ALPACA_ROWS), ["AAPL"], START, END)
    assert report == NewsIngestReport(rows=2, rows_by_provider={"alpaca": 2})
    stored = tmp_lake.read_table(NEWS.name)
    assert list(stored["article_id"]) == ["alpaca-1", "alpaca-2"]
    assert list(stored["symbols"]) == [("AAPL",), ("AAPL", "MSFT")]
    assert (stored["available_at"] == stored["published_at"]).all()


def test_reingest_is_idempotent(tmp_lake: Lake) -> None:
    provider = FakeNewsProvider("alpaca", ALPACA_ROWS)
    ingest_news(tmp_lake, provider, None, START, END)
    first = tmp_lake.read_table(NEWS.name)
    ingest_news(tmp_lake, provider, None, START, END)
    again = tmp_lake.read_table(NEWS.name)
    assert again.equals(first) and len(again) == 3


def test_overlapping_windows_merge(tmp_lake: Lake) -> None:
    provider = FakeNewsProvider("alpaca", ALPACA_ROWS)
    ingest_news(tmp_lake, provider, None, START, utc(2020, 3, 2, 16))
    ingest_news(tmp_lake, provider, None, utc(2020, 3, 2, 14), END)
    assert list(tmp_lake.read_table(NEWS.name)["article_id"]) == [
        "alpaca-1",
        "alpaca-2",
        "alpaca-3",
    ]


def test_empty_answer_writes_nothing(tmp_lake: Lake) -> None:
    report = ingest_news(tmp_lake, FakeNewsProvider("alpaca", ALPACA_ROWS), ["TSLA"], START, END)
    assert report == NewsIngestReport(rows=0, rows_by_provider={})
    assert not tmp_lake.has_table(NEWS.name)


def test_provider_error_writes_nothing(tmp_lake: Lake) -> None:
    broken = FakeNewsProvider("alpaca", ALPACA_ROWS, error=DataError("vendor down"))
    with pytest.raises(DataError, match="vendor down"):
        ingest_news(tmp_lake, broken, ["AAPL"], START, END)
    assert not tmp_lake.has_table(NEWS.name)


def test_ingest_checks_the_request(tmp_lake: Lake) -> None:
    with pytest.raises(ValueError, match="one string"):
        ingest_news(tmp_lake, FakeNewsProvider("alpaca", ALPACA_ROWS), "AAPL", START, END)


def test_fallback_uses_secondary_on_error(tmp_path: Path) -> None:
    primary = FakeNewsProvider("alpaca", ALPACA_ROWS, error=DataError("down"))
    secondary = FakeNewsProvider("yahoo", YAHOO_ROWS)
    chain = FallbackNewsProvider(primary, secondary)
    out, lines = logged_events(
        tmp_path / "atlas.log", lambda: chain.fetch_news(["AAPL"], START, END)
    )
    assert list(out["article_id"]) == ["yahoo-a"]
    assert primary.calls == secondary.calls == [(("AAPL",), START, END)]
    assert [ln["vendor"] for ln in lines if ln["event"] == "news.primary_failed"] == ["alpaca"]
    assert chain.name == "fallback"


def test_fallback_primary_answer_used_when_it_works() -> None:
    secondary = FakeNewsProvider("yahoo", YAHOO_ROWS)
    chain = FallbackNewsProvider(FakeNewsProvider("alpaca", ALPACA_ROWS), secondary)
    out = chain.fetch_news(["AAPL"], START, END)
    assert set(out["provider"]) == {"alpaca"} and secondary.calls == []


def test_fallback_both_fail() -> None:
    chain = FallbackNewsProvider(
        FakeNewsProvider("alpaca", ALPACA_ROWS, error=DataError("a down")),
        FakeNewsProvider("yahoo", YAHOO_ROWS, error=DataError("y down")),
    )
    with pytest.raises(DataError, match=r"both news providers failed.*a down.*y down"):
        chain.fetch_news(["AAPL"], START, END)


def test_ingest_counts_rows_by_provider(tmp_lake: Lake) -> None:
    chain = FallbackNewsProvider(
        FakeNewsProvider("alpaca", ALPACA_ROWS, error=DataError("down")),
        FakeNewsProvider("yahoo", YAHOO_ROWS),
    )
    report = ingest_news(tmp_lake, chain, ["AAPL"], START, END)
    assert report.rows_by_provider == {"yahoo": 1}


def test_build_provider_alpaca_with_keys() -> None:
    provider = build_news_provider(AtlasConfig(secrets=_keys()))
    assert isinstance(provider, FallbackNewsProvider)
    assert isinstance(provider.primary, AlpacaNewsProvider)
    assert isinstance(provider.secondary, YahooNewsProvider)


def test_build_provider_without_keys_is_yahoo(tmp_path: Path) -> None:
    provider, lines = logged_events(
        tmp_path / "atlas.log", lambda: build_news_provider(AtlasConfig())
    )
    assert isinstance(provider, YahooNewsProvider)
    warned = [ln for ln in lines if ln["event"] == "news.alpaca_unavailable"]
    assert [ln["level"] for ln in warned] == ["warning"]


def test_build_provider_yahoo() -> None:
    cfg = AtlasConfig(data=DataConfig(providers=DataProviders(news="yahoo")), secrets=_keys())
    assert isinstance(build_news_provider(cfg), YahooNewsProvider)
