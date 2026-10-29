"""Stage 11 Task 8: the point-in-time news query and the stage's acceptance tests.

Times: 2020-03-02/03 are before the US switch to daylight time (2020-03-08), so New York is
UTC-5 and the default 08:00 decision of 2020-03-03 is 13:00 UTC.
"""

import json
from collections.abc import Iterator
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
import respx
from news_helpers import FIXTURES, REPO_ROOT, FakeNewsProvider, article, articles_frame, utc
from pydantic import SecretStr

import atlas.data.news.pit as pit_module
from atlas.core.config import AtlasConfig, CalendarConfig, NewsConfig
from atlas.core.errors import DataError, LookAheadError
from atlas.core.pit import AsOf
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.core.types.research import Evidence
from atlas.data.news import (
    DETAIL_COLUMNS,
    News,
    ingest_news,
    market_news_pit,
    news_pit,
    news_pit_detail,
)
from atlas.data.news.alpaca_news import AlpacaNewsProvider

AT_0303 = AsOf.for_session(date(2020, 3, 3))  # cutoff 2020-03-03 13:00 UTC
AT_0304 = AsOf.for_session(date(2020, 3, 4))
DEFAULTS = {"lookback_days": 14, "limit": 50}
NATO = [
    "Alpha",
    "Bravo",
    "Charlie",
    "Delta",
    "Echo",
    "Foxtrot",
    "Golf",
    "Hotel",
    "India",
    "Juliett",
    "Kilo",
    "Lima",
    "Mike",
    "November",
    "Oscar",
    "Papa",
    "Quebec",
    "Romeo",
    "Sierra",
    "Tango",
]
YAHOO_COPY = article(
    article_id="yahoo-5f1c0e7a-0000-4000-8000-000000000001",
    provider="yahoo",
    source="Reuters",
    published_at=utc(2020, 3, 2, 16, 5),
    headline="Apple shares rise as analysts shrug off virus hit",
    symbols=["AAPL"],
)


def _alpaca_handler(request: httpx.Request) -> httpx.Response:
    name = "alpaca_news_p2.json" if request.url.params.get("page_token") else "alpaca_news_p1.json"
    return httpx.Response(200, json=json.loads((FIXTURES / name).read_text()))


@pytest.fixture
def alpaca() -> Iterator[AlpacaNewsProvider]:
    with respx.mock(assert_all_called=False) as router:
        router.get("https://data.alpaca.markets/v1beta1/news").mock(side_effect=_alpaca_handler)
        yield AlpacaNewsProvider(
            SecretStr("PKX"), SecretStr("sx"), NewsConfig(min_request_interval_seconds=0)
        )


def _lake(tmp_path: Path, name: str = "lake") -> Lake:
    return Lake(tmp_path / name, registry=DEFAULT_REGISTRY.copy())


def _ingest(lake: Lake, provider: Any, start: Any, end: Any) -> None:
    ingest_news(lake, provider, ["AAPL", "MSFT"], start, end)


def _ids(evidence: list[Evidence]) -> list[str]:
    return [e.id for e in evidence]


# --- acceptance ------------------------------------------------------------------------------


def test_acceptance_article_after_cutoff_invisible() -> None:
    frame = articles_frame(
        [
            article(article_id="a-0830", published_at=utc(2020, 3, 3, 13, 30)),  # 08:30 ET
            article(article_id="a-0800", published_at=utc(2020, 3, 3, 13, 0, 0), headline="B up"),
            article(article_id="a-0759", published_at=utc(2020, 3, 3, 12, 59, 59), headline="C"),
        ]
    )
    assert _ids(news_pit(frame, "AAPL", AT_0303, **DEFAULTS)) == ["news:a-0759"]
    later = news_pit(frame, "AAPL", AT_0304, **DEFAULTS)
    assert set(_ids(later)) == {"news:a-0830", "news:a-0800", "news:a-0759"}


def test_acceptance_evidence_ids_stable_across_reingests(
    tmp_path: Path, alpaca: AlpacaNewsProvider
) -> None:
    lake = _lake(tmp_path)
    _ingest(lake, alpaca, utc(2020, 3, 2), utc(2020, 3, 3))
    first = News(lake).pit("AAPL", date(2020, 3, 3))
    assert _ids(first) == ["news:alpaca-15000001", "news:alpaca-15000004"]
    _ingest(lake, alpaca, utc(2020, 3, 2), utc(2020, 3, 3))
    _ingest(lake, alpaca, utc(2020, 3, 2, 14), utc(2020, 3, 3))  # overlapping window
    again = News(lake).pit("AAPL", date(2020, 3, 3))
    assert again == first
    _ingest(lake, alpaca, utc(2020, 3, 2), utc(2020, 3, 4))  # a wider window adds a new article
    wider = _ids(News(lake).pit("AAPL", date(2020, 3, 3)))
    assert wider == ["news:alpaca-15000001", "news:alpaca-15000005", "news:alpaca-15000004"]


def test_acceptance_ids_independent_of_ingest_order(
    tmp_path: Path, alpaca: AlpacaNewsProvider
) -> None:
    yahoo = FakeNewsProvider("yahoo", articles_frame([YAHOO_COPY]))
    window = (utc(2020, 3, 2), utc(2020, 3, 3))
    one, two = _lake(tmp_path, "one"), _lake(tmp_path, "two")
    _ingest(one, alpaca, *window)
    _ingest(one, yahoo, *window)
    _ingest(two, yahoo, *window)
    _ingest(two, alpaca, *window)
    assert News(one).pit("AAPL", AT_0303) == News(two).pit("AAPL", AT_0303)
    assert _ids(News(one).pit("AAPL", AT_0304)) == _ids(News(two).pit("AAPL", AT_0304))


def test_acceptance_same_story_two_sources_one_evidence(
    tmp_path: Path, alpaca: AlpacaNewsProvider
) -> None:
    lake = _lake(tmp_path)
    _ingest(lake, alpaca, utc(2020, 3, 2), utc(2020, 3, 3))
    _ingest(
        lake,
        FakeNewsProvider("yahoo", articles_frame([YAHOO_COPY])),
        utc(2020, 3, 2),
        utc(2020, 3, 3),
    )
    assert len(lake.read_table("news")) == 5  # both copies are stored
    ids = _ids(News(lake).pit("AAPL", AT_0303))
    assert ids == ["news:alpaca-15000001", "news:alpaca-15000004"]  # the Yahoo copy is hidden


# --- point in time ---------------------------------------------------------------------------


def test_guard_raises_if_filter_bypassed(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = articles_frame([article(published_at=utc(2020, 3, 3, 13, 30))])
    monkeypatch.setattr(pit_module, "pit_filter", lambda df, as_of: df)
    with pytest.raises(LookAheadError):
        news_pit(frame, "AAPL", AT_0303, **DEFAULTS)
    with pytest.raises(LookAheadError):
        market_news_pit(frame, AT_0303, **DEFAULTS)


def test_future_rows_change_nothing() -> None:
    past = [
        article(article_id="a-1", published_at=utc(2020, 3, 2, 15), headline="Apple up"),
        article(article_id="a-2", published_at=utc(2020, 3, 2, 18), headline="Apple supplier news"),
    ]
    future = [
        article(article_id="a-0", published_at=utc(2020, 3, 3, 13), headline="Apple supplier news"),
        article(article_id="a-3", published_at=utc(2020, 3, 5), headline="Apple up"),
        article(article_id="a-4", published_at=utc(2020, 3, 3, 14), headline="$AAPL jumps"),
    ]
    alone = news_pit_detail(articles_frame(past), "AAPL", AT_0303, **DEFAULTS)
    mixed = news_pit_detail(articles_frame(past + future), "AAPL", AT_0303, **DEFAULTS)
    pd.testing.assert_frame_equal(alone, mixed)
    assert list(alone["article_id"]) == ["a-2", "a-1"]


def test_lookback_and_limit() -> None:
    cutoff = AT_0303.cutoff
    frame = articles_frame(
        [
            article(article_id="edge", published_at=cutoff - timedelta(days=14), headline="Edge"),
            article(
                article_id="too-old",
                published_at=cutoff - timedelta(days=14, seconds=1),
                headline="Old",
            ),
            article(article_id="new", published_at=cutoff - timedelta(hours=1), headline="New"),
            article(
                article_id="named",
                published_at=cutoff - timedelta(days=3),
                headline="Apple (AAPL) named",
            ),
        ]
    )
    detail = news_pit_detail(frame, "AAPL", AT_0303, **DEFAULTS)
    assert list(detail["article_id"]) == ["named", "new", "edge"]  # relevance, then newest
    assert tuple(detail.columns) == DETAIL_COLUMNS
    assert list(detail["relevance"]) == [1.0, 0.5, 0.5]
    assert _ids(news_pit(frame, "AAPL", AT_0303, lookback_days=14, limit=2)) == [
        "news:named",
        "news:new",
    ]
    assert _ids(news_pit(frame, "AAPL", AT_0303, lookback_days=1, limit=50)) == ["news:new"]


def test_duplicate_of_article_before_window_stays_hidden() -> None:
    cutoff = AT_0303.cutoff
    frame = articles_frame(
        [
            article(article_id="orig", published_at=cutoff - timedelta(days=2, hours=1)),
            article(article_id="copy", published_at=cutoff - timedelta(hours=20)),
        ]
    )
    assert _ids(news_pit(frame, "AAPL", AT_0303, lookback_days=1, limit=50)) == []


def test_roundup_does_not_hide_focused_duplicate() -> None:
    tags = ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "NFLX", "NVDA", "AMD", "INTC", "BA"]
    headline = "Apple shares rise as analysts shrug off virus hit"
    frame = articles_frame(
        [
            article(
                article_id="roundup",
                published_at=utc(2020, 3, 2, 15),
                headline=headline,
                summary="And ten other movers.",
                symbols=tags,
            ),
            article(article_id="focused", published_at=utc(2020, 3, 2, 15, 30), headline=headline),
        ]
    )
    assert _ids(news_pit(frame, "AAPL", AT_0303, **DEFAULTS)) == ["news:focused"]


def test_evidence_fields() -> None:
    frame = articles_frame(
        [
            article(
                article_id="a-1",
                published_at=utc(2020, 3, 2, 15),
                source="benzinga",
                headline="Apple up",
                summary="Shares rose 2%.",
                url="https://example.com/a-1",
            ),
            article(article_id="a-2", published_at=utc(2020, 3, 2, 16), headline="Apple down"),
        ]
    )
    by_id = {e.id: e for e in news_pit(frame, "AAPL", AT_0303, **DEFAULTS)}
    first = by_id["news:a-1"]
    assert first == Evidence(
        id="news:a-1",
        source="benzinga",
        as_of=utc(2020, 3, 2, 15),
        text="Apple up\n\nShares rose 2%.",
        url="https://example.com/a-1",
    )
    assert by_id["news:a-2"].text == "Apple down" and by_id["news:a-2"].url is None


def test_market_news() -> None:
    frame = articles_frame(
        [
            article(
                article_id="m-1",
                published_at=utc(2020, 3, 2, 14),
                headline="Stocks slide",
                symbols=[],
            ),
            article(
                article_id="m-2",
                published_at=utc(2020, 3, 2, 15),
                headline="SPY falls 3%",
                symbols=["SPY"],
            ),
            article(
                article_id="m-3",
                published_at=utc(2020, 3, 2, 16),
                headline="Nasdaq drops",
                symbols=["QQQ", "AAPL"],
            ),
            article(
                article_id="m-4",
                published_at=utc(2020, 3, 2, 17),
                headline="Stocks slide!",
                symbols=[],
            ),
            article(article_id="x-1", published_at=utc(2020, 3, 2, 18), headline="Apple only"),
        ]
    )
    ids = _ids(market_news_pit(frame, AT_0303, **DEFAULTS))
    assert ids == ["news:m-3", "news:m-2", "news:m-1"]  # newest first; m-4 copies m-1
    assert _ids(market_news_pit(frame, AT_0303, lookback_days=14, limit=1)) == ["news:m-3"]


def test_bad_lookback_or_limit() -> None:
    frame = articles_frame([article()])
    for kwargs in (
        {"lookback_days": 0, "limit": 5},
        {"lookback_days": 5, "limit": 0},
        {"lookback_days": True, "limit": 5},
    ):
        with pytest.raises(ValueError):
            news_pit(frame, "AAPL", AT_0303, **kwargs)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            market_news_pit(frame, AT_0303, **kwargs)  # type: ignore[arg-type]


def test_symbol_normalised() -> None:
    frame = articles_frame([article(headline="Berkshire buys", symbols=["BRK.B"])])
    assert _ids(news_pit(frame, "brk-b", AT_0303, **DEFAULTS)) == ["news:alpaca-1"]


# --- the News reader --------------------------------------------------------------------------


def test_news_reader_defaults_from_config(tmp_path: Path) -> None:
    lake = _lake(tmp_path)
    rows = [
        article(
            article_id=f"a-{i}",
            published_at=utc(2020, 3, 2) - timedelta(days=i),
            headline=f"{word} reports",
        )
        for i, word in enumerate(NATO)
    ]
    ingest_news(
        lake,
        FakeNewsProvider("alpaca", articles_frame(rows)),
        None,
        utc(2020, 1, 1),
        utc(2020, 4, 1),
    )
    # 14 days before the 2020-03-03 13:00 UTC cutoff: 2020-02-19 .. 2020-03-02 midnight rows
    assert len(News(lake).pit("AAPL", AT_0303)) == 13
    cfg = AtlasConfig.model_validate({"data": {"news": {"lookback_days": 30, "limit": 5}}})
    assert len(News(lake, cfg).pit("AAPL", AT_0303)) == 5
    assert len(News(lake, cfg).pit("AAPL", AT_0303, lookback_days=2, limit=50)) == 1
    assert tuple(News(lake).pit_detail("AAPL", AT_0303).columns) == DETAIL_COLUMNS


def test_news_reader_date_as_of_uses_decision_time(tmp_path: Path) -> None:
    lake = _lake(tmp_path)
    frame = articles_frame([article(published_at=utc(2020, 3, 3, 13, 30))])  # 08:30 ET
    ingest_news(lake, FakeNewsProvider("alpaca", frame), None, utc(2020, 3, 1), utc(2020, 3, 4))
    assert News(lake).pit("AAPL", date(2020, 3, 3)) == []
    nine = AtlasConfig(calendar=CalendarConfig(decision_time=time(9, 0)))
    assert _ids(News(lake, nine).pit("AAPL", date(2020, 3, 3))) == ["news:alpaca-1"]
    assert _ids(News(lake, nine).market(date(2020, 3, 3))) == []  # AAPL-only news


def test_news_reader_market(tmp_path: Path) -> None:
    lake = _lake(tmp_path)
    frame = articles_frame([article(headline="Stocks slide", symbols=[])])
    ingest_news(lake, FakeNewsProvider("alpaca", frame), None, utc(2020, 3, 1), utc(2020, 3, 4))
    assert _ids(News(lake).market(AT_0303)) == ["news:alpaca-1"]


def test_news_reader_rejects_datetime(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="date"):
        News(_lake(tmp_path)).pit("AAPL", utc(2020, 3, 3))  # type: ignore[arg-type]


def test_news_reader_empty_lake(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="ingest_news"):
        News(_lake(tmp_path)).pit("AAPL", AT_0303)


def test_news_reader_articles_window(tmp_path: Path) -> None:
    lake = _lake(tmp_path)
    frame = articles_frame(
        [
            article(article_id=f"a-{h}", published_at=utc(2020, 3, 2, h), headline=f"H{h}")
            for h in (10, 11, 12)
        ]
    )
    ingest_news(lake, FakeNewsProvider("alpaca", frame), None, utc(2020, 3, 1), utc(2020, 3, 4))
    got = News(lake).articles(utc(2020, 3, 2, 11), utc(2020, 3, 2, 12))
    assert list(got["article_id"]) == ["a-11"]
    assert got.loc[0, "symbols"] == ("AAPL",)


def test_data_limitations_doc_covers_news() -> None:
    text = (REPO_ROOT / "docs" / "data-limitations.md").read_text(encoding="utf-8")
    assert "## News (Stage 11)" in text
    section = text.split("## News (Stage 11)", 1)[1].lower()
    for word in ("headline", "summary", "updated", "roundup", "duplicate", "yahoo"):
        assert word in section


def test_same_article_id_twice_cited_once(tmp_path: Path) -> None:
    # Review fix: a vendor that re-dates an article (same id, new publish time) leaves two rows
    # under the (article_id, published_at) key; the earliest-known row is the one cited.
    lake = _lake(tmp_path)
    first = articles_frame([article(published_at=utc(2020, 3, 2, 15), headline="Apple up")])
    redated = articles_frame([article(published_at=utc(2020, 3, 2, 17), headline="Apple jumps 4%")])
    window = (utc(2020, 3, 1), utc(2020, 3, 4))
    ingest_news(lake, FakeNewsProvider("alpaca", first), None, *window)
    ingest_news(lake, FakeNewsProvider("alpaca", redated), None, *window)
    assert len(lake.read_table("news")) == 2
    cited = News(lake).pit("AAPL", AT_0303)
    assert [(e.id, e.text) for e in cited] == [("news:alpaca-1", "Apple up")]
    market = articles_frame([article(published_at=utc(2020, 3, 2, 15), symbols=[])])
    both = pd.concat(
        [market, market.assign(published_at=market["published_at"] + timedelta(hours=1))]
    )
    both["available_at"] = both["published_at"]
    assert _ids(market_news_pit(both, AT_0303, **DEFAULTS)) == ["news:alpaca-1"]


@pytest.mark.parametrize("path", ["pit", "pit_detail", "market"])
def test_news_reader_guard_raises_if_filter_bypassed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    # Audit fix (stage protocol §5): every data path asks for data after as_of and expects
    # LookAheadError. The reader's own read window is widened and the filter bypassed, so a
    # stored article published after the cutoff reaches the guard.
    lake = _lake(tmp_path)
    frame = articles_frame([article(published_at=utc(2020, 3, 3, 13, 30), symbols=[])])
    ingest_news(lake, FakeNewsProvider("alpaca", frame), None, utc(2020, 3, 1), utc(2020, 3, 4))
    reader = News(lake)
    monkeypatch.setattr(
        reader, "_window", lambda point, lookback: reader.articles(utc(2020, 1, 1), utc(2021, 1, 1))
    )
    monkeypatch.setattr(pit_module, "pit_filter", lambda df, as_of: df)
    calls = {
        "pit": lambda: reader.pit("AAPL", AT_0303),
        "pit_detail": lambda: reader.pit_detail("AAPL", AT_0303),
        "market": lambda: reader.market(AT_0303),
    }
    with pytest.raises(LookAheadError):
        calls[path]()
