"""Sync datasets (Stage 14): the dataset names and their steps, the sources a sync fetches
from (`SyncSources`), the run report and the decision-day rules."""

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from atlas.core.calendar import TradingCalendar, market_date
from atlas.core.clock import get_clock
from atlas.core.config import AtlasConfig
from atlas.core.errors import ConfigError
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf, bar_available_at
from atlas.core.storage import Lake
from atlas.core.types.base import FrozenDict
from atlas.data.archive import ArchivingNewsProvider, ArchivingPriceProvider
from atlas.data.events import (
    EarningsCalendarProvider,
    build_upcoming_provider,
)
from atlas.data.fundamentals import (
    CikMap,
    EdgarClient,
    build_cik_map,
    build_edgar_client,
)
from atlas.data.macro import FredClient, build_fred_client
from atlas.data.news import NewsProvider, build_news_provider
from atlas.data.news.alpaca_news import AlpacaNewsProvider
from atlas.data.providers import PriceProvider
from atlas.data.providers.fallback import build_price_provider
from atlas.data.reference import fetch_sp500_pages
from atlas.data.sentiment import (
    Lexicon,
    SocialProvider,
    build_social_providers,
    download_lm_lexicon,
    load_lexicon,
)
from atlas.data.sync.engine import StepReport

_log = get_logger(__name__)

# --- datasets --------------------------------------------------------------------------------

DATASETS: tuple[str, ...] = (
    "universe",
    "prices",
    "fundamentals",
    "macro",
    "news",
    "events",
    "sentiment",
    "quality",
)
"""Dataset names accepted by `run_sync` (and `--datasets`), in the order they run."""

DATASET_STEPS: Mapping[str, tuple[str, ...]] = FrozenDict(
    {
        "universe": ("universe",),
        "prices": ("prices",),
        "fundamentals": ("fundamentals",),
        "macro": ("macro",),
        "news": ("news",),
        "events": ("earnings", "upcoming", "macro_calendar", "reactions"),
        "sentiment": ("news_sentiment", "social"),
        "quality": ("quality",),
    }
)
"""The steps of each dataset, in run order (step names key the checkpoint)."""

ALL_KEY = "_all"
"""Pseudo-key of steps that cover the whole lake (reactions, news scoring)."""


@dataclass(frozen=True)
class SyncSources:
    """Lazy factories for everything a sync fetches from; each is called at most once per run,
    and only when a step that needs it runs (a missing API key then fails that step only).

    `cik_map(client)` builds the ticker → CIK map from the EDGAR client; `calendar_fred()`
    may return `None` (the macro calendar then keeps FOMC dates only); `upcoming_provider()`
    may return `None` (no scheduled-dates provider configured); `social_providers()` returns
    `()` when social posts are disabled; `universe_pages()` returns the constituents and
    change-log HTML; `lexicon()` returns the loaded Loughran-McDonald lexicon.
    """

    price_provider: Callable[[], PriceProvider]
    edgar: Callable[[], EdgarClient]
    cik_map: Callable[[EdgarClient], CikMap]
    fred: Callable[[], FredClient]
    calendar_fred: Callable[[], FredClient | None]
    news_provider: Callable[[], NewsProvider]
    upcoming_provider: Callable[[], EarningsCalendarProvider | None]
    social_providers: Callable[[], tuple[SocialProvider, ...]]
    universe_pages: Callable[[], tuple[str, str]]
    lexicon: Callable[[], Lexicon]

    @classmethod
    def live(cls, config: AtlasConfig, lake: Lake) -> "SyncSources":
        """The real vendors of the Stage 07-12 packages for `config` (secrets only from
        `config.secrets`; EDGAR and FRED answers archived under `lake`'s `raw/`, and price and
        news answers too when `data.sync.archive_raw` is on).

        News uses Alpaca alone when its keys are set: during a backfill a failed Alpaca
        request must stay failed (and be retried), not be "covered" by Yahoo's recent-only
        news. Without the keys it is Yahoo (recent news only, with a warning).
        """

        archive = config.data.sync.archive_raw

        def news() -> NewsProvider:
            alpaca = AlpacaNewsProvider.from_config(config)
            if alpaca is not None and config.data.providers.news == "alpaca":
                chosen: NewsProvider = alpaca
            else:
                chosen = build_news_provider(config)
            return ArchivingNewsProvider(chosen, lake.raw_dir) if archive else chosen

        def prices() -> PriceProvider:
            chosen = build_price_provider(config)
            return ArchivingPriceProvider(chosen, lake.raw_dir) if archive else chosen

        def calendar_fred() -> FredClient | None:
            try:
                return build_fred_client(config, lake)
            except ConfigError as exc:
                _log.warning("sync.calendar_without_fred", reason=str(exc))
                return None

        def lexicon() -> Lexicon:
            path = config.data.sentiment.lexicon_file
            if not path.exists():
                download_lm_lexicon(config.data.sentiment)
            return load_lexicon(path)

        return cls(
            price_provider=prices,
            edgar=lambda: build_edgar_client(config, lake),
            cik_map=lambda client: build_cik_map(client, config),
            fred=lambda: build_fred_client(config, lake),
            calendar_fred=calendar_fred,
            news_provider=news,
            upcoming_provider=lambda: build_upcoming_provider(config),
            social_providers=lambda: build_social_providers(config),
            universe_pages=lambda: fetch_sp500_pages(config.data.reference),
            lexicon=lexicon,
        )


class _Resources:
    """Builds each source once per run (thread-safe) and closes what has a `close()`."""

    def __init__(self, sources: SyncSources) -> None:
        self._sources = sources
        self._made: dict[str, Any] = {}
        self._lock = threading.RLock()

    def get(self, name: str, *args: Any) -> Any:
        with self._lock:
            if name not in self._made:
                self._made[name] = getattr(self._sources, name)(*args)
            return self._made[name]

    def close(self) -> None:
        with self._lock:
            made = list(self._made.values())
            self._made.clear()
        for thing in made:
            items = thing if isinstance(thing, tuple) else (thing,)
            for item in items:
                close = getattr(item, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception as exc:  # closing must not hide the run's outcome
                        _log.warning(
                            "sync.close_failed", source=type(item).__name__, error=str(exc)
                        )


@dataclass(frozen=True)
class SyncReport:
    """Outcome of `run_sync`: the window (`start`..`end`, inclusive), one `StepReport` per
    step run, in order, and the checkpoint file. `ok` is `False` when a step could not run."""

    command: str
    start: date
    end: date
    steps: tuple[StepReport, ...]
    state_path: Path

    @property
    def ok(self) -> bool:
        """`True` when every step ran (keys may still have failed; see each report)."""
        return all(step.error is None for step in self.steps)

    @property
    def errors(self) -> tuple[StepReport, ...]:
        """The steps that could not run."""
        return tuple(step for step in self.steps if step.error is not None)


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _decision_after(calendar: TradingCalendar, config: AtlasConfig, now: datetime) -> date:
    day = market_date(now)
    if not calendar.is_session(day):
        return calendar.next_session(day)
    if AsOf.for_session(day, config=config.calendar).cutoff > now:
        return day
    return calendar.next_session(day)


def next_decision_day(config: AtlasConfig, now: datetime | None = None) -> date:
    """The first session whose decision cutoff (`calendar.decision_time`, New York) is after
    `now` (aware; default `get_clock().now()`): the day the next decision is made for."""
    instant = now if now is not None else get_clock().now()
    return _decision_after(TradingCalendar(config=config.calendar), config, instant)


def last_published_session(
    calendar: TradingCalendar, day: date, now: datetime, *, floor: date
) -> date | None:
    """The latest session on or before `day` whose daily bar is published at `now` (close +
    `calendar.bar_publish_lag_minutes`), not before `floor`; `None` when there is none."""
    current = day if calendar.is_session(day) else calendar.prev_session(day)
    while current >= floor:
        if bar_available_at(current, calendar) <= now:
            return current
        current = calendar.prev_session(current)
    return None


def parse_datasets(names: Iterable[str] | None) -> tuple[str, ...]:
    """The requested datasets in run order (all when `None`); an unknown name raises
    `ValueError` listing the known ones."""
    if names is None:
        return DATASETS
    wanted = [n.strip().lower() for n in names if n.strip()]
    unknown = sorted(set(wanted) - set(DATASETS))
    if unknown:
        raise ValueError(f"unknown datasets {unknown}; choose from {', '.join(DATASETS)}")
    if not wanted:
        raise ValueError(f"no datasets given; choose from {', '.join(DATASETS)}")
    return tuple(d for d in DATASETS if d in set(wanted))


def state_path(lake: Lake, config: AtlasConfig) -> Path:
    """The checkpoint file of `lake`: `<lake root>/<data.sync.state_file>`."""
    return lake.root / config.data.sync.state_file
