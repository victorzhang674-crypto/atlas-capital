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
    assert {"live", "llm", "integration", "timing"} <= names


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


def _make_recipe(repo_root: Path, target: str) -> list[str]:
    """Every command line of `target`'s recipe."""
    makefile = (repo_root / "Makefile").read_text()
    found = re.search(rf"^{re.escape(target)}:\n((?:\t.+\n?)+)", makefile, re.MULTILINE)
    assert found is not None, target
    return [line.strip() for line in found.group(1).splitlines()]


TIMING_TESTS = {
    "tests/unit/core/storage/test_storage_upsert.py": (
        "test_upsert_one_million_rows_under_five_seconds"
    ),
    "tests/unit/quant/risk_model/test_factor_risk_model.py": "test_build_time_500_names_under_2s",
    "tests/unit/quant/features/test_feature_performance.py": "test_all_features_500x2000_under_20s",
}
"""The wall-clock budgets of the briefs (Stages 06, 19, 15)."""


def test_timing_tests_run_alone_after_the_parallel_part(
    repo_root: Path, pytest_ini: dict[str, Any]
) -> None:
    # Phase 2 checkpoint: CI failed twice when the 5 s upsert ran beside other test files on
    # a small runner (best of three 5.18 s). `make test` runs everything else in parallel,
    # then the budgets alone, so they are measured as before the parallel run; a plain
    # `uv run pytest -q` (the gate) still runs them with the rest.
    default = "not live and not llm and not integration"
    first, second = _make_recipe(repo_root, "test")
    assert "-n auto" in first and "--dist loadfile" in first, first
    assert f"-m '{default} and not timing'" in first, first
    assert second.startswith("uv run pytest ") and "-m timing" in second, second
    assert "-n " not in second, second
    assert f"-m '{default}'" in pytest_ini["addopts"]  # the gate keeps them
    for path, name in TIMING_TESTS.items():
        text = (repo_root / path).read_text()
        assert f"@pytest.mark.timing\ndef {name}(" in text, name


def test_make_test_runs_in_parallel_one_file_per_worker(
    repo_root: Path, pytest_ini: dict[str, Any]
) -> None:
    # Phase 2 checkpoint, Tier 3: the suite passed 6 minutes. `make test` (so `make check` and
    # CI) spreads the test files over the cores; a file stays on one worker, so its module
    # fixtures (the synthetic lakes) are still built once.
    for target in ("test", "test-integration"):
        rule = _make_recipe(repo_root, target)[0]
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
