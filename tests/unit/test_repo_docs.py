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


def test_llm_doc_covers_the_gateway(repo_root: Path) -> None:
    # Phase 3 checkpoint, Tier 4 item 1: one page for the Stage 24 gateway. Every config key
    # under `llm` and every priced model is named, so a new key or model fails here until
    # the page explains it.
    text = (repo_root / "docs" / "llm.md").read_text()
    for heading in (
        "## Tiers and providers",
        "## A call",
        "## Budgets",
        "## Prices and the usage ledger",
        "## Retries and outages",
        "## Untrusted text",
        "## Testing without a vendor",
        "## Configuration",
    ):
        assert heading in text, heading
    from atlas.core.config import LLMConfig, load_config

    llm = load_config("dev", config_dir=repo_root / "config").llm
    for key in LLMConfig.model_fields:
        assert f"`{key}`" in text, key
    for group in (llm.retry, llm.budgets, llm.thinking, llm.tiers):
        for key in type(group).model_fields:
            assert f"`{key}`" in text, key
    for model in llm.prices:
        assert f"`{model}`" in text, model
    for needle in (
        "`openai_compatible`",
        "`anthropic`",
        "`FakeLLM`",
        "`FakeReply`",
        "`OfflineProvider`",
        "`data_block`",
        "`DATA_POLICY`",
        "`BudgetExceeded`",
        "`LLMTransientError`",
        "`CacheMiss`",
        "`llm_usage`",
        "atlas llm usage",
        "llm-cache.md",
        "agents.md",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        assert needle in text, needle
