"""Stage 11 Task 7: light relevance scoring and the roundup filter."""

import pytest
from news_helpers import article, articles_frame

from atlas.core.config import NewsConfig
from atlas.data.news import headline_mentions, score_relevance

TWELVE = [
    "AAPL",
    "MSFT",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "NFLX",
    "NVDA",
    "AMD",
    "INTC",
    "BA",
    "DIS",
]


def test_headline_mention_ranks_above_tag_only() -> None:
    frame = articles_frame(
        [
            article(article_id="a-1", headline="Apple (AAPL) beats estimates"),
            article(article_id="a-2", headline="iPhone maker beats estimates"),
        ]
    )
    out = score_relevance(frame, "AAPL")
    assert list(out["relevance"]) == [1.0, 0.5]
    assert list(out["headline_match"]) == [True, False]


@pytest.mark.parametrize(
    ("headline", "symbol", "expected"),
    [
        ("AT&T (T) cuts dividend", "T", True),
        ("$F rallies on EV news", "F", True),
        ("NYSE: GM recalls trucks", "GM", True),
        ("Ford (NYSE:F) recalls trucks", "F", True),
        ("NASDAQ:AAPL slips", "AAPL", True),
        ("T-Mobile and Ford climb", "T", False),
        ("T-Mobile and Ford climb", "F", False),
        ("GM recalls trucks", "GM", False),
        ("AAPLX fund adds shares", "AAPL", False),
        ("Apple (AAPL) beats", "AAPL", True),
        ("Why AAPL stock is moving", "AAPL", True),
        ("Buy AAPL.", "AAPL", True),
        ("Apple beats estimates", "AAPL", False),
        ("aapl beats", "AAPL", False),
        ("BRK-B buys more Apple", "BRK.B", True),
        ("Berkshire (BRK/B) buys", "BRK.B", True),
        ("BRK.B buys more Apple", "BRK.B", True),
        ("BRK-B buys more Apple", "BRK.A", False),
        ("$TSLA$", "TSLA", True),
        ("XTSLA soars", "TSLA", False),
    ],
)
def test_headline_mentions_markers_and_short_tickers(
    headline: str, symbol: str, expected: bool
) -> None:
    assert headline_mentions(headline, symbol) is expected


def test_roundup_dropped_unless_headline_names_ticker() -> None:
    eleven = TWELVE[:11]
    frame = articles_frame(
        [
            article(article_id="a-1", headline="11 stocks to watch", symbols=eleven),
            article(article_id="a-2", headline="11 stocks to watch: $AAPL leads", symbols=eleven),
            article(article_id="a-3", headline="10 stocks to watch", symbols=TWELVE[:10]),
            article(article_id="a-4", headline="12 movers", symbols=TWELVE),
        ]
    )
    out = score_relevance(frame, "AAPL")
    assert list(out["article_id"]) == ["a-2", "a-3"]
    assert list(out["relevance"]) == [1.0, 0.5]
    assert list(score_relevance(frame, "MSFT")["article_id"]) == ["a-3"]


def test_untagged_article_not_a_candidate() -> None:
    frame = articles_frame([article(headline="Apple (AAPL) rises", symbols=["MSFT"])])
    out = score_relevance(frame, "AAPL")
    assert out.empty and {"relevance", "headline_match"} <= set(out.columns)


def test_score_uses_config_weights_and_normalises_symbol() -> None:
    frame = articles_frame(
        [
            article(article_id="a-1", headline="Berkshire (BRK.B) adds", symbols=["BRK.B"]),
            article(article_id="a-2", headline="Buffett buys", symbols=["BRK.B"]),
        ]
    )
    cfg = NewsConfig(headline_relevance=2.0, symbols_relevance=0.25)
    assert list(score_relevance(frame, "brk-b", cfg)["relevance"]) == [2.0, 0.25]


def test_roundup_limit_from_config() -> None:
    frame = articles_frame([article(headline="3 movers", symbols=["AAPL", "MSFT", "AMZN"])])
    assert score_relevance(frame, "AAPL", NewsConfig(roundup_max_symbols=2)).empty
