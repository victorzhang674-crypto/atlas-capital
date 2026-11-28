"""Stage 31: the news analyst end to end with FakeLLM (golden prompt, schema, evidence
references, report checks and repairs, the catalyst rules, the empty-news guard), and the
recorded live AAPL run replayed offline.

The brief's Verify command runs this file.
"""

import json
import os
from datetime import date
from pathlib import Path

import pytest
from news_world import AS_OF, CONFIG, NewsWorld, write_world

from atlas.agents.context import AgentContext
from atlas.agents.research import NewsAnalyst, news_registry
from atlas.agents.research.checks import uncited_numbers
from atlas.agents.research.news import (
    CATALYST_EVIDENCE,
    SECTIONS,
    SIGNALS,
    catalyst_problems,
    says_no_news,
)
from atlas.agents.trace import TraceStore
from atlas.core.config import AtlasConfig
from atlas.core.errors import ConfigError, DataError
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.core.types.research import Direction, Impact, NewsCatalyst, NewsReport, Signal
from atlas.data.view import DataView
from atlas.llm import FakeLLM, LLMGateway
from atlas.llm.providers.fake import FakeReply
from atlas.llm.schemas import ProviderRequest, ToolCall

GOLDEN = Path(__file__).resolve().parent / "golden" / "news_analyst_prompt.txt"
UPDATE = os.environ.get("ATLAS_UPDATE_GOLDEN") == "1"

AGENT = "news_analyst"
STEP = (AGENT, "round-1")
TAG = "news:alpaca-a1"
EARNINGS = "evt:AAA:earnings:2024-03-07"


def catalyst(**kw: object) -> NewsCatalyst:
    base: dict[str, object] = {
        "date": date(2024, 3, 7),
        "description": "Earnings after the close.",
        "evidence_id": EARNINGS,
        "expected_impact": Impact.MIXED,
    }
    return NewsCatalyst(**{**base, **kw})  # type: ignore[arg-type]


def report(
    symbol: str = "AAA",
    *,
    summary: str | None = None,
    signals: tuple[Signal, ...] | None = None,
    catalysts: tuple[NewsCatalyst, ...] | None = None,
    ids: tuple[str, ...] = (TAG,),
    risks: tuple[str, ...] = ("Guidance could be cut again [E:news:alpaca-a1].",),
    ticker: str | None = None,
    as_of: date = AS_OF,
    analyst: str = AGENT,
    confidence: float = 0.7,
) -> NewsReport:
    tag = f"[E:{ids[0]}]"
    text = summary or "\n".join(f"{s}: The guidance article matters {tag}." for s in SECTIONS)
    return NewsReport(
        ticker=ticker or symbol,
        as_of=as_of,
        analyst=analyst,
        summary=text,
        signals=signals
        or tuple(
            Signal(name=n, direction=Direction.LONG, strength=0.5, evidence_ids=ids)
            for n in SIGNALS
        ),
        risks=risks,
        catalysts=(catalyst(),) if catalysts is None else catalysts,
        confidence=confidence,
    )


def analyst(fake: FakeLLM, config: AtlasConfig = CONFIG, **kw: object) -> NewsAnalyst:
    return NewsAnalyst(LLMGateway(fake, config), config=config, traces=TraceStore(), **kw)  # type: ignore[arg-type]


def ctx(nworld: NewsWorld, symbol: str = "AAA", **inputs: object) -> AgentContext:
    return AgentContext(nworld.view(), inputs={"symbol": symbol, **inputs})


def user_text(request: ProviderRequest) -> str:
    return request.messages[1].content


def no_news_report(**kw: object) -> NewsReport:
    """A good report on P03: no article, one dated event."""
    tag = "[E:ncov:P03:2024-03-01:company]"
    lines = {
        "Material vs noise": f"There are no news articles about this stock lately {tag}.",
        "Catalysts": "Earnings are the only catalyst [E:evt:P03:earnings:2024-03-06].",
    }
    summary = "\n".join(f"{s}: {lines.get(s, f'Nothing to add {tag}.')}" for s in SECTIONS)
    signals = tuple(
        Signal(
            name=n,
            direction=Direction.FLAT,
            strength=0.1,
            evidence_ids=("ncov:P03:2024-03-01:company",),
        )
        for n in SIGNALS
    )
    base: dict[str, object] = {
        "symbol": "P03",
        "summary": summary,
        "signals": signals,
        "ids": ("ncov:P03:2024-03-01:company",),
        "risks": (),
        "confidence": 0.2,
        "catalysts": (
            catalyst(
                date=date(2024, 3, 6),
                evidence_id="evt:P03:earnings:2024-03-06",
                description="Earnings after the close.",
            ),
        ),
    }
    return report(**{**base, **kw})  # type: ignore[arg-type]


# --- the prompt -------------------------------------------------------------------------------


def test_the_prompt_matches_the_golden_file(nworld: NewsWorld) -> None:
    fake = FakeLLM({STEP: report()})
    analyst(fake).run(ctx(nworld))
    system, user = fake.calls[0].messages[0].content, fake.calls[0].messages[1].content
    text = f"=== system ===\n{system}\n=== user ===\n{user}\n"
    if UPDATE:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_the_prompt_holds_every_prefetched_item_and_the_rules(nworld: NewsWorld) -> None:
    fake = FakeLLM({STEP: report()})
    out = analyst(fake).run(ctx(nworld))
    user = user_text(fake.calls[0])
    for evidence_id in out.trace.evidence_ids:
        assert f"[E:{evidence_id}]" in user
    prefixes = {i.split(":")[0] for i in out.trace.evidence_ids}
    assert prefixes == {"ncov", "news", "evt", "rx", "rxs"}
    system = fake.calls[0].messages[0].content
    for label in SECTIONS:
        assert f'"{label}:"' in system
    assert "at most 8" in system and "at most 0.2" in system
    assert "in the last 21 days" in system
    assert "Today is 2024-03-01." in system
    assert 'ticker "AAA", as_of "2024-03-01", analyst "news_analyst"' in user
    assert fake.calls[0].schema is NewsReport


def test_the_four_tools_are_offered_and_versioned(nworld: NewsWorld) -> None:
    fake = FakeLLM({STEP: report()})
    agent = analyst(fake)
    agent.run(ctx(nworld))
    assert [t.name for t in fake.calls[0].tools or ()] == [
        "get_company_news",
        "get_upcoming_events",
        "get_past_earnings_reactions",
        "get_sector_news",
    ]
    assert agent.prompt_version == (
        "news_analyst.j2@v1|get_company_news@v1,get_past_earnings_reactions@v1,"
        "get_sector_news@v1,get_upcoming_events@v1"
    )
    assert set(news_registry().names) >= {"get_company_news", "news", "price_history"}


# --- a good report ----------------------------------------------------------------------------


def test_a_cited_report_validates_and_returns_its_evidence(nworld: NewsWorld) -> None:
    good = report(catalysts=(catalyst(), catalyst(date=None, evidence_id="news:alpaca-a6")))
    fake = FakeLLM({STEP: good})
    out = analyst(fake).run(ctx(nworld))
    assert out.output == good and not out.low_quality and out.problems == ()
    assert isinstance(out.output, NewsReport)
    assert out.trace.status == "ok" and out.trace.llm_calls == 1
    # the catalysts' ids are citations, so they are returned with the evidence
    assert [e.id for e in out.evidence] == [TAG, EARNINGS, "news:alpaca-a6"]
    assert NewsReport.model_validate_json(out.trace.output_json or "") == good


def test_every_catalyst_has_an_evidence_id_the_run_showed(nworld: NewsWorld) -> None:
    out = analyst(FakeLLM({STEP: report()})).run(ctx(nworld))
    shown = set(out.trace.evidence_ids)
    assert out.output.catalysts and all(c.evidence_id in shown for c in out.output.catalysts)


def test_a_report_may_have_no_catalyst(nworld: NewsWorld) -> None:
    out = analyst(FakeLLM({STEP: report(catalysts=())})).run(ctx(nworld))
    assert out.trace.status == "ok" and out.output.catalysts == ()


def test_the_symbol_is_normalised_and_required(nworld: NewsWorld) -> None:
    fake = FakeLLM({STEP: report()})
    assert analyst(fake).run(ctx(nworld, " aaa ")).output.ticker == "AAA"
    with pytest.raises(ConfigError, match="inputs\\['symbol'\\]"):
        analyst(fake).run(AgentContext(nworld.view()))


# --- report checks and repairs ----------------------------------------------------------------


def _sig(**changes: object) -> tuple[Signal, ...]:
    return tuple(
        Signal(name=n, direction=Direction.LONG, strength=0.5, evidence_ids=(TAG,)).model_copy(
            update=changes if n == "news_flow" else {}
        )
        for n in SIGNALS
    )


@pytest.mark.parametrize(
    ("bad", "problem"),
    [
        (report(ticker="P01"), "ticker must be AAA, not P01"),
        (report(as_of=date(2024, 2, 29)), "as_of must be 2024-03-01"),
        (report(analyst="someone"), 'analyst must be "news_analyst"'),
        (
            report(signals=tuple(s for s in _sig() if s.name != "event_risk")),
            "signals must be exactly",
        ),
        (report(summary=f"Catalysts: up [E:{TAG}]."), '"Material vs noise:"'),
        (report(risks=("Revenue may fall 4%.",)), "risks[0]: Revenue may fall 4%."),
        (
            report(
                catalysts=(
                    catalyst(),
                    catalyst(evidence_id="news:alpaca-a6", date=None),
                    *(catalyst() for _ in range(7)),
                )
            ),
            "at most 8 catalysts, not 9",
        ),
        (
            report(catalysts=(catalyst(evidence_id="ncov:AAA:2024-03-01:company"),)),
            "catalysts[0]: evidence_id ncov:AAA:2024-03-01:company must be a news:, evt: or rx:",
        ),
        (
            report(catalysts=(catalyst(evidence_id="rxs:AAA:2024-03-01:summary", date=None),)),
            "must be a news:, evt: or rx: item",
        ),
        (
            report(catalysts=(catalyst(date=date(2024, 2, 20)),)),
            "catalysts[0]: date 2024-02-20 is before the as-of day 2024-03-01",
        ),
        (
            report(catalysts=(catalyst(date=date(2024, 3, 8)),)),
            "it rests on evt:AAA:earnings:2024-03-07, so its date must be 2024-03-07 "
            "(not 2024-03-08)",
        ),
        (
            report(catalysts=(catalyst(date=None),)),
            "so its date must be 2024-03-07 (not null)",
        ),
    ],
)
def test_a_report_problem_gets_a_repair_turn(
    nworld: NewsWorld, bad: NewsReport, problem: str
) -> None:
    fake = FakeLLM({STEP: bad, (AGENT, "output_repair"): report()})
    out = analyst(fake).run(ctx(nworld))
    assert out.trace.status == "ok" and out.output == report()
    assert problem in fake.calls[1].messages[-1].content


def test_a_catalyst_number_the_evidence_does_not_hold_gets_a_repair_turn(
    nworld: NewsWorld,
) -> None:
    ok = report(
        catalysts=(
            catalyst(
                date=None,
                evidence_id=TAG,
                description="Guidance lifted the revenue outlook by 3%.",
                expected_impact=Impact.POSITIVE,
            ),
        )
    )
    assert analyst(FakeLLM({STEP: ok})).run(ctx(nworld)).problems == ()
    bad = report(
        catalysts=(
            catalyst(
                date=None,
                evidence_id=TAG,
                description="Guidance lifted the revenue outlook by 9%.",
                expected_impact=Impact.POSITIVE,
            ),
        )
    )
    fake = FakeLLM({STEP: bad, (AGENT, "output_repair"): ok})
    out = analyst(fake).run(ctx(nworld))
    assert out.output == ok and out.trace.status == "ok"
    repair = fake.calls[1].messages[-1].content
    assert "every number must be stated by an evidence item cited in the same sentence" in repair
    assert "catalysts[0]: Guidance lifted the revenue outlook by 9%." in repair


def test_a_number_the_cited_evidence_does_not_hold_in_the_summary(nworld: NewsWorld) -> None:
    bad = report(summary="\n".join(f"{s}: Sales rose 77% [E:{TAG}]." for s in SECTIONS))
    fake = FakeLLM({STEP: bad, (AGENT, "output_repair"): report()})
    out = analyst(fake).run(ctx(nworld))
    assert out.trace.status == "ok"
    assert "Sales rose 77%" in fake.calls[1].messages[-1].content


def test_problems_left_after_the_repair_make_the_report_low_quality(nworld: NewsWorld) -> None:
    past = catalyst(date=date(2024, 2, 20), evidence_id="news:alpaca-a6")
    bad = report(catalysts=(past,), confidence=0.9)
    fake = FakeLLM({STEP: bad, (AGENT, "output_repair"): bad})
    out = analyst(fake).run(ctx(nworld))
    assert out.low_quality and out.trace.status == "low_quality"
    assert out.output.confidence == CONFIG.agents.low_quality_confidence_cap
    assert isinstance(out.output, NewsReport)
    assert len(out.problems) == 1 and out.problems[0].startswith("catalysts[0]: date 2024-02-20")


def test_an_unknown_catalyst_id_gets_the_citation_repair(nworld: NewsWorld) -> None:
    """A catalyst's `evidence_id` is a citation: an id the run never showed is unknown."""
    bad = report(catalysts=(catalyst(date=None, evidence_id="news:alpaca-999"),))
    fake = FakeLLM({STEP: bad, (AGENT, "evidence_repair"): report()})
    out = analyst(fake).run(ctx(nworld))
    assert out.trace.status == "ok"
    assert "news:alpaca-999" in fake.calls[1].messages[-1].content


def test_an_unknown_signal_id_gets_the_citation_repair(nworld: NewsWorld) -> None:
    fake = FakeLLM({STEP: report(ids=("news:alpaca-404",)), (AGENT, "evidence_repair"): report()})
    out = analyst(fake).run(ctx(nworld))
    assert out.trace.status == "ok"
    assert "news:alpaca-404" in fake.calls[1].messages[-1].content


def test_a_schema_violation_is_repaired_by_the_gateway(nworld: NewsWorld) -> None:
    """A catalyst without its evidence id fails the schema; the gateway asks again."""
    good = report()
    data = good.model_dump(mode="json")
    del data["catalysts"][0]["evidence_id"]
    fake = FakeLLM({STEP: [FakeReply(content=json.dumps(data)), good]})
    out = analyst(fake).run(ctx(nworld))
    assert out.output == good and out.trace.status == "ok"
    assert "evidence_id" in fake.calls[1].messages[-1].content


def test_catalyst_problems_unit() -> None:
    day = date(2024, 3, 1)
    assert catalyst_problems(catalyst(), 0, day) == []
    today = catalyst(date=day, evidence_id="news:alpaca-a6")
    assert catalyst_problems(today, 0, day) == []  # today is ahead enough
    undated = catalyst(date=None, evidence_id="rx:AAA:2024-02-01")
    assert catalyst_problems(undated, 0, day) == []
    none_item = catalyst(date=None, evidence_id="evt:AAA:2024-03-01:none")
    (problem,) = catalyst_problems(none_item, 2, day)
    assert problem.startswith("catalysts[2]: evidence_id evt:AAA:2024-03-01:none must be")
    assert CATALYST_EVIDENCE == ("news:", "evt:", "rx:")


# --- the empty-news guard ---------------------------------------------------------------------


def test_with_no_news_a_good_report_passes_and_keeps_its_event_catalyst(nworld: NewsWorld) -> None:
    fake = FakeLLM({STEP: no_news_report()})
    out = analyst(fake).run(ctx(nworld, "P03"))
    assert out.trace.status == "ok" and out.problems == ()
    assert out.output.confidence == 0.2
    assert [c.evidence_id for c in out.output.catalysts] == ["evt:P03:earnings:2024-03-06"]
    assert "ncov:P03:2024-03-01:company" in out.trace.evidence_ids


def test_with_no_news_the_report_must_say_so_stay_flat_and_cap_its_confidence(
    nworld: NewsWorld,
) -> None:
    lines = {s: "A strong week [E:ncov:P03:2024-03-01:company]." for s in SECTIONS}
    bad = no_news_report(
        summary="\n".join(f"{s}: {t}" for s, t in lines.items()),
        signals=tuple(
            Signal(name=n, direction=Direction.LONG, strength=0.5, evidence_ids=()).model_copy(
                update={"evidence_ids": ("ncov:P03:2024-03-01:company",)}
            )
            for n in SIGNALS
        ),
        confidence=0.8,
    )
    fake = FakeLLM({STEP: bad, (AGENT, "output_repair"): no_news_report()})
    out = analyst(fake).run(ctx(nworld, "P03"))
    assert out.trace.status == "ok" and out.output == no_news_report()
    repair = fake.calls[1].messages[-1].content
    assert 'say so plainly in "Material vs noise:"' in repair
    assert "the confidence must be at most 0.2, not 0.8" in repair
    assert "the news_flow signal must be FLAT, not LONG" in repair


def test_the_cap_holds_even_when_the_repair_fails(nworld: NewsWorld) -> None:
    """`finalize`: 0.2 is below the low-quality cap of 0.3, so it is enforced after it."""
    stubborn = no_news_report(confidence=0.9)
    fake = FakeLLM({STEP: stubborn, (AGENT, "output_repair"): stubborn})
    out = analyst(fake).run(ctx(nworld, "P03"))
    assert out.low_quality and out.trace.status == "low_quality"
    assert out.output.confidence == 0.2  # not 0.3
    assert json.loads(out.trace.output_json or "{}")["confidence"] == 0.2
    assert any("the confidence must be at most 0.2" in p for p in out.problems)


def test_the_cap_comes_from_the_config(nworld: NewsWorld) -> None:
    config = AtlasConfig.model_validate({"agents": {"news": {"empty_news_confidence_cap": 0.1}}})
    fake = FakeLLM({STEP: no_news_report(confidence=0.1)})
    view = DataView(nworld.clean, AS_OF, config)
    out = analyst(fake, config).run(AgentContext(view, inputs={"symbol": "P03"}))
    assert out.output.confidence == 0.1 and out.problems == ()
    assert "at most 0.1" in fake.calls[0].messages[0].content


def test_the_guard_does_not_touch_a_report_with_news(nworld: NewsWorld) -> None:
    out = analyst(FakeLLM({STEP: report(confidence=0.9)})).run(ctx(nworld))
    assert out.output.confidence == 0.9 and out.problems == ()


def test_a_missing_news_table_is_the_empty_path_with_a_citable_gap(tmp_path: Path) -> None:
    full = Lake(tmp_path / "full", registry=DEFAULT_REGISTRY.copy())
    write_world(full, future=False)
    lake = Lake(tmp_path / "part", registry=DEFAULT_REGISTRY.copy())
    for name in ("prices_daily", "corporate_actions", "instruments", "index_membership"):
        lake.write_table(name, full.read_table(name), mode="upsert")
    gap = "gap:AAA:2024-03-01:get_company_news"
    lines = {"Material vs noise": f"There are no news articles to read [E:{gap}]."}
    summary = "\n".join(f"{s}: {lines.get(s, f'Nothing to add [E:{gap}].')}" for s in SECTIONS)
    flat = tuple(
        Signal(name=n, direction=Direction.FLAT, strength=0.1, evidence_ids=(gap,)) for n in SIGNALS
    )
    answer = report(
        summary=summary, signals=flat, ids=(gap,), catalysts=(), risks=(), confidence=0.9
    )
    fake = FakeLLM({STEP: answer, (AGENT, "output_repair"): answer})
    agent = analyst(fake)
    out = agent.run(AgentContext(DataView(lake, AS_OF, CONFIG), inputs={"symbol": "AAA"}))
    assert f"[E:{gap}] " in user_text(fake.calls[0])
    assert "news is empty" in user_text(fake.calls[0])
    assert out.output.confidence == 0.2  # finalize: the model said 0.9


@pytest.mark.parametrize(
    "text",
    [
        "There are no news articles about the stock.",
        "No recent company news.",
        "Zero headlines were found.",
        "The coverage item shows 0 articles in 21 days.",
        "no news",
        "There is no relevant recent news coverage.",
    ],
)
def test_says_no_news(text: str) -> None:
    assert says_no_news(text)


@pytest.mark.parametrize(
    "text",
    [
        "Several articles cover guidance.",
        "There is no sign of trouble in the news articles.",
        "Nothing notable.",
        "",
        "Coverage is heavy.",
        "A notable news day.",
    ],
)
def test_says_no_news_is_not_fooled(text: str) -> None:
    assert not says_no_news(text)


# --- failure modes and tools ------------------------------------------------------------------


def test_a_blocked_symbol_fails_the_run_with_a_failed_trace(
    nworld: NewsWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DataView, "blocked", property(lambda self: frozenset({"AAA"})))
    fake = FakeLLM({STEP: report()})
    agent = analyst(fake)
    with pytest.raises(DataError, match="blocked"):
        agent.run(ctx(nworld))
    assert fake.calls == []  # nothing spent
    (trace,) = agent.traces.traces()
    assert trace.status == "failed" and "blocked" in (trace.error or "")


def test_a_gap_is_shown_and_citable(nworld: NewsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    from atlas.agents.tools import news_tools

    def broken(*args: object, **kw: object) -> object:
        raise DataError("no peers readable")

    monkeypatch.setattr(news_tools, "_sector_peers", broken)
    gap = "gap:AAA:2024-03-01:get_sector_news"
    fake = FakeLLM({STEP: report(ids=(gap,), risks=(), catalysts=())})
    out = analyst(fake).run(ctx(nworld))
    assert f"[E:{gap}] " in user_text(fake.calls[0])
    assert out.trace.status == "ok"


def test_the_model_may_look_at_a_peer_with_a_tool(nworld: NewsWorld) -> None:
    call = ToolCall(id="t1", name="get_company_news", arguments={"symbol": "P01"})
    fake = FakeLLM(
        {
            STEP: FakeReply(tool_calls=(call,)),
            (AGENT, "round-2"): report(ids=("news:alpaca-p3",), catalysts=()),
        }
    )
    out = analyst(fake).run(ctx(nworld))
    assert [(c.name, c.error) for c in out.trace.tool_calls] == [("get_company_news", None)]
    assert out.trace.status == "ok"
    assert "news:alpaca-p3" in out.trace.cited_ids


def test_memories_reach_the_prompt_point_in_time(nworld: NewsWorld) -> None:
    from datetime import UTC, datetime

    from atlas.agents.memory import AgentMemory, HashEmbedder

    cfg = AtlasConfig.model_validate(
        {"agents": {"memory": {"embedder": "hash", "min_similarity": 0.0}}}
    )
    memory = AgentMemory(HashEmbedder(), config=cfg)
    known = "AAA news and events: guidance raises were followed by a gap up; check the date."
    later = "AAA news and events: a lesson written after the as-of must not show."
    memory.add_lesson(AGENT, known, created_at=datetime(2024, 2, 1, tzinfo=UTC))
    memory.add_lesson(AGENT, later, created_at=datetime(2024, 3, 5, tzinfo=UTC))
    fake = FakeLLM({STEP: report()})
    context = AgentContext(nworld.view(), memory=memory, inputs={"symbol": "AAA"})
    analyst(fake).run(context)
    user = user_text(fake.calls[0])
    assert known in user and later not in user


def test_without_a_memory_the_prompt_says_so(nworld: NewsWorld) -> None:
    fake = FakeLLM({STEP: report()})
    analyst(fake).run(ctx(nworld))
    assert "(no memories)" in user_text(fake.calls[0])


def test_uncited_numbers_is_the_shared_check() -> None:
    assert uncited_numbers("Revenue rose 3%.") == ["Revenue rose 3%."]


# --- the recorded live run --------------------------------------------------------------------


def test_the_recorded_aapl_run_replays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Brief acceptance: the report validates (schema), every catalyst has an evidence id the
    run showed, every number is cited, and it runs offline from the cache: no main lake, no
    key, no provider call, the recorded report."""
    from recorded import recorded_run, replay_config, replay_lake
    from recorded_news import AS_OF as DAY
    from recorded_news import FIXTURE, SYMBOL

    from atlas.llm import build_gateway
    from atlas.llm.providers.offline import OfflineProvider

    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    lake = replay_lake(tmp_path, FIXTURE)
    config = replay_config(lake.root)
    gateway = build_gateway(config, lake=lake)
    assert isinstance(gateway.provider, OfflineProvider)
    agent = NewsAnalyst(gateway, config=config, traces=TraceStore())
    out = agent.run(AgentContext(DataView(lake, DAY, config), inputs={"symbol": SYMBOL}))
    run = recorded_run(FIXTURE)
    expected = NewsReport.model_validate_json((FIXTURE / "expected_report.json").read_text())
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
    assert out.output.catalysts  # the report has catalysts ...
    shown = set(out.trace.evidence_ids)
    assert all(c.evidence_id in shown for c in out.output.catalysts)  # ... each with its evidence
    assert all(c.date is None or c.date >= DAY for c in out.output.catalysts)
    assert (out.output.ticker, out.output.as_of) == (SYMBOL, DAY)
    assert set(c.evidence_id for c in out.output.catalysts) <= set(out.trace.cited_ids)


# --- audit: exact problem texts, guard conditions, wiring (mutation testing) -------------------


def problems_of(nworld: NewsWorld, bad: NewsReport, symbol: str = "AAA") -> list[str]:
    agent = analyst(FakeLLM({}))
    return list(agent.check_output(ctx(nworld, symbol), bad))


def test_the_problem_texts_are_exact(nworld: NewsWorld) -> None:
    """Their text goes into the repair turn and so into the LLM cache key: pinned."""
    flat = tuple(s for s in _sig() if s.name == "news_flow")
    bad = report(
        signals=flat,
        summary=f"Catalysts: up [E:{TAG}].",
        risks=("Revenue may fall 4%.",),
        catalysts=(catalyst(date=date(2024, 2, 20), evidence_id="ncov:AAA:2024-03-01:company"),),
    )
    assert problems_of(nworld, bad) == [
        "signals must be exactly news_flow, catalysts, event_risk (each once); got news_flow",
        "the summary needs these sections, in order, each starting a line with its label and a "
        'colon: "Material vs noise:", "Risk events:", "Narrative shifts:", "What the market '
        'may be missing:", "Event calendar:"',
        "every sentence that states a number needs an [E:<id>] tag of its evidence; these have "
        'none: "risks[0]: Revenue may fall 4%."',
        "catalysts[0]: evidence_id ncov:AAA:2024-03-01:company must be a news:, evt: or rx: "
        "item (an article, an event or a reaction), not a coverage, summary, none or gap item",
        "catalysts[0]: date 2024-02-20 is before the as-of day 2024-03-01: a catalyst is ahead, "
        "or has no date",
    ]
    assert problems_of(nworld, report().model_copy(update={"signals": ()})) == [
        "signals must be exactly news_flow, catalysts, event_risk (each once); got none"
    ]
    many = report(catalysts=tuple(catalyst() for _ in range(9)))
    assert problems_of(nworld, many) == [
        "at most 8 catalysts, not 9: keep the ones that matter most"
    ]


def test_uncited_numbers_of_the_summary_and_the_risks_are_both_listed(nworld: NewsWorld) -> None:
    bad = report(
        summary="\n".join(
            f"{s}: " + ("Sales rose 5%." if s == "Catalysts" else "Nothing.") for s in SECTIONS
        ),
        risks=("Costs rose 7%.",),
    )
    (problem,) = problems_of(nworld, bad)
    assert "Sales rose 5%." in problem and "risks[0]: Costs rose 7%." in problem


def test_the_empty_news_problems_are_exact_and_add_to_the_others(nworld: NewsWorld) -> None:
    loud = tuple(
        Signal(
            name=n,
            direction=Direction.LONG,
            strength=0.5,
            evidence_ids=("ncov:P03:2024-03-01:company",),
        )
        for n in SIGNALS
    )
    bad = no_news_report(
        signals=loud,
        confidence=0.8,
        summary="\n".join(f"{s}: A fine week [E:ncov:P03:2024-03-01:company]." for s in SECTIONS),
        analyst="someone",
    )
    assert problems_of(nworld, bad, "P03") == [
        'analyst must be "news_analyst", not "someone"',  # the earlier problem is kept
        "there is no company news in the window (its coverage item says 0 articles): say so "
        'plainly in "Material vs noise:" (for example "There are no news articles about this '
        'stock in the last weeks")',
        "with no company news the confidence must be at most 0.2, not 0.8",
        "with no company news the news_flow signal must be FLAT, not LONG",
    ]


def test_only_the_news_flow_signal_must_be_flat_without_news(nworld: NewsWorld) -> None:
    signals = tuple(
        Signal(
            name=n,
            direction=Direction.FLAT if n == "news_flow" else Direction.SHORT,
            strength=0.3,
            evidence_ids=("ncov:P03:2024-03-01:company",),
        )
        for n in SIGNALS
    )
    assert problems_of(nworld, no_news_report(signals=signals), "P03") == []


def test_a_confidence_at_the_cap_is_left_alone(nworld: NewsWorld) -> None:
    good = no_news_report(confidence=0.2)
    out = analyst(FakeLLM({STEP: good})).run(ctx(nworld, "P03"))
    assert out.output is not good or out.output == good  # equal either way
    assert out.output.confidence == 0.2 and out.problems == ()
    agent = analyst(FakeLLM({}))
    same = agent.finalize(ctx(nworld, "P03"), good)
    assert same is good  # not even copied


def test_a_custom_tool_registry_is_kept(nworld: NewsWorld) -> None:
    custom = news_registry()
    assert analyst(FakeLLM({}), tool_registry=custom)._registry is custom
    assert analyst(FakeLLM({}))._registry is not custom


def test_the_memory_is_asked_for_this_agent_this_symbol_and_this_query(nworld: NewsWorld) -> None:
    from atlas.agents.research.news import MEMORY_QUERY

    class Spy:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def recall(
            self, agent: str, query: str, as_of: object, k: object, symbol: object, **kw: object
        ) -> list[object]:
            self.calls.append((agent, query, symbol))
            return []

    spy = Spy()
    context = AgentContext(nworld.view(), memory=spy, inputs={"symbol": "aaa"})  # type: ignore[arg-type]
    analyst(FakeLLM({STEP: report()})).run(context)
    assert spy.calls == [(AGENT, MEMORY_QUERY.format(symbol="AAA"), "AAA")]


def test_the_signal_list_in_the_problem_is_comma_separated(nworld: NewsWorld) -> None:
    two = report().model_copy(update={"signals": _sig()[:2]})
    assert problems_of(nworld, two) == [
        "signals must be exactly news_flow, catalysts, event_risk (each once); got "
        "news_flow, catalysts"
    ]


def test_the_missing_symbol_error_is_exact(nworld: NewsWorld) -> None:
    with pytest.raises(ConfigError) as caught:
        NewsAnalyst.symbol(AgentContext(nworld.view()))
    assert str(caught.value) == "news_analyst needs inputs['symbol'] (a ticker)"
