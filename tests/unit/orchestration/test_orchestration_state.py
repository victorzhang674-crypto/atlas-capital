"""Stage 28 Task 2 (brief task 1): `FundState` and its reducers; parallel writes merge."""

import copy
import typing
from datetime import date
from typing import Any

import pytest
from graph_world import DAY, node_error, report, thesis
from hypothesis import given, settings
from hypothesis import strategies as st
from langgraph.errors import InvalidUpdateError
from langgraph.graph import END, START, StateGraph

from atlas.core.errors import OrchestrationError
from atlas.orchestration.state import (
    FundState,
    ResearchOutput,
    ResearchState,
    append_errors,
    merge_keyed,
    merge_reports,
)

BRIEF_KEYS = {
    "as_of",
    "regime",
    "macro_outlook",
    "research_queue",
    "reports",
    "theses",
    "proposals",
    "allocation",
    "target",
    "risk_decision",
    "compliance",
    "orders",
    "errors",
}


def test_fund_state_has_the_brief_keys() -> None:
    assert set(typing.get_type_hints(FundState)) == BRIEF_KEYS


def _reducer(schema: type, key: str) -> Any:
    hint = typing.get_type_hints(schema, include_extras=True)[key]
    metadata = getattr(hint, "__metadata__", ())
    return metadata[0] if metadata else None


@pytest.mark.parametrize(
    ("key", "reducer"),
    [
        ("reports", merge_reports),
        ("theses", merge_keyed),
        ("proposals", merge_keyed),
        ("errors", append_errors),
    ],
)
def test_fields_written_in_parallel_have_reducers(key: str, reducer: object) -> None:
    assert _reducer(FundState, key) is reducer


@pytest.mark.parametrize(
    "key",
    [
        "as_of",
        "regime",
        "macro_outlook",
        "research_queue",
        "allocation",
        "target",
        "risk_decision",
        "compliance",
        "orders",
    ],
)
def test_single_writer_fields_have_no_reducer(key: str) -> None:
    assert _reducer(FundState, key) is None


def test_research_output_carries_only_the_merged_fields() -> None:
    assert set(typing.get_type_hints(ResearchOutput)) == {"reports", "theses", "errors"}
    for key in ("reports", "theses", "errors"):
        assert _reducer(ResearchOutput, key) is _reducer(FundState, key)
        assert _reducer(ResearchState, key) is _reducer(FundState, key)


# --- reducers ---------------------------------------------------------------------------


def test_merge_keyed_merges_disjoint_keys() -> None:
    a, b = thesis("AAA"), thesis("BBB")
    assert merge_keyed({"AAA": a}, {"BBB": b}) == {"AAA": a, "BBB": b}


@pytest.mark.parametrize(("left", "right"), [(None, None), ({}, None), (None, {}), ({}, {})])
def test_merge_keyed_handles_empty_sides(left: Any, right: Any) -> None:
    assert merge_keyed(left, right) == {}


def test_merge_keyed_accepts_an_identical_rewrite() -> None:
    a = thesis("AAA")
    assert merge_keyed({"AAA": a}, {"AAA": thesis("AAA")}) == {"AAA": a}


def test_merge_keyed_refuses_a_conflicting_write() -> None:
    with pytest.raises(OrchestrationError, match="AAA"):
        merge_keyed({"AAA": thesis("AAA")}, {"AAA": thesis("AAA", conviction=0.9)})


def test_merge_keyed_does_not_mutate_its_inputs() -> None:
    left, right = {"AAA": thesis("AAA")}, {"BBB": thesis("BBB")}
    before = (copy.deepcopy(left), copy.deepcopy(right))
    merged = merge_keyed(left, right)
    assert (left, right) == before
    assert merged is not left and merged is not right


def test_merge_reports_merges_two_levels() -> None:
    left = {"AAA": {"fundamental": report("AAA", "fundamental")}}
    right = {
        "AAA": {"technical": report("AAA", "technical")},
        "BBB": {"news": report("BBB", "news")},
    }
    assert merge_reports(left, right) == {
        "AAA": {
            "fundamental": report("AAA", "fundamental"),
            "technical": report("AAA", "technical"),
        },
        "BBB": {"news": report("BBB", "news")},
    }


def test_merge_reports_refuses_a_conflicting_report_and_names_it() -> None:
    left = {"AAA": {"news": report("AAA", "news")}}
    right = {"AAA": {"news": report("AAA", "news", confidence=0.9)}}
    with pytest.raises(OrchestrationError, match=r"AAA.*news"):
        merge_reports(left, right)


def test_merge_reports_does_not_mutate_its_inputs() -> None:
    left = {"AAA": {"news": report("AAA", "news")}}
    right = {"AAA": {"quant": report("AAA", "quant")}}
    before = (copy.deepcopy(left), copy.deepcopy(right))
    merged = merge_reports(left, right)
    assert (left, right) == before
    assert merged["AAA"] is not left["AAA"]


@pytest.mark.parametrize(("left", "right"), [(None, None), ({}, None), (None, {})])
def test_merge_reports_handles_empty_sides(left: Any, right: Any) -> None:
    assert merge_reports(left, right) == {}


def test_append_errors_keeps_order_and_returns_a_list() -> None:
    e1, e2, e3 = node_error("a"), node_error("b"), node_error("c")
    merged = append_errors([e1], [e2, e3])
    assert merged == [e1, e2, e3]
    assert isinstance(merged, list)
    assert append_errors(None, None) == []
    assert append_errors(None, [e1]) == [e1]


_SYMBOLS = st.sampled_from(["AAA", "BBB", "CCC", "DDD"])
_ANALYSTS = st.sampled_from(["fundamental", "technical", "news", "sentiment", "quant"])


@settings(max_examples=60, deadline=None)
@given(
    cells=st.sets(st.tuples(_SYMBOLS, _ANALYSTS), min_size=1, max_size=12),
    data=st.data(),
)
def test_disjoint_report_writes_merge_the_same_in_any_order(
    cells: set[tuple[str, str]], data: st.DataObject
) -> None:
    # Any split of a set of disjoint writes into parallel updates, merged in any order, gives
    # the one state that holds every write.
    items = sorted(cells)
    groups = data.draw(st.lists(st.integers(0, 3), min_size=len(items), max_size=len(items)))
    updates: list[dict[str, dict[str, Any]]] = [{} for _ in range(4)]
    for (symbol, analyst), group in zip(items, groups, strict=True):
        updates[group].setdefault(symbol, {})[analyst] = report(symbol, analyst)
    order = data.draw(st.permutations(range(4)))
    merged: dict[str, dict[str, Any]] = {}
    for index in order:
        merged = merge_reports(merged, updates[index])
    expected: dict[str, dict[str, Any]] = {}
    for symbol, analyst in items:
        expected.setdefault(symbol, {})[analyst] = report(symbol, analyst)
    assert merged == expected


# --- graph level -----------------------------------------------------------------------


def _writer(symbol: str, analyst: str) -> Any:
    def node(state: FundState) -> dict[str, Any]:
        return {
            "reports": {symbol: {analyst: report(symbol, analyst)}},
            "theses": {symbol: thesis(symbol)},
            "errors": [node_error(f"{symbol}-{analyst}", symbol)],
        }

    return node


def test_parallel_nodes_merge_their_writes_in_a_graph() -> None:
    cells = [(s, a) for s in ("AAA", "BBB", "CCC") for a in ("news", "quant")]
    graph = StateGraph(FundState)
    for symbol, analyst in cells:
        name = f"{symbol}_{analyst}"
        graph.add_node(name, _writer(symbol, analyst))
        graph.add_edge(START, name)
        graph.add_edge(name, END)
    out = graph.compile().invoke({"as_of": DAY})
    assert out["reports"] == {
        s: {a: report(s, a) for a in ("news", "quant")} for s in ("AAA", "BBB", "CCC")
    }
    assert out["theses"] == {s: thesis(s) for s in ("AAA", "BBB", "CCC")}
    assert sorted(e.node for e in out["errors"]) == sorted(f"{s}-{a}" for s, a in cells)


def test_parallel_conflicting_writes_fail_loudly() -> None:
    graph = StateGraph(FundState)
    for name, conviction in (("one", 0.5), ("two", 0.9)):

        def node(state: FundState, conviction: float = conviction) -> dict[str, Any]:
            return {"theses": {"AAA": thesis("AAA", conviction=conviction)}}

        graph.add_node(name, node)
        graph.add_edge(START, name)
        graph.add_edge(name, END)
    with pytest.raises(OrchestrationError):
        graph.compile().invoke({"as_of": DAY})


def test_two_parallel_writes_to_a_single_writer_field_fail() -> None:
    graph = StateGraph(FundState)
    for name in ("one", "two"):
        graph.add_node(name, lambda state: {"research_queue": ["AAA"]})
        graph.add_edge(START, name)
        graph.add_edge(name, END)
    with pytest.raises(InvalidUpdateError):
        graph.compile().invoke({"as_of": date(2024, 3, 1)})
