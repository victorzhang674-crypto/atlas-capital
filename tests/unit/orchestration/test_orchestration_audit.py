"""Stage 28 audit findings (artifacts/audit-report.md), each pinned by a test."""

import random
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from graph_world import DAY, Spy, raiser
from langsmith.run_helpers import tracing_context
from langsmith.utils import tracing_is_enabled
from ulid import ULID

from atlas.core.errors import OrchestrationError
from atlas.orchestration.checkpoint import checkpoint_path, resume_graph, run_graph
from atlas.orchestration.research_graph import build_research_graph
from atlas.orchestration.stubs import stub_team

SYMBOLS = ["EEE", "AAA", "DDD", "BBB", "CCC"]
INPUTS: dict[str, Any] = {"as_of": DAY, "research_queue": SYMBOLS}
HERE = Path(__file__).resolve().parent


# --- A1: LangSmith tracing is always off --------------------------------------------------


def _tracing_probe() -> tuple[Spy, list[bool]]:
    seen: list[bool] = []
    spy = Spy(hooks={("*", "director"): lambda: seen.append(tracing_is_enabled())})
    return spy, seen


# Tracing is switched on with `tracing_context(enabled=True)` here, never through the
# environment: langsmith caches what it read from the environment, so an env-based test would
# leave tracing on for later tests in the same process (the env case runs in a subprocess).


def test_a1_in_memory_runs_never_trace() -> None:
    spy, seen = _tracing_probe()
    with tracing_context(enabled=True):
        assert tracing_is_enabled()  # what a caller with tracing on would see
        build_research_graph(spy.team(), max_concurrency=2).invoke(INPUTS)
    assert seen == [False] * len(SYMBOLS)


def test_a1_checkpointed_and_resumed_runs_never_trace(tmp_path: Path) -> None:
    run_id = str(ULID())
    spy, seen = _tracing_probe()
    spy.hooks[("CCC", "news")] = raiser(KeyboardInterrupt())
    spy2, seen2 = _tracing_probe()
    with tracing_context(enabled=True):
        with pytest.raises(KeyboardInterrupt):
            run_graph(
                build_research_graph(spy.team(), max_concurrency=2),
                INPUTS,
                run_id=run_id,
                lake_path=tmp_path,
            )
        resume_graph(
            build_research_graph(spy2.team(), max_concurrency=2),
            run_id=run_id,
            lake_path=tmp_path,
        )
    assert seen and seen2 and not any(seen + seen2)


def test_a1_no_connection_is_attempted_with_tracing_in_the_environment(tmp_path: Path) -> None:
    # End to end in a fresh process: every socket connect is recorded (and refused).
    script = textwrap.dedent(
        """
        import os, socket, sys, time
        attempts = []
        def connect(self, address):
            attempts.append(address)
            raise OSError("refused by the test")
        socket.socket.connect = connect
        os.environ.update(LANGSMITH_TRACING="true", LANGSMITH_API_KEY="lsv2_not_a_real_key",
                          LANGSMITH_ENDPOINT="http://127.0.0.1:9")
        from graph_world import DAY
        from atlas.orchestration import build_research_graph, stub_team
        build_research_graph(stub_team(), max_concurrency=2).invoke(
            {"as_of": DAY, "research_queue": ["AAA", "BBB"]})
        time.sleep(2)  # the tracer uploads from a background thread
        print("attempts", len(attempts))
        """
    )
    path = tmp_path / "trace_probe.py"
    path.write_text(script)
    done = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**_env(), "PYTHONPATH": str(HERE)},
        check=True,
    )
    assert done.stdout.strip().splitlines()[-1] == "attempts 0"


def _env() -> dict[str, str]:
    import os

    return {k: v for k, v in os.environ.items() if not k.startswith(("LANGSMITH", "LANGCHAIN"))}


# --- A2: a run resumes only with the graph it was started with ---------------------------


def test_a2_resume_refuses_a_differently_built_graph(tmp_path: Path) -> None:
    run_id = str(ULID())
    first = Spy(hooks={("CCC", "news"): raiser(KeyboardInterrupt())})
    with pytest.raises(KeyboardInterrupt):
        run_graph(
            build_research_graph(first.team(), max_concurrency=2),
            INPUTS,
            run_id=run_id,
            lake_path=tmp_path,
        )
    other_roster = Spy(("fundamental", "news"))
    with pytest.raises(OrchestrationError, match="built differently"):
        resume_graph(
            build_research_graph(other_roster.team(), max_concurrency=2),
            run_id=run_id,
            lake_path=tmp_path,
        )
    assert other_roster.calls == []
    # The same graph (another concurrency limit is fine) resumes.
    same = Spy()
    out = resume_graph(
        build_research_graph(same.team(), max_concurrency=3), run_id=run_id, lake_path=tmp_path
    )
    assert set(out["theses"]) == set(SYMBOLS)


# --- A3: malformed inputs never create a checkpoint ---------------------------------------


@pytest.mark.parametrize(
    "inputs",
    [{"research_queue": ["AAA"]}, {"as_of": DAY, "research_queue": ["bad symbol!"]}],
)
def test_a3_malformed_inputs_leave_no_checkpoint(tmp_path: Path, inputs: dict[str, Any]) -> None:
    run_id = str(ULID())
    graph = build_research_graph(stub_team(), max_concurrency=2)
    with pytest.raises(OrchestrationError):
        run_graph(graph, inputs, run_id=run_id, lake_path=tmp_path)
    assert not checkpoint_path(tmp_path, run_id).exists()
    out = run_graph(graph, INPUTS, run_id=run_id, lake_path=tmp_path)  # the id is still usable
    assert set(out["theses"]) == set(SYMBOLS)


# --- A4: the fund state follows the queue order, whatever finishes first -----------------


@pytest.mark.parametrize("seed", range(3))
def test_a4_fund_level_order_is_the_queue_order(seed: int) -> None:
    rng = random.Random(seed)
    delays = {s: rng.uniform(0, 0.03) for s in SYMBOLS}
    spy = Spy(hooks={(s, "director"): (lambda d=d: time.sleep(d)) for s, d in delays.items()})
    out = build_research_graph(spy.team(), max_concurrency=5).invoke(INPUTS)
    assert list(out["theses"]) == SYMBOLS
    assert list(out["reports"]) == SYMBOLS


# --- A5: gaps found by the mutation run ----------------------------------------------------


def _record_invokes(monkeypatch: pytest.MonkeyPatch, module: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    real = module.invoke_untraced

    def spy(app: Any, inputs: Any, config: Any, **kwargs: Any) -> Any:
        calls.append({"config": config, **kwargs})
        return real(app, inputs, config, **kwargs)

    monkeypatch.setattr(module, "invoke_untraced", spy)
    return calls


def test_a5_checkpointed_runs_are_durable_step_by_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas.orchestration import checkpoint

    calls = _record_invokes(monkeypatch, checkpoint)
    graph = build_research_graph(stub_team(), max_concurrency=3)
    run_id = str(ULID())
    run_graph(graph, INPUTS, run_id=run_id, lake_path=tmp_path)
    resume_graph(graph, run_id=run_id, lake_path=tmp_path)
    assert [c["durability"] for c in calls] == ["sync", "sync"]
    for call in calls:
        assert call["config"] == graph.config(thread_id=run_id)
        assert call["config"]["max_concurrency"] == 3


def test_a5_in_memory_runs_pass_the_concurrency_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from atlas.orchestration import research_graph

    calls = _record_invokes(monkeypatch, research_graph)
    graph = build_research_graph(stub_team(), max_concurrency=2)
    graph.invoke(INPUTS)
    assert [c["config"]["max_concurrency"] for c in calls] == [2]


@pytest.mark.parametrize("node", ["news", "debate", "director"])
def test_a5_every_agent_call_holds_the_limiter(node: str) -> None:
    import threading

    from atlas.orchestration.research_graph import build_research_subgraph

    limiter = threading.BoundedSemaphore(1)
    held: list[bool] = []
    spy = Spy(hooks={("AAA", node): lambda: held.append(not limiter.acquire(blocking=False))})
    out = build_research_subgraph(spy.team(), limiter=limiter).invoke(
        {"symbol": "AAA", "as_of": DAY}
    )
    assert held == [True] and out["errors"] == []


def test_a5_a_message_of_exactly_the_limit_is_kept_whole() -> None:
    from atlas.orchestration.errors import MAX_MESSAGE_CHARS, guarded

    text = "z" * MAX_MESSAGE_CHARS

    def work(state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(text)

    [error] = guarded("news", work)({"symbol": "AAA", "as_of": DAY})["errors"]
    assert error.message == text


def test_a5_runs_log_their_id_checkpoint_and_dropped_symbols(tmp_path: Path) -> None:
    from structlog.testing import capture_logs

    run_id = str(ULID())
    spy = Spy(hooks={("BBB", "news"): raiser(RuntimeError("x"))})
    graph = build_research_graph(spy.team(), max_concurrency=2)
    with capture_logs() as logs:
        run_graph(graph, INPUTS, run_id=run_id, lake_path=tmp_path)
        resume_graph(graph, run_id=run_id, lake_path=tmp_path)
    path = str(checkpoint_path(tmp_path, run_id))
    events = {r["event"]: r for r in logs}
    for event in ("orchestration_run_started", "orchestration_run_resumed"):
        assert (events[event]["run_id"], events[event]["checkpoint"]) == (run_id, path)
    done = events["orchestration_research_done"]
    assert (done["as_of"], done["dropped"], done["researched"]) == (str(DAY), ["BBB"], 4)


@pytest.mark.parametrize(
    ("node", "replace", "prefix"),
    [
        ("news", lambda out: "text", "analyst 'news' returned str"),
        ("debate", lambda out: [7], "debate returned a int"),
        ("director", lambda out: "text", "director returned str"),
    ],
)
def test_a5_bad_output_messages_name_the_agent_and_the_type(
    node: str, replace: Any, prefix: str
) -> None:
    from atlas.orchestration.research_graph import build_research_subgraph

    spy = Spy(outputs={("AAA", node): replace})
    out = build_research_subgraph(spy.team()).invoke({"symbol": "AAA", "as_of": DAY})
    [error] = out["errors"]
    assert error.message.startswith(prefix)


def test_a5_invoke_untraced_passes_every_argument_through() -> None:
    from atlas.orchestration.research_graph import invoke_untraced

    seen: list[tuple[Any, ...]] = []

    class App:
        def invoke(self, inputs: Any, config: Any, **kwargs: Any) -> dict[str, Any]:
            seen.append((inputs, config, kwargs, tracing_is_enabled()))
            return {"ok": True}

    with tracing_context(enabled=True):
        out = invoke_untraced(App(), {"a": 1}, {"b": 2}, durability="sync")  # type: ignore[arg-type]
    assert out == {"ok": True}
    assert seen == [({"a": 1}, {"b": 2}, {"durability": "sync"}, False)]
