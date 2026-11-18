"""Spend per day, run, agent and model from the usage ledger (Phase 3 checkpoint, Tier 3
item 2): `CostLedger.usage_summary`, the same answer in memory and on DuckDB `llm_usage`."""

from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from atlas.core.storage import Database, Lake
from atlas.llm.cost import USAGE_DIMENSIONS, CostLedger, UsageGroup, UsageRecord

DAY1, DAY2 = date(2026, 11, 16), date(2026, 11, 17)


def _record(
    call_id: str,
    *,
    day: date = DAY1,
    run_id: str | None = "run-a",
    agent: str | None = "news",
    model: str = "deepseek-flash",
    cost: float = 0.01,
    hit: bool = False,
    attempt: int = 1,
    tokens: tuple[int, int, int, int] = (100, 20, 5, 30),
) -> UsageRecord:
    fresh, cached, written, out = tokens
    return UsageRecord(
        call_id=call_id,
        ts=datetime(day.year, day.month, day.day, 15, tzinfo=UTC),
        day=day,
        run_id=run_id,
        agent=agent,
        step="report",
        tier="quick",
        provider="openai_compatible",
        model=model,
        input_tokens=0 if hit else fresh,
        cached_input_tokens=0 if hit else cached,
        cache_write_tokens=0 if hit else written,
        output_tokens=0 if hit else out,
        cost_usd=0.0 if hit else cost,
        latency_ms=1.0,
        cache_hit=hit,
        attempt=attempt,
    )


RECORDS = (
    _record("c1", cost=0.01),
    _record("c2", cost=0.02, attempt=2),  # a repair retry of c1: billed, not a new call
    _record("c3", hit=True),
    _record("c4", agent="technical", model="deepseek-v4-pro", cost=0.10),
    _record("c5", day=DAY2, run_id="run-b", cost=0.04),
    _record("c6", day=DAY2, run_id=None, agent=None, cost=0.005),
)


@pytest.fixture(params=["memory", "duckdb"])
def ledger(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[CostLedger]:
    """The same six records, in an in-memory ledger and in a lake's `llm_usage`."""
    if request.param == "memory":
        memory = CostLedger()
        for record in RECORDS:
            memory.record(record)
        yield memory
        return
    with Database(Lake(tmp_path / "lake")) as database:
        stored = CostLedger(database)
        for record in RECORDS:
            stored.record(record)
        yield stored


def _by_key(groups: tuple[UsageGroup, ...]) -> dict[str | None, UsageGroup]:
    return {group.key: group for group in groups}


def test_dimensions_are_day_run_agent_model() -> None:
    assert USAGE_DIMENSIONS == ("day", "run", "agent", "model")


def test_total_counts_calls_hits_paid_tokens_and_cost(ledger: CostLedger) -> None:
    (total,) = ledger.usage_summary()
    assert total.key is None
    assert total.calls == 6
    assert total.hits == 1
    assert total.paid == 4  # c2 is a repair retry, c3 a free cache hit
    assert total.input_tokens == 5 * (100 + 20 + 5)  # fresh + cached + cache-write input
    assert total.output_tokens == 5 * 30
    assert total.cost_usd == pytest.approx(0.175)


def test_spend_per_day_is_in_date_order(ledger: CostLedger) -> None:
    groups = ledger.usage_summary("day")
    assert [g.key for g in groups] == ["2026-11-16", "2026-11-17"]
    assert groups[0].cost_usd == pytest.approx(0.13)
    assert groups[1].cost_usd == pytest.approx(0.045)
    assert sum(g.cost_usd for g in groups) == pytest.approx(ledger.usage_summary()[0].cost_usd)


def test_spend_per_run_agent_and_model_is_largest_first(ledger: CostLedger) -> None:
    runs = ledger.usage_summary("run")
    assert [g.key for g in runs] == ["run-a", "run-b", None]  # None: calls outside any run
    assert [g.cost_usd for g in runs] == pytest.approx([0.13, 0.04, 0.005])
    agents = ledger.usage_summary("agent")
    assert [g.key for g in agents] == ["technical", "news", None]
    assert _by_key(agents)["news"].calls == 4
    assert _by_key(agents)["news"].hits == 1
    models = ledger.usage_summary("model")
    assert [g.key for g in models] == [
        "openai_compatible/deepseek-v4-pro",
        "openai_compatible/deepseek-flash",
    ]


def test_filters_by_run_and_by_day(ledger: CostLedger) -> None:
    (run_a,) = ledger.usage_summary(run_id="run-a")
    assert (run_a.calls, run_a.cost_usd) == (4, pytest.approx(0.13))
    assert run_a.cost_usd == pytest.approx(ledger.spent_in_run("run-a"))
    day2 = ledger.usage_summary("agent", day=DAY2)
    assert [(g.key, g.calls) for g in day2] == [("news", 1), (None, 1)]
    assert sum(g.cost_usd for g in day2) == pytest.approx(ledger.spent_on(DAY2))
    assert ledger.usage_summary("model", run_id="run-b", day=DAY1) == ()


@pytest.mark.parametrize("storage", ["memory", "duckdb"])
def test_equal_costs_are_ordered_by_key(storage: str, tmp_path: Path) -> None:
    """Ties are broken by the key (a missing key last), so the order never depends on the
    storage."""
    tied = (
        _record("t1", run_id="run-z"),
        _record("t2", run_id=None),
        _record("t3", run_id="run-y"),
    )
    with ExitStack() as stack:
        database = None
        if storage == "duckdb":
            database = stack.enter_context(Database(Lake(tmp_path / "lake")))
        ledger = CostLedger(database)
        for record in tied:
            ledger.record(record)
        assert [g.key for g in ledger.usage_summary("run")] == ["run-y", "run-z", None]


def test_empty_ledger_has_a_zero_total_and_no_groups() -> None:
    empty = CostLedger()
    assert empty.usage_summary() == (UsageGroup(None, 0, 0, 0, 0, 0, 0.0),)
    for dimension in USAGE_DIMENSIONS:
        assert empty.usage_summary(dimension) == ()


def test_unknown_dimension_is_refused() -> None:
    with pytest.raises(ValueError, match="sector"):
        CostLedger().usage_summary("sector")  # type: ignore[arg-type]
