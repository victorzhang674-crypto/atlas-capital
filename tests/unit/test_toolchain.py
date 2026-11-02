"""The default pytest run never reaches live services, real LLMs or slow integration tests."""

import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def pytest_ini(repo_root: Path) -> dict[str, Any]:
    meta = tomllib.loads((repo_root / "pyproject.toml").read_text())
    ini: dict[str, Any] = meta["tool"]["pytest"]["ini_options"]
    return ini


def test_markers_registered(pytest_ini: dict[str, Any]) -> None:
    names = {marker.split(":", 1)[0].strip() for marker in pytest_ini["markers"]}
    assert {"live", "llm", "integration"} <= names


def test_default_run_deselects_external_markers(pytest_ini: dict[str, Any]) -> None:
    addopts = pytest_ini["addopts"]
    assert "-m 'not live and not llm and not integration'" in addopts
    assert "--strict-markers" in addopts


def test_pre_commit_hooks_run_the_locked_tools_offline(repo_root: Path) -> None:
    # Phase 0 checkpoint: remote hook repos pin their own tool versions (which drift from
    # uv.lock) and need GitHub at install time; local `uv run` hooks need neither.
    config = yaml.safe_load((repo_root / ".pre-commit-config.yaml").read_text())
    assert [repo["repo"] for repo in config["repos"]] == ["local"]
    hooks = {hook["id"]: hook for hook in config["repos"][0]["hooks"]}
    assert set(hooks) == {"ruff", "ruff-format", "end-of-file-fixer", "trailing-whitespace"}
    for hook in hooks.values():
        assert hook["language"] == "system", hook["id"]
        assert hook["entry"].startswith("uv run --no-sync "), hook["id"]
    for hook_id in ("end-of-file-fixer", "trailing-whitespace"):  # recordings stay byte-exact
        assert "tests/fixtures/" in hooks[hook_id]["exclude"]
