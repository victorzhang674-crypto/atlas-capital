"""Stage 26 Task 8: agent traces (in memory and in `agent_runs`) and `AGENT_REPORT` events."""

import json
from pathlib import Path

import pytest
from agent_helpers import EchoAnalyst, StepClock, ToolAnalyst, report
from agent_world import AGENT_PROMPTS, World

from atlas.agents.prompting import PromptRegistry
from atlas.agents.trace import AgentTrace, TraceStore, trace_from_row, trace_row
from atlas.core.audit import AuditLog, EventType
from atlas.core.config import AtlasConfig
from atlas.core.errors import BudgetExceeded, LLMError
from atlas.core.run_context import run_context
from atlas.core.storage import Database, Lake
from atlas.llm import FakeLLM, FakeReply, LLMGateway
from atlas.llm.schemas import ToolCall


def agent(fake: FakeLLM, cls: type = EchoAnalyst, **kw: object) -> EchoAnalyst:
    return cls(  # type: ignore[no-any-return]
        LLMGateway(fake, AtlasConfig()),
        prompts=PromptRegistry([AGENT_PROMPTS]),
        clock=StepClock(),
        **kw,
    )


def tool_run(world: World, fake: FakeLLM, traces: TraceStore) -> AgentTrace:
    fake.add(
        "tool_analyst",
        None,
        FakeReply(tool_calls=(ToolCall(id="c1", name="news", arguments={"symbol": "AAA"}),)),
        report("news:alpaca-2"),
    )
    return agent(fake, ToolAnalyst, traces=traces).run(world.ctx(inputs={"symbol": "AAA"})).trace


def test_a_run_writes_one_trace(world: World, fake_llm: FakeLLM) -> None:
    traces = TraceStore()
    with run_context() as run:
        trace = tool_run(world, fake_llm, traces)
    assert traces.traces() == (trace,) and traces.traces("tool_analyst") == (trace,)
    assert traces.traces("other") == ()
    assert trace.run_id == run.run_id and len(trace.agent_run_id) == 26
    assert trace.agent == "tool_analyst" and trace.tier == "quick"
    assert str(trace.as_of) == "2024-03-01" and trace.cutoff == world.view().as_of.cutoff
    assert trace.finished_at > trace.started_at
    assert (trace.prompt_name, trace.prompt_version) == ("echo_analyst.j2", 1)
    assert len(trace.prompt_digest) == 64 and len(trace.inputs_digest) == 64
    assert trace.versions == "echo_analyst.j2@v1|fundamentals@v1,news@v1,price_history@v1"
    assert [c.name for c in trace.tool_calls] == ["news"]
    assert trace.evidence_ids == ("news:alpaca-2", "news:alpaca-1")
    assert trace.cited_ids == ("news:alpaca-2",) and trace.unknown_ids == ()
    assert json.loads(trace.output_json or "") == report("news:alpaca-2").model_dump(mode="json")
    assert trace.llm_calls == 2 and trace.cost_usd > 0 and trace.usage.output_tokens > 0
    assert trace.status == "ok" and trace.error is None


def test_inputs_digest_is_stable_and_follows_the_inputs(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, report())
    echo = agent(fake_llm)
    first = echo.run(world.ctx(inputs={"symbol": "AAA"})).trace
    second = echo.run(world.ctx(inputs={"symbol": "AAA"})).trace
    other = echo.run(world.ctx(inputs={"symbol": "BBB"})).trace
    assert first.inputs_digest == second.inputs_digest != other.inputs_digest
    assert first.agent_run_id != second.agent_run_id


def test_a_failed_run_is_traced_and_the_error_re_raised(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, BudgetExceeded("daily budget", scope="daily_usd",
                                                      limit=1.0, spent=1.0))  # fmt: skip
    traces = TraceStore()
    with pytest.raises(LLMError, match="daily budget"):
        agent(fake_llm, traces=traces).run(world.ctx(inputs={"symbol": "AAA"}))
    (trace,) = traces.traces()
    assert trace.status == "failed" and "daily budget" in (trace.error or "")
    assert trace.output_json is None and trace.llm_calls == 0
    assert trace.evidence_ids == ("news:alpaca-2", "news:alpaca-1")
    assert len(trace.inputs_digest) == 64


def test_row_round_trip(world: World, fake_llm: FakeLLM) -> None:
    trace = tool_run(world, fake_llm, TraceStore())
    assert trace_from_row(trace_row(trace)) == trace


def test_traces_persist_in_agent_runs(world: World, fake_llm: FakeLLM, tmp_lake: Lake) -> None:
    with Database(tmp_lake) as db:
        stored = TraceStore(db)
        trace = tool_run(world, fake_llm, stored)
        frame = db.query("select agent_run_id, agent, status, cost_usd from agent_runs")
        assert frame["agent_run_id"].tolist() == [trace.agent_run_id]
        assert frame["status"].tolist() == ["ok"]
        assert stored.traces() == (trace,)
        assert stored.traces("tool_analyst") == (trace,) and stored.traces("x") == ()
    with Database(tmp_lake) as reopened:
        assert TraceStore(reopened).traces() == (trace,)


def test_memory_and_duckdb_stores_agree(world: World, tmp_lake: Lake) -> None:
    memory = TraceStore()
    trace = tool_run(world, FakeLLM(), memory)
    with Database(tmp_lake) as db:
        on_disk = TraceStore(db)
        on_disk.save(trace)
        assert on_disk.traces() == memory.traces()


def test_agent_report_is_audited(world: World, fake_llm: FakeLLM, tmp_path: Path) -> None:
    fake_llm.add("echo_analyst", None, report("news:alpaca-2"), report("news:alpaca-2"))
    path = tmp_path / "audit.jsonl"
    with run_context(as_of=world.view().day) as run:
        log = AuditLog(path)
        out = agent(fake_llm, audit=log).run(world.ctx(inputs={"symbol": "AAA"}))
    (event,) = [json.loads(line) for line in path.read_text().splitlines()]
    assert event["event_type"] == EventType.AGENT_REPORT == "AGENT_REPORT"
    assert event["actor"] == "echo_analyst" and event["run_id"] == run.run_id
    payload = event["payload"]
    assert payload["agent_run_id"] == out.trace.agent_run_id
    assert payload["status"] == "ok" and payload["prompt"] == "echo_analyst.j2@v1"
    assert len(payload["output_sha256"]) == 64 and payload["error"] is None
    assert AuditLog.verify_chain(path)


def test_a_failed_run_is_audited_too(world: World, fake_llm: FakeLLM, tmp_path: Path) -> None:
    fake_llm.add("echo_analyst", None, LLMError("provider down"))
    path = tmp_path / "audit.jsonl"
    with run_context():
        log = AuditLog(path)
        with pytest.raises(LLMError):
            agent(fake_llm, audit=log).run(world.ctx(inputs={"symbol": "AAA"}))
    (event,) = [json.loads(line) for line in path.read_text().splitlines()]
    assert event["payload"]["status"] == "failed"
    assert event["payload"]["output_sha256"] is None
    assert "provider down" in event["payload"]["error"]
