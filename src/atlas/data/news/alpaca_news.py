"""Alpaca news adapter: the Benzinga feed of Alpaca's market data API (history from 2015).

`GET /v1beta1/news` with `symbols` (comma-separated; left out for market-wide news), `start`,
`end` (RFC 3339 UTC), `limit` (at most 50), `sort=asc` and `include_content=false` (article
bodies are never requested), paged with `next_page_token`. Transport errors, HTTP 429 and 5xx
are retried (see `NewsConfig`); any other HTTP error, a body that is not `{"news": [...]}` or an
item without `id` or `created_at` raises `DataError`. The API keys are sent only as request
headers and never appear in logs, errors or `repr`.
"""

import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, Self

import httpx
import pandas as pd
from pydantic import SecretStr

from atlas.core.config import AtlasConfig, NewsConfig
from atlas.core.errors import DataError
from atlas.data.news.base import RAW_COLUMNS, check_news_window, empty_news, normalize_news
from atlas.data.providers.base import RetryPolicy, Throttle, call_with_retry


class _Retryable(Exception):
    """An HTTP answer worth retrying (429 or 5xx)."""


class AlpacaNewsProvider:
    """`NewsProvider` backed by Alpaca's news API. `client`/`sleep` are injectable for tests."""

    name = "alpaca"

    def __init__(
        self,
        key_id: SecretStr,
        secret_key: SecretStr,
        config: NewsConfig | None = None,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """A provider using the given paper-account data keys and `config` (`cfg.data.news`)."""
        self._settings = config if config is not None else NewsConfig()
        self._key_id = key_id
        self._secret_key = secret_key
        self._url = self._settings.alpaca_news_url
        self._policy = RetryPolicy(
            max_retries=self._settings.max_retries,
            backoff_seconds=self._settings.backoff_seconds,
            min_interval_seconds=self._settings.min_request_interval_seconds,
        )
        self._sleep = sleep
        self._throttle = Throttle(self._policy.min_interval_seconds, sleep)
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.Client(timeout=self._settings.request_timeout_seconds)
        )

    @classmethod
    def from_config(cls, cfg: AtlasConfig) -> Self | None:
        """A provider from `cfg.secrets` and `cfg.data.news`, or `None` if either Alpaca key is
        unset (Alpaca is optional; Yahoo is the fallback)."""
        key_id, secret = cfg.secrets.alpaca_api_key_id, cfg.secrets.alpaca_api_secret_key
        if key_id is None or secret is None:
            return None
        if not key_id.get_secret_value() or not secret.get_secret_value():
            return None
        return cls(key_id, secret, cfg.data.news)

    def __repr__(self) -> str:
        return f"AlpacaNewsProvider(url={self._url!r})"

    def close(self) -> None:
        """Close the HTTP client if this provider created it."""
        if self._owns_client:
            self._client.close()

    def fetch_news(
        self, symbols: Sequence[str] | None, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Articles tagged with any of `symbols` (`None`: all news) published in `[start, end)`."""
        wanted = check_news_window(symbols, start, end)
        if wanted is None:
            batches: list[tuple[str, ...] | None] = [None]
        else:
            size = self._settings.symbols_per_request
            batches = [wanted[i : i + size] for i in range(0, len(wanted), size)]
        rows: list[dict[str, Any]] = []
        for batch in batches:
            params = {
                "start": _rfc3339(start),
                "end": _rfc3339(end),
                "limit": str(self._settings.page_limit),
                "sort": "asc",
                "include_content": "false",
            }
            if batch is not None:
                params["symbols"] = ",".join(batch)
            for page in self._pages(params):
                rows.extend(_row(item) for item in page)
        if not rows:
            return empty_news()
        out = normalize_news(pd.DataFrame(rows, columns=list(RAW_COLUMNS)), self._settings)
        inside = (out["published_at"] >= start) & (out["published_at"] < end)
        return out.loc[inside].reset_index(drop=True)

    def _pages(self, params: dict[str, str]) -> Iterator[list[Any]]:
        """The `news` list of every page for `params`, following `next_page_token`."""
        token: str | None = None
        while True:
            query = dict(params) if token is None else {**params, "page_token": token}
            body = self._get(query)
            news = body.get("news")
            if not isinstance(news, list):
                raise DataError("alpaca news: expected a 'news' list in the response")
            yield news
            token = body.get("next_page_token")
            if not token:
                return

    def _get(self, params: dict[str, str]) -> dict[str, Any]:
        self._throttle.wait()

        def _attempt() -> dict[str, Any]:
            response = self._client.get(self._url, params=params, headers=self._headers())
            if response.status_code == 429 or response.status_code >= 500:
                raise _Retryable(f"HTTP {response.status_code} from alpaca news")
            if response.status_code >= 400:
                raise DataError(f"alpaca news: HTTP {response.status_code}: {response.text[:200]}")
            try:
                body = response.json()
            except ValueError as exc:
                raise DataError("alpaca news: response is not JSON") from exc
            if not isinstance(body, dict):
                raise DataError("alpaca news: expected a JSON object")
            return body

        return call_with_retry(
            _attempt,
            self._policy,
            self._sleep,
            "alpaca news",
            (_Retryable, httpx.TransportError),
            event="news.request_failed",
        )

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._key_id.get_secret_value(),
            "APCA-API-SECRET-KEY": self._secret_key.get_secret_value(),
        }


def _rfc3339(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(text: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataError(f"alpaca news: bad timestamp {text!r}") from exc


def _row(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or item.get("id") is None or not item.get("created_at"):
        raise DataError(f"alpaca news: an item lacks 'id' or 'created_at': {str(item)[:200]}")
    updated = item.get("updated_at")
    return {
        "article_id": f"alpaca-{item['id']}",
        "published_at": _parse_ts(item["created_at"]),
        "updated_at": _parse_ts(updated) if updated else None,
        "provider": AlpacaNewsProvider.name,
        "source": str(item.get("source") or AlpacaNewsProvider.name),
        "headline": item.get("headline"),
        "summary": item.get("summary"),
        "url": item.get("url"),
        "symbols": item.get("symbols") or [],
    }
