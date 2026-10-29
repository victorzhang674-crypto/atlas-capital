"""Stage 11: live smoke tests against the real news vendors (`-m live`, deselected by default)."""

from datetime import UTC, datetime, timedelta

import pytest

from atlas.core.clock import LiveClock, get_clock, use_clock
from atlas.core.config import load_config
from atlas.data.news.alpaca_news import AlpacaNewsProvider
from atlas.data.news.yahoo_news import YahooNewsProvider

pytestmark = pytest.mark.live


def test_live_alpaca_news() -> None:
    cfg = load_config("dev")
    if AlpacaNewsProvider.from_config(cfg) is None:
        pytest.skip("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set")
    provider = AlpacaNewsProvider.from_config(cfg)
    assert provider is not None
    start, end = datetime(2020, 3, 2, tzinfo=UTC), datetime(2020, 3, 3, tzinfo=UTC)
    try:
        out = provider.fetch_news(["AAPL"], start, end)
    finally:
        provider.close()
    assert len(out) > 0
    assert out["article_id"].str.startswith("alpaca-").all()
    assert ((out["published_at"] >= start) & (out["published_at"] < end)).all()
    assert out["symbols"].map(lambda tags: "AAPL" in tags).all()
    assert "content" not in out.columns


def test_live_yahoo_news() -> None:
    with use_clock(LiveClock()):
        end = get_clock().now() + timedelta(minutes=1)
    out = YahooNewsProvider().fetch_news(["AAPL"], end - timedelta(days=30), end)
    assert len(out) > 0
    assert out["article_id"].str.startswith("yahoo-").all()
    assert out["symbols"].map(lambda tags: "AAPL" in tags).all()
