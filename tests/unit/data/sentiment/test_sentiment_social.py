"""Stage 12 Task 10: optional StockTwits / Reddit posts behind a flag (off by default)."""

from datetime import date

import httpx
import pandas as pd
import pytest
import respx
from sentiment_helpers import FIXTURES, utc

from atlas.core.config import AtlasConfig, SocialConfig
from atlas.core.errors import DataError, LookAheadError
from atlas.core.pit import AsOf
from atlas.data.sentiment.social import (
    POST_COLUMNS,
    RedditProvider,
    SocialProvider,
    StockTwitsProvider,
    build_social_providers,
    social_evidence,
)

ST = "https://api.stocktwits.com/api/2/streams/symbol/AAPL.json"
RD = "https://www.reddit.com"
START, END = utc(2024, 3, 1), utc(2024, 3, 8)


class NoSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _enabled(**fields: object) -> AtlasConfig:
    base = AtlasConfig()
    social = base.data.sentiment.social.model_copy(update={"enabled": True, **fields})
    sentiment = base.data.sentiment.model_copy(update={"social": social})
    return base.model_copy(update={"data": base.data.model_copy(update={"sentiment": sentiment})})


def test_disabled_by_default() -> None:
    assert build_social_providers(AtlasConfig()) == ()


def test_enabled_builds_both() -> None:
    providers = build_social_providers(_enabled())
    assert [p.name for p in providers] == ["stocktwits", "reddit"]
    assert all(isinstance(p, SocialProvider) for p in providers)
    only = build_social_providers(_enabled(providers=("reddit",)))
    assert [p.name for p in only] == ["reddit"]


def test_stocktwits_pages_until_start() -> None:
    pages = [(FIXTURES / f"stocktwits_aapl_p{i}.json").read_bytes() for i in (1, 2)]
    with respx.mock() as mock:
        route = mock.get(ST).mock(side_effect=[httpx.Response(200, content=p) for p in pages])
        with httpx.Client() as http:
            out = StockTwitsProvider(http=http, sleep=NoSleep()).fetch_posts("aapl", START, END)
    assert len(route.calls) == 2  # page 2 reaches back before START, so paging stops
    assert "max" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["max"] == "571000001"
    assert "AtlasCapital" in route.calls[0].request.headers["user-agent"]
    assert tuple(out.columns) == POST_COLUMNS
    assert out["post_id"].tolist() == [
        "stocktwits-571000000", "stocktwits-571000001", "stocktwits-571000002",
        "stocktwits-571000003",
    ]  # fmt: skip
    first = out.iloc[0]
    assert (first["symbol"], first["source"]) == ("AAPL", "stocktwits")
    assert first["created_at"] == pd.Timestamp("2024-03-01T14:00:00Z")
    assert (out["available_at"] == out["created_at"]).all()
    assert first["url"] == "https://stocktwits.com/message/571000000"


def test_stocktwits_stops_at_max_items() -> None:
    page = (FIXTURES / "stocktwits_aapl_p1.json").read_bytes()
    with respx.mock() as mock:
        route = mock.get(ST).respond(200, content=page)
        with httpx.Client() as http:
            provider = StockTwitsProvider(SocialConfig(max_items=2), http=http, sleep=NoSleep())
            out = provider.fetch_posts("AAPL", START, END)
    assert len(route.calls) == 1 and len(out) == 3


def test_reddit_search_each_subreddit() -> None:
    body = (FIXTURES / "reddit_wsb_aapl.json").read_bytes()
    empty = {"kind": "Listing", "data": {"after": None, "children": []}}
    with respx.mock() as mock:
        wsb = mock.get(f"{RD}/r/wallstreetbets/search.json").respond(200, content=body)
        stocks = mock.get(f"{RD}/r/stocks/search.json").respond(200, json=empty)
        mock.get(f"{RD}/r/investing/search.json").respond(200, json=empty)
        with httpx.Client() as http:
            out = RedditProvider(http=http, sleep=NoSleep()).fetch_posts("AAPL", START, END)
    params = wsb.calls[0].request.url.params
    assert params["q"] == "AAPL" and params["restrict_sr"] == "1"
    assert (params["sort"], params["limit"], params["t"]) == ("new", "100", "month")
    assert stocks.called
    assert out["post_id"].tolist() == ["reddit-1b8abc2", "reddit-1b9xyz1"]  # broken item skipped
    post = out.iloc[1]
    assert post["text"] == "AAPL calls printing\n\nStrong gains after the event."
    assert post["source"] == "reddit/r/wallstreetbets"
    assert post["url"] == f"{RD}/r/wallstreetbets/comments/1b9xyz1/aapl_calls_printing/"
    assert post["created_at"] == pd.Timestamp("2024-03-07T17:00:00Z")


def test_posts_window_filter() -> None:
    body = (FIXTURES / "reddit_wsb_aapl.json").read_bytes()
    with respx.mock() as mock:
        mock.get(url__startswith=RD).respond(200, content=body)
        with httpx.Client() as http:
            provider = RedditProvider(SocialConfig(reddit_subreddits=("wallstreetbets",)),
                                      http=http, sleep=NoSleep())  # fmt: skip
            out = provider.fetch_posts("AAPL", utc(2024, 3, 7), utc(2024, 3, 7, 17))
    assert out.empty  # 17:00 is the exclusive end


def test_http_errors() -> None:
    sleep = NoSleep()
    with respx.mock() as mock:
        mock.get(ST).respond(503)
        with httpx.Client() as http, pytest.raises(DataError, match="stocktwits"):
            StockTwitsProvider(http=http, sleep=sleep).fetch_posts("AAPL", START, END)
    assert sleep.calls == [1.0, 2.0, 4.0]
    for status, body in ((403, b"denied"), (200, b"[]"), (200, b"not json")):
        with respx.mock() as mock:
            mock.get(ST).respond(status, content=body)
            with httpx.Client() as http, pytest.raises(DataError):
                StockTwitsProvider(http=http, sleep=NoSleep()).fetch_posts("AAPL", START, END)


def test_fetch_validates_window() -> None:
    provider = StockTwitsProvider(sleep=NoSleep())
    with pytest.raises(ValueError):
        provider.fetch_posts("AAPL", END, START)
    with pytest.raises(ValueError):
        provider.fetch_posts("^GSPC", START, END)


def _posts() -> pd.DataFrame:
    rows = [
        ("stocktwits-1", utc(2024, 3, 7, 18), "$AAPL weak", "stocktwits"),
        ("reddit-a", utc(2024, 3, 7, 17), "AAPL calls", "reddit/r/wallstreetbets"),
        ("stocktwits-2", utc(2024, 3, 8, 13), "$AAPL at the cutoff", "stocktwits"),
        ("stocktwits-0", utc(2024, 2, 20), "$AAPL old", "stocktwits"),
    ]
    out = pd.DataFrame(
        {
            "post_id": [r[0] for r in rows],
            "symbol": "AAPL",
            "created_at": pd.Series([r[1] for r in rows], dtype="datetime64[us, UTC]"),
            "available_at": pd.Series([r[1] for r in rows], dtype="datetime64[us, UTC]"),
            "source": [r[3] for r in rows],
            "text": [r[2] for r in rows],
            "url": None,
        }
    )
    return out[list(POST_COLUMNS)]


def test_social_evidence_pit_and_ids() -> None:
    as_of = AsOf.for_session(date(2024, 3, 8))  # 13:00 UTC cutoff
    got = social_evidence(_posts(), as_of, lookback_days=7, limit=10)
    assert [e.id for e in got] == ["social:stocktwits-1", "social:reddit-a"]
    assert got[0].source == "stocktwits" and got[0].text == "$AAPL weak"
    assert got[0].as_of == utc(2024, 3, 7, 18)
    assert len(social_evidence(_posts(), as_of, lookback_days=7, limit=1)) == 1


def test_social_evidence_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("atlas.data.sentiment.social.pit_filter", lambda df, a, *x: df.copy())
    with pytest.raises(LookAheadError):
        social_evidence(_posts(), AsOf.for_session(date(2024, 3, 8)), lookback_days=7, limit=10)
