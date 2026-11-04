"""Sync steps (Stage 14): one builder per step, turning a run (`_Run`) into the `StepSpec`
that calls the Stage 07-13 ingest function for a chunk of keys."""

import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta

from atlas.core.calendar import TradingCalendar, market_date
from atlas.core.clock import get_clock
from atlas.core.config import AtlasConfig, SyncConfig
from atlas.core.errors import ConfigError, DataError
from atlas.core.logging import get_logger
from atlas.core.storage import Lake
from atlas.core.types.base import FrozenDict
from atlas.core.types.instruments import normalize_symbol
from atlas.data.events import (
    ingest_earnings,
    ingest_earnings_reactions,
    ingest_macro_calendar,
    ingest_upcoming_events,
)
from atlas.data.fundamentals import (
    CikMap,
    ConceptMap,
    EdgarClient,
    ingest_fundamentals,
    load_concept_map,
)
from atlas.data.macro import ingest_macro, load_macro_series
from atlas.data.news import NEWS, ingest_news
from atlas.data.prices import ingest_prices
from atlas.data.quality import (
    quality_report,
)
from atlas.data.sentiment import (
    ingest_news_sentiment,
    ingest_social_posts,
)
from atlas.data.sync.datasets import (
    ALL_KEY,
    SyncSources,
    _decision_after,
    _midnight,
    _Resources,
    last_published_session,
)
from atlas.data.sync.engine import ChunkOutcome, FetchFn, Provider, StepMode, StepSpec
from atlas.data.sync.state import SyncState
from atlas.data.universe import INDEX_MEMBERSHIP, refresh_universe_reference

_DAY = timedelta(days=1)

_log = get_logger(__name__)


class _Run:
    """Everything one `run_sync` call shares between its steps."""

    def __init__(
        self,
        lake: Lake,
        config: AtlasConfig,
        sources: SyncSources,
        state: SyncState,
        start: date,
        end: date,
        symbols: tuple[str, ...] | None,
    ) -> None:
        self.lake = lake
        self.config = config
        self.resources = _Resources(sources)
        self.state = state
        self.start = start
        self.end = end
        self.explicit = symbols
        self.calendar = TradingCalendar(config=config.calendar)
        self.pages: tuple[str, str] | None = None
        self.news_from: date | None = None
        self._lock = threading.Lock()
        self._universe: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._concept_map: ConceptMap | None = None

    @property
    def settings(self) -> SyncConfig:
        return self.config.data.sync

    def etfs(self) -> tuple[str, ...]:
        """Tickers of every ticker-list universe, in configuration order."""
        tickers = [
            normalize_symbol(t)
            for u in self.config.data.universes.values()
            if u.index is None
            for t in u.tickers
        ]
        return tuple(dict.fromkeys(tickers))

    def index_id(self) -> str:
        name = self.settings.universe
        universes = self.config.data.universes
        if name not in universes or universes[name].index is None:
            raise ConfigError(f"data.sync.universe {name!r} is not an index universe")
        index = universes[name].index
        assert index is not None
        return index

    def universe(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """`(symbols, equities)`: every symbol to sync, and those that file with the SEC."""
        if self._universe is None:
            etfs = self.etfs()
            if self.explicit is not None:
                symbols = self.explicit
            else:
                members = _members_between(self.lake, self.index_id(), self.start, self.end)
                symbols = tuple(dict.fromkeys([*members, *etfs]))
            equities = tuple(s for s in symbols if s not in set(etfs))
            self._universe = (symbols, equities)
        return self._universe

    def concept_map(self) -> ConceptMap:
        with self._lock:
            if self._concept_map is None:
                self._concept_map = load_concept_map(self.config.data.fundamentals.xbrl_map_file)
            return self._concept_map

    def note_news(self, first: date) -> None:
        with self._lock:
            self.news_from = first if self.news_from is None else min(self.news_from, first)


def _members_between(lake: Lake, index_id: str, start: date, end: date) -> tuple[str, ...]:
    """Sorted symbols that were in `index_id` on any day of `start..end`."""
    if not lake.has_table(INDEX_MEMBERSHIP.name):
        raise DataError(
            "no index_membership table: run the universe dataset first (atlas data sync "
            "--datasets universe) or pass --symbols"
        )
    rows = lake.read_table(INDEX_MEMBERSHIP.name, filters=[("index_id", "=", index_id)])
    if rows.empty:
        raise DataError(f"no {index_id} rows in index_membership: run the universe dataset")
    ends = rows["end_date"]
    open_ended = ends.isna()
    alive = open_ended | (ends.where(~open_ended, date.max) > start)
    began = rows["start_date"] <= end
    return tuple(sorted(set(rows.loc[alive & began, "symbol"])))


def _classify(
    keys: Sequence[str],
    ok: Sequence[str],
    reasons: Mapping[str, str],
    empty_if: Callable[[str], bool],
) -> ChunkOutcome:
    """Split a Stage 07-12 ingest report into a `ChunkOutcome`: failures whose reason means
    "the vendor has nothing" are `empty`."""
    empty = tuple(k for k in keys if k in reasons and empty_if(reasons[k]))
    failed = {k: reasons[k] for k in keys if k in reasons and k not in set(empty)}
    return ChunkOutcome(ok=tuple(ok), empty=empty, failed=failed)


def _no_sec_data(reason: str) -> bool:
    return reason.startswith("no CIK") or reason == "no mapped facts in companyfacts"


def _step(
    run: _Run,
    name: str,
    dataset: str,
    provider: Provider,
    mode: StepMode,
    keys: tuple[str, ...],
    fetch: FetchFn,
    *,
    chunk_size: int = 1,
    start: date | None = None,
    end: date | None = None,
    window_days: int | None = None,
) -> StepSpec:
    return StepSpec(
        name=name,
        dataset=dataset,
        provider=provider,
        mode=mode,
        keys=keys,
        start=run.start if start is None else start,
        end=run.end if end is None else end,
        chunk_size=chunk_size,
        fetch=fetch,
        window_days=window_days,
        overlap_days=run.settings.update_overlap_days if mode == "window" else 0,
        empty_retry_days=run.settings.empty_retry_days if mode == "window" else None,
    )


def _universe_step(run: _Run) -> StepSpec:
    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        pages = run.resources.get("universe_pages")
        report = refresh_universe_reference(run.lake, *pages, config=run.config)
        run.pages = pages
        return ChunkOutcome(rows=report.instruments + report.intervals, ok=keys)

    return _step(run, "universe", "universe", "wikipedia", "snapshot", (run.index_id(),), fetch)


def _prices_step(run: _Run) -> StepSpec:
    symbols, _ = run.universe()
    provider = run.resources.get("price_provider")
    now = get_clock().now()
    last = last_published_session(run.calendar, run.end, now, floor=run.start)
    pool: Provider = "alpaca" if run.config.data.providers.prices == "alpaca" else "yahoo"

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_prices(
            run.lake,
            provider,
            keys,
            start,
            end,
            calendar=run.calendar,
            config=run.config.data.prices,
        )
        outcome = _classify(
            keys, report.symbols_ok, report.failure_reasons, lambda r: r == "no price data returned"
        )
        return ChunkOutcome(
            rows=report.rows + report.action_rows,
            ok=outcome.ok,
            empty=outcome.empty,
            failed=outcome.failed,
        )

    return _step(
        run,
        "prices",
        "prices",
        pool,
        "window",
        symbols,
        fetch,
        chunk_size=run.settings.chunk_size.prices,
        end=last if last is not None else run.start - _DAY,
    )


def _edgar(run: _Run) -> tuple[EdgarClient, CikMap]:
    client = run.resources.get("edgar")
    return client, run.resources.get("cik_map", client)


def _fundamentals_step(run: _Run) -> StepSpec:
    _, equities = run.universe()
    client, cik_map = _edgar(run)
    concept_map = run.concept_map()

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_fundamentals(
            run.lake,
            client,
            keys,
            cik_map=cik_map,
            config=run.config,
            calendar=run.calendar,
            concept_map=concept_map,
        )
        outcome = _classify(keys, report.symbols_ok, report.failure_reasons, _no_sec_data)
        return ChunkOutcome(report.rows, outcome.ok, outcome.empty, outcome.failed)

    return _step(
        run,
        "fundamentals",
        "fundamentals",
        "sec",
        "snapshot",
        equities,
        fetch,
        chunk_size=run.settings.chunk_size.fundamentals,
    )


def _macro_step(run: _Run) -> StepSpec:
    catalog = load_macro_series(run.config.data.macro.series_file)
    client = run.resources.get("fred")

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_macro(run.lake, client, keys, config=run.config, catalog=catalog)
        outcome = _classify(keys, report.series_ok, report.failure_reasons, lambda r: False)
        return ChunkOutcome(report.rows, outcome.ok, outcome.empty, outcome.failed)

    return _step(
        run,
        "macro",
        "macro",
        "fred",
        "snapshot",
        catalog.ids,
        fetch,
        chunk_size=run.settings.chunk_size.macro,
    )


def _news_step(run: _Run) -> StepSpec:
    symbols, _ = run.universe()
    provider = run.resources.get("news_provider")
    pool: Provider = "alpaca" if getattr(provider, "name", "") == "alpaca" else "yahoo"

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_news(run.lake, provider, keys, _midnight(start), _midnight(end + _DAY))
        if report.rows:
            run.note_news(start)
        return ChunkOutcome(rows=report.rows, ok=keys)

    return _step(
        run,
        "news",
        "news",
        pool,
        "window",
        symbols,
        fetch,
        chunk_size=run.settings.chunk_size.news,
        window_days=run.settings.news_window_days,
    )


def _earnings_step(run: _Run) -> StepSpec:
    _, equities = run.universe()
    client, cik_map = _edgar(run)

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_earnings(
            run.lake, client, keys, cik_map=cik_map, config=run.config, calendar=run.calendar
        )
        outcome = _classify(keys, report.symbols_ok, report.failure_reasons, _no_sec_data)
        return ChunkOutcome(report.rows, outcome.ok, outcome.empty, outcome.failed)

    return _step(
        run,
        "earnings",
        "events",
        "sec",
        "snapshot",
        equities,
        fetch,
        chunk_size=run.settings.chunk_size.earnings,
    )


def _upcoming_step(run: _Run) -> StepSpec | None:
    if run.end < get_clock().today():
        _log.info("sync.upcoming_skipped", reason="the run ends in the past; a snapshot is today's")
        return None
    provider = run.resources.get("upcoming_provider")
    if provider is None:
        _log.info("sync.upcoming_skipped", reason="data.events.upcoming_earnings is none")
        return None
    _, equities = run.universe()
    if run.explicit is None:  # only names trading now have scheduled dates
        current = set(_members_between(run.lake, run.index_id(), run.end, run.end))
        equities = tuple(s for s in equities if s in current)

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        try:
            return ChunkOutcome(rows=ingest_upcoming_events(run.lake, provider, keys), ok=keys)
        except DataError as exc:
            if len(keys) == 1:
                raise
            _log.warning("sync.upcoming_chunk_failed", keys=len(keys), error=str(exc))
        rows, ok, failed = 0, [], {}
        for key in keys:  # one bad ticker must not fail its whole chunk
            try:
                rows += ingest_upcoming_events(run.lake, provider, [key])
                ok.append(key)
            except DataError as exc:
                failed[key] = str(exc)
        return ChunkOutcome(rows=rows, ok=tuple(ok), failed=failed)

    return _step(
        run,
        "upcoming",
        "events",
        "yahoo",
        "snapshot",
        equities,
        fetch,
        chunk_size=run.settings.chunk_size.upcoming,
    )


def _macro_calendar_step(run: _Run) -> StepSpec:
    client = run.resources.get("calendar_fred")

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        rows = ingest_macro_calendar(run.lake, client, config=run.config)
        return ChunkOutcome(rows=rows, ok=keys)

    return _step(run, "macro_calendar", "events", "fred", "snapshot", ("_calendar",), fetch)


def _reactions_step(run: _Run) -> StepSpec:
    wanted = None if run.explicit is None else run.universe()[1]

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_earnings_reactions(
            run.lake, wanted, config=run.config, calendar=run.calendar
        )
        return ChunkOutcome(rows=report.rows, ok=keys)

    return _step(run, "reactions", "events", "local", "snapshot", (ALL_KEY,), fetch)


def _news_sentiment_step(run: _Run) -> StepSpec:
    if run.news_from is not None:  # news written this run: score it again from there
        run.state.truncate("news_sentiment", [ALL_KEY], run.news_from - _DAY)

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        if not run.lake.has_table(NEWS.name):
            return ChunkOutcome(empty=keys)
        lexicon = run.resources.get("lexicon")
        rows = ingest_news_sentiment(
            run.lake, lexicon, _midnight(start), _midnight(end + _DAY), config=run.config
        )
        return ChunkOutcome(rows=rows, ok=keys)

    return _step(
        run,
        "news_sentiment",
        "sentiment",
        "local",
        "window",
        (ALL_KEY,),
        fetch,
        window_days=run.settings.sentiment_window_days,
    )


def _social_step(run: _Run) -> StepSpec | None:
    providers = run.resources.get("social_providers")
    if not providers:
        _log.info("sync.social_skipped", reason="data.sentiment.social.enabled is false")
        return None
    symbols, _ = run.universe()

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = ingest_social_posts(
            run.lake, providers, keys, _midnight(start), _midnight(end + _DAY)
        )
        if len(report.providers_failed) == len(providers):
            reason = "; ".join(f"{k}: {v}" for k, v in report.failure_reasons.items())
            return ChunkOutcome(failed={k: reason for k in keys})
        return ChunkOutcome(rows=report.rows, ok=keys)

    return _step(
        run,
        "social",
        "sentiment",
        "local",
        "window",
        symbols,
        fetch,
        chunk_size=run.settings.chunk_size.social,
    )


def _quality_step(run: _Run) -> StepSpec:
    now = get_clock().now()
    if run.end >= market_date(now):
        day = _decision_after(run.calendar, run.config, now)
    else:
        day = run.calendar.next_session(run.end)

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        report = quality_report(
            run.lake,
            day,
            config=run.config,
            calendar=run.calendar,
            concept_map=run.concept_map(),
            persist=True,
        )
        return ChunkOutcome(rows=len(report), ok=keys)

    return _step(run, "quality", "quality", "local", "snapshot", ("_report",), fetch)


_BUILDERS: Mapping[str, Callable[[_Run], StepSpec | None]] = FrozenDict(
    {
        "universe": _universe_step,
        "prices": _prices_step,
        "fundamentals": _fundamentals_step,
        "macro": _macro_step,
        "news": _news_step,
        "earnings": _earnings_step,
        "upcoming": _upcoming_step,
        "macro_calendar": _macro_calendar_step,
        "reactions": _reactions_step,
        "news_sentiment": _news_sentiment_step,
        "social": _social_step,
        "quality": _quality_step,
    }
)
