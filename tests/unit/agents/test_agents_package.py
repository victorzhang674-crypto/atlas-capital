"""Stage 26 Task 9: the public API of `atlas.agents` and its documentation."""

import ast
from pathlib import Path

import atlas.agents as agents

STAGE_26 = {
    "AgentContext",
    "AgentOutput",
    "AgentTrace",
    "BaseAgent",
    "CitationReport",
    "EvidenceLedger",
    "PromptRegistry",
    "PromptTemplate",
    "RenderedPrompt",
    "Tool",
    "ToolCallRecord",
    "ToolOutcome",
    "ToolRegistry",
    "TraceStore",
    "cap_confidence",
    "check_citations",
    "default_tools",
    "evidence_line",
    "evidence_lines",
    "evidence_tag",
}

AGENTS_DIR = Path(agents.__file__).resolve().parent


def test_public_api_is_exported() -> None:
    assert set(agents.__all__) == STAGE_26
    assert len(agents.__all__) == len(set(agents.__all__))
    for name in STAGE_26:
        assert getattr(agents, name) is not None


def test_every_module_has_a_docstring() -> None:
    for path in sorted(AGENTS_DIR.rglob("*.py")):
        assert ast.get_docstring(ast.parse(path.read_text())), path


def test_docs_explain_how_to_build_an_agent(repo_root: Path) -> None:
    text = (repo_root / "docs" / "agents.md").read_text()
    for needle in (
        "{# version: N #}",
        "BaseAgent",
        "max_tool_rounds",
        "low_quality",
        "agent_runs",
        "AGENT_REPORT",
        "DataView",
        "prompt_version",
    ):
        assert needle in text, needle
