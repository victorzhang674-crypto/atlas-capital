"""Stage 28 Task 5 (brief task 2, part 2): fan-out over the research queue with `Send`, and the
concurrency limit."""

from datetime import datetime
from typing import Any

import pytest
from graph_world import DAY, Spy, barrier_hook
from structlog.testing import capture_logs

from atlas.core.errors import OrchestrationError
from atlas.orchestration.research_graph import build_research_graph, build_research_subgraph
from atlas.orchestration.stubs import ANALYSTS, stub_team

SYMBOLS = ["AAA", "BBB", "CCC", "DDD", "EEE"]
CALLS_PER_SYMBOL = len(ANALYSTS) + 2  # analysts, debate, director


def _alone(symbol: str) -> dict[str, Any]:
    out: dict[str, Any] = build_research_subgraph(stub_team()).invoke(
        {"symbol": symbol, "as_of": DAY}
    )
    return out


def test_five_symbols_fan_out_and_complete() -> None:
    out = build_research_graph(stub_team(), max_concurrency=4).invoke(
        {"as_of": DAY, "research_queue": SYMBOLS}
    )
    assert out["errors"] == []
    assert list(out["research_queue"]) == SYMBOLS
    assert set(out["theses"]) == set(SYMBOLS)
    assert set(out["reports"]) == set(SYMBOLS)
    for symbol in SYMBOLS:
        alone = _alone(symbol)
        assert out["reports"][symbol] == alone["reports"][symbol]
        assert list(out["reports"][symbol]) == list(ANALYSTS)
        assert out["theses"][symbol] == alone["theses"][symbol]


def test_each_symbol_is_researched_by_its_own_subgraph() -> None:
    spy = Spy()
    build_research_graph(spy.team(), max_concurrency=4).invoke(
        {"as_of": DAY, "research_queue": SYMBOLS}
    )
    for symbol in SYMBOLS:
        calls = spy.calls_of(symbol)
        assert sorted(calls[: len(ANALYSTS)]) == sorted(ANALYSTS)
        assert calls[len(ANALYSTS) :] == ["debate", "director"]


def test_a_duplicate_in_the_queue_is_researched_once() -> None:
    spy = Spy()
    out = build_research_graph(spy.team(), max_concurrency=4).invoke(
        {"as_of": DAY, "research_queue": ["BBB", "AAA", "BBB", "AAA"]}
    )
    assert list(out["research_queue"]) == ["BBB", "AAA"]
    assert len(spy.calls_of("BBB")) == CALLS_PER_SYMBOL
    assert len(spy.calls_of("AAA")) == CALLS_PER_SYMBOL
    assert out["errors"] == []


@pytest.mark.parametrize("queue", [[], None])
def test_an_empty_queue_goes_straight_to_collect(queue: list[str] | None) -> None:
    spy = Spy()
    inputs: dict[str, Any] = {"as_of": DAY}
    if queue is not None:
        inputs["research_queue"] = queue
    with capture_logs() as logs:
        out = build_research_graph(spy.team(), max_concurrency=4).invoke(inputs)
    assert spy.calls == []
    assert out.get("theses", {}) == {} and out.get("reports", {}) == {}
    assert out["research_queue"] == []
    [done] = [r for r in logs if r["event"] == "orchestration_research_done"]
    assert (done["queued"], done["researched"], done["dropped"]) == (0, 0, [])


def test_collect_logs_what_was_researched() -> None:
    with capture_logs() as logs:
        build_research_graph(stub_team(), max_concurrency=4).invoke(
            {"as_of": DAY, "research_queue": SYMBOLS}
        )
    [done] = [r for r in logs if r["event"] == "orchestration_research_done"]
    assert (done["queued"], done["researched"], done["dropped"]) == (5, 5, [])


@pytest.mark.parametrize(
    ("inputs", "needle"),
    [
        ({"research_queue": ["AAA"]}, "as_of"),
        ({"as_of": "2024-03-01", "research_queue": ["AAA"]}, "as_of"),
        ({"as_of": datetime(2024, 3, 1, 12), "research_queue": ["AAA"]}, "as_of"),
        ({"as_of": DAY, "research_queue": ["AAA", "bad symbol!"]}, "symbol"),
    ],
)
def test_malformed_inputs_are_refused(inputs: dict[str, Any], needle: str) -> None:
    graph = build_research_graph(stub_team(), max_concurrency=4)
    with pytest.raises(OrchestrationError, match=needle):
        graph.invoke(inputs)


@pytest.mark.parametrize("bad", [0, -1])
def test_max_concurrency_must_be_positive(bad: int) -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        build_research_graph(stub_team(), max_concurrency=bad)


# --- concurrency limit ------------------------------------------------------------------


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_the_limit_caps_agent_calls_and_symbols_in_flight(limit: int) -> None:
    spy = Spy(hold_seconds=0.01)
    graph = build_research_graph(spy.team(), max_concurrency=limit)
    assert graph.max_concurrency == limit
    assert graph.config()["max_concurrency"] == limit
    out = graph.invoke({"as_of": DAY, "research_queue": [*SYMBOLS, "FFF"]})
    assert out["errors"] == [] and len(out["theses"]) == 6
    assert len(spy.calls) == 6 * CALLS_PER_SYMBOL
    assert 1 <= spy.peak_calls <= limit
    assert spy.peak_symbols <= limit


def test_a_limit_of_one_runs_one_call_at_a_time() -> None:
    spy = Spy(hold_seconds=0.005)
    build_research_graph(spy.team(), max_concurrency=1).invoke(
        {"as_of": DAY, "research_queue": SYMBOLS}
    )
    assert spy.peak_calls == 1 and spy.peak_symbols == 1


def test_symbols_are_researched_in_parallel() -> None:
    # Both directors wait until the other one is running: only parallel symbols get through.
    hook = barrier_hook(2)
    spy = Spy(hooks={("AAA", "director"): hook, ("BBB", "director"): hook})
    out = build_research_graph(spy.team(), max_concurrency=2).invoke(
        {"as_of": DAY, "research_queue": ["AAA", "BBB"]}
    )
    assert out["errors"] == []
    assert spy.peak_symbols == 2
