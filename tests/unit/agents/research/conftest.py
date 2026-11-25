"""Fixtures for the research-agent tests: the Stage 29 world (`fundamental_world`) and the
Stage 30 world (`technical_world`)."""

from pathlib import Path

import pytest
from fundamental_world import FundamentalWorld, build_fundamental_world
from technical_world import TechnicalWorld, build_technical_world


@pytest.fixture(scope="module")
def fworld(tmp_path_factory: pytest.TempPathFactory) -> FundamentalWorld:
    """The Stage 29 world (clean and dirty lakes), built once per test module."""
    root: Path = tmp_path_factory.mktemp("fundamental-world")
    return build_fundamental_world(root)


@pytest.fixture(scope="module")
def tworld(tmp_path_factory: pytest.TempPathFactory) -> TechnicalWorld:
    """The Stage 30 world (clean and dirty lakes), built once per test module."""
    return build_technical_world(tmp_path_factory.mktemp("technical-world"))
