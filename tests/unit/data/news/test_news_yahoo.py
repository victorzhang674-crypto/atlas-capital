"""Stage 11 Task 4: the Yahoo news adapter (recent news only, the fallback), fed a recorded
Yahoo search answer through an injected fetch function (no network)."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from news_helpers import FIXTURES, utc

from atlas.core.config import NewsConfig
from atlas.core.errors import DataError
from atlas.data.news import ARTICLE_COLUMNS, NewsProvider
from atlas.data.news.yahoo_news import YahooNewsProvider

ITEMS: list[dict[str, Any]] = json.loads((FIXTURES / "yahoo_news_aapl.json").read_text())
FIRST = min(i["providerPublishTime"] for i in ITEMS)
LAST = max(i["providerPublishTime"] for i in ITEMS)
START = datetime.fromtimestamp(FIRST, UTC)
END = datetime.fromtimestamp(LAST + 1, UTC)


class _Feed:
    """A fake `news_fn`: answers per vendor symbol, records the calls."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]], fail: int = 0) -> None:
        self.answers = answers
        self.calls: list[str] = []
        self._fail = fail

    def __call__(self, symbol: str) -> list[dict[str, Any]]:
        self.calls.append(symbol)
        if self._fail:
            self._fail -= 1
            raise RuntimeError("Yahoo is down")
        return self.answers.get(symbol, [])


def _provider(feed: _Feed, sleeps: list[float] | None = None, **cfg: Any) -> YahooNewsProvider:
    sink = sleeps if sleeps is not None else []
    return YahooNewsProvider(NewsConfig(**cfg), news_fn=feed, sleep=sink.append)


def test_is_a_news_provider() -> None:
    provider = _provider(_Feed({}))
    assert isinstance(provider, NewsProvider) and provider.name == "yahoo"


def test_parses_recorded_items() -> None:
    out = _provider(_Feed({"AAPL": ITEMS})).fetch_news(["AAPL"], START, END)
    assert tuple(out.columns) == ARTICLE_COLUMNS
    assert len(out) == len(ITEMS) == 20
    assert set(out["provider"]) == {"yahoo"}
    assert out["article_id"].str.match(r"^yahoo-[0-9a-f-]{36}$").all()
    wsj = out.loc[out["article_id"] == f"yahoo-{ITEMS[-1]['uuid']}"]
    assert len(wsj) == 1
    row = wsj.iloc[0]
    assert row["source"] == "The Wall Street Journal"
    assert row["headline"] == ITEMS[-1]["title"]
    assert row["summary"] == ""
    assert row["url"] == ITEMS[-1]["link"]
    assert row["published_at"] == datetime.fromtimestamp(ITEMS[-1]["providerPublishTime"], UTC)
    assert row["symbols"] == ("AAPL",)


def test_tags_normalised() -> None:
    out = _provider(_Feed({"AAPL": ITEMS})).fetch_news(["AAPL"], START, END)
    tags = dict(zip(out["article_id"], out["symbols"], strict=True))
    by_uuid = {f"yahoo-{i['uuid']}": i for i in ITEMS}
    samsung = next(a for a, item in by_uuid.items() if "005930.KS" in item["relatedTickers"])
    assert tags[samsung] == ("AAPL", "TTGT")  # 005930.KS is not a valid symbol
    buffett = next(a for a, item in by_uuid.items() if "AXP" in item["relatedTickers"])
    assert "BRK.B" in tags[buffett]


def test_window_filter() -> None:
    cut = datetime.fromtimestamp(LAST, UTC)
    out = _provider(_Feed({"AAPL": ITEMS})).fetch_news(["AAPL"], START, cut)
    assert len(out) == 19 and (out["published_at"] < cut).all()


def test_keeps_only_articles_tagged_with_a_requested_symbol() -> None:
    other = {**ITEMS[0], "uuid": "00000000-0000-0000-0000-000000000001", "relatedTickers": ["TSLA"]}
    out = _provider(_Feed({"AAPL": [ITEMS[0], other]})).fetch_news(["AAPL"], START, END)
    assert list(out["article_id"]) == [f"yahoo-{ITEMS[0]['uuid']}"]


@pytest.mark.parametrize("missing", ["uuid", "title", "providerPublishTime"])
def test_skips_malformed_items(missing: str) -> None:
    broken = {k: v for k, v in ITEMS[0].items() if k != missing}
    out = _provider(_Feed({"AAPL": [broken, ITEMS[1], "not an item"]})).fetch_news(
        ["AAPL"], START, END
    )
    assert list(out["article_id"]) == [f"yahoo-{ITEMS[1]['uuid']}"]


def test_market_wide_uses_market_symbols() -> None:
    feed = _Feed({"SPY": [ITEMS[0]], "QQQ": [ITEMS[1]]})
    out = _provider(feed).fetch_news(None, START, END)
    assert feed.calls == ["SPY", "QQQ"]
    assert len(out) == 2  # market-wide keeps every article, whatever its tags


def test_vendor_symbol_spelling() -> None:
    feed = _Feed({})
    _provider(feed).fetch_news(["brk.b"], START, END)
    assert feed.calls == ["BRK-B"]


def test_errors_retried_then_data_error() -> None:
    sleeps: list[float] = []
    with pytest.raises(DataError, match="4 attempt"):
        _provider(_Feed({}, fail=10), sleeps, min_request_interval_seconds=0).fetch_news(
            ["AAPL"], START, END
        )
    assert sleeps == [1.0, 2.0, 4.0]
    recovered = _provider(_Feed({"AAPL": ITEMS}, fail=1)).fetch_news(["AAPL"], START, END)
    assert len(recovered) == 20


def test_programming_error_not_retried() -> None:
    # Phase 1 checkpoint, Tier 2: a bug in the fetch function is not a vendor outage
    # (as Stage 12's calendar adapter).
    calls: list[str] = []

    def broken(symbol: str) -> list[dict[str, Any]]:
        calls.append(symbol)
        raise TypeError("bug in the news function")

    sleeps: list[float] = []
    provider = YahooNewsProvider(NewsConfig(), news_fn=broken, sleep=sleeps.append)
    with pytest.raises(TypeError, match="bug"):
        provider.fetch_news(["AAPL"], START, END)
    assert calls == ["AAPL"]
    assert sleeps == []


def test_non_list_answer_is_data_error() -> None:
    feed = _Feed({})
    feed.answers = {"AAPL": {"news": []}}  # type: ignore[dict-item]
    with pytest.raises(DataError, match="list"):
        _provider(feed).fetch_news(["AAPL"], START, END)


def test_same_article_for_two_symbols_once() -> None:
    feed = _Feed({"AAPL": ITEMS[:3], "NVDA": ITEMS[:1]})
    sleeps: list[float] = []
    out = _provider(feed, sleeps).fetch_news(["AAPL", "NVDA"], START, END)
    assert out["article_id"].is_unique and len(out) == 3
    assert sleeps == [0.3]


def test_empty_answer() -> None:
    out = _provider(_Feed({})).fetch_news(["AAPL"], utc(2020, 1, 1), utc(2020, 1, 2))
    assert out.empty and tuple(out.columns) == ARTICLE_COLUMNS
