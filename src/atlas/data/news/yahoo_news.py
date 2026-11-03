"""Yahoo Finance news adapter: recent news only, the fallback behind Alpaca.

Yahoo serves only the latest articles (no date parameters), so a historical window gets what
is still in the feed. The default fetch function is yfinance's search API
(`yf.Search(symbol, news_count=yahoo_max_items).news`); yfinance's `Ticker.get_news` endpoint
answered HTTP 404 when this adapter was written (2026-10-29). Each item carries `uuid`, `title`,
`publisher`, `link`, `providerPublishTime` (epoch seconds) and `relatedTickers`; there is no
summary. Items missing `uuid`, `title` or `providerPublishTime` are skipped. A symbol request
keeps the articles tagged with one of the requested symbols; a market-wide request asks for
`market_symbols` and keeps everything. An exception raised inside the fetch function is
retried and, when retries run out, raised as `DataError`; a programming error
(`PROGRAMMING_ERRORS`: a bug, not an outage) propagates at once, as in Stage 12's calendar.
"""

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import yfinance as yf

from atlas.core.config import NewsConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.types.instruments import vendor_symbol
from atlas.data.news.base import RAW_COLUMNS, check_news_window, empty_news, normalize_news
from atlas.data.providers.base import PROGRAMMING_ERRORS, RetryPolicy, Throttle, call_with_retry

NewsFn = Callable[[str], list[dict[str, Any]]]
"""`vendor symbol -> Yahoo news items` (see the module docstring for the item fields)."""

_log = get_logger(__name__)


class YahooNewsProvider:
    """`NewsProvider` backed by Yahoo Finance. `news_fn`/`sleep` are injectable for tests."""

    name = "yahoo"

    def __init__(
        self,
        config: NewsConfig | None = None,
        *,
        news_fn: NewsFn | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """A provider using `config` (`cfg.data.news`)."""
        self._settings = config if config is not None else NewsConfig()
        self._news_fn = news_fn if news_fn is not None else self._search
        self._policy = RetryPolicy(
            max_retries=self._settings.max_retries,
            backoff_seconds=self._settings.backoff_seconds,
            min_interval_seconds=self._settings.min_request_interval_seconds,
        )
        self._sleep = sleep
        self._throttle = Throttle(self._policy.min_interval_seconds, sleep)

    def _search(self, symbol: str) -> list[dict[str, Any]]:
        news: list[dict[str, Any]] = yf.Search(
            symbol, news_count=self._settings.yahoo_max_items, max_results=1
        ).news
        return news

    def fetch_news(
        self, symbols: Sequence[str] | None, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Recent articles tagged with any of `symbols` (`None`: market-wide) published in
        `[start, end)`."""
        wanted = check_news_window(symbols, start, end)
        queries = self._settings.market_symbols if wanted is None else wanted
        rows: list[dict[str, Any]] = []
        for symbol in queries:
            rows.extend(r for item in self._items(symbol) if (r := _row(item)) is not None)
        if not rows:
            return empty_news()
        out = normalize_news(pd.DataFrame(rows, columns=list(RAW_COLUMNS)), self._settings)
        inside = (out["published_at"] >= start) & (out["published_at"] < end)
        if wanted is not None:
            inside &= out["symbols"].map(lambda tags: bool(set(tags) & set(wanted)))
        return out.loc[inside].reset_index(drop=True)

    def _items(self, symbol: str) -> list[Any]:
        self._throttle.wait()
        ticker = vendor_symbol(symbol)
        items = call_with_retry(
            lambda: self._call(ticker),
            self._policy,
            self._sleep,
            f"yahoo news {ticker}",
            (_VendorError,),
            event="news.request_failed",
        )
        if not isinstance(items, list):
            raise DataError(f"yahoo news {ticker}: expected a list of items")
        return items

    def _call(self, ticker: str) -> list[Any]:
        try:
            return self._news_fn(ticker)
        except PROGRAMMING_ERRORS:
            raise
        except Exception as exc:
            raise _VendorError(f"{type(exc).__name__}: {exc}") from exc


class _VendorError(Exception):
    """Any other failure inside the news function: retried."""


def _row(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        _log.debug("news.bad_item", provider="yahoo", item=str(item)[:100])
        return None
    uuid, title, stamp = item.get("uuid"), item.get("title"), item.get("providerPublishTime")
    if not uuid or not title or not isinstance(stamp, int | float):
        _log.debug("news.bad_item", provider="yahoo", item=str(item)[:100])
        return None
    return {
        "article_id": f"yahoo-{uuid}",
        "published_at": datetime.fromtimestamp(stamp, UTC),
        "updated_at": None,
        "provider": YahooNewsProvider.name,
        "source": str(item.get("publisher") or YahooNewsProvider.name),
        "headline": title,
        "summary": item.get("summary"),
        "url": item.get("link"),
        "symbols": item.get("relatedTickers") or [],
    }
