"""Point-in-time news queries: what an agent could read at `as_of`, as citable `Evidence`.

An article is known at `as_of` iff its `available_at` (its publish time) is strictly before
`as_of.cutoff` (`pit_filter`, checked again with `assert_no_lookahead`). For one ticker
(`news_pit_detail`): the known articles tagged with it, roundups dropped (`score_relevance`),
re-published copies dropped (`mark_duplicates`, over the window plus `dedup_window_hours` of
earlier context, so a copy of an article just before the window stays hidden), then the
articles from `cutoff - lookback_days` on, most relevant first, then newest, then by
`article_id`, at most `limit`. `news_pit` returns them as `Evidence` with
`id = "news:<article_id>"`, which depends only on the vendor's id and so is stable across
re-ingests. `market_news_pit` does the same for market-wide news (untagged, or tagged with one
of `market_symbols`), newest first, without relevance scoring.
"""

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from atlas.core.config import AtlasConfig, NewsConfig
from atlas.core.errors import DataError
from atlas.core.pit import AsOf, assert_no_lookahead, pit_filter
from atlas.core.storage import Lake
from atlas.core.types.research import Evidence
from atlas.data.news.base import ARTICLE_COLUMNS, NEWS
from atlas.data.news.dedup import DUPLICATE_OF, mark_duplicates
from atlas.data.news.relevance import score_relevance

DETAIL_COLUMNS = (*ARTICLE_COLUMNS, "relevance", "headline_match")
"""Columns of `news_pit_detail`: the article columns plus the relevance score and whether the
headline names the ticker."""

EVIDENCE_PREFIX = "news:"
"""`Evidence.id` of an article is this prefix plus its `article_id`."""


def _check_window(lookback_days: int, limit: int) -> None:
    for name, value in (("lookback_days", lookback_days), ("limit", limit)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer, got {value!r}")


def _known(articles: pd.DataFrame, as_of: AsOf) -> pd.DataFrame:
    """Rows known at `as_of`, one per `article_id` (the earliest known, should a vendor have
    re-dated an article), so an evidence id is never cited twice."""
    known = pit_filter(articles, as_of)
    assert_no_lookahead(known, as_of)
    first = known.sort_values(["available_at", "article_id"], kind="stable")
    return first.drop_duplicates(subset=["article_id"], keep="first")


def _originals(
    candidates: pd.DataFrame, as_of: AsOf, lookback_days: int, cfg: NewsConfig
) -> pd.DataFrame:
    """Candidates published in the lookback window that are not copies of an earlier story."""
    start = pd.Timestamp(as_of.cutoff) - pd.Timedelta(days=lookback_days)
    context = candidates.loc[
        candidates["available_at"] >= start - pd.Timedelta(hours=cfg.dedup_window_hours)
    ]
    marked = mark_duplicates(context, cfg)
    keep = marked[DUPLICATE_OF].isna() & (marked["available_at"] >= start)
    return marked.loc[keep].drop(columns=DUPLICATE_OF)


def news_pit_detail(
    articles: pd.DataFrame,
    symbol: str,
    as_of: AsOf,
    *,
    lookback_days: int,
    limit: int,
    settings: NewsConfig | None = None,
) -> pd.DataFrame:
    """The rows behind `news_pit`, with columns `DETAIL_COLUMNS` (see the module docstring).

    `lookback_days` is in calendar days before `as_of.cutoff` (an article exactly that long
    before it is included); `limit` is the maximum number of articles; both must be positive
    integers (`ValueError`). `relevance` is `headline_relevance` or `symbols_relevance`.
    """
    cfg = settings if settings is not None else NewsConfig()
    _check_window(lookback_days, limit)
    scored = score_relevance(_known(articles, as_of), symbol, cfg)
    chosen = _originals(scored, as_of, lookback_days, cfg)
    chosen = chosen.sort_values(
        ["relevance", "available_at", "article_id"], ascending=[False, False, True], kind="stable"
    )
    return chosen.head(limit)[list(DETAIL_COLUMNS)].reset_index(drop=True)


def news_pit(
    articles: pd.DataFrame,
    symbol: str,
    as_of: AsOf,
    *,
    lookback_days: int,
    limit: int,
    settings: NewsConfig | None = None,
) -> list[Evidence]:
    """News about `symbol` known at `as_of`, as `Evidence` (see the module docstring);
    `lookback_days` in calendar days before the cutoff, at most `limit` articles."""
    detail = news_pit_detail(
        articles, symbol, as_of, lookback_days=lookback_days, limit=limit, settings=settings
    )
    return [to_evidence(row) for row in detail.to_dict("records")]


def market_news_pit(
    articles: pd.DataFrame,
    as_of: AsOf,
    *,
    lookback_days: int,
    limit: int,
    settings: NewsConfig | None = None,
) -> list[Evidence]:
    """Market-wide news known at `as_of` (untagged, or tagged with a `market_symbols` ETF),
    copies dropped, newest first, at most `limit`, as `Evidence`; `lookback_days` in calendar
    days before the cutoff."""
    cfg = settings if settings is not None else NewsConfig()
    _check_window(lookback_days, limit)
    known = _known(articles, as_of)
    market = set(cfg.market_symbols)
    wide = known["symbols"].map(lambda tags: not tags or bool(market & set(tags))).astype(bool)
    chosen = _originals(known.loc[wide], as_of, lookback_days, cfg)
    chosen = chosen.sort_values(
        ["available_at", "article_id"], ascending=[False, True], kind="stable"
    )
    return [to_evidence(row) for row in chosen.head(limit).to_dict("records")]


def to_evidence(row: Mapping[Any, Any]) -> Evidence:
    """One article row as `Evidence`: `id = "news:<article_id>"`, `source` the publisher,
    `as_of` its `available_at` (UTC), `text` the headline plus a blank line and the summary
    (the headline alone when there is no summary), `url` if known."""
    summary = row["summary"]
    headline = str(row["headline"])
    text = headline if not isinstance(summary, str) or not summary else f"{headline}\n\n{summary}"
    url = row["url"]
    return Evidence(
        id=f"{EVIDENCE_PREFIX}{row['article_id']}",
        source=str(row["source"]),
        as_of=pd.Timestamp(row["available_at"]).to_pydatetime(),
        text=text,
        url=url if isinstance(url, str) and url else None,
    )


class News:
    """Point-in-time news read from the lake's `news` table.

    `config` defaults to `AtlasConfig()` (pass the run's loaded config: its calendar sets the
    decision time of a plain `date` as-of; `data.news` sets the defaults and thresholds).
    """

    def __init__(self, lake: Lake, config: AtlasConfig | None = None) -> None:
        self._lake = lake
        self._cfg = config if config is not None else AtlasConfig()
        self._settings = self._cfg.data.news

    def _as_of(self, as_of: date | AsOf) -> AsOf:
        if isinstance(as_of, AsOf):
            return as_of
        if isinstance(as_of, datetime):
            raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
        return AsOf.for_session(as_of, config=self._cfg.calendar)

    def articles(self, start: datetime, end: datetime) -> pd.DataFrame:
        """Stored articles with `available_at` in `[start, end)` (aware datetimes), in table
        order. Raises `DataError` when the table has never been written (run `ingest_news`)."""
        if not self._lake.has_table(NEWS.name):
            raise DataError("news is empty: run ingest_news first")
        return self._lake.read_table(
            NEWS.name, filters=[("available_at", ">=", start), ("available_at", "<", end)]
        )

    def _window(self, point: AsOf, lookback_days: int) -> pd.DataFrame:
        span = timedelta(days=lookback_days, hours=self._settings.dedup_window_hours)
        return self.articles(point.cutoff - span, point.cutoff)

    def _defaults(self, lookback_days: int | None, limit: int | None) -> tuple[int, int]:
        lookback = self._settings.lookback_days if lookback_days is None else lookback_days
        count = self._settings.limit if limit is None else limit
        _check_window(lookback, count)
        return lookback, count

    def pit_detail(
        self,
        symbol: str,
        as_of: date | AsOf,
        lookback_days: int | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """`news_pit_detail` of `symbol` at `as_of`; `lookback_days` (calendar days) and
        `limit` default to `data.news.lookback_days` (14) and `data.news.limit` (50)."""
        point = self._as_of(as_of)
        lookback, count = self._defaults(lookback_days, limit)
        return news_pit_detail(
            self._window(point, lookback),
            symbol,
            point,
            lookback_days=lookback,
            limit=count,
            settings=self._settings,
        )

    def pit(
        self,
        symbol: str,
        as_of: date | AsOf,
        lookback_days: int | None = None,
        limit: int | None = None,
    ) -> list[Evidence]:
        """`news_pit`: news about `symbol` known at `as_of`, as `Evidence`."""
        detail = self.pit_detail(symbol, as_of, lookback_days, limit)
        return [to_evidence(row) for row in detail.to_dict("records")]

    def market(
        self, as_of: date | AsOf, lookback_days: int | None = None, limit: int | None = None
    ) -> list[Evidence]:
        """`market_news_pit`: market-wide news known at `as_of`, for the macro agent."""
        point = self._as_of(as_of)
        lookback, count = self._defaults(lookback_days, limit)
        return market_news_pit(
            self._window(point, lookback),
            point,
            lookback_days=lookback,
            limit=count,
            settings=self._settings,
        )
