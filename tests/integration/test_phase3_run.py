"""Phase 3 end to end, offline (phase 3 checkpoint): the LLM and agent infrastructure of
Stages 24-28 run together, so CI covers the chain each stage tested alone.

Two real `BaseAgent`s (26) sit in the analyst slots of the Stage 28 research graph: a news
analyst with pre-fetched evidence and recalled memories (27), and a fundamental analyst that
works through the tool loop. They talk to an `LLMGateway` (24) with the record/replay cache
(25), the cost ledger, the trace store and the agent memory all in the lake's one DuckDB
file, and an audit log per run. The debate and the director are the Stage 28 stubs (the real
ones are Phase 4). The model is a `FakeLLM` that answers from each request, so four symbols
researched on four threads each get their own answer.

Run 1 is interrupted (Ctrl-C inside one symbol's research) and resumed from its checkpoint.
Run 2 replays the same day from the cache with no provider at all (`OfflineProvider`, mode
`replay_only`): spec §3 principle 3, "a re-run in replay mode makes zero API calls and gives
identical results". Run 3 replays with one recording missing: the miss is recorded and that
symbol dropped, the others still replay.

The lake is the Stage 13 test world: AAA (news, fundamentals), BBB (fundamentals), CCC
(prices only) and BAD (blocked by a data-quality issue), decision day 2024-03-01.
"""

import json
import re
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

for _folder in ("agents", "data"):
    _path = str(Path(__file__).resolve().parents[1] / "unit" / _folder)
    if _path not in sys.path:
        sys.path.append(_path)

from agent_world import World, build_agent_world  # noqa: E402

from atlas.agents.base import BaseAgent  # noqa: E402
from atlas.agents.context import AgentContext  # noqa: E402
from atlas.agents.memory import AgentMemory, HashEmbedder  # noqa: E402
from atlas.agents.prompting import PromptRegistry  # noqa: E402
from atlas.agents.trace import TraceStore  # noqa: E402
from atlas.core.audit import AuditLog, EventType, audit_path  # noqa: E402
from atlas.core.config import AgentsConfig, AtlasConfig, MemoryConfig  # noqa: E402
from atlas.core.run_context import run_context  # noqa: E402
from atlas.core.storage.db import Database, get_database  # noqa: E402
from atlas.core.types.research import (  # noqa: E402
    AnalystReport,
    Direction,
    Evidence,
    Signal,
)
from atlas.data.view import DataView  # noqa: E402
from atlas.llm import (  # noqa: E402
    DATA_POLICY,
    CostLedger,
    FakeLLM,
    FakeReply,
    LLMCache,
    LLMGateway,
    ProviderRequest,
    ToolCall,
)
from atlas.llm.providers.offline import OfflineProvider  # noqa: E402
from atlas.orchestration import (  # noqa: E402
    ResearchTask,
    ResearchTeam,
    build_research_graph,
    checkpoint_path,
    resume_graph,
    run_graph,
)
from atlas.orchestration.stubs import stub_debate, stub_director  # noqa: E402

pytestmark = pytest.mark.integration

DAY = date(2024, 3, 1)
QUEUE = ["AAA", "BBB", "CCC", "BAD", "AAA"]  # one blocked symbol, one duplicate
RESEARCHED = ["AAA", "BBB", "CCC"]
PROMPTS = Path(__file__).resolve().parent / "phase3_prompts"
CONFIG = AtlasConfig(agents=AgentsConfig(memory=MemoryConfig(embedder="hash", min_similarity=0.1)))

PAST_LESSON = "AAA news about improved profitability was followed by gains"
FUTURE_LESSON = "AAA news about bankruptcy fears came before the decline"

_ASK = re.compile(r'AnalystReport for (\w+) as of (\S+) \(analyst: "(\w+)"\)')
_TAG = re.compile(r"\[E:([A-Za-z0-9_.:/-]+)\]")  # real ids only, not the rules' "<id>"


class NewsAnalyst(BaseAgent[AnalystReport]):
    """Pre-fetches the symbol's news and recalls its own lessons about it."""

    name = "news"
    tier = "quick"
    prompt_template = "phase3_news.j2"
    output_schema = AnalystReport

    def gather(self, ctx: AgentContext) -> Sequence[Evidence]:
        return ctx.view.news(ctx.inputs["symbol"])

    def prompt_inputs(self, ctx: AgentContext) -> Mapping[str, Any]:
        symbol = ctx.inputs["symbol"]
        return {"symbol": symbol, "memories": ctx.recall(self.name, f"{symbol} news", k=3)}


class FundamentalAnalyst(BaseAgent[AnalystReport]):
    """Starts with no data and asks the tools for it."""

    name = "fundamental"
    tier = "deep"
    prompt_template = "phase3_fundamental.j2"
    output_schema = AnalystReport
    tools = ("fundamentals", "price_history")


def _report(request: ProviderRequest) -> AnalystReport:
    """The answer a careful model would give: the asked symbol, day and analyst, and one
    signal per evidence id the conversation showed (at most three); none when it showed none."""
    text = "\n".join(m.content for m in request.messages if m.role in ("user", "tool"))
    asked = _ASK.search(text)
    assert asked is not None, text[:200]
    ids = list(dict.fromkeys(_TAG.findall(text)))[:3]
    return AnalystReport(
        ticker=asked.group(1),
        as_of=date.fromisoformat(asked.group(2)),
        analyst=asked.group(3),
        summary=f"{asked.group(3)} view on {asked.group(1)}",
        signals=tuple(
            Signal(name=f"s{i}", direction=Direction.LONG, strength=0.5, evidence_ids=(e,))
            for i, e in enumerate(ids)
        ),
        risks=("test world",),
        confidence=0.7 if ids else 0.2,
    )


def _ask_tools(request: ProviderRequest) -> FakeReply:
    asked = _ASK.search(request.messages[1].content)
    assert asked is not None
    symbol = asked.group(1)
    return FakeReply(
        tool_calls=(
            ToolCall(id=f"{symbol}-1", name="fundamentals", arguments={"symbol": symbol}),
            ToolCall(
                id=f"{symbol}-2", name="price_history", arguments={"symbol": symbol, "lookback": 5}
            ),
        )
    )


def scripted_model() -> FakeLLM:
    """A model that answers every request from the request itself."""
    fake = FakeLLM()
    fake.add("news", None, _report)
    fake.add("fundamental", "round-1", _ask_tools)
    fake.add("fundamental", None, _report)
    return fake


@dataclass
class Fund:
    """The shared parts of the three runs: one lake, one database, one memory."""

    world: World
    view: DataView
    database: Database
    memory: AgentMemory
    traces: TraceStore
    interrupt_once: set[str]
    done: set[tuple[str, str]] = field(default_factory=set)
    progress: threading.Condition = field(default_factory=threading.Condition)

    def _mark(self, symbol: str, who: str) -> None:
        with self.progress:
            self.done.add((symbol, who))
            self.progress.notify_all()

    def _wait_for_the_rest(self, symbol: str) -> None:
        """Block until every other piece of agent work of the day has finished, so the
        interrupt that follows leaves nothing half-done on another thread."""
        rest = {(s, a) for s in QUEUE for a in ("news", "fundamental")}
        rest |= {(s, "director") for s in RESEARCHED if s != symbol}
        with self.progress:
            assert self.progress.wait_for(lambda: rest <= self.done, timeout=60), rest - self.done

    @property
    def lake_path(self) -> Path:
        return self.world.clean.root

    def gateway(self, provider: Any, mode: str) -> LLMGateway:
        return LLMGateway(
            provider,
            CONFIG,
            ledger=CostLedger(self.database),
            cache=LLMCache(self.database),
            cache_mode=mode,  # type: ignore[arg-type]
        )

    def graph(self, gateway: LLMGateway, audit: AuditLog) -> Any:
        prompts = PromptRegistry([PROMPTS])
        agents: dict[str, BaseAgent[AnalystReport]] = {
            "news": NewsAnalyst(
                gateway, config=CONFIG, prompts=prompts, traces=self.traces, audit=audit
            ),
            "fundamental": FundamentalAnalyst(
                gateway, config=CONFIG, prompts=prompts, traces=self.traces, audit=audit
            ),
        }

        def slot(agent: BaseAgent[AnalystReport]) -> Any:
            def run(task: ResearchTask) -> AnalystReport:
                try:
                    # Every agent of the day reads the same view, from its own thread.
                    assert task.as_of == self.view.day
                    ctx = AgentContext(
                        self.view, memory=self.memory, inputs={"symbol": task.symbol}
                    )
                    return agent.run(ctx).output
                finally:
                    self._mark(task.symbol, agent.name)

            return run

        def director(task: ResearchTask, reports: Any, turns: Any) -> Any:
            if task.symbol in self.interrupt_once:
                self._wait_for_the_rest(task.symbol)
                self.interrupt_once.discard(task.symbol)
                raise KeyboardInterrupt  # the operator stops the run mid-symbol
            thesis = stub_director(task, reports, turns)
            self._mark(task.symbol, "director")
            return thesis

        team = ResearchTeam(
            analysts={name: slot(agent) for name, agent in agents.items()},
            debate=stub_debate,
            director=director,
        )
        return build_research_graph(team, max_concurrency=CONFIG.orchestration.max_concurrency)


@pytest.fixture(scope="module")
def fund(tmp_path_factory: pytest.TempPathFactory) -> Fund:
    world = build_agent_world(tmp_path_factory.mktemp("phase3"))
    database = get_database(world.clean)
    memory = AgentMemory(HashEmbedder(), database=database, config=CONFIG)
    memory.add_lesson(
        "news", PAST_LESSON, created_at=datetime(2024, 2, 20, 21, tzinfo=UTC), tags=("AAA",)
    )
    memory.add_lesson(  # written after the decision day: no agent may see it on 2024-03-01
        "news", FUTURE_LESSON, created_at=datetime(2024, 3, 8, 21, tzinfo=UTC), tags=("AAA",)
    )
    view = world.view("clean", DAY)
    return Fund(world, view, database, memory, TraceStore(database), interrupt_once={"BBB"})


@dataclass
class Run:
    run_id: str
    state: dict[str, Any]
    requests: list[ProviderRequest]


@pytest.fixture(scope="module")
def recorded(fund: Fund) -> Run:
    """Run 1: recorded with the scripted model, interrupted once and resumed."""
    model = scripted_model()
    gateway = fund.gateway(model, "read_write")
    inputs = {"as_of": DAY, "research_queue": QUEUE}
    with run_context(mode="backtest", as_of=DAY) as ctx:
        graph = fund.graph(gateway, AuditLog(audit_path(fund.lake_path, ctx.run_id)))
        with pytest.raises(KeyboardInterrupt):
            run_graph(graph, inputs, run_id=ctx.run_id, lake_path=fund.lake_path)  # type: ignore[arg-type]
        before = len(model.calls)
        state = resume_graph(graph, run_id=ctx.run_id, lake_path=fund.lake_path)
        # Every agent had finished before the interrupt: the resume asks the model nothing.
        assert len(model.calls) == before
    return Run(ctx.run_id, dict(state), list(model.calls))


@pytest.fixture(scope="module")
def replayed(fund: Fund, recorded: Run) -> Run:
    """Run 2: the same day replayed from the cache, with no provider behind the gateway."""
    gateway = fund.gateway(OfflineProvider("fake"), "replay_only")
    with run_context(mode="backtest", as_of=DAY) as ctx:
        graph = fund.graph(gateway, AuditLog(audit_path(fund.lake_path, ctx.run_id)))
        state = run_graph(
            graph,
            {"as_of": DAY, "research_queue": QUEUE},  # type: ignore[arg-type]
            run_id=ctx.run_id,
            lake_path=fund.lake_path,
        )
    return Run(ctx.run_id, dict(state), [])


def _usage(fund: Fund, run_id: str) -> Any:
    return fund.database.query("SELECT * FROM llm_usage WHERE run_id = ?", [run_id])


def _traces(fund: Fund, run_id: str) -> Any:
    return fund.database.query("SELECT * FROM agent_runs WHERE run_id = ?", [run_id])


def _audit_events(fund: Fund, run_id: str) -> list[dict[str, Any]]:
    path = audit_path(fund.lake_path, run_id)
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_the_day_is_researched_and_the_blocked_symbol_dropped(recorded: Run) -> None:
    state = recorded.state
    assert state["research_queue"] == ["AAA", "BBB", "CCC", "BAD"]  # the duplicate is gone
    assert list(state["theses"]) == RESEARCHED and list(state["reports"]) == RESEARCHED
    for symbol in RESEARCHED:
        reports = state["reports"][symbol]
        assert list(reports) == ["news", "fundamental"]  # roster order
        assert all(r.ticker == symbol and r.as_of == DAY for r in reports.values())
        assert state["theses"][symbol].ticker == symbol
    errors = state["errors"]
    assert {e.symbol for e in errors} == {"BAD"}
    assert [(e.node, e.kind) for e in errors] == [("news", "DataError")]
    assert "blocked" in errors[0].message


def test_reports_cite_only_evidence_the_agents_were_shown(fund: Fund, recorded: Run) -> None:
    traces = _traces(fund, recorded.run_id)
    by_status = traces.groupby("status").size().to_dict()
    # BBB and CCC have no news: their news reports cite nothing and are marked low quality.
    # BAD is blocked: the news analyst fails on its pre-fetch; the fundamental analyst is told
    # so by its tools and can only answer without evidence (low quality).
    assert by_status == {"ok": 4, "low_quality": 3, "failed": 1}
    reports = recorded.state["reports"]
    assert reports["AAA"]["news"].confidence == pytest.approx(0.7)
    cited = {e for s in reports["AAA"]["news"].signals for e in s.evidence_ids}
    assert cited == {"news:alpaca-1", "news:alpaca-2"}
    assert reports["CCC"]["news"].signals == () and reports["CCC"]["news"].confidence <= 0.3
    for symbol in RESEARCHED:
        used = {e for s in reports[symbol]["fundamental"].signals for e in s.evidence_ids}
        assert used, symbol
        assert all(e.startswith((f"fund:{symbol}:", f"px:{symbol}:")) for e in used), used


def test_prompts_are_point_in_time_and_carry_the_data_policy(recorded: Run) -> None:
    requests = recorded.requests
    assert requests
    everything = "\n".join(m.content for r in requests for m in r.messages)
    assert PAST_LESSON in everything  # recalled by the news analyst
    assert FUTURE_LESSON not in everything  # written a week after the decision day
    for request in requests:
        system = request.messages[0]
        assert system.role == "system" and DATA_POLICY in system.content
        assert f"Today is {DAY.isoformat()}" in system.content
        assert request.metadata["prompt_version"].startswith("phase3_")
    tool_results = [m.content for r in requests for m in r.messages if m.role == "tool"]
    data = [t for t in tool_results if t.startswith('<data source="tool:')]
    assert data and all(t.startswith("ERROR: ") for t in tool_results if t not in data)
    assert {r.tier for r in requests if r.metadata["agent"] == "fundamental"} == {"deep"}
    assert {r.tier for r in requests if r.metadata["agent"] == "news"} == {"quick"}


def test_every_call_is_costed_under_the_run_and_matches_the_traces(
    fund: Fund, recorded: Run
) -> None:
    usage = _usage(fund, recorded.run_id)
    assert len(usage) == len(recorded.requests) > 0
    assert not usage["cache_hit"].any() and (usage["cost_usd"] > 0).all()
    assert set(usage["agent"]) == {"news", "fundamental"}
    assert set(usage["model"]) == {CONFIG.llm.model_for("quick"), CONFIG.llm.model_for("deep")}
    traces = _traces(fund, recorded.run_id)
    assert traces["cost_usd"].sum() == pytest.approx(usage["cost_usd"].sum())
    assert traces["llm_calls"].sum() == len(usage)


def test_the_audit_log_has_one_chained_event_per_agent_run(fund: Fund, recorded: Run) -> None:
    path = audit_path(fund.lake_path, recorded.run_id)
    assert AuditLog.verify_chain(path)
    events = _audit_events(fund, recorded.run_id)
    assert {e["event_type"] for e in events} == {EventType.AGENT_REPORT.value}
    assert {e["run_id"] for e in events} == {recorded.run_id}
    traces = _traces(fund, recorded.run_id)
    assert sorted(e["payload"]["agent_run_id"] for e in events) == sorted(traces["agent_run_id"])
    assert checkpoint_path(fund.lake_path, recorded.run_id).is_file()


def test_a_replay_makes_no_provider_call_and_gives_identical_results(
    fund: Fund, recorded: Run, replayed: Run
) -> None:
    assert replayed.run_id != recorded.run_id
    for key in ("research_queue", "reports", "theses", "errors"):
        assert replayed.state[key] == recorded.state[key], key
    usage = _usage(fund, replayed.run_id)
    assert len(usage) == len(recorded.requests)
    assert usage["cache_hit"].all() and (usage["cost_usd"] == 0).all()
    traces = _traces(fund, replayed.run_id)
    assert traces["cost_usd"].sum() == 0 and traces["cache_hits"].sum() == len(usage)
    recorded_traces = _traces(fund, recorded.run_id)
    assert sorted(traces["inputs_digest"]) == sorted(recorded_traces["inputs_digest"])
    assert AuditLog.verify_chain(audit_path(fund.lake_path, replayed.run_id))


def test_a_replay_with_a_missing_recording_drops_only_that_symbol(
    fund: Fund, recorded: Run, replayed: Run
) -> None:
    cache = LLMCache(fund.database)
    asked = 'AnalystReport for AAA as of 2024-03-01 (analyst: "news")'
    (lost,) = [
        e
        for e in cache.entries()
        if any(asked in m["content"] for m in json.loads(e.request_json)["messages"])
    ]
    assert cache.delete(lost.key)
    try:
        gateway = fund.gateway(OfflineProvider("fake"), "replay_only")
        with run_context(mode="backtest", as_of=DAY) as ctx:
            graph = fund.graph(gateway, AuditLog(audit_path(fund.lake_path, ctx.run_id)))
            state = run_graph(
                graph,
                {"as_of": DAY, "research_queue": QUEUE},  # type: ignore[arg-type]
                run_id=ctx.run_id,
                lake_path=fund.lake_path,
            )
    finally:
        assert cache.put(lost)
    assert list(state["theses"]) == ["BBB", "CCC"]
    missed = [e for e in state["errors"] if e.symbol == "AAA"]
    assert [(e.node, e.kind) for e in missed] == [("news", "CacheMiss")]
    assert "replay_only" in missed[0].message and lost.key in missed[0].message
    for symbol in ("BBB", "CCC"):
        assert state["reports"][symbol] == recorded.state["reports"][symbol]
