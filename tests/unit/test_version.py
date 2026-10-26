"""The package version is defined once and agrees with pyproject.toml."""

import tomllib
from pathlib import Path

import atlas


def test_version() -> None:
    assert atlas.__version__ == "0.1.0"


def test_version_matches_pyproject(repo_root: Path) -> None:
    meta = tomllib.loads((repo_root / "pyproject.toml").read_text())
    assert meta["project"]["version"] == atlas.__version__
