"""Live run of a tool-using agent on the default provider (DeepSeek), both tiers (audit A5).

Marked `llm`: deselected by default; needs `DEEPSEEK_API_KEY` and costs about a cent. It
checks that the real model goes through the framework end to end: versioned prompt, tool
calls answered from the point-in-time test world, JSON output parsed, citations checked.
"""

from pathlib import Path

import pytest
from agent_helpers import ToolAnalyst
from agent_world import AGENT_PROMPTS, World

from atlas.agents.prompting import PromptRegistry
from atlas.core.config import load_config
from atlas.llm import build_gateway

pytestmark = pytest.mark.llm


class LiveAnalyst(ToolAnalyst):
    name = "live_tool_analyst"
    prompt_template = "tool_analyst.j2"
    tools = ("news", "price_history")


@pytest.mark.parametrize("tier", ["quick", "deep"])
def test_a_real_model_runs_a_tool_agent_end_to_end(
    world: World, repo_root: Path, tier: str
) -> None:
    config = load_config("dev", config_dir=repo_root / "config")
    if config.secrets.deepseek_api_key is None:
        pytest.skip("DEEPSEEK_API_KEY is not set")

    class Agent(LiveAnalyst):
        pass

    Agent.tier = tier  # type: ignore[misc]
    agent = Agent(build_gateway(config), config=config, prompts=PromptRegistry([AGENT_PROMPTS]))
    out = agent.run(world.ctx(inputs={"symbol": "AAA"}))
    trace = out.trace
    print(  # shown with -s; recorded in the stage artifacts
        f"\nLIVE {tier}: status={trace.status} llm_calls={trace.llm_calls} "
        f"tools={[(c.round, c.name, c.error) for c in trace.tool_calls]} "
        f"cited={trace.cited_ids} unknown={trace.unknown_ids} uncited={trace.uncited_claims} "
        f"cost_usd={trace.cost_usd:.6f} output={trace.output_json}"
    )
    assert trace.tool_calls, "the model was told to call the tools first"
    assert {c.name for c in trace.tool_calls} <= {"news", "price_history"}
    assert out.output.ticker == "AAA"
    assert set(trace.cited_ids) - set(trace.unknown_ids) <= set(trace.evidence_ids)
    assert not {"news:alpaca-10", "news:alpaca-11", "news:alpaca-12"} & set(trace.evidence_ids)
    assert trace.status in {"ok", "low_quality"}
    assert 0 < trace.cost_usd < 0.05
