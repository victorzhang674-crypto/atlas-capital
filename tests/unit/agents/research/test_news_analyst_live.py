"""Live run of the news analyst on DeepSeek's quick tier (Stage 31).

Marked `llm`: deselected by default; needs `DEEPSEEK_API_KEY` and costs about 0.3 US cents.
It runs AAPL as of 2024-05-01 on a copy of the recorded fixture's lake slice with the cache
off, so the model really answers: the report must validate, cite only shown evidence, tag every
number and give every catalyst an evidence id the run showed. The recording itself is made by
the stage's `artifacts/record_fixture.py`.
"""

import shutil
from pathlib import Path

import pytest
from recorded_news import AS_OF, FIXTURE, SYMBOL

from atlas.agents.context import AgentContext
from atlas.agents.research import NewsAnalyst
from atlas.agents.research.checks import uncited_numbers
from atlas.agents.research.news import SIGNALS
from atlas.agents.trace import TraceStore
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.llm import build_gateway

pytestmark = pytest.mark.llm

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_a_real_model_writes_a_cited_aapl_news_report(tmp_path: Path) -> None:
    shutil.copytree(FIXTURE / "lake", tmp_path / "lake")
    config = load_config(
        "dev",
        overrides={
            "data": {"lake_path": str(tmp_path / "lake")},
            "llm": {"cache": {"mode": "off"}},
        },
        config_dir=REPO_ROOT / "config",
    )
    if config.secrets.deepseek_api_key is None:
        pytest.skip("DEEPSEEK_API_KEY is not set")
    lake = Lake(tmp_path / "lake")
    agent = NewsAnalyst(build_gateway(config, lake=lake), config=config, traces=TraceStore())
    out = agent.run(AgentContext(DataView(lake, AS_OF, config), inputs={"symbol": SYMBOL}))
    trace = out.trace
    print(  # shown with -s; recorded in the stage artifacts
        f"\nLIVE news: status={trace.status} llm_calls={trace.llm_calls} "
        f"tools={[c.name for c in trace.tool_calls]} cited={len(trace.cited_ids)} "
        f"problems={list(out.problems)} tokens={trace.usage.input_tokens}+"
        f"{trace.usage.output_tokens} cost_usd={trace.cost_usd:.6f} "
        f"catalysts={[(c.date, c.evidence_id, c.expected_impact) for c in out.output.catalysts]}"
    )
    assert trace.status in {"ok", "low_quality"}
    assert (out.output.ticker, out.output.as_of) == (SYMBOL, AS_OF)
    assert sorted(s.name for s in out.output.signals) == sorted(SIGNALS)
    assert set(trace.cited_ids) - set(trace.unknown_ids) <= set(trace.evidence_ids)
    assert all(c.evidence_id in trace.evidence_ids for c in out.output.catalysts)
    if trace.status == "ok":
        assert uncited_numbers(out.output.summary) == []
        assert all(c.date is None or c.date >= AS_OF for c in out.output.catalysts)
    assert 0 < trace.cost_usd < 0.05
