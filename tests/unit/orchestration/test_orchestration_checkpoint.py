"""Stage 28 Task 6 (brief task 3): SQLite checkpoints; kill mid-run, resume, same final state."""

import logging
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from graph_world import DAY, Spy, full_fund_state, full_research_state, raiser
from ulid import ULID

from atlas.core.errors import OrchestrationError
from atlas.core.types import Evidence
from atlas.orchestration.checkpoint import (
    CHECKPOINT_FILE,
    checkpoint_path,
    resume_graph,
    run_graph,
    serializer,
)
from atlas.orchestration.research_graph import build_research_graph
from atlas.orchestration.state import state_types
from atlas.orchestration.stubs import ANALYSTS, stub_team

SYMBOLS = ["AAA", "BBB", "CCC", "DDD", "EEE"]
INPUTS: dict[str, Any] = {"as_of": DAY, "research_queue": SYMBOLS}
HERE = Path(__file__).resolve().parent


def _run_id() -> str:
    return str(ULID())


def _clean_final() -> dict[str, Any]:
    out: dict[str, Any] = build_research_graph(stub_team(), max_concurrency=2).invoke(INPUTS)
    return out


def _assert_same_final_state(got: dict[str, Any], want: dict[str, Any]) -> None:
    assert set(got) == set(want)
    for key in want:
        assert got[key] == want[key], key
    for symbol in want["reports"]:
        assert list(got["reports"][symbol]) == list(ANALYSTS)  # roster order survives


# --- path and run id --------------------------------------------------------------------


def test_the_checkpoint_lives_in_the_run_folder(tmp_path: Path) -> None:
    run_id = _run_id()
    assert CHECKPOINT_FILE == "checkpoints.sqlite"
    assert checkpoint_path(tmp_path, run_id) == tmp_path / "runs" / run_id / "checkpoints.sqlite"


@pytest.mark.parametrize(
    # Fixed strings: xdist workers must collect identical test ids (no random ULIDs here).
    "bad",
    [
        "",
        "abc",
        "../" + "0" * 23,
        "01jd8x3k5v2n7q9r4t6w8y0z1a",
        "8" + "0" * 25,
        "01JD8X3K5V2N7Q9R4T6W8Y0Z1A/x",
    ],
)
def test_a_run_id_must_be_a_ulid(tmp_path: Path, bad: str) -> None:
    with pytest.raises(OrchestrationError, match="run id"):
        checkpoint_path(tmp_path, bad)


# --- serializer -------------------------------------------------------------------------


def test_state_types_cover_every_contract_in_the_state() -> None:
    names = {name for _, name in state_types()}
    assert {
        "AnalystReport",
        "Signal",
        "Direction",
        "InvestmentThesis",
        "ReturnDistribution",
        "Catalyst",
        "ThesisStatus",
        "DebateTurn",
        "Claim",
        "DebateRole",
        "MacroOutlook",
        "AssetView",
        "PodProposal",
        "View",
        "CapitalAllocation",
        "TargetPortfolio",
        "RiskDecision",
        "RiskStatus",
        "Breach",
        "ComplianceResult",
        "Violation",
        "Severity",
        "Order",
        "Side",
        "OrderType",
        "TimeInForce",
        "OrderStatus",
        "NodeError",
        "RegimeState",
    } <= names
    assert len(state_types()) == len(set(state_types()))


@pytest.mark.parametrize("make", [full_fund_state, full_research_state])
def test_every_state_value_round_trips_through_the_serializer(
    make: Any, caplog: pytest.LogCaptureFixture
) -> None:
    state = make()
    serde = serializer()
    with caplog.at_level(logging.DEBUG):
        back = serde.loads_typed(serde.dumps_typed(state))
    assert back == state
    for key, value in state.items():
        assert type(back[key]) is type(value), key
    assert not [r for r in caplog.records if "eserializ" in r.getMessage()]


def test_a_type_outside_the_allow_list_comes_back_as_a_plain_dict() -> None:
    # Documents plan D1 (j): LangGraph does not raise for a blocked type, so the allow-list
    # must cover the state (test above); anything else is not rebuilt.
    serde = serializer()
    evidence = Evidence(
        id="e1", source="x", as_of=full_fund_state()["orders"][0].created_at, text="t"
    )
    back = serde.loads_typed(serde.dumps_typed({"x": evidence}))
    assert isinstance(back["x"], dict)


# --- run and resume ---------------------------------------------------------------------


def test_run_graph_checkpoints_and_returns_the_final_state(tmp_path: Path) -> None:
    run_id = _run_id()
    graph = build_research_graph(stub_team(), max_concurrency=2)
    out = run_graph(graph, INPUTS, run_id=run_id, lake_path=tmp_path)
    _assert_same_final_state(out, _clean_final())
    path = checkpoint_path(tmp_path, run_id)
    assert path.is_file()
    with sqlite3.connect(path) as conn:
        threads = {row[0] for row in conn.execute("SELECT DISTINCT thread_id FROM checkpoints")}
    assert threads == {run_id}


def test_a_run_id_with_a_checkpoint_cannot_be_run_again(tmp_path: Path) -> None:
    run_id = _run_id()
    graph = build_research_graph(stub_team(), max_concurrency=2)
    run_graph(graph, INPUTS, run_id=run_id, lake_path=tmp_path)
    with pytest.raises(OrchestrationError, match="resume"):
        run_graph(graph, INPUTS, run_id=run_id, lake_path=tmp_path)


def test_resume_needs_a_checkpoint(tmp_path: Path) -> None:
    graph = build_research_graph(stub_team(), max_concurrency=2)
    run_id = _run_id()
    with pytest.raises(OrchestrationError, match="no checkpoint"):
        resume_graph(graph, run_id=run_id, lake_path=tmp_path)
    assert not checkpoint_path(tmp_path, run_id).exists()  # resume never creates the file
    path = checkpoint_path(tmp_path, run_id)
    path.parent.mkdir(parents=True)
    sqlite3.connect(path).close()  # an empty file (killed before the first checkpoint)
    with pytest.raises(OrchestrationError, match="no checkpoint"):
        resume_graph(graph, run_id=run_id, lake_path=tmp_path)
    out = run_graph(graph, INPUTS, run_id=run_id, lake_path=tmp_path)  # a fresh run is fine
    assert set(out["theses"]) == set(SYMBOLS)


def test_ctrl_c_mid_run_then_resume_gives_the_same_final_state(tmp_path: Path) -> None:
    run_id = _run_id()
    first = Spy(hooks={("CCC", "news"): raiser(KeyboardInterrupt())})
    with pytest.raises(KeyboardInterrupt):
        run_graph(
            build_research_graph(first.team(), max_concurrency=2),
            INPUTS,
            run_id=run_id,
            lake_path=tmp_path,
        )
    done_before = {s for s, node in first.calls if node == "director"}
    second = Spy()
    out = resume_graph(
        build_research_graph(second.team(), max_concurrency=2), run_id=run_id, lake_path=tmp_path
    )
    _assert_same_final_state(out, _clean_final())
    resumed = {s for s, _ in second.calls}
    assert "CCC" in resumed
    assert not resumed & done_before  # a finished symbol is not researched again
    assert ("CCC", "news") in second.calls


def _wait_for(log: Path, predicate: Any, proc: subprocess.Popen[bytes], timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = log.read_text() if log.exists() else ""
        if predicate(text):
            return text
        if proc.poll() is not None:
            stderr = (log.parent / "worker.err").read_text()
            raise AssertionError(f"worker exited early ({proc.returncode}):\n{text}\n{stderr}")
        time.sleep(0.05)
    raise AssertionError(f"timed out; log:\n{log.read_text() if log.exists() else ''}")


def test_a_killed_process_resumes_from_its_last_checkpoint(tmp_path: Path) -> None:
    run_id = _run_id()
    log = tmp_path / "calls.log"
    others = [s for s in SYMBOLS if s != "CCC"]

    def ready(text: str) -> bool:
        return "CCC news blocked" in text and all(f"{s} director done" in text for s in others)

    err = (tmp_path / "worker.err").open("wb")
    proc = subprocess.Popen(
        [
            sys.executable,
            str(HERE / "kill_worker.py"),
            str(tmp_path),
            run_id,
            str(log),
            "CCC",
            ",".join(SYMBOLS),
        ],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=err,
    )
    try:
        text = _wait_for(log, ready, proc, timeout=60)
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=30)
        err.close()
    assert proc.returncode == -signal.SIGKILL
    assert "finished" not in text

    spy = Spy()
    out = resume_graph(
        build_research_graph(spy.team(), max_concurrency=2), run_id=run_id, lake_path=tmp_path
    )
    _assert_same_final_state(out, _clean_final())
    resumed = {s for s, _ in spy.calls}
    assert resumed == {"CCC"}  # the four finished symbols are not researched again
    finished_ccc = {
        line.split()[1]
        for line in text.splitlines()
        if line.startswith("CCC ") and line.endswith(" done")
    }
    assert ("CCC", "news") in spy.calls
    assert not {node for _, node in spy.calls} & finished_ccc  # nor CCC's finished analysts


def test_resuming_a_finished_run_reruns_nothing(tmp_path: Path) -> None:
    run_id = _run_id()
    graph = build_research_graph(stub_team(), max_concurrency=2)
    first = run_graph(graph, INPUTS, run_id=run_id, lake_path=tmp_path)
    spy = Spy()
    again = resume_graph(
        build_research_graph(spy.team(), max_concurrency=2), run_id=run_id, lake_path=tmp_path
    )
    assert spy.calls == []
    _assert_same_final_state(again, first)
