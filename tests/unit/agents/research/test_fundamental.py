"""Stage 29: the fundamental analyst end to end with FakeLLM (golden prompt, schema, evidence
references, report checks and repairs), and the recorded live AAPL run replayed offline.

The brief's Verify command runs this file.
"""

import os
from datetime import date
from pathlib import Path

import pytest
from fundamental_world import AS_OF, CONFIG, FundamentalWorld

from atlas.agents.context import AgentContext
from atlas.agents.research import FundamentalAnalyst
from atlas.agents.research.fundamental import (
    SECTIONS,
    SIGNALS,
    missing_sections,
    sentences,
    uncited_numbers,
)
from atlas.agents.trace import TraceStore
from atlas.core.errors import ConfigError, DataError
from atlas.core.types.research import AnalystReport, Direction, Signal
from atlas.llm import FakeLLM, LLMGateway
from atlas.llm.providers.fake import FakeReply
from atlas.llm.schemas import ProviderRequest, ToolCall

GOLDEN = Path(__file__).resolve().parent / "golden" / "fundamental_analyst_prompt.txt"
UPDATE = os.environ.get("ATLAS_UPDATE_GOLDEN") == "1"

STEP = ("fundamental_analyst", "round-1")


def report(
    symbol: str = "AAA",
    *,
    summary: str | None = None,
    signals: tuple[str, ...] = SIGNALS,
    ids: tuple[str, ...] = ("fsum:AAA:2023-12-31:revenue",),
    risks: tuple[str, ...] = ("Margins could compress [E:fsum:AAA:2023-12-31:margins].",),
    ticker: str | None = None,
    as_of: date = AS_OF,
    analyst: str = "fundamental_analyst",
    confidence: float = 0.7,
) -> AnalystReport:
    tag = f"[E:{ids[0]}]"
    text = summary or "\n".join(f"{s}: The latest TTM figure is 1.0 {tag}." for s in SECTIONS)
    return AnalystReport(
        ticker=ticker or symbol,
        as_of=as_of,
        analyst=analyst,
        summary=text,
        signals=tuple(
            Signal(name=n, direction=Direction.LONG, strength=0.5, evidence_ids=ids)
            for n in signals
        ),
        risks=risks,
        confidence=confidence,
    )


def analyst(fake: FakeLLM, **kw: object) -> FundamentalAnalyst:
    return FundamentalAnalyst(LLMGateway(fake, CONFIG), config=CONFIG, traces=TraceStore(), **kw)  # type: ignore[arg-type]


def ctx(fworld: FundamentalWorld, symbol: str = "AAA", which: str = "clean") -> AgentContext:
    return AgentContext(fworld.view(which), inputs={"symbol": symbol})


def user_text(request: ProviderRequest) -> str:
    return request.messages[1].content


# --- the prompt -------------------------------------------------------------------------------


def test_the_prompt_matches_the_golden_file(fworld: FundamentalWorld) -> None:
    fake = FakeLLM({STEP: report()})
    analyst(fake).run(ctx(fworld))
    system, user = fake.calls[0].messages[0].content, fake.calls[0].messages[1].content
    text = f"=== system ===\n{system}\n=== user ===\n{user}\n"
    if UPDATE:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_the_prompt_holds_every_prefetched_item_and_the_rules(fworld: FundamentalWorld) -> None:
    fake = FakeLLM({STEP: report()})
    out = analyst(fake).run(ctx(fworld))
    user = user_text(fake.calls[0])
    for evidence_id in out.trace.evidence_ids:
        assert f"[E:{evidence_id}]" in user
    prefixes = {i.split(":")[0] for i in out.trace.evidence_ids}
    assert prefixes == {"fsum", "frank", "val", "rflag"}
    system = fake.calls[0].messages[0].content
    for label in SECTIONS:
        assert f'"{label}:"' in system
    assert "Today is 2024-03-01." in system
    assert 'ticker "AAA", as_of "2024-03-01", analyst "fundamental_analyst"' in user


def test_the_four_tools_are_offered_and_versioned(fworld: FundamentalWorld) -> None:
    fake = FakeLLM({STEP: report()})
    agent = analyst(fake)
    agent.run(ctx(fworld))
    assert [t.name for t in fake.calls[0].tools or ()] == [
        "get_fundamentals_summary",
        "get_factor_ranks",
        "get_valuation",
        "get_red_flags",
    ]
    assert agent.prompt_version == (
        "fundamental_analyst.j2@v1|get_factor_ranks@v1,get_fundamentals_summary@v1,"
        "get_red_flags@v1,get_valuation@v1"
    )


# --- a good report ----------------------------------------------------------------------------


def test_a_cited_report_validates_and_returns_its_evidence(fworld: FundamentalWorld) -> None:
    good = report(ids=("fsum:AAA:2023-12-31:revenue", "val:AAA:2024-03-01:pe"))
    fake = FakeLLM({STEP: good})
    out = analyst(fake).run(ctx(fworld))
    assert out.output == good and not out.low_quality and out.problems == ()
    assert out.trace.status == "ok" and out.trace.llm_calls == 1
    assert [e.id for e in out.evidence] == [
        "fsum:AAA:2023-12-31:revenue",
        "val:AAA:2024-03-01:pe",
        "fsum:AAA:2023-12-31:margins",
    ]
    AnalystReport.model_validate_json(out.trace.output_json or "")


def test_the_symbol_is_normalised_and_required(fworld: FundamentalWorld) -> None:
    fake = FakeLLM({STEP: report()})
    assert analyst(fake).run(ctx(fworld, " aaa ")).output.ticker == "AAA"
    with pytest.raises(ConfigError, match="inputs\\['symbol'\\]"):
        analyst(fake).run(AgentContext(fworld.view()))


# --- report checks and repairs ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("bad", "problem"),
    [
        (report(ticker="BBB"), "ticker must be AAA, not BBB"),
        (report(as_of=date(2024, 2, 29)), "as_of must be 2024-03-01"),
        (report(analyst="someone"), 'analyst must be "fundamental_analyst"'),
        (report(signals=("quality", "valuation", "growth")), "signals must be exactly"),
        (report(signals=(*SIGNALS, "quality")), "signals must be exactly"),
        (
            report(summary="Business quality: fine [E:fsum:AAA:2023-12-31:revenue]."),
            '"Growth trajectory:"',
        ),
        (report(risks=("Debt doubled to 4.2B.",)), "risks[0]: Debt doubled to 4.2B."),
    ],
)
def test_a_report_problem_gets_a_repair_turn(
    fworld: FundamentalWorld, bad: AnalystReport, problem: str
) -> None:
    fake = FakeLLM({STEP: bad, ("fundamental_analyst", "output_repair"): report()})
    out = analyst(fake).run(ctx(fworld))
    assert out.trace.status == "ok" and out.output == report()
    repair = fake.calls[1].messages[-1].content
    assert problem in repair


def _uncited_line(fworld: FundamentalWorld, risks: tuple[str, ...]) -> str:
    fake = FakeLLM({STEP: report(risks=risks), ("fundamental_analyst", "output_repair"): report()})
    analyst(fake).run(ctx(fworld))
    lines = fake.calls[1].messages[-1].content.splitlines()
    (line,) = [x for x in lines if "these have none: " in x]
    return line.split("these have none: ", 1)[1]


def test_the_repair_quotes_at_most_five_uncited_sentences_cut_at_120_characters(
    fworld: FundamentalWorld,
) -> None:
    long = "Debt rose 9% while " + "the cash pile shrank " * 6 + "fast."
    risks = (long, *(f"Debt rose {i}%." for i in range(1, 7)))  # seven uncited sentences
    first = f"risks[0]: {long}"
    assert len(first) > 120
    shown = [first[:120], *(f"risks[{i}]: Debt rose {i}%." for i in range(1, 5))]
    assert _uncited_line(fworld, risks) == "; ".join(f'"{s}"' for s in shown) + " and 2 more"
    five = tuple(f"Debt rose {i}%." for i in range(5))
    assert _uncited_line(fworld, five) == "; ".join(
        f'"risks[{i}]: Debt rose {i}%."' for i in range(5)
    )


def test_problems_left_after_the_repair_make_the_report_low_quality(
    fworld: FundamentalWorld,
) -> None:
    bad = report(ticker="BBB", confidence=0.9)
    fake = FakeLLM({STEP: bad, ("fundamental_analyst", "output_repair"): bad})
    out = analyst(fake).run(ctx(fworld))
    assert out.low_quality and out.trace.status == "low_quality"
    assert out.output.confidence == CONFIG.agents.low_quality_confidence_cap
    assert out.problems == ("ticker must be AAA, not BBB",)


def test_an_unknown_evidence_id_gets_the_citation_repair(fworld: FundamentalWorld) -> None:
    bad = report(ids=("fsum:AAA:2019-12-31:revenue",))
    fake = FakeLLM({STEP: bad, ("fundamental_analyst", "evidence_repair"): report()})
    out = analyst(fake).run(ctx(fworld))
    assert out.trace.status == "ok"
    assert "fsum:AAA:2019-12-31:revenue" in fake.calls[1].messages[-1].content


# --- the number check -------------------------------------------------------------------------


def test_sentences_split_after_stops_and_lines_and_keep_trailing_tags() -> None:
    text = "Revenue rose 5%. [E:fsum:A:2023-12-31:revenue]\nMargins: 30.1% [E:x]! Fine? yes"
    assert sentences(text) == [
        "Revenue rose 5%. [E:fsum:A:2023-12-31:revenue]",
        "Margins: 30.1% [E:x]!",
        "Fine?",
        "yes",
    ]


@pytest.mark.parametrize(
    ("text", "uncited"),
    [
        ("Revenue grew 5% [E:a].", []),
        ("Revenue grew 5%.", ["Revenue grew 5%."]),
        ("As of 2024-03-01 the company is profitable.", []),  # a date is not a number claim
        ("Strong franchise. Cash 3.2B.", ["Cash 3.2B."]),
        ("No numbers here [E:fsum:A:2023-12-31:revenue].", []),
        ("P/E sits at percentile 40 of its history [E:val:A:2024-03-01:pe].", []),
    ],
)
def test_uncited_numbers(text: str, uncited: list[str]) -> None:
    assert uncited_numbers(text) == uncited


def test_sections_must_start_lines_and_keep_their_order() -> None:
    ok = "\n".join(f"**{s}:** text" for s in SECTIONS)
    assert missing_sections(ok) == []
    assert missing_sections("\n".join(f"{s.upper()}: x" for s in SECTIONS)) == []
    swapped = "\n".join(f"{s}: x" for s in (SECTIONS[1], SECTIONS[0], *SECTIONS[2:]))
    assert missing_sections(swapped) == ["Growth trajectory"]
    inline = "Business quality: x. Growth trajectory: y."
    assert "Growth trajectory" in missing_sections(inline)


# --- failure modes and tools ------------------------------------------------------------------


def test_a_blocked_symbol_fails_the_run_with_a_failed_trace(world: object) -> None:
    fake = FakeLLM({STEP: report("BAD")})
    agent = FundamentalAnalyst(LLMGateway(fake, CONFIG), config=CONFIG, traces=TraceStore())
    with pytest.raises(DataError, match="blocked"):
        agent.run(world.ctx(inputs={"symbol": "BAD"}))  # type: ignore[attr-defined]
    assert fake.calls == []  # nothing spent
    (trace,) = agent.traces.traces()
    assert trace.status == "failed" and "blocked" in (trace.error or "")


def test_a_gap_is_shown_and_citable(fworld: FundamentalWorld) -> None:
    ids = ("gap:LLL:2024-03-01:get_factor_ranks",)
    fake = FakeLLM({STEP: report("LLL", ids=ids, risks=())})
    out = analyst(fake).run(ctx(fworld, "LLL"))
    assert "[E:gap:LLL:2024-03-01:get_factor_ranks] " in user_text(fake.calls[0])
    assert out.trace.status == "ok"


def test_the_model_may_look_at_a_peer_with_a_tool(fworld: FundamentalWorld) -> None:
    call = ToolCall(id="t1", name="get_valuation", arguments={"symbol": "BBB"})
    fake = FakeLLM(
        {
            STEP: FakeReply(tool_calls=(call,)),
            ("fundamental_analyst", "round-2"): report(ids=("val:BBB:2024-03-01:pe",)),
        }
    )
    out = analyst(fake).run(ctx(fworld))
    assert [(c.name, c.error) for c in out.trace.tool_calls] == [("get_valuation", None)]
    assert out.trace.status == "ok"
    assert "val:BBB:2024-03-01:pe" in out.trace.cited_ids


def test_memories_reach_the_prompt_point_in_time(fworld: FundamentalWorld) -> None:
    from datetime import UTC, datetime

    from atlas.agents.memory import AgentMemory, HashEmbedder
    from atlas.core.config import AtlasConfig

    cfg = AtlasConfig.model_validate(
        {"agents": {"memory": {"embedder": "hash", "min_similarity": 0.0}}}
    )
    memory = AgentMemory(HashEmbedder(), config=cfg)
    known = "AAA fundamentals: valuation looked cheap while margins fell; weigh margins first."
    later = "AAA fundamentals: a lesson written after the as-of must not show."
    memory.add_lesson("fundamental_analyst", known, created_at=datetime(2024, 2, 1, tzinfo=UTC))
    memory.add_lesson("fundamental_analyst", later, created_at=datetime(2024, 3, 5, tzinfo=UTC))
    fake = FakeLLM({STEP: report()})
    context = AgentContext(fworld.view(), memory=memory, inputs={"symbol": "AAA"})
    analyst(fake).run(context)
    user = user_text(fake.calls[0])
    assert known in user and later not in user


def test_without_a_memory_the_prompt_says_so(fworld: FundamentalWorld) -> None:
    fake = FakeLLM({STEP: report()})
    analyst(fake).run(ctx(fworld))
    assert "(no memories)" in user_text(fake.calls[0])


# --- the recorded live run --------------------------------------------------------------------


def test_the_recorded_aapl_run_replays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Brief acceptance: the report validates, cites evidence for its numbers and runs offline
    from the cache: no lake, no key, no provider call, the recorded report."""
    from recorded import AS_OF as DAY
    from recorded import FIXTURE, SYMBOL, recorded_run, replay_config, replay_lake

    from atlas.data.view import DataView
    from atlas.llm import build_gateway
    from atlas.llm.providers.offline import OfflineProvider

    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    lake = replay_lake(tmp_path)
    config = replay_config(lake.root)
    gateway = build_gateway(config, lake=lake)
    assert isinstance(gateway.provider, OfflineProvider)
    agent = FundamentalAnalyst(gateway, config=config, traces=TraceStore())
    out = agent.run(AgentContext(DataView(lake, DAY, config), inputs={"symbol": SYMBOL}))
    run = recorded_run()
    expected = AnalystReport.model_validate_json((FIXTURE / "expected_report.json").read_text())
    assert out.output == expected
    assert out.trace.llm_calls == out.trace.cache_hits == run["llm_calls"]
    assert out.trace.cost_usd == 0.0
    assert out.trace.status == run["status"] == "ok"
    assert out.citations.ok and out.problems == ()
    assert set(out.citations.cited) <= set(out.trace.evidence_ids)
    assert uncited_numbers(out.output.summary) == []
    assert all(uncited_numbers(risk) == [] for risk in out.output.risks)
    assert sorted(s.name for s in out.output.signals) == sorted(SIGNALS)
    assert (out.output.ticker, out.output.as_of) == (SYMBOL, DAY)
