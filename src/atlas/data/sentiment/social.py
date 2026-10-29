"""Optional social-media posts (StockTwits, Reddit), off unless `data.sentiment.social.enabled`.

Each provider answers `fetch_posts(symbol, start, end)` with a post frame (`POST_COLUMNS`):

| Column | Type | Meaning |
|---|---|---|
| `post_id` | `str` | `<provider>-<vendor id>` (`stocktwits-571000003`, `reddit-1b9xyz1`) |
| `symbol` | `str` | canonical ticker asked for |
| `created_at` | UTC timestamp | the post's time |
| `available_at` | UTC timestamp | equal to `created_at` (public when posted) |
| `source` | `str` | `stocktwits` or `reddit/r/<subreddit>` |
| `text` | `str` | message body; Reddit: title, a blank line and the body |
| `url` | `str` or `None` | link to the post |

Posts are not stored (the free APIs serve recent posts only, so there is no history to replay);
`social_evidence` turns fetched posts into citable `Evidence` (`id = "social:<post_id>"`),
point in time. Requests send `user_agent`, are throttled, and retry transport errors, HTTP 429
and 5xx; any other answer, or a body that is not the documented JSON object, raises
`DataError`.
"""

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol, runtime_checkable

import httpx
import pandas as pd

from atlas.core.config import AtlasConfig, SocialConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf, assert_no_lookahead, pit_filter
from atlas.core.storage import AVAILABLE_AT
from atlas.core.types.instruments import normalize_symbol
from atlas.core.types.research import Evidence
from atlas.data.providers.base import RetryPolicy, Throttle, call_with_retry

_log = get_logger(__name__)

POST_COLUMNS: Final = ("post_id", "symbol", "created_at", AVAILABLE_AT, "source", "text", "url")
"""Columns of a post frame, in order."""

EVIDENCE_PREFIX: Final = "social:"
"""`Evidence.id` of a post is this prefix plus its `post_id`."""


class _Retryable(Exception):
    """A transport error or an HTTP 429 / 5xx answer."""


@runtime_checkable
class SocialProvider(Protocol):
    """A source of recent social posts about a symbol."""

    name: str

    def fetch_posts(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Posts about `symbol` created in `[start, end)` (aware), as a post frame."""
        ...


def _check(symbol: str, start: datetime, end: datetime) -> str:
    for bound in (start, end):
        if not isinstance(bound, datetime) or bound.utcoffset() is None:
            raise ValueError(f"start and end must be timezone-aware datetimes, got {bound!r}")
    if start >= end:
        raise ValueError(f"start ({start.isoformat()}) must be before end ({end.isoformat()})")
    return normalize_symbol(symbol)


def _frame(rows: list[dict[str, Any]], start: datetime, end: datetime) -> pd.DataFrame:
    out = pd.DataFrame(rows, columns=list(POST_COLUMNS))
    out["created_at"] = pd.to_datetime(out["created_at"], utc=True).dt.as_unit("us")
    out[AVAILABLE_AT] = out["created_at"]
    for col in ("post_id", "symbol", "source", "text"):
        out[col] = out[col].astype("str")
    out = out.drop_duplicates(subset=["post_id"], keep="first")
    inside = (out["created_at"] >= pd.Timestamp(start)) & (out["created_at"] < pd.Timestamp(end))
    out = out.loc[inside].sort_values(["created_at", "post_id"], kind="stable")
    return out.reset_index(drop=True)


class _JsonSource:
    """Shared HTTP plumbing: user agent, throttle, retries, JSON-object bodies."""

    name = ""

    def __init__(
        self,
        config: SocialConfig | None,
        http: httpx.Client | None,
        sleep: Callable[[float], None],
    ) -> None:
        self._settings = config if config is not None else SocialConfig()
        self._http = (
            http
            if http is not None
            else httpx.Client(timeout=self._settings.request_timeout_seconds, follow_redirects=True)
        )
        self._policy = RetryPolicy(
            max_retries=self._settings.max_retries,
            backoff_seconds=self._settings.backoff_seconds,
            min_interval_seconds=self._settings.min_request_interval_seconds,
        )
        self._sleep = sleep
        self._throttle = Throttle(self._policy.min_interval_seconds, sleep)

    def _get(self, url: str, params: Mapping[str, str]) -> dict[str, Any]:
        self._throttle.wait()
        what = f"{self.name} GET {url}"
        resp = call_with_retry(
            lambda: self._request(url, params),
            self._policy,
            self._sleep,
            what,
            (_Retryable,),
            event="sentiment.request_failed",
        )
        if resp.status_code != 200:
            raise DataError(f"{what} returned HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise DataError(f"{what}: not valid JSON") from exc
        if not isinstance(body, dict):
            raise DataError(f"{what}: expected a JSON object, got {type(body).__name__}")
        return body

    def _request(self, url: str, params: Mapping[str, str]) -> httpx.Response:
        headers = {"User-Agent": self._settings.user_agent}
        try:
            resp = self._http.get(url, params=dict(params), headers=headers)
        except httpx.TransportError as exc:
            raise _Retryable(f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _Retryable(f"HTTP {resp.status_code}")
        return resp


class StockTwitsProvider(_JsonSource):
    """`SocialProvider` for the StockTwits symbol stream (`{stocktwits_url}/{SYMBOL}.json`).

    Pages back with `max=<cursor.max>` until a page reaches before `start`, the stream has no
    more, or `max_items` posts were collected.
    """

    name = "stocktwits"

    def __init__(
        self,
        config: SocialConfig | None = None,
        *,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """A provider using `config` (`cfg.data.sentiment.social`)."""
        super().__init__(config, http, sleep)

    def fetch_posts(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """See `SocialProvider.fetch_posts`."""
        canonical = _check(symbol, start, end)
        url = f"{self._settings.stocktwits_url}/{canonical}.json"
        rows: list[dict[str, Any]] = []
        params: dict[str, str] = {}
        while True:
            body = self._get(url, params)
            messages, cursor = body.get("messages"), body.get("cursor")
            if not isinstance(messages, list):
                raise DataError(f"stocktwits {canonical}: no messages list")
            page = [r for m in messages if (r := self._row(m, canonical)) is not None]
            rows.extend(page)
            oldest = min((r["created_at"] for r in page), default=None)
            more = isinstance(cursor, Mapping) and bool(cursor.get("more"))
            if not page or not more or len(rows) >= self._settings.max_items:
                break
            if oldest is not None and oldest < start:
                break
            params = {"max": str(cursor["max"])}  # type: ignore[index]
        return _frame(rows, start, end)

    @staticmethod
    def _row(message: Any, symbol: str) -> dict[str, Any] | None:
        if not isinstance(message, Mapping) or not message.get("id") or not message.get("body"):
            _log.debug("sentiment.bad_post", provider="stocktwits")
            return None
        try:
            created = datetime.fromisoformat(str(message["created_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            _log.debug("sentiment.bad_post", provider="stocktwits")
            return None
        return {
            "post_id": f"stocktwits-{message['id']}",
            "symbol": symbol,
            "created_at": created,
            "source": "stocktwits",
            "text": str(message["body"]),
            "url": f"https://stocktwits.com/message/{message['id']}",
        }


class RedditProvider(_JsonSource):
    """`SocialProvider` searching each of `reddit_subreddits` for the symbol (newest first, at
    most `max_items` per subreddit, past month), through Reddit's public JSON search."""

    name = "reddit"

    def __init__(
        self,
        config: SocialConfig | None = None,
        *,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """A provider using `config` (`cfg.data.sentiment.social`)."""
        super().__init__(config, http, sleep)

    def fetch_posts(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """See `SocialProvider.fetch_posts`."""
        canonical = _check(symbol, start, end)
        rows: list[dict[str, Any]] = []
        for sub in self._settings.reddit_subreddits:
            body = self._get(
                f"{self._settings.reddit_url}/r/{sub}/search.json",
                {
                    "q": canonical,
                    "restrict_sr": "1",
                    "sort": "new",
                    "limit": str(self._settings.max_items),
                    "t": "month",
                },
            )
            data = body.get("data")
            children = data.get("children") if isinstance(data, Mapping) else None
            if not isinstance(children, list):
                raise DataError(f"reddit r/{sub}: no data.children list")
            rows.extend(r for c in children if (r := self._row(c, canonical, sub)) is not None)
        return _frame(rows, start, end)

    def _row(self, child: Any, symbol: str, sub: str) -> dict[str, Any] | None:
        post = child.get("data") if isinstance(child, Mapping) else None
        if not isinstance(post, Mapping) or not post.get("id") or not post.get("title"):
            _log.debug("sentiment.bad_post", provider="reddit")
            return None
        stamp = post.get("created_utc")
        if not isinstance(stamp, int | float):
            _log.debug("sentiment.bad_post", provider="reddit")
            return None
        body = str(post.get("selftext") or "").strip()
        title = str(post["title"]).strip()
        permalink = post.get("permalink")
        return {
            "post_id": f"reddit-{post['id']}",
            "symbol": symbol,
            "created_at": datetime.fromtimestamp(stamp, UTC),
            "source": f"reddit/r/{sub}",
            "text": f"{title}\n\n{body}" if body else title,
            "url": f"{self._settings.reddit_url}{permalink}" if permalink else None,
        }


def build_social_providers(config: AtlasConfig) -> tuple[SocialProvider, ...]:
    """The configured social providers, in order; `()` unless
    `data.sentiment.social.enabled`."""
    settings = config.data.sentiment.social
    if not settings.enabled:
        return ()
    makers: dict[str, Callable[[SocialConfig], SocialProvider]] = {
        "stocktwits": lambda s: StockTwitsProvider(s),
        "reddit": lambda s: RedditProvider(s),
    }
    return tuple(makers[name](settings) for name in settings.providers)


def social_evidence(
    posts: pd.DataFrame, as_of: AsOf, *, lookback_days: int, limit: int
) -> list[Evidence]:
    """The posts known at `as_of` from `lookback_days` calendar days before its cutoff, newest
    first, at most `limit`, as `Evidence` (`id = "social:<post_id>"`, `as_of` the post time)."""
    known = pit_filter(posts, as_of)
    assert_no_lookahead(known, as_of)
    start = pd.Timestamp(as_of.cutoff - timedelta(days=lookback_days))
    recent = known.loc[known[AVAILABLE_AT] >= start].sort_values(
        [AVAILABLE_AT, "post_id"], ascending=[False, True], kind="stable"
    )
    return [
        Evidence(
            id=f"{EVIDENCE_PREFIX}{row['post_id']}",
            source=str(row["source"]),
            as_of=pd.Timestamp(row[AVAILABLE_AT]).to_pydatetime(),
            text=str(row["text"]),
            url=row["url"] if isinstance(row["url"], str) and row["url"] else None,
        )
        for row in recent.head(limit).to_dict("records")
    ]
