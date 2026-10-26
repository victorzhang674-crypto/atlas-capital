"""Repository docs keep the governing-rules import and stay safe from tooling rewrites."""

from pathlib import Path


def test_claude_md_imports_project_rules_first(repo_root: Path) -> None:
    lines = (repo_root / "CLAUDE.md").read_text().splitlines()
    assert "@Project.md" in lines
    assert "## Engineering conventions (digest)" in lines
    assert lines.index("@Project.md") < lines.index("## Engineering conventions (digest)")


def test_precommit_excludes_governing_docs(repo_root: Path) -> None:
    config = (repo_root / ".pre-commit-config.yaml").read_text()
    assert config.count(r"exclude: ^(Project\.md|Planning/)") >= 2
