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


def test_duckdb_and_pyarrow_floors_are_tested_versions(pyproject: dict[str, object]) -> None:
    # The suite passes on duckdb 2.0.0 with pyarrow 19.0.0 and fails below either: duckdb 1.1
    # has no `allowed_directories`, 1.2-1.3 refuse the lake's own Parquet globs once external
    # access is off, and 1.4-1.5 read `PRAGMA version` as a SELECT; pyarrow 17-18 load string
    # columns as `object` instead of pandas 3's `str` (Stage 06 storage tests).
    project = pyproject["project"]
    assert isinstance(project, dict)
    assert _floor(project["dependencies"], "duckdb") >= (2, 0)
    assert _floor(project["dependencies"], "pyarrow") >= (19, 0)


def test_matplotlib_is_a_runtime_dependency(pyproject: dict[str, object]) -> None:
    # Stage 17: the signal tear sheet (`atlas research signal`) saves matplotlib figures, so
    # matplotlib is a runtime dependency of the CLI, not a dev-only one.
    project = pyproject["project"]
    assert isinstance(project, dict)
    assert _floor(project["dependencies"], "matplotlib") >= (3, 11)


def test_arch_is_a_runtime_dependency(pyproject: dict[str, object]) -> None:
    # Stage 18: GARCH(1,1) volatility forecasts (`atlas.quant.risk_model.volatility`) fit with
    # `arch` at runtime (spec §11 numerics), so it is a runtime dependency.
    project = pyproject["project"]
    assert isinstance(project, dict)
    assert _floor(project["dependencies"], "arch") >= (8, 0)
