"""Stage 28 Task 8: the mermaid export, the docs and the public API of `atlas.orchestration`."""

import ast
import re
from pathlib import Path

import atlas.orchestration as orchestration
from atlas.orchestration.research_graph import build_research_graph
from atlas.orchestration.stubs import ANALYSTS, stub_team

STAGE_28 = {
    "ANALYSTS",
    "AnalystFn",
    "CHECKPOINT_FILE",
    "DebateFn",
    "DirectorFn",
    "FundState",
    "GRAPH_METADATA_KEY",
    "MAX_MESSAGE_CHARS",
    "NodeError",
    "RESERVED_NODES",
    "ResearchGraph",
    "ResearchInput",
    "ResearchOutput",
    "ResearchState",
    "ResearchTask",
    "ResearchTeam",
    "append_errors",
    "build_research_graph",
    "build_research_subgraph",
    "checkpoint_path",
    "failed_symbols",
    "guarded",
    "invoke_untraced",
    "merge_keyed",
    "merge_reports",
    "resume_graph",
    "run_graph",
    "serializer",
    "sqlite_checkpointer",
    "state_types",
    "stub_team",
}

PACKAGE_DIR = Path(orchestration.__file__).resolve().parent


PHASE_3_CHECKPOINT = {"INTEGRITY_ERRORS", "integrity_errors"}  # Tier 2 item 2


def test_public_api_is_exported() -> None:
    assert set(orchestration.__all__) == STAGE_28 | PHASE_3_CHECKPOINT
    assert len(orchestration.__all__) == len(set(orchestration.__all__))
    for name in STAGE_28 | PHASE_3_CHECKPOINT:
        assert getattr(orchestration, name) is not None


def test_every_module_has_a_docstring() -> None:
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        assert ast.get_docstring(ast.parse(path.read_text())), path


def _diagram() -> str:
    return build_research_graph(stub_team(), max_concurrency=4).mermaid()


def test_the_mermaid_export_shows_the_fan_out_and_every_subgraph_node() -> None:
    text = _diagram()
    assert text.startswith("---") and "graph TD;" in text
    assert "prepare -.-> research" in text  # the conditional Send fan-out
    assert "subgraph research" in text
    for node in (*ANALYSTS, "join", "debate", "director", "publish"):
        assert f"research\\3a{node}({node})" in text, node
    assert "collect --> __end__" in text


def _mermaid_block(markdown: str) -> str:
    match = re.search(r"```mermaid\n(.*?)```", markdown, re.DOTALL)
    assert match, "no mermaid block"
    return match.group(1)


def test_the_docs_diagram_is_the_current_export(repo_root: Path) -> None:
    text = (repo_root / "docs" / "orchestration.md").read_text()
    assert _mermaid_block(text).strip() == _diagram().strip()


def test_the_docs_explain_the_graph(repo_root: Path) -> None:
    text = (repo_root / "docs" / "orchestration.md").read_text()
    for needle in (
        "FundState",
        "ResearchTeam",
        "Send",
        "max_concurrency",
        "checkpoints.sqlite",
        "resume_graph",
        "NodeError",
        "dropped",
        "KeyboardInterrupt",
        "roster order",
        "mermaid()",
        "invoke_untraced",
        "LANGSMITH_TRACING",
        "fingerprint",
    ):
        assert needle in text, needle
