"""Stage 30: the technical analyst end to end with FakeLLM (golden prompt, schema, evidence
references, report checks and repairs, the timing and stop rules), and the recorded live AAPL
run replayed offline.

The brief's Verify command runs this file.
"""

import os
from datetime import date
from pathlib import Path

import pytest
from technical_world import AS_OF, CONFIG, TechnicalWorld

from atlas.agents.context import AgentContext
from atlas.agents.research import TechnicalAnalyst, technical_registry
from atlas.agents.research.checks import uncited_numbers
from atlas.agents.research.technical import SECTIONS, SIGNALS, stop_problems
from atlas.agents.trace import TraceStore
from atlas.core.errors import ConfigError, DataError
from atlas.core.types.research import Direction, TechnicalReport, TechnicalSignal, Timing
from atlas.llm import FakeLLM, LLMGateway
from atlas.llm.providers.fake import FakeReply
from atlas.llm.schemas import ProviderRequest, ToolCall
from atlas.quant.regime import RegimeState

GOLDEN = Path(__file__).resolve().parent / "golden" / "technical_analyst_prompt.txt"
UPDATE = os.environ.get("ATLAS_UPDATE_GOLDEN") == "1"

AGENT = "technical_analyst"
STEP = (AGENT, "round-1")
TAG = "pxs:UPP:2024-02-29:levels"


def signal(
    name: str,
    direction: Direction = Direction.LONG,
    timing: Timing = Timing.WAIT_FOR_PULLBACK,
    stop: float | None = 0.06,
    ids: tuple[str, ...] = (TAG,),
) -> TechnicalSignal:
    return TechnicalSignal(
        name=name,
        direction=direction,
        strength=0.5,
        evidence_ids=ids,
        timing=timing,
        suggested_stop_pct=stop,
    )


def report(
    symbol: str = "UPP",
    *,
    summary: str | None = None,
    signals: tuple[TechnicalSignal, ...] | None = None,
    ids: tuple[str, ...] = (TAG,),
    risks: tuple[str, ...] = (
        "A close below support breaks the trend [E:pxs:UPP:2024-02-29:levels].",
    ),
    ticker: str | None = None,
    as_of: date = AS_OF,
    analyst: str = AGENT,
    confidence: float = 0.7,
) -> TechnicalReport:
    tag = f"[E:{ids[0]}]"
    text = summary or "\n".join(f"{s}: The level is 1.0 {tag}." for s in SECTIONS)
    return TechnicalReport(
        ticker=ticker or symbol,
        as_of=as_of,
        analyst=analyst,
        summary=text,
        signals=signals or tuple(signal(n, ids=ids) for n in SIGNALS),
        risks=risks,
        confidence=confidence,
    )


def analyst(fake: FakeLLM, **kw: object) -> TechnicalAnalyst:
    return TechnicalAnalyst(LLMGateway(fake, CONFIG), config=CONFIG, traces=TraceStore(), **kw)  # type: ignore[arg-type]


def ctx(tworld: TechnicalWorld, symbol: str = "UPP", **inputs: object) -> AgentContext:
    return AgentContext(tworld.view(), inputs={"symbol": symbol, **inputs})


def user_text(request: ProviderRequest) -> str:
    return request.messages[1].content


def given_regime(day: date = AS_OF) -> RegimeState:
    return RegimeState(
        as_of=day,
        data_date=date(2024, 2, 29),
        vol_regime="CRISIS",
        trend_regime="DOWN",
        hmm_probs=None,
        hmm_labels=(),
        macro_quadrant=None,
        stress_flag=True,
        risk_scalar=0.25,
    )


# --- the prompt -------------------------------------------------------------------------------


def test_the_prompt_matches_the_golden_file(tworld: TechnicalWorld) -> None:
    fake = FakeLLM({STEP: report()})
    analyst(fake).run(ctx(tworld))
    system, user = fake.calls[0].messages[0].content, fake.calls[0].messages[1].content
    text = f"=== system ===\n{system}\n=== user ===\n{user}\n"
    if UPDATE:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_the_prompt_holds_every_prefetched_item_and_the_rules(tworld: TechnicalWorld) -> None:
    fake = FakeLLM({STEP: report()})
    out = analyst(fake).run(ctx(tworld))
    user = user_text(fake.calls[0])
    for evidence_id in out.trace.evidence_ids:
        assert f"[E:{evidence_id}]" in user
    prefixes = {i.split(":")[0] for i in out.trace.evidence_ids}
    assert prefixes == {"pxs", "tsnap", "regime"}
    assert len(out.trace.evidence_ids) == 5 + 5 + 1
    system = fake.calls[0].messages[0].content
    for label in SECTIONS:
        assert f'"{label}:"' in system
    assert "between 0.01 and 0.25" in system
    assert "Today is 2024-03-01." in system
    assert 'ticker "UPP", as_of "2024-03-01", analyst "technical_analyst"' in user
    schema = fake.calls[0].schema
    assert schema is TechnicalReport


def test_the_two_tools_are_offered_and_versioned(tworld: TechnicalWorld) -> None:
    fake = FakeLLM({STEP: report()})
    agent = analyst(fake)
    agent.run(ctx(tworld))
    assert [t.name for t in fake.calls[0].tools or ()] == [
        "get_technical_snapshot",
        "get_price_summary",
    ]
    assert agent.prompt_version == (
        "technical_analyst.j2@v1|get_price_summary@v1,get_technical_snapshot@v1"
    )
    assert set(technical_registry().names) >= {"get_technical_snapshot", "get_price_summary"}


def test_a_given_regime_is_shown_and_another_days_is_refused(tworld: TechnicalWorld) -> None:
    fake = FakeLLM({STEP: report()})
    analyst(fake).run(ctx(tworld, regime=given_regime()))
    assert "volatility regime CRISIS" in user_text(fake.calls[0])
    fake = FakeLLM({STEP: report()})
    agent = analyst(fake)
    with pytest.raises(ConfigError, match="regime for 2024-03-04"):
        agent.run(ctx(tworld, regime=given_regime(date(2024, 3, 4))))
    assert fake.calls == []
    with pytest.raises(ConfigError, match="RegimeState"):
        agent.run(ctx(tworld, regime="HIGH"))


# --- a good report ----------------------------------------------------------------------------


def test_a_cited_report_validates_and_returns_its_evidence(tworld: TechnicalWorld) -> None:
    good = report(ids=(TAG, "tsnap:UPP:2024-02-29:momentum"))
    fake = FakeLLM({STEP: good})
    out = analyst(fake).run(ctx(tworld))
    assert out.output == good and not out.low_quality and out.problems == ()
    assert isinstance(out.output, TechnicalReport)
    assert out.trace.status == "ok" and out.trace.llm_calls == 1
    assert [e.id for e in out.evidence] == [TAG, "tsnap:UPP:2024-02-29:momentum"]
    assert TechnicalReport.model_validate_json(out.trace.output_json or "") == good


def test_a_flat_signal_without_a_stop_that_avoids_is_fine(tworld: TechnicalWorld) -> None:
    signals = (
        signal("trend"),
        signal("momentum", Direction.SHORT, Timing.NOW, 0.25),
        signal("volatility", Direction.FLAT, Timing.AVOID, None),
        signal("levels", Direction.LONG, Timing.NOW, 0.01),
    )
    fake = FakeLLM({STEP: report(signals=signals)})
    out = analyst(fake).run(ctx(tworld))
    assert out.trace.status == "ok" and out.problems == ()


def test_the_symbol_is_normalised_and_required(tworld: TechnicalWorld) -> None:
    fake = FakeLLM({STEP: report()})
    assert analyst(fake).run(ctx(tworld, " upp ")).output.ticker == "UPP"
    with pytest.raises(ConfigError, match="inputs\\['symbol'\\]"):
        analyst(fake).run(AgentContext(tworld.view()))


# --- report checks and repairs ----------------------------------------------------------------


def _sig(**changes: object) -> tuple[TechnicalSignal, ...]:
    name = str(changes.pop("name", "trend"))
    return tuple(signal(n).model_copy(update=changes) if n == name else signal(n) for n in SIGNALS)


@pytest.mark.parametrize(
    ("bad", "problem"),
    [
        (report(ticker="DWN"), "ticker must be UPP, not DWN"),
        (report(as_of=date(2024, 2, 29)), "as_of must be 2024-03-01"),
        (report(analyst="someone"), 'analyst must be "technical_analyst"'),
        (report(signals=tuple(signal(n) for n in SIGNALS[:3])), "signals must be exactly"),
        (
            report(signals=(*(signal(n) for n in SIGNALS), signal("trend"))),
            "signals must be exactly",
        ),
        (report(summary=f"Trend: up [E:{TAG}]."), '"Momentum:"'),
        (report(risks=("Support at 182.5 may break.",)), "risks[0]: Support at 182.5 may break."),
        (
            report(signals=_sig(suggested_stop_pct=None)),
            "signal trend is LONG and needs a suggested_stop_pct between 0.01 and 0.25",
        ),
        (
            report(signals=_sig(name="momentum", suggested_stop_pct=0.005)),
            "signal momentum: suggested_stop_pct 0.005 is outside [0.01, 0.25]",
        ),
        (
            report(signals=_sig(name="levels", direction=Direction.SHORT, suggested_stop_pct=0.4)),
            "signal levels: suggested_stop_pct 0.4 is outside [0.01, 0.25]",
        ),
        (
            report(signals=_sig(direction=Direction.FLAT, timing=Timing.AVOID)),
            "signal trend is FLAT: its suggested_stop_pct must be null",
        ),
        (
            report(signals=_sig(direction=Direction.FLAT, suggested_stop_pct=None)),
            "signal trend is FLAT: its timing must be AVOID, not WAIT_FOR_PULLBACK",
        ),
    ],
)
def test_a_report_problem_gets_a_repair_turn(
    tworld: TechnicalWorld, bad: TechnicalReport, problem: str
) -> None:
    fake = FakeLLM({STEP: bad, (AGENT, "output_repair"): report()})
    out = analyst(fake).run(ctx(tworld))
    assert out.trace.status == "ok" and out.output == report()
    repair = fake.calls[1].messages[-1].content
    assert problem in repair


def test_stop_problems() -> None:
    settings = CONFIG.agents.technical
    assert stop_problems(signal("trend"), settings) == []
    assert stop_problems(signal("trend", stop=0.01), settings) == []
    assert stop_problems(signal("trend", stop=0.25), settings) == []
    assert stop_problems(signal("trend", Direction.FLAT, Timing.NOW, 0.3), settings) == [
        "signal trend is FLAT: its suggested_stop_pct must be null (no position, no stop)",
        "signal trend is FLAT: its timing must be AVOID, not NOW",
    ]


def test_problems_left_after_the_repair_make_the_report_low_quality(
    tworld: TechnicalWorld,
) -> None:
    bad = report(signals=_sig(suggested_stop_pct=0.5), confidence=0.9)
    fake = FakeLLM({STEP: bad, (AGENT, "output_repair"): bad})
    out = analyst(fake).run(ctx(tworld))
    assert out.low_quality and out.trace.status == "low_quality"
    assert out.output.confidence == CONFIG.agents.low_quality_confidence_cap
    assert isinstance(out.output, TechnicalReport)
    assert out.problems == (
        "signal trend: suggested_stop_pct 0.5 is outside [0.01, 0.25] "
        "(a fraction of the last close: 0.08 = 8%)",
    )


def test_an_unknown_evidence_id_gets_the_citation_repair(tworld: TechnicalWorld) -> None:
    bad = report(ids=("pxs:UPP:2023-12-29:levels",))
    fake = FakeLLM({STEP: bad, (AGENT, "evidence_repair"): report()})
    out = analyst(fake).run(ctx(tworld))
    assert out.trace.status == "ok"
    assert "pxs:UPP:2023-12-29:levels" in fake.calls[1].messages[-1].content


def test_a_schema_violation_is_repaired_by_the_gateway(tworld: TechnicalWorld) -> None:
    """A stop of 8 (meaning 8%) fails the schema; the gateway asks again."""
    good = report()
    data = good.model_dump(mode="json")
    data["signals"][0]["suggested_stop_pct"] = 8
    import json

    fake = FakeLLM({STEP: [FakeReply(content=json.dumps(data)), good]})
    out = analyst(fake).run(ctx(tworld))
    assert out.output == good and out.trace.status == "ok"
    assert "suggested_stop_pct" in fake.calls[1].messages[-1].content


# --- failure modes and tools ------------------------------------------------------------------


def test_a_blocked_symbol_fails_the_run_with_a_failed_trace(
    tworld: TechnicalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas.data.view import DataView

    monkeypatch.setattr(DataView, "blocked", property(lambda self: frozenset({"UPP"})))
    fake = FakeLLM({STEP: report()})
    agent = analyst(fake)
    with pytest.raises(DataError, match="blocked"):
        agent.run(ctx(tworld))
    assert fake.calls == []  # nothing spent
    (trace,) = agent.traces.traces()
    assert trace.status == "failed" and "blocked" in (trace.error or "")


def test_a_symbol_without_bars_fails_the_run(tworld: TechnicalWorld) -> None:
    fake = FakeLLM({STEP: report("ZZZ")})
    with pytest.raises(DataError, match="no bars known for ZZZ"):
        analyst(fake).run(ctx(tworld, "ZZZ"))
    assert fake.calls == []


def test_a_gap_is_shown_and_citable(
    tworld: TechnicalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas.agents.tools import technical_tools

    def broken(*args: object, **kw: object) -> object:
        raise DataError("no cross-section")

    monkeypatch.setattr(technical_tools, "build_snapshots", broken)
    ids = ("gap:UPP:2024-03-01:get_technical_snapshot",)
    fake = FakeLLM({STEP: report(ids=ids, risks=())})
    out = analyst(fake).run(ctx(tworld))
    assert "[E:gap:UPP:2024-03-01:get_technical_snapshot] " in user_text(fake.calls[0])
    assert out.trace.status == "ok"


def test_the_model_may_look_at_a_peer_with_a_tool(tworld: TechnicalWorld) -> None:
    call = ToolCall(id="t1", name="get_price_summary", arguments={"symbol": "DWN"})
    fake = FakeLLM(
        {
            STEP: FakeReply(tool_calls=(call,)),
            (AGENT, "round-2"): report(ids=("pxs:DWN:2024-02-29:range",)),
        }
    )
    out = analyst(fake).run(ctx(tworld))
    assert [(c.name, c.error) for c in out.trace.tool_calls] == [("get_price_summary", None)]
    assert out.trace.status == "ok"
    assert "pxs:DWN:2024-02-29:range" in out.trace.cited_ids


def test_a_peer_snapshot_repeats_the_regime_item_without_conflict(tworld: TechnicalWorld) -> None:
    """The snapshot tool also returns the market regime; for a peer it is the item already
    prefetched (same id, same text), which the evidence ledger accepts."""
    call = ToolCall(id="t1", name="get_technical_snapshot", arguments={"symbol": "DWN"})
    fake = FakeLLM(
        {
            STEP: FakeReply(tool_calls=(call,)),
            (AGENT, "round-2"): report(
                ids=("tsnap:DWN:2024-02-29:trend", "regime:2024-03-01:market")
            ),
        }
    )
    out = analyst(fake).run(ctx(tworld))
    assert [(c.name, c.error) for c in out.trace.tool_calls] == [("get_technical_snapshot", None)]
    assert out.trace.status == "ok"
    assert out.trace.evidence_ids.count("regime:2024-03-01:market") == 1
    assert "tsnap:DWN:2024-02-29:trend" in out.trace.evidence_ids


def test_memories_reach_the_prompt_point_in_time(tworld: TechnicalWorld) -> None:
    from datetime import UTC, datetime

    from atlas.agents.memory import AgentMemory, HashEmbedder
    from atlas.core.config import AtlasConfig

    cfg = AtlasConfig.model_validate(
        {"agents": {"memory": {"embedder": "hash", "min_similarity": 0.0}}}
    )
    memory = AgentMemory(HashEmbedder(), config=cfg)
    known = "UPP technicals: the pullback to the 50-day average held; wait for it again."
    later = "UPP technicals: a lesson written after the as-of must not show."
    memory.add_lesson(AGENT, known, created_at=datetime(2024, 2, 1, tzinfo=UTC))
    memory.add_lesson(AGENT, later, created_at=datetime(2024, 3, 5, tzinfo=UTC))
    fake = FakeLLM({STEP: report()})
    context = AgentContext(tworld.view(), memory=memory, inputs={"symbol": "UPP"})
    analyst(fake).run(context)
    user = user_text(fake.calls[0])
    assert known in user and later not in user


def test_without_a_memory_the_prompt_says_so(tworld: TechnicalWorld) -> None:
    fake = FakeLLM({STEP: report()})
    analyst(fake).run(ctx(tworld))
    assert "(no memories)" in user_text(fake.calls[0])


def test_uncited_numbers_is_the_shared_check() -> None:
    assert uncited_numbers("Support at 182.5.") == ["Support at 182.5."]


# --- the recorded live run --------------------------------------------------------------------


def test_the_recorded_aapl_run_replays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Brief acceptance: the report validates (schema), cites evidence for its numbers, obeys
    the stop and timing rules and runs offline from the cache: no main lake, no key, no
    provider call, the recorded report."""
    from recorded import recorded_run, replay_config, replay_lake
    from recorded_technical import AS_OF as DAY
    from recorded_technical import FIXTURE, SYMBOL, recorded_regime

    from atlas.data.view import DataView
    from atlas.llm import build_gateway
    from atlas.llm.providers.offline import OfflineProvider

    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    lake = replay_lake(tmp_path, FIXTURE)
    config = replay_config(lake.root)
    gateway = build_gateway(config, lake=lake)
    assert isinstance(gateway.provider, OfflineProvider)
    agent = TechnicalAnalyst(gateway, config=config, traces=TraceStore())
    inputs = {"symbol": SYMBOL, "regime": recorded_regime()}
    out = agent.run(AgentContext(DataView(lake, DAY, config), inputs=inputs))
    run = recorded_run(FIXTURE)
    expected = TechnicalReport.model_validate_json((FIXTURE / "expected_report.json").read_text())
    assert out.output == expected
    assert agent.prompt_version == run["prompt_version"]
    assert out.trace.llm_calls == out.trace.cache_hits == run["llm_calls"]
    assert out.trace.cost_usd == 0.0
    assert out.trace.status == run["status"] == "ok"
    assert out.citations.ok and out.problems == ()
    assert set(out.citations.cited) <= set(out.trace.evidence_ids)
    assert uncited_numbers(out.output.summary) == []
    assert all(uncited_numbers(risk) == [] for risk in out.output.risks)
    assert sorted(s.name for s in out.output.signals) == sorted(SIGNALS)
    assert all(stop_problems(s, config.agents.technical) == [] for s in out.output.signals)
    assert (out.output.ticker, out.output.as_of) == (SYMBOL, DAY)
