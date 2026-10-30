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

The free APIs serve recent posts only, so history builds up from what is stored:
`ingest_social_posts` upserts fetched posts into the `social_posts` table (one row per symbol
and post), and `Social(lake).evidence(symbol, as_of)` / `social_evidence` turn them into
citable `Evidence` (`id = "social:<post_id>"`), point in time. Providers own their HTTP client
unless one is passed in: `close()` (or a `with` block) closes only their own. Requests send
`user_agent`, are throttled, and retry transport errors, HTTP 429 and 5xx; any other answer,
or a body that is not the documented JSON object, raises `DataError`.
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from types import TracebackType
from typing import Any, Final, Protocol, Self, runtime_checkable

import httpx
import pandas as pd

from atlas.core.config import AtlasConfig, SocialConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf, assert_no_lookahead, pit_filter
from atlas.core.storage import (
    AVAILABLE_AT,
    DEFAULT_REGISTRY,
    YEAR,
    Column,
    ColumnType,
    Lake,
    TableSchema,
)
from atlas.core.types.base import FrozenDict
from atlas.core.types.instruments import normalize_symbol
from atlas.core.types.research import Evidence
from atlas.data.providers.base import RetryPolicy, Throttle, call_with_retry

_log = get_logger(__name__)

POST_COLUMNS: Final = ("post_id", "symbol", "created_at", AVAILABLE_AT, "source", "text", "url")
"""Columns of a post frame, in order."""

EVIDENCE_PREFIX: Final = "social:"
"""`Evidence.id` of a post is this prefix plus its `post_id`."""

SOCIAL_POSTS = DEFAULT_REGISTRY.register(
    TableSchema(
        name="social_posts",
        columns=(
            Column(name="post_id", dtype=ColumnType.STRING),
            Column(name="symbol", dtype=ColumnType.STRING),
            Column(name="created_at", dtype=ColumnType.TIMESTAMP),
            Column(name=AVAILABLE_AT, dtype=ColumnType.TIMESTAMP),
            Column(name="source", dtype=ColumnType.STRING),
            Column(name="text", dtype=ColumnType.STRING),
            Column(name="url", dtype=ColumnType.STRING, nullable=True),
        ),
        primary_key=("symbol", "post_id", "created_at"),
        partition_by=(YEAR,),
        year_column="created_at",
        description="Optional StockTwits / Reddit posts per symbol (text as posted); Stage 12",
    )
)
"""The curated `social_posts` table (registered in `DEFAULT_REGISTRY` on import)."""


@dataclass(frozen=True)
class SocialIngestReport:
    """Outcome of `ingest_social_posts`: `rows` upserted; `providers_failed` (in order) and
    `failure_reasons` name the providers whose fetch failed (the others still ran)."""

    rows: int
    providers_failed: tuple[str, ...] = ()
    failure_reasons: FrozenDict[str, str] = field(default_factory=lambda: FrozenDict({}))


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
        self._owns_http = http is None
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

    def close(self) -> None:
        """Close the HTTP connection pool (only if this provider created it)."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

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


def ingest_social_posts(
    lake: Lake,
    providers: Sequence[SocialProvider],
    symbols: Sequence[str],
    start: datetime,
    end: datetime,
) -> SocialIngestReport:
    """Fetch the posts about `symbols` created in `[start, end)` (aware) from each provider and
    upsert them into `social_posts` (idempotent). A provider whose fetch raises `DataError` is
    recorded and skipped; the others still run. A bare string raises `ValueError`."""
    if isinstance(symbols, str):
        raise ValueError("symbols must be a sequence of tickers, not one string")
    wanted = tuple(dict.fromkeys(normalize_symbol(s) for s in symbols))
    frames: list[pd.DataFrame] = []
    reasons: dict[str, str] = {}
    for provider in providers:
        try:
            frames.extend(provider.fetch_posts(s, start, end) for s in wanted)
        except DataError as exc:
            reasons[provider.name] = str(exc)
            _log.warning("sentiment.provider_failed", provider=provider.name, error=str(exc))
    posts = [f for f in frames if not f.empty]
    rows = 0
    if posts:
        batch = pd.concat(posts, ignore_index=True)[list(POST_COLUMNS)]
        batch = batch.drop_duplicates(subset=["symbol", "post_id", "created_at"])
        lake.write_table(
            SOCIAL_POSTS.name, batch, mode="upsert", keys=list(SOCIAL_POSTS.primary_key)
        )
        rows = len(batch)
    _log.info("sentiment.ingest_social", rows=rows, providers_failed=len(reasons))
    return SocialIngestReport(rows, tuple(reasons), FrozenDict(reasons))


class Social:
    """Point-in-time social posts read from the lake's `social_posts` table (empty when the
    table was never written). `config` defaults to `AtlasConfig()` (its calendar sets the
    decision time of a plain `date` as-of; `data.sentiment.social` the query defaults)."""

    def __init__(self, lake: Lake, config: AtlasConfig | None = None) -> None:
        self._lake = lake
        self._cfg = config if config is not None else AtlasConfig()
        self._settings = self._cfg.data.sentiment.social

    def evidence(
        self,
        symbol: str,
        as_of: date | AsOf,
        lookback_days: int | None = None,
        limit: int | None = None,
    ) -> list[Evidence]:
        """`social_evidence` of the stored posts about `symbol` at `as_of` (a `date` at the
        configured decision time, or an `AsOf`); `lookback_days` (calendar days) and `limit`
        default to `data.sentiment.social.lookback_days` / `.limit`."""
        if isinstance(as_of, datetime):
            raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
        point = (
            as_of if isinstance(as_of, AsOf) else AsOf.for_session(as_of, config=self._cfg.calendar)
        )
        lookback = self._settings.lookback_days if lookback_days is None else lookback_days
        count = self._settings.limit if limit is None else limit
        if not self._lake.has_table(SOCIAL_POSTS.name):
            return []
        start = point.cutoff - timedelta(days=lookback)
        posts = self._lake.read_table(
            SOCIAL_POSTS.name,
            filters=[("symbol", "=", normalize_symbol(symbol)), (AVAILABLE_AT, ">=", start)],
        )
        return social_evidence(posts, point, lookback_days=lookback, limit=count)
