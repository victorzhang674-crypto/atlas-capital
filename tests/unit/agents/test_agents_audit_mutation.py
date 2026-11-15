"""Stage 26 post-DONE audit: tests for the behaviour a mutation run showed unpinned (mutmut
3.8.1 over `src/atlas/agents`, run 1: 925 / 1117 killed; see the stage worklog)."""

import json
from pathlib import Path
from typing import Any

import pytest
from agent_helpers import EchoAnalyst, StepClock, ToolAnalyst, report
from agent_world import AGENT_PROMPTS, World
from pydantic import BaseModel

from atlas.agents.base import BaseAgent
from atlas.agents.context import AgentContext
from atlas.agents.evidence import check_citations
from atlas.agents.prompting import PromptRegistry
from atlas.agents.tools import Tool, ToolRegistry, default_tools
from atlas.agents.trace import TraceStore
from atlas.core.audit import AuditLog
from atlas.core.config import AtlasConfig
from atlas.core.errors import ConfigError, LLMError
from atlas.core.run_context import run_context
from atlas.core.types.research import AnalystReport, Claim, DebateRole, DebateTurn, Evidence
from atlas.data.view import DataView
from atlas.llm import FakeLLM, FakeReply, LLMGateway
from atlas.llm.schemas import LLMResult, ToolCall, Usage


def tool_reply(*specs: tuple[str, str, dict[str, object]]) -> FakeReply:
    return FakeReply(tool_calls=tuple(ToolCall(id=i, name=n, arguments=a) for i, n, a in specs))


def call(world: World, name: str, **args: object) -> Any:
    return default_tools().call(world.ctx(), ToolCall(id="c", name=name, arguments=args))


def test_a_registered_tool_not_given_to_the_agent_is_refused(
    world: World, fake_llm: FakeLLM
) -> None:
    # `market_news` is in the default registry but not in ToolAnalyst.tools.
    fake_llm.add("tool_analyst", None, tool_reply(("m", "market_news", {})), report(), report())
    agent = ToolAnalyst(
        LLMGateway(fake_llm, AtlasConfig()), prompts=PromptRegistry([AGENT_PROMPTS])
    )
    out = agent.run(world.ctx(inputs={"symbol": "AAA"}))
    (record,) = out.trace.tool_calls
    assert record.error is not None and "unknown tool 'market_news'" in record.error
    assert record.evidence_ids == ()


def test_trace_usage_sums_every_call(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, report("news:alpaca-9"), report("news:alpaca-2"))
    gateway = LLMGateway(fake_llm, AtlasConfig())
    out = EchoAnalyst(gateway, prompts=PromptRegistry([AGENT_PROMPTS])).run(
        world.ctx(inputs={"symbol": "AAA"})
    )
    records = gateway.ledger.records()
    assert len(records) == 2 == out.trace.llm_calls
    assert out.usage.input_tokens == sum(r.input_tokens for r in records)
    assert out.usage.output_tokens == sum(r.output_tokens for r in records)
    assert out.cost_usd == pytest.approx(sum(r.cost_usd for r in records))


def test_news_arguments_reach_the_view(world: World) -> None:
    view = world.view()
    assert (
        [e.id for e in call(world, "news", symbol="AAA", limit=1).evidence]
        == [e.id for e in view.news("AAA", limit=1)]
        == ["news:alpaca-2"]
    )
    one_day = call(world, "news", symbol="AAA", lookback_days=1).evidence
    assert [e.id for e in one_day] == [e.id for e in view.news("AAA", lookback=1)]
    assert len(one_day) < len(view.news("AAA"))
    assert call(world, "market_news", limit=1).evidence == tuple(view.market_news(limit=1))
    assert call(world, "market_news", lookback_days=1).evidence == tuple(
        view.market_news(lookback=1)
    )


def test_market_news_lookback_is_passed(world: World) -> None:
    # alpaca-3 (2024-02-28 14:00 UTC) is outside a 1-day lookback at 2024-03-01 08:00 ET.
    assert call(world, "market_news", lookback_days=1).evidence == ()
    assert [e.id for e in call(world, "market_news", lookback_days=5).evidence] == ["news:alpaca-3"]


def test_stale_fundamentals_are_not_cited(world: World) -> None:
    cfg = AtlasConfig.model_validate({"data": {"fundamentals": {"max_staleness_days": 30}}})
    view = DataView(
        world.clean,
        world.view().day,
        cfg,
        concept_map=world.concept_map,
        catalog=world.catalog,
        lexicon=world.lexicon,
    )
    detail = view.fundamentals_detail("AAA")
    assert (detail["source"] == "stale").any()
    out = default_tools().call(
        AgentContext(view), ToolCall(id="c", name="fundamentals", arguments={"symbol": "AAA"})
    )
    stale = set(detail.loc[detail["source"] == "stale", "metric"])
    assert not {e.id.rsplit(":", 1)[1] for e in out.evidence} & stale


def test_evidence_sources_and_price_text(world: World) -> None:
    fund = call(world, "fundamentals", symbol="AAA").evidence
    assert {e.source for e in fund} == {"sec_edgar"}
    (px,) = call(world, "price_history", symbol="AAA", lookback=5).evidence
    assert px.source == "prices"
    bars = world.view().prices(["AAA"], 5)
    adj = bars["adj_close"]
    assert f"(adjusted close {adj.iloc[0]:.2f} -> {adj.iloc[-1]:.2f})" in px.text
    assert px.as_of == bars["available_at"].max().to_pydatetime()


def test_the_audit_payload_is_complete(world: World, fake_llm: FakeLLM, tmp_path: Path) -> None:
    fake_llm.add("echo_analyst", None, report("news:alpaca-2"))
    path = tmp_path / "audit.jsonl"
    with run_context():
        out = EchoAnalyst(
            LLMGateway(fake_llm, AtlasConfig()),
            prompts=PromptRegistry([AGENT_PROMPTS]),
            audit=AuditLog(path),
        ).run(world.ctx(inputs={"symbol": "AAA"}))
    payload = json.loads(path.read_text())["payload"]
    assert payload["as_of"] == "2024-03-01"
    assert payload["versions"] == "echo_analyst.j2@v1|"
    assert payload["cost_usd"] == pytest.approx(out.cost_usd)


def test_a_custom_tool_registry_is_used(world: World, fake_llm: FakeLLM) -> None:
    class Args(BaseModel):
        word: str

    item = Evidence(id="echo:hi", source="echo", as_of=world.view().as_of.cutoff.replace(year=2023),
                    text="hi")  # fmt: skip
    echo = Tool(name="news", description="A stand-in news tool.", args=Args, fn=lambda c, a: [item])
    registry = ToolRegistry([echo, *[t for t in default_tools().tools if t.name != "news"]])
    fake_llm.add("tool_analyst", None, tool_reply(("c", "news", {"word": "hi"})), report("echo:hi"))
    agent = ToolAnalyst(
        LLMGateway(fake_llm, AtlasConfig()),
        prompts=PromptRegistry([AGENT_PROMPTS]),
        tool_registry=registry,
    )
    out = agent.run(world.ctx(inputs={"symbol": "AAA"}))
    assert out.citations.ok and [e.id for e in out.evidence] == ["echo:hi"]
    assert fake_llm.calls[0].tools[0].description == "A stand-in news tool."


def test_a_subclass_without_output_schema_is_a_config_error(fake_llm: FakeLLM) -> None:
    class NoSchema(BaseAgent[AnalystReport]):
        name = "no_schema"
        prompt_template = "echo_analyst.j2"

    with pytest.raises(ConfigError, match="output_schema"):
        NoSchema(LLMGateway(fake_llm, AtlasConfig()), prompts=PromptRegistry([AGENT_PROMPTS]))


def test_template_variables_agent_and_output_schema(world: World, tmp_path: Path) -> None:
    (tmp_path / "vars.j2").write_text(
        "{# version: 1 #}\n{% block system %}{{ agent }} answers {{ output_schema }}"
        "{% endblock %}{% block user %}{{ symbol }}{% endblock %}\n"
    )

    class Vars(EchoAnalyst):
        name = "vars_agent"
        prompt_template = "vars.j2"

    fake = FakeLLM(default=report("news:alpaca-2"))
    Vars(LLMGateway(fake, AtlasConfig()), prompts=PromptRegistry([tmp_path])).run(
        world.ctx(inputs={"symbol": "AAA"})
    )
    assert fake.calls[0].messages[0].content.startswith("vars_agent answers AnalystReport")


def test_deep_claim_paths() -> None:
    class Debate(BaseModel):
        turns: tuple[DebateTurn, ...]

    turn = DebateTurn(
        speaker=DebateRole.BEAR, claims=(Claim(text="weak", evidence_ids=(), strength=0.5),)
    )
    result = check_citations(Debate(turns=(turn,)), {"news:1"})
    assert result.uncited_claims == ("<root>", "turns[0].claims[0]")


def test_an_evidence_field_of_models_is_walked() -> None:
    class Thesis(BaseModel):
        evidence: tuple[Claim, ...]

    result = check_citations(
        Thesis(evidence=(Claim(text="t", evidence_ids=("news:7",), strength=0.5),)), {"news:1"}
    )
    assert result.unknown == ("news:7",)


def test_a_parsed_answer_of_another_type_fails(world: World) -> None:
    class Other(BaseModel):
        x: int

    class WrongType:
        def complete(self, messages: Any, **kw: Any) -> LLMResult:
            return LLMResult(content="{}", parsed=Other(x=1), usage=Usage(), cost_usd=0.0,
                             model="m", latency_ms=0.0)  # fmt: skip

    agent = EchoAnalyst(WrongType(), prompts=PromptRegistry([AGENT_PROMPTS]))  # type: ignore[arg-type]
    with pytest.raises(LLMError, match="has no AnalystReport"):
        agent.run(world.ctx(inputs={"symbol": "AAA"}))


def test_an_atlas_error_is_traced_with_its_own_message(world: World, fake_llm: FakeLLM) -> None:
    fake_llm.add("echo_analyst", None, LLMError("provider down"))
    traces = TraceStore()
    agent = EchoAnalyst(
        LLMGateway(fake_llm, AtlasConfig()),
        prompts=PromptRegistry([AGENT_PROMPTS]),
        traces=traces,
        clock=StepClock(),
    )
    with pytest.raises(LLMError):
        agent.run(world.ctx(inputs={"symbol": "AAA"}))
    assert traces.traces()[0].error is not None
    assert traces.traces()[0].error.endswith("provider down")
    assert not traces.traces()[0].error.startswith("LLMError")


def test_a_low_quality_trace_keeps_its_uncited_claims(world: World, fake_llm: FakeLLM) -> None:
    from atlas.core.types.research import Direction, Signal

    loose = Signal(name="v", direction=Direction.LONG, strength=0.4, evidence_ids=())
    fake_llm.add("echo_analyst", None, report().model_copy(update={"signals": (loose,)}))
    out = EchoAnalyst(
        LLMGateway(fake_llm, AtlasConfig()), prompts=PromptRegistry([AGENT_PROMPTS])
    ).run(world.ctx(inputs={"symbol": "AAA"}))
    assert out.trace.uncited_claims == ("<root>", "signals[0]")


def test_all_referenced_partials_are_found_after_a_shared_one(tmp_path: Path) -> None:
    # t includes _y, _c and _b; _b includes _c again: _y must still be visited.
    (tmp_path / "_y.j2").write_text("{# version: 1 #}\nY\n")
    (tmp_path / "_c.j2").write_text("{# version: 1 #}\nC\n")
    (tmp_path / "_b.j2").write_text('{# version: 1 #}\n{% include "_c.j2" %}\n')
    (tmp_path / "t.j2").write_text(
        '{# version: 1 #}\n{% block system %}{% include "_y.j2" %}{% include "_c.j2" %}'
        '{% include "_b.j2" %}{% endblock %}{% block user %}u{% endblock %}\n'
    )
    assert PromptRegistry([tmp_path]).get("t.j2").sources == ("_b.j2", "_c.j2", "_y.j2", "t.j2")


def test_fundamentals_cites_every_known_metric(world: World) -> None:
    detail = world.view().fundamentals_detail("AAA")
    known = detail[detail["value"].notna() & (detail["source"] != "stale")]
    ids = {e.id.rsplit(":", 1)[1] for e in call(world, "fundamentals", symbol="AAA").evidence}
    assert ids == set(known["metric"]) and len(ids) >= 2  # a missing metric is skipped, not a stop


def test_repair_text_lists_at_most_the_cap() -> None:
    from datetime import UTC, datetime

    from atlas.agents.base import MAX_LISTED_IDS, _repair_text
    from atlas.agents.evidence import CitationReport, EvidenceLedger

    stamp = datetime(2024, 1, 2, tzinfo=UTC)
    report_ = CitationReport(cited=("x",), unknown=("x",), uncited_claims=())

    def ledger(n: int) -> EvidenceLedger:
        return EvidenceLedger(
            Evidence(id=f"n:{i}", source="s", as_of=stamp, text="t") for i in range(n)
        )

    full = _repair_text(report_, ledger(MAX_LISTED_IDS))
    assert f"n:{MAX_LISTED_IDS - 1}." in full and ", ..." not in full
    cut = _repair_text(report_, ledger(MAX_LISTED_IDS + 1))
    assert f"n:{MAX_LISTED_IDS - 1}, ..." in cut and f"n:{MAX_LISTED_IDS}" not in cut
    assert "(none)" in _repair_text(report_, ledger(0))


def test_an_error_outcome_keeps_its_call(world: World) -> None:
    bad = ToolCall(id="z9", name="nope", arguments={})
    assert default_tools().call(world.ctx(), bad).call == bad


def test_a_citing_field_inside_an_optional_counts() -> None:
    class Maybe(BaseModel):
        claim: Claim | None = None

    assert check_citations(Maybe(), {"news:1"}).uncited_claims == ("<root>",)
