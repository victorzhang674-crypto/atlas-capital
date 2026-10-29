"""Per-symbol news sentiment: a relevance- and age-weighted mean of article scores.

At a cutoff `c`, article *i* (known, about the symbol, not a copy of an earlier story, not a
roundup: Stage 11's `news_pit_detail`) has weight `relevance_i * 0.5 ** (age_i /
half_life_days)`, `age_i = c - available_at_i` in days. The score is the weighted mean of the
article scores (`score_text`, in [-1, 1]); with no article it is NaN (unknown, never 0). Sums
use `math.fsum`, so the result does not depend on row order. The daily series evaluates this
at each session's decision cutoff.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.pit import AsOf, assert_no_lookahead
from atlas.core.storage import Lake
from atlas.data.news import News, news_pit_detail
from atlas.data.sentiment.lexicon import Lexicon, load_lexicon, score_articles

_DAY_SECONDS = 86_400.0


@dataclass(frozen=True)
class SentimentAggregate:
    """A symbol's sentiment at one cutoff: `score` in [-1, 1] (NaN when no article), the
    number of `articles` used and their total `weight`."""

    score: float
    articles: int
    weight: float


def decayed_sentiment(
    detail: pd.DataFrame, as_of: AsOf, *, half_life_days: float
) -> SentimentAggregate:
    """The weighted mean score of `detail` (columns `available_at`, `relevance`, `score`)
    at `as_of` (see the module docstring); `half_life_days` in days. A row not known at
    `as_of` raises `LookAheadError`."""
    assert_no_lookahead(detail, as_of)
    if detail.empty:
        return SentimentAggregate(float("nan"), 0, 0.0)
    cutoff = pd.Timestamp(as_of.cutoff)
    ages = [(cutoff - t).total_seconds() / _DAY_SECONDS for t in detail["available_at"]]
    weights = [
        float(r) * 0.5 ** (age / half_life_days)
        for r, age in zip(detail["relevance"], ages, strict=True)
    ]
    total = math.fsum(weights)
    weighted = math.fsum(w * float(s) for w, s in zip(weights, detail["score"], strict=True))
    return SentimentAggregate(weighted / total, len(detail), total)


class Sentiment:
    """Point-in-time lexicon sentiment of a symbol, from the lake's news.

    `config` defaults to `AtlasConfig()` (its calendar sets the decision time of a plain
    `date` as-of; `data.sentiment` the half-life, lookback and limits; `data.news` the
    de-duplication). `lexicon` defaults to `load_lexicon(data.sentiment.lexicon_file)`, loaded
    on first use. A `news` table that was never written raises `DataError`.
    """

    def __init__(
        self, lake: Lake, config: AtlasConfig | None = None, lexicon: Lexicon | None = None
    ) -> None:
        self._lake = lake
        self._cfg = config if config is not None else AtlasConfig()
        self._settings = self._cfg.data.sentiment
        self._lexicon = lexicon

    @property
    def lexicon(self) -> Lexicon:
        """The lexicon used for scoring."""
        if self._lexicon is None:
            self._lexicon = load_lexicon(self._settings.lexicon_file)
        return self._lexicon

    def _as_of(self, as_of: date | AsOf) -> AsOf:
        if isinstance(as_of, AsOf):
            return as_of
        if isinstance(as_of, datetime):
            raise TypeError("pass a trading day (date) or an AsOf, not a datetime")
        return AsOf.for_session(as_of, config=self._cfg.calendar)

    def _scored(self, start: datetime, end: datetime) -> tuple[pd.DataFrame, dict[str, float]]:
        frame = News(self._lake, self._cfg).articles(start, end)
        scores = score_articles(frame, self.lexicon, self._settings)
        return frame, dict(zip(scores["article_id"], scores["score"], strict=True))

    def _aggregate(
        self, frame: pd.DataFrame, scores: dict[str, float], symbol: str, point: AsOf
    ) -> SentimentAggregate:
        chosen = news_pit_detail(
            frame,
            symbol,
            point,
            lookback_days=self._settings.lookback_days,
            limit=self._settings.max_articles,
            settings=self._cfg.data.news,
        )
        chosen = chosen.assign(score=[scores[a] for a in chosen["article_id"]])
        return decayed_sentiment(chosen, point, half_life_days=self._settings.half_life_days)

    def _span(self) -> timedelta:
        return timedelta(
            days=self._settings.lookback_days, hours=self._cfg.data.news.dedup_window_hours
        )

    def at(self, symbol: str, as_of: date | AsOf) -> SentimentAggregate:
        """The sentiment of `symbol` at `as_of` (a `date` at the configured decision time, or
        an `AsOf`)."""
        point = self._as_of(as_of)
        frame, scores = self._scored(point.cutoff - self._span(), point.cutoff)
        return self._aggregate(frame, scores, symbol, point)

    def daily(self, symbol: str, as_of: date | AsOf, days: int | None = None) -> pd.DataFrame:
        """The sentiment of `symbol` at the decision cutoff of each of the last `days` sessions
        up to `as_of` (default `data.sentiment.daily_days`; the last row uses `as_of` itself
        when it is a session): columns `date`, `score` (NaN without articles), `articles`.
        The news window is read once."""
        count = self._settings.daily_days if days is None else days
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(f"days must be a positive integer, got {count!r}")
        point = self._as_of(as_of)
        cal = TradingCalendar(config=self._cfg.calendar)
        last = point.day if cal.is_session(point.day) else cal.prev_session(point.day)
        sessions = cal.sessions(cal.offset(last, -(count - 1)), last)
        points = [
            point if d == point.day else AsOf.for_session(d, config=self._cfg.calendar)
            for d in sessions
        ]
        frame, scores = self._scored(points[0].cutoff - self._span(), points[-1].cutoff)
        values = [self._aggregate(frame, scores, symbol, p) for p in points]
        return pd.DataFrame(
            {
                "date": pd.Series(list(sessions), dtype="object"),
                "score": pd.Series([v.score for v in values], dtype="float64"),
                "articles": pd.Series([v.articles for v in values], dtype="int64"),
            }
        )
