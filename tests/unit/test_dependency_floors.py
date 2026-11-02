"""Declared dependency floors admit only versions the code runs on (phase 0 checkpoint)."""

import re
import tomllib
from pathlib import Path

import pytest


def _floor(requirements: list[str], name: str) -> tuple[int, ...]:
    """The `>=` floor of `name` in a list of PEP 508 requirement strings."""
    for req in requirements:
        match = re.fullmatch(rf"{re.escape(name)}\s*>=\s*([0-9.]+)", req.strip())
        if match:
            return tuple(int(part) for part in match.group(1).split("."))
    raise AssertionError(f"no '{name}>=' requirement in {requirements}")


@pytest.fixture
def pyproject(repo_root: Path) -> dict[str, object]:
    return tomllib.loads((repo_root / "pyproject.toml").read_text())


def test_pandas_floor_is_pandas_3(pyproject: dict[str, object]) -> None:
    # `atlas.core.calendar` and the yfinance adapter import `pandas.errors.Pandas4Warning`,
    # which exists from pandas 3.0 on; the storage tests also assume pandas 3's `str` dtype.
    project = pyproject["project"]
    groups = pyproject["dependency-groups"]
    assert isinstance(project, dict) and isinstance(groups, dict)
    assert _floor(project["dependencies"], "pandas") >= (3, 0)
    assert _floor(groups["dev"], "pandas-stubs") >= (3, 0)
