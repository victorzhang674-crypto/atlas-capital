"""Stage 14 Task 5: `run_sync` / `run_update` over the fake world (real ingest functions)."""

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import respx
from sync_helpers import (
    CHANGE_DAY,
    CURRENT,
    ETFS,
    LAST_BAR,
    MEMBERS,
    START,
    TODAY,
    World,
    WorldNews,
    WorldPrices,
    at,
    changes_page,
    constituents_page,
    sec_router,
    world_config,
)

from atlas.core.audit import AuditLog, EventType
from atlas.core.clock import SimClock, use_clock
from atlas.core.config import AtlasConfig
from atlas.core.errors import ConfigError, DataError
from atlas.core.run_context import run_context
from atlas.core.storage import Lake
from atlas.data.quality import DATA_QUALITY_ISSUES
from atlas.data.sync import (
    ALL_KEY,
    DATASETS,
    SyncReport,
    SyncState,
    parse_datasets,
    run_sync,
    run_update,
    state_path,
)

STEPS = (
    "universe",
    "prices",
    "fundamentals",
    "macro",
    "news",
    "earnings",
    "upcoming",
    "macro_calendar",
    "reactions",
    "news_sentiment",
    "quality",
)


@pytest.fixture(autouse=True)
def sec() -> Iterator[respx.MockRouter]:
    yield from sec_router()


@pytest.fixture(autouse=True)
def clock() -> Iterator[SimClock]:
    with use_clock(SimClock(start=TODAY)) as installed:
        assert isinstance(installed, SimClock)
        yield installed


@pytest.fixture
def cfg(tmp_path: Path) -> AtlasConfig:
    return world_config(tmp_path)


@pytest.fixture
def world(cfg: AtlasConfig) -> World:
    return World(lexicon_file=cfg.data.sentiment.lexicon_file)


def by_step(report: SyncReport) -> dict[str, object]:
    return {r.step: r for r in report.steps}


def symbols_of(lake: Lake, table: str, column: str = "symbol") -> set[str]:
    return set(lake.read_table(table)[column])


def test_sync_all_datasets_fills_every_table(
    tmp_lake: Lake, cfg: AtlasConfig, world: World
) -> None:
    report = run_sync(tmp_lake, cfg, world.sources(), start=START)
    assert report.ok, [(r.step, r.error) for r in report.errors]
    assert tuple(r.step for r in report.steps) == STEPS
    assert (report.start, report.end) == (START, TODAY)
    assert symbols_of(tmp_lake, "prices_daily") == {*MEMBERS, *ETFS}
    assert symbols_of(tmp_lake, "index_membership") == set(MEMBERS)
    assert {"AAPL", "MSFT", "NEW", "OLD", "SPY", "QQQ"} <= symbols_of(tmp_lake, "instruments")
    assert symbols_of(tmp_lake, "fundamentals_facts") == {"AAPL", "MSFT"}
    assert symbols_of(tmp_lake, "macro_observations", "series_id") == {"CPIAUCSL", "DGS10"}
    assert set(tmp_lake.read_table("news")["article_id"]) == {f"alpaca-{i}" for i in range(1, 6)}
    events = tmp_lake.read_table("events")
    assert set(events["symbol"]) == {"AAPL", "MSFT"}
    assert {"yahoo"} <= set(events["source"])  # the scheduled snapshot
    assert len(tmp_lake.read_table("macro_events")) > 0  # FOMC dates from the YAML
    assert len(tmp_lake.read_table("news_sentiment")) == 5
    assert tmp_lake.has_table("earnings_reactions")
    assert tmp_lake.has_table(DATA_QUALITY_ISSUES.name)
    steps = by_step(report)
    assert steps["fundamentals"].empty == 2  # type: ignore[attr-defined]  # NEW, OLD: no CIK
    assert steps["prices"].ok == 6  # type: ignore[attr-defined]


def test_state_file_written_under_lake(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    run_sync(tmp_lake, cfg, world.sources(), start=START, datasets=["universe", "prices"])
    path = state_path(tmp_lake, cfg)
    assert path == tmp_lake.root / "cache" / "sync_state.json"
    raw = json.loads(path.read_text())
    assert set(raw["steps"]) == {"universe", "prices"}
    assert raw["runs"][-1]["command"] == "sync"
    assert raw["runs"][-1]["datasets"] == ["universe", "prices"]
    assert raw["runs"][-1]["crashed"] is None


def test_symbols_are_members_in_window_plus_etfs(
    tmp_lake: Lake, cfg: AtlasConfig, world: World
) -> None:
    run_sync(tmp_lake, cfg, world.sources(), start=START, datasets=["universe", "prices"])
    asked = {s for syms, _, _ in world.prices.calls for s in syms}
    assert asked == {*MEMBERS, *ETFS}
    later = World(lexicon_file=world.lexicon_file)
    other = Lake(tmp_lake.root.parent / "lake2", registry=tmp_lake.registry)
    run_sync(other, cfg, later.sources(), start=date(2024, 3, 1), datasets=["universe", "prices"])
    assert {s for syms, _, _ in later.prices.calls for s in syms} == {"AAPL", "MSFT", "NEW", *ETFS}


def test_explicit_symbols_replace_the_list(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    report = run_sync(
        tmp_lake,
        cfg,
        world.sources(),
        start=START,
        datasets=["prices", "fundamentals", "events"],
        symbols=["aapl", "SPY"],
    )
    assert report.ok, report.errors
    assert {s for syms, _, _ in world.prices.calls for s in syms} == {"AAPL", "SPY"}
    steps = by_step(report)
    assert steps["fundamentals"].keys == 1  # type: ignore[attr-defined]  # SPY is an ETF
    assert world.calendar.calls == [("AAPL",)]
    assert "universe" not in steps


def test_equity_only_steps_skip_etfs(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    report = run_sync(tmp_lake, cfg, world.sources(), start=START)
    steps = by_step(report)
    for name in ("fundamentals", "earnings"):
        assert steps[name].keys == 4, name  # type: ignore[attr-defined]
    assert steps["upcoming"].keys == 3  # type: ignore[attr-defined]  # current members only


def test_upcoming_uses_current_members_only(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    run_sync(tmp_lake, cfg, world.sources(), start=START, datasets=["universe", "events"])
    assert {s for call in world.calendar.calls for s in call} == set(CURRENT)


def test_upcoming_chunk_failure_retried_per_symbol(
    tmp_lake: Lake, cfg: AtlasConfig, world: World
) -> None:
    world.calendar.fail = {"MSFT"}
    report = run_sync(tmp_lake, cfg, world.sources(), start=START, datasets=["universe", "events"])
    upcoming = by_step(report)["upcoming"]
    assert dict(upcoming.failed).keys() == {"MSFT"}  # type: ignore[attr-defined]
    assert upcoming.ok == 2  # type: ignore[attr-defined]
    assert ("AAPL",) in world.calendar.calls and ("NEW",) in world.calendar.calls
    stored = tmp_lake.read_table("events")
    assert set(stored.loc[stored["source"] == "yahoo", "symbol"]) == {"AAPL"}


def test_second_sync_skips_everything(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    run_sync(tmp_lake, cfg, world.sources(), start=START)
    calls = len(world.prices.calls)
    news_calls = len(world.news.calls)
    again = run_sync(tmp_lake, cfg, world.sources(), start=START)
    assert again.ok
    assert len(world.prices.calls) == calls and len(world.news.calls) == news_calls
    assert world.page_calls == 1
    for step in again.steps:
        assert step.skipped == step.keys and step.chunks == 0, step.step


def test_resume_after_price_crash(tmp_lake: Lake, cfg: AtlasConfig, tmp_path: Path) -> None:
    small = cfg.model_copy(
        update={
            "data": cfg.data.model_copy(
                update={
                    "sync": cfg.data.sync.model_copy(
                        update={
                            "chunk_size": cfg.data.sync.chunk_size.model_copy(update={"prices": 2})
                        }
                    )
                }
            )
        }
    )
    crashing = World(prices=WorldPrices(crash_on=2), lexicon_file=cfg.data.sentiment.lexicon_file)
    with pytest.raises(KeyboardInterrupt):
        run_sync(tmp_lake, small, crashing.sources(), start=START, datasets=["universe", "prices"])
    state = SyncState.load(state_path(tmp_lake, small))
    first_chunk = set(crashing.prices.calls[0][0])
    assert set(state.keys("prices")) == first_chunk
    assert "KeyboardInterrupt" in str(state.runs[-1]["crashed"])

    healthy = World(lexicon_file=cfg.data.sentiment.lexicon_file)
    report = run_sync(
        tmp_lake, small, healthy.sources(), start=START, datasets=["universe", "prices"]
    )
    assert report.ok
    resumed = {s for syms, _, _ in healthy.prices.calls for s in syms}
    assert resumed == {*MEMBERS, *ETFS} - first_chunk
    assert by_step(report)["prices"].skipped == 2  # type: ignore[attr-defined]

    clean = Lake(tmp_path / "clean", registry=tmp_lake.registry)
    run_sync(clean, small, World().sources(), start=START, datasets=["universe", "prices"])
    pd.testing.assert_frame_equal(
        tmp_lake.read_table("prices_daily"), clean.read_table("prices_daily")
    )


def test_update_fetches_only_new_days(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    with use_clock(SimClock(start=date(2024, 3, 1))):
        run_sync(tmp_lake, cfg, world.sources(), start=START, datasets=["universe", "prices"])
    cov = SyncState.load(state_path(tmp_lake, cfg)).coverage("prices", "AAPL")
    assert cov is not None and cov.end == date(2024, 2, 29)
    later = World(lexicon_file=world.lexicon_file)
    report = run_update(tmp_lake, cfg, later.sources(), datasets=["universe", "prices"])
    assert report.command == "update" and report.start == START
    windows = {(start, end) for _, start, end in later.prices.calls}
    assert windows == {(date(2024, 2, 25), LAST_BAR)}  # covered end + 1 - 5 overlap days
    stored = tmp_lake.read_table("prices_daily", filters=[("symbol", "=", "AAPL")])
    assert stored["date"].max() == LAST_BAR
    assert stored["date"].is_unique


def test_update_backfills_new_member(tmp_lake: Lake, cfg: AtlasConfig, tmp_path: Path) -> None:
    before = World(
        pages=(
            constituents_page(
                [("AAPL", "Apple", date(1982, 11, 30)), ("MSFT", "Microsoft", date(1994, 6, 1)),
                 ("OLD", "Old Co", date(2000, 1, 3))]
            ),
            changes_page([]),
        ),
        lexicon_file=cfg.data.sentiment.lexicon_file,
    )  # fmt: skip
    with use_clock(SimClock(start=date(2024, 1, 16))):
        run_sync(tmp_lake, cfg, before.sources(), start=START, datasets=["universe", "prices"])
    after = World(lexicon_file=cfg.data.sentiment.lexicon_file)
    run_update(tmp_lake, cfg, after.sources(), datasets=["universe", "prices"])
    new_calls = [(s, e) for syms, s, e in after.prices.calls if "NEW" in syms]
    assert new_calls == [(START, LAST_BAR)]
    assert "NEW" in symbols_of(tmp_lake, "prices_daily")
    assert CHANGE_DAY in set(
        tmp_lake.read_table("prices_daily", filters=[("symbol", "=", "NEW")])["date"]
    )


def test_update_without_state_refuses(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    with pytest.raises(DataError, match="atlas data sync"):
        run_update(tmp_lake, cfg, world.sources())


def test_price_end_capped_at_published_bar(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    report = run_sync(tmp_lake, cfg, world.sources(), start=START, datasets=["universe", "prices"])
    assert by_step(report)["prices"].end == LAST_BAR  # type: ignore[attr-defined]
    assert max(end for _, _, end in world.prices.calls) == LAST_BAR
    evening = World(lexicon_file=world.lexicon_file)
    other = Lake(tmp_lake.root.parent / "evening", registry=tmp_lake.registry)
    clock = SimClock(start=TODAY)
    clock.set(at(TODAY, 16, 20))  # the 2024-04-01 bar is published at 16:15
    with use_clock(clock):
        late = run_sync(other, cfg, evening.sources(), start=START, datasets=["universe", "prices"])
    assert by_step(late)["prices"].end == TODAY  # type: ignore[attr-defined]


def test_missing_key_step_errors_others_run(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    def no_key() -> None:
        raise ConfigError("FRED_API_KEY is not set; add it to .env")

    report = run_sync(tmp_lake, cfg, world.sources(fred=no_key), start=START)
    assert not report.ok
    assert [r.step for r in report.errors] == ["macro"]
    assert "FRED_API_KEY" in str(report.errors[0].error)
    assert by_step(report)["prices"].error is None  # type: ignore[attr-defined]
    assert by_step(report)["quality"].error is None  # type: ignore[attr-defined]
    assert SyncState.load(state_path(tmp_lake, cfg)).runs[-1]["errors"] == ["macro"]


def test_step_needing_universe_errors_without_it(
    tmp_lake: Lake, cfg: AtlasConfig, world: World
) -> None:
    report = run_sync(tmp_lake, cfg, world.sources(), start=START, datasets=["prices"])
    assert [r.step for r in report.errors] == ["prices"]
    assert "universe" in str(report.errors[0].error)


def test_news_failure_leaves_keys_uncovered(tmp_lake: Lake, cfg: AtlasConfig) -> None:
    failing = World(news=WorldNews(error=DataError("alpaca down")))
    report = run_sync(
        tmp_lake, cfg, failing.sources(), start=START, datasets=["news"], symbols=["AAPL"]
    )
    assert dict(by_step(report)["news"].failed) == {"AAPL": "alpaca down"}  # type: ignore[attr-defined]
    assert SyncState.load(state_path(tmp_lake, cfg)).coverage("news", "AAPL") is None
    healthy = World()
    again = run_sync(
        tmp_lake, cfg, healthy.sources(), start=START, datasets=["news"], symbols=["AAPL"]
    )
    assert by_step(again)["news"].ok == 1  # type: ignore[attr-defined]
    assert len(healthy.news.calls) == 2  # two 90-day windows: 2024-01-02..03-31, 04-01


def test_news_scoring_follows_news_backfill(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    run_sync(
        tmp_lake,
        cfg,
        world.sources(),
        start=START,
        datasets=["news", "sentiment"],
        symbols=["AAPL"],
    )
    assert set(tmp_lake.read_table("news_sentiment")["article_id"]) == {"alpaca-1", "alpaca-3"}
    cov = SyncState.load(state_path(tmp_lake, cfg)).coverage("news_sentiment", ALL_KEY)
    assert cov is not None and cov.end == TODAY
    run_sync(
        tmp_lake,
        cfg,
        world.sources(),
        start=START,
        datasets=["news", "sentiment"],
        symbols=["MSFT"],
    )
    assert set(tmp_lake.read_table("news_sentiment")["article_id"]) == {
        "alpaca-1",
        "alpaca-2",
        "alpaca-3",
    }


def test_quality_report_persisted(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    report = run_sync(tmp_lake, cfg, world.sources(), start=START)
    assert by_step(report)["quality"].ok == 1  # type: ignore[attr-defined]
    stored = tmp_lake.read_table(DATA_QUALITY_ISSUES.name)
    assert set(stored["report_day"]) <= {date(2024, 4, 2)}  # the next decision day
    cov = SyncState.load(state_path(tmp_lake, cfg)).coverage("quality", "_report")
    assert cov is not None and cov.end == TODAY


def test_reference_refreshed_after_prices(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    run_sync(tmp_lake, cfg, world.sources(), start=START, datasets=["universe", "prices"])
    instruments = tmp_lake.read_table("instruments").set_index("symbol")
    assert instruments.loc["AAPL", "first_trade_date"] == START  # first stored bar
    assert instruments.loc["OLD", "last_trade_date"] == date(2024, 1, 31)


def test_resources_closed_and_built_once(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    run_sync(tmp_lake, cfg, world.sources(), start=START)
    assert world.fred.closed
    assert world.built == {
        "price_provider": 1,
        "edgar": 1,
        "fred": 1,
        "news_provider": 1,
        "upcoming_provider": 1,
    }


def test_unknown_dataset_rejected(tmp_lake: Lake, cfg: AtlasConfig, world: World) -> None:
    with pytest.raises(ValueError, match="unknown datasets"):
        run_sync(tmp_lake, cfg, world.sources(), datasets=["prices", "options"])
    assert parse_datasets(None) == DATASETS
    assert parse_datasets(["quality", "Prices"]) == ("prices", "quality")
    with pytest.raises(ValueError, match="no datasets"):
        parse_datasets([" "])
    with pytest.raises(ValueError, match="after"):
        run_sync(tmp_lake, cfg, world.sources(), start=date(2024, 5, 1))


def test_audit_one_event_per_step(
    tmp_lake: Lake, cfg: AtlasConfig, world: World, tmp_path: Path
) -> None:
    with run_context(config=cfg) as ctx:
        audit = AuditLog(tmp_path / "audit.jsonl")
        report = run_sync(tmp_lake, cfg, world.sources(), start=START, audit=audit)
    events = [json.loads(line) for line in audit.path.read_text().splitlines()]
    assert [e["payload"]["step"] for e in events] == list(STEPS)
    assert {e["event_type"] for e in events} == {EventType.DATA_SYNC.value}
    assert {e["run_id"] for e in events} == {ctx.run_id}
    assert SyncState.load(report.state_path).runs[-1]["run_id"] == ctx.run_id


def test_live_sources_are_lazy_and_fail_safe(tmp_lake: Lake, cfg: AtlasConfig) -> None:
    from pydantic import SecretStr

    from atlas.core.config import Secrets
    from atlas.data.news.alpaca_news import AlpacaNewsProvider
    from atlas.data.news.yahoo_news import YahooNewsProvider
    from atlas.data.sync import SyncSources

    bare = cfg.model_copy(update={"secrets": Secrets()})
    sources = SyncSources.live(bare, tmp_lake)  # builds nothing yet: no key is needed here
    assert sources.calendar_fred() is None  # no FRED key: the calendar keeps FOMC dates only
    assert isinstance(sources.news_provider().inner, YahooNewsProvider)  # archived (Tier 4)
    with pytest.raises(ConfigError, match="FRED_API_KEY"):
        sources.fred()
    with pytest.raises(ConfigError, match="SEC_USER_AGENT"):
        sources.edgar()
    assert sources.lexicon().version  # the configured file exists: nothing is downloaded
    keyed = cfg.model_copy(
        update={
            "secrets": Secrets(
                alpaca_api_key_id=SecretStr("id"), alpaca_api_secret_key=SecretStr("secret")
            )
        }
    )
    news = SyncSources.live(keyed, tmp_lake).news_provider()
    assert isinstance(news.inner, AlpacaNewsProvider)  # Alpaca alone: no recent-only fallback
    news.close()
