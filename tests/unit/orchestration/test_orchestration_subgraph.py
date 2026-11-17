"""Stage 28 Task 4 (brief task 2, part 1): one symbol's research subgraph with stub agents."""

import time
from typing import Any

import pytest
from graph_world import DAY, Spy, barrier_hook, report, thesis

from atlas.orchestration.research_graph import (
    RESERVED_NODES,
    ResearchTask,
    ResearchTeam,
    build_research_subgraph,
)
from atlas.orchestration.stubs import ANALYSTS, stub_team

INPUT: dict[str, Any] = {"symbol": "AAA", "as_of": DAY}


def _expected(symbol: str = "AAA") -> dict[str, Any]:
    team = stub_team()
    task = ResearchTask(symbol=symbol, as_of=DAY)
    reports = {name: run(task) for name, run in team.analysts.items()}
    turns = team.debate(task, reports)
    return {"reports": reports, "thesis": team.director(task, reports, turns)}


# --- team validation --------------------------------------------------------------------


def _team(**analysts: Any) -> ResearchTeam:
    stubs = stub_team()
    return ResearchTeam(analysts=analysts, debate=stubs.debate, director=stubs.director)


def test_a_team_needs_analysts() -> None:
    with pytest.raises(ValueError, match="at least one analyst"):
        _team()


@pytest.mark.parametrize("name", ["Fundamental", "1st", "news-flow", "", *sorted(RESERVED_NODES)])
def test_analyst_names_must_be_valid_node_names(name: str) -> None:
    with pytest.raises(ValueError, match="analyst name"):
        _team(**{name: stub_team().analysts["news"]})


def test_reserved_names_cover_the_graph_nodes() -> None:
    assert {"prepare", "research", "collect", "join", "debate", "director", "publish"} <= (
        RESERVED_NODES
    )


def test_the_team_roster_cannot_be_changed_after_construction() -> None:
    analysts = dict(stub_team().analysts)
    team = ResearchTeam(analysts=analysts, debate=stub_team().debate, director=stub_team().director)
    analysts.pop("news")
    assert "news" in team.analysts
    with pytest.raises(TypeError):
        team.analysts["extra"] = stub_team().analysts["news"]  # type: ignore[index]


# --- one symbol through the subgraph ----------------------------------------------------


def test_one_symbol_publishes_every_report_and_the_thesis() -> None:
    out = build_research_subgraph(stub_team()).invoke(INPUT)
    expected = _expected()
    assert set(out) == {"reports", "theses", "errors"}
    assert out["reports"] == {"AAA": expected["reports"]}
    assert out["theses"] == {"AAA": expected["thesis"]}
    assert out["errors"] == []


def test_debate_sees_all_reports_and_director_sees_reports_and_debate() -> None:
    spy = Spy()
    build_research_subgraph(spy.team()).invoke(INPUT)
    expected = _expected()
    assert spy.seen[("AAA", "debate")] == expected["reports"]
    reports, turns = spy.seen[("AAA", "director")]
    assert reports == expected["reports"]
    assert turns == list(stub_team().debate(ResearchTask("AAA", DAY), expected["reports"]))
    assert spy.calls_of("AAA")[-2:] == ["debate", "director"]
    assert sorted(spy.calls_of("AAA")[:-2]) == sorted(ANALYSTS)


def test_agents_and_the_fund_state_see_reports_in_roster_order() -> None:
    # LangGraph merges the parallel analyst writes in its own order; prompts built from the
    # reports must not depend on it (determinism, spec §3.3).
    roster = ("quant", "news", "fundamental")
    spy = Spy(roster)
    out = build_research_subgraph(spy.team()).invoke(INPUT)
    assert list(spy.seen[("AAA", "debate")]) == list(roster)
    assert list(spy.seen[("AAA", "director")][0]) == list(roster)
    assert list(out["reports"]["AAA"]) == list(roster)


def test_the_analysts_run_in_parallel() -> None:
    # Every analyst waits until all five wait at once; run one after another, the barrier
    # would time out and the analysts would fail.
    hook = barrier_hook(len(ANALYSTS))
    spy = Spy(hooks={("*", name): hook for name in ANALYSTS})
    out = build_research_subgraph(spy.team()).invoke(INPUT)
    assert out["errors"] == []
    assert spy.peak_calls == len(ANALYSTS)


def test_the_join_waits_for_the_slowest_analyst() -> None:
    spy = Spy(hooks={("AAA", "quant"): lambda: time.sleep(0.3)})
    out = build_research_subgraph(spy.team()).invoke(INPUT)
    assert set(spy.seen[("AAA", "debate")]) == set(ANALYSTS)
    assert out["errors"] == []


def test_agents_get_a_read_only_view_of_the_reports() -> None:
    def debate(task: ResearchTask, reports: Any) -> Any:
        reports["news"] = None  # an agent must not be able to edit the shared state
        return []

    stubs = stub_team()
    team = ResearchTeam(analysts=stubs.analysts, debate=debate, director=stubs.director)
    out = build_research_subgraph(team).invoke(INPUT)
    [error] = out["errors"]
    assert (error.node, error.kind) == ("debate", "TypeError")
    assert out["reports"] == {} and out["theses"] == {}


# --- outputs are checked ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("node", "replace", "needle"),
    [
        ("news", lambda out: "not a report", "AnalystReport"),
        ("news", lambda out: report("BBB", "news"), "ticker"),
        ("news", lambda out: report("AAA", "quant"), "analyst"),
        ("news", lambda out: out.model_copy(update={"as_of": DAY.replace(day=4)}), "as_of"),
        ("debate", lambda out: ["not a turn"], "DebateTurn"),
        ("director", lambda out: "not a thesis", "InvestmentThesis"),
        ("director", lambda out: thesis("BBB"), "ticker"),
        ("director", lambda out: out.model_copy(update={"as_of": DAY.replace(day=4)}), "as_of"),
    ],
)
def test_a_wrong_output_drops_the_symbol(node: str, replace: Any, needle: str) -> None:
    spy = Spy(outputs={("AAA", node): replace})
    out = build_research_subgraph(spy.team()).invoke(INPUT)
    [error] = out["errors"]
    assert (error.node, error.symbol, error.kind) == (node, "AAA", "DataError")
    assert needle in error.message
    assert out["reports"] == {} and out["theses"] == {}


def test_an_empty_debate_is_accepted() -> None:
    spy = Spy(outputs={("AAA", "debate"): lambda out: []})
    out = build_research_subgraph(spy.team()).invoke(INPUT)
    assert out["errors"] == [] and set(out["theses"]) == {"AAA"}
