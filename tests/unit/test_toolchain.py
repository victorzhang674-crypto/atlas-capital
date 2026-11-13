"""The default pytest run never reaches live services, real LLMs or slow integration tests."""

import re
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


def test_ci_runs_the_full_check_offline(repo_root: Path) -> None:
    # CI runs exactly what a stage must pass locally (`make check`) plus the integration
    # tests, from the lock file, and needs no secrets: live/llm tests stay deselected.
    text = (repo_root / ".github" / "workflows" / "ci.yml").read_text()
    workflow = yaml.safe_load(text)
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    runs = [step.get("run", "") for step in steps]
    assert "uv sync --locked --group dev" in runs
    assert runs.index("make check") < runs.index("make test-integration")
    assert "secrets." not in text
    # Full release tags: some actions (astral-sh/setup-uv) publish no moving major tag, and the
    # first CI run failed on `@v10` (Phase 0 checkpoint).
    for ref in [step["uses"] for step in steps if "uses" in step]:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+\.\d+\.\d+", ref), ref


def _make_rule(repo_root: Path, target: str) -> str:
    makefile = (repo_root / "Makefile").read_text()
    found = re.search(rf"^{re.escape(target)}:\n\t(.+)$", makefile, re.MULTILINE)
    assert found is not None, target
    return found.group(1)


def test_make_test_runs_in_parallel_one_file_per_worker(
    repo_root: Path, pytest_ini: dict[str, Any]
) -> None:
    # Phase 2 checkpoint, Tier 3: the suite passed 6 minutes. `make test` (so `make check` and
    # CI) spreads the test files over the cores; a file stays on one worker, so its module
    # fixtures (the synthetic lakes) are still built once.
    for target in ("test", "test-integration"):
        rule = _make_rule(repo_root, target)
        assert rule.startswith("uv run pytest ") and "-n auto" in rule, rule
        assert "--dist loadfile" in rule, rule
    assert "-m integration" in _make_rule(repo_root, "test-integration")
    # a plain `uv run pytest` stays serial: single tests, `--pdb` and the mutation scripts
    assert "-n " not in pytest_ini["addopts"] and "--dist" not in pytest_ini["addopts"]
    meta = tomllib.loads((repo_root / "pyproject.toml").read_text())
    assert any(
        dep.startswith("pytest-xdist")
        for dep in meta["dependency-groups"]["dev"]
        if isinstance(dep, str)
    )
