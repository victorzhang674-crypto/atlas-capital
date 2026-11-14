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
