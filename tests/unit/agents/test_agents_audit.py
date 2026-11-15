"""Stage 26 audit findings, each pinned by a test (see the stage worklog, "Audit")."""

from datetime import UTC, datetime

from agent_helpers import EchoAnalyst, StepClock, report
from agent_world import AGENT_PROMPTS, World

from atlas.agents.prompting import PromptRegistry
from atlas.agents.trace import TraceStore
from atlas.core.config import AtlasConfig
from atlas.core.storage import Database, Lake
from atlas.core.types.ids import SeededIds, use_ids
from atlas.llm import FakeLLM, LLMGateway


class _Frozen:
    """A clock that never moves (so seeded ids depend on the seed alone)."""

    def now(self) -> datetime:
        return datetime(2024, 3, 1, 13, tzinfo=UTC)


def test_a1_seeded_re_runs_do_not_collide_in_agent_runs(
    world: World, fake_llm: FakeLLM, tmp_lake: Lake
) -> None:
    # A backtest installs `SeededIds`, so ids minted with `new_ulid` repeat across a re-run
    # with the same seed. Two such re-runs on one lake must both persist their trace.
    fake_llm.add("echo_analyst", None, report("news:alpaca-2"))
    with Database(tmp_lake) as db:
        traces = TraceStore(db)
        agent = EchoAnalyst(
            LLMGateway(fake_llm, AtlasConfig()),
            prompts=PromptRegistry([AGENT_PROMPTS]),
            traces=traces,
            clock=StepClock(),
        )
        ids = []
        for _ in range(2):
            with use_ids(SeededIds(7, _Frozen())):
                ids.append(agent.run(world.ctx(inputs={"symbol": "AAA"})).trace.agent_run_id)
        assert ids[0] != ids[1]
        assert [t.agent_run_id for t in traces.traces()] == ids


# --- post-DONE audit (2026-11-14, second pass) ------------------------------------------------


def test_a2_a_template_edited_on_disk_does_not_change_a_loaded_prompt(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "t.j2"
    path.write_text(
        "{# version: 1 #}\n{% block system %}old{% endblock %}{% block user %}u{% endblock %}\n"
    )
    registry = PromptRegistry([tmp_path])
    first = registry.render("t.j2", {})
    path.write_text(
        "{# version: 1 #}\n{% block system %}NEW{% endblock %}{% block user %}u{% endblock %}\n"
    )
    second = registry.render("t.j2", {})
    # The recorded version and digest describe the text that is actually sent.
    assert second.system == first.system == "old"
    assert second.template == first.template


def test_a3_an_output_that_cites_nothing_is_uncited() -> None:
    from atlas.agents.evidence import check_citations

    empty = report()  # an AnalystReport with no signals and no inline tag
    result = check_citations(empty, {"news:1"})
    assert not result.ok and result.uncited_claims == ("<root>",)
    assert "cites no evidence" in result.describe()
    tagged = report(summary="Upbeat [E:news:1].")
    assert check_citations(tagged, {"news:1"}).ok


def test_a3_outputs_that_cannot_cite_are_left_alone() -> None:
    from pydantic import BaseModel

    from atlas.agents.evidence import check_citations

    class Plain(BaseModel):
        text: str

    assert check_citations(Plain(text="x"), set()).ok


def test_a3_an_empty_evidence_list_is_uncited() -> None:
    from pydantic import BaseModel

    from atlas.agents.evidence import check_citations

    class Outlook(BaseModel):
        summary: str
        evidence: tuple[str, ...]

    assert check_citations(Outlook(summary="s", evidence=()), {"news:1"}).uncited_claims == (
        "<root>",
    )


def test_a4_conviction_is_capped_like_confidence() -> None:
    from pydantic import BaseModel, Field

    from atlas.agents.evidence import cap_confidence

    class Thesis(BaseModel):
        conviction: float = Field(ge=0, le=1)

    class Both(BaseModel):
        confidence: float
        conviction: float

    assert cap_confidence(Thesis(conviction=0.9), 0.3).conviction == 0.3
    capped = cap_confidence(Both(confidence=0.9, conviction=0.2), 0.3)
    assert (capped.confidence, capped.conviction) == (0.3, 0.2)


def test_a6_any_exception_is_traced_as_failed(world: World, fake_llm: FakeLLM) -> None:
    import pytest

    class Broken(EchoAnalyst):
        def gather(self, ctx):  # type: ignore[no-untyped-def]
            raise ValueError("bug in gather")

    traces = TraceStore()
    agent = Broken(
        LLMGateway(fake_llm, AtlasConfig()), prompts=PromptRegistry([AGENT_PROMPTS]), traces=traces
    )
    with pytest.raises(ValueError, match="bug in gather"):
        agent.run(world.ctx(inputs={"symbol": "AAA"}))
    (trace,) = traces.traces()
    assert trace.status == "failed" and trace.error == "ValueError: bug in gather"


def test_a7_audit_without_a_run_context_fails_before_any_llm_call(
    world: World, fake_llm: FakeLLM, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    import pytest

    from atlas.core.audit import AuditLog
    from atlas.core.errors import AtlasError

    fake_llm.add("echo_analyst", None, report("news:alpaca-2"))
    agent = EchoAnalyst(
        LLMGateway(fake_llm, AtlasConfig()),
        prompts=PromptRegistry([AGENT_PROMPTS]),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    with pytest.raises(AtlasError, match="run context"):
        agent.run(world.ctx(inputs={"symbol": "AAA"}))
    assert fake_llm.calls == []


def test_a8_price_history_of_a_blocked_symbol_says_blocked(world: World) -> None:
    from atlas.agents.tools import default_tools
    from atlas.llm.schemas import ToolCall

    out = default_tools().call(
        world.ctx(), ToolCall(id="c", name="price_history", arguments={"symbol": "BAD"})
    )
    assert out.error is not None and "BAD" in out.error and "blocked" in out.error


def test_a9_price_range_is_split_adjusted(world: World) -> None:
    # AAA split 2:1 on 2024-01-16: a raw high before the split is not comparable with the
    # close after it, so the range is given in adjusted closes, like the return.
    from atlas.agents.tools import default_tools
    from atlas.llm.schemas import ToolCall

    args = {"symbol": "AAA", "lookback": 60}
    out = default_tools().call(world.ctx(), ToolCall(id="c", name="price_history", arguments=args))
    (item,) = out.evidence
    bars = world.view().prices(["AAA"], 60)
    adj = bars["adj_close"]
    assert f"adjusted close range {adj.min():.2f} to {adj.max():.2f}" in item.text
    assert f"{bars['high'].max():.2f}" not in item.text  # the raw pre-split high
