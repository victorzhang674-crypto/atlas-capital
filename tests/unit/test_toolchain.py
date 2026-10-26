"""The default pytest run never reaches live services, real LLMs or slow integration tests."""

import tomllib
from pathlib import Path
from typing import Any

import pytest


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
