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


def test_data_doc_sections(repo_root: Path) -> None:  # Stage 14 brief: docs/data.md
    text = (repo_root / "docs" / "data.md").read_text()
    for heading in (
        "## Sources",
        "## Licences and terms of use",
        "## Limitations",
        "## How to refresh",
    ):
        assert heading in text, heading
    from atlas.data.sync import DATASETS

    for dataset in DATASETS:
        assert f"`{dataset}`" in text, dataset
    for needle in (
        "sync_state.json",
        "atlas data sync",
        "atlas data update",
        "atlas data status",
        "atlas data quality",
        "data-limitations.md",
    ):
        assert needle in text, needle
