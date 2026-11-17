"""Stage 28 Task 7 (brief task 4): one failing symbol does not affect the others."""

import threading
from pathlib import Path
from typing import Any

import pytest
from graph_world import DAY, Spy, raiser, report
from ulid import ULID

from atlas.core.errors import BudgetExceeded, LookAheadError
from atlas.orchestration.checkpoint import resume_graph, run_graph
from atlas.orchestration.errors import failed_symbols
from atlas.orchestration.research_graph import build_research_graph
from atlas.orchestration.stubs import ANALYSTS, stub_team

SYMBOLS = ["AAA", "BBB", "CCC", "DDD", "EEE"]
INPUTS: dict[str, Any] = {"as_of": DAY, "research_queue": SYMBOLS}


@pytest.fixture(scope="module")
def clean() -> dict[str, Any]:
    out: dict[str, Any] = build_research_graph(stub_team(), max_concurrency=3).invoke(INPUTS)
    assert out["errors"] == []
    return out


def _run(spy: Spy) -> dict[str, Any]:
    out: dict[str, Any] = build_research_graph(spy.team(), max_concurrency=3).invoke(INPUTS)
    return out


def _assert_only_dropped(out: dict[str, Any], clean: dict[str, Any], dropped: set[str]) -> None:
    assert set(failed_symbols(out["errors"])) == dropped
    kept = set(SYMBOLS) - dropped
    assert set(out["theses"]) == kept and set(out["reports"]) == kept
    for symbol in kept:
        assert out["theses"][symbol] == clean["theses"][symbol]
        assert out["reports"][symbol] == clean["reports"][symbol]


@pytest.mark.parametrize("node", [*ANALYSTS, "debate", "director"])
@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("vendor down"),
        LookAheadError("future row"),
        BudgetExceeded("over", scope="daily_usd", limit=5.0, spent=5.1),
    ],
    ids=lambda e: type(e).__name__,
)
def test_one_failing_node_drops_only_its_symbol(
    node: str, exc: Exception, clean: dict[str, Any]
) -> None:
    out = _run(Spy(hooks={("CCC", node): raiser(exc)}))
    _assert_only_dropped(out, clean, {"CCC"})
    [error] = out["errors"]
    assert (error.node, error.symbol, error.kind, error.as_of) == (
        node,
        "CCC",
        type(exc).__name__,
        DAY,
    )


@pytest.mark.parametrize(
    ("node", "replace"),
    [("news", lambda out: report("ZZZ", "news")), ("director", lambda out: None)],
)
def test_a_bad_output_drops_only_its_symbol(node: str, replace: Any, clean: dict[str, Any]) -> None:
    out = _run(Spy(outputs={("BBB", node): replace}))
    _assert_only_dropped(out, clean, {"BBB"})
    assert [e.kind for e in out["errors"]] == ["DataError"]


def test_a_failed_analyst_stops_its_symbol_before_the_debate(clean: dict[str, Any]) -> None:
    spy = Spy(hooks={("CCC", "quant"): raiser(RuntimeError("x"))})
    _run(spy)
    assert sorted(spy.calls_of("CCC")) == sorted(ANALYSTS)  # siblings finish, no debate
    assert "debate" not in spy.calls_of("CCC") and "director" not in spy.calls_of("CCC")


def test_several_failing_analysts_of_one_symbol_are_all_recorded(clean: dict[str, Any]) -> None:
    hooks = {("AAA", name): raiser(RuntimeError(name)) for name in ("news", "quant")}
    out = _run(Spy(hooks=hooks))
    _assert_only_dropped(out, clean, {"AAA"})
    assert sorted(e.node for e in out["errors"]) == ["news", "quant"]


def test_failures_in_several_symbols(clean: dict[str, Any]) -> None:
    hooks = {
        ("AAA", "news"): raiser(RuntimeError("a")),
        ("DDD", "director"): raiser(ValueError("d")),
    }
    out = _run(Spy(hooks=hooks))
    _assert_only_dropped(out, clean, {"AAA", "DDD"})


def test_every_symbol_failing_still_completes_the_run(clean: dict[str, Any]) -> None:
    out = _run(Spy(hooks={("*", "debate"): raiser(RuntimeError("all"))}))
    assert out["theses"] == {} and out["reports"] == {}
    assert sorted(e.symbol for e in out["errors"] if e.symbol) == SYMBOLS


def test_a_failing_symbol_does_not_hold_up_the_others(clean: dict[str, Any]) -> None:
    # CCC's news analyst fails only after every other symbol has finished, so the others
    # could not have been waiting for it; and the run still completes for them.
    others_done = threading.Event()
    finished: set[str] = set()
    lock = threading.Lock()

    def mark(out: Any) -> Any:
        with lock:
            finished.add(out.ticker)
            if finished >= set(SYMBOLS) - {"CCC"}:
                others_done.set()
        return out

    def late_failure() -> None:
        assert others_done.wait(10), "the other symbols did not finish while CCC was failing"
        raise RuntimeError("late")

    spy = Spy(
        hooks={("CCC", "news"): late_failure},
        outputs={(s, "director"): mark for s in SYMBOLS if s != "CCC"},
    )
    out = build_research_graph(spy.team(), max_concurrency=5).invoke(INPUTS)
    _assert_only_dropped(out, clean, {"CCC"})


def test_errors_survive_a_resume(tmp_path: Path, clean: dict[str, Any]) -> None:
    run_id = str(ULID())
    hooks = {
        ("AAA", "news"): raiser(RuntimeError("recorded before the stop")),
        ("CCC", "quant"): raiser(KeyboardInterrupt()),
    }
    with pytest.raises(KeyboardInterrupt):
        run_graph(
            build_research_graph(Spy(hooks=hooks).team(), max_concurrency=1),
            INPUTS,
            run_id=run_id,
            lake_path=tmp_path,
        )
    hooks.pop(("CCC", "quant"))
    out = resume_graph(
        build_research_graph(Spy(hooks=hooks).team(), max_concurrency=1),
        run_id=run_id,
        lake_path=tmp_path,
    )
    _assert_only_dropped(out, clean, {"AAA"})
    assert [(e.node, e.symbol) for e in out["errors"]] == [("news", "AAA")]
