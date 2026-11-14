"""Stage 26 Task 1: the `agents` config section, the AGENT_REPORT audit kind and Jinja2."""

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.core.audit import EventType
from atlas.core.config import AgentsConfig, AtlasConfig, load_config


def test_agents_config_defaults() -> None:
    agents = AtlasConfig().agents
    assert agents == AgentsConfig()
    assert agents.max_tool_rounds == 4
    assert agents.evidence_repair_retries == 1
    assert agents.low_quality_confidence_cap == pytest.approx(0.3)


def test_agents_config_in_fund_yaml_matches_defaults(repo_root: Path) -> None:
    assert load_config("dev", config_dir=repo_root / "config").agents == AgentsConfig()


@pytest.mark.parametrize(
    "bad",
    [
        {"max_tool_rounds": 0},
        {"max_tool_rounds": -1},
        {"evidence_repair_retries": -1},
        {"low_quality_confidence_cap": -0.1},
        {"low_quality_confidence_cap": 1.1},
        {"low_quality_confidence_cap": float("nan")},
        {"unknown_key": 1},
    ],
)
def test_agents_config_validation(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentsConfig.model_validate(bad)


def test_agent_report_event_type() -> None:
    assert EventType.AGENT_REPORT == "AGENT_REPORT"
    assert EventType("AGENT_REPORT") is EventType.AGENT_REPORT


def test_jinja2_is_a_runtime_dependency(repo_root: Path) -> None:
    # Stage 26: agent prompts are Jinja2 templates rendered at runtime (brief: "Jinja2
    # templates with {# version: N #}"), so jinja2 is a runtime dependency.
    project = tomllib.loads((repo_root / "pyproject.toml").read_text())["project"]
    assert any(dep.startswith("jinja2>=3.1") for dep in project["dependencies"])
