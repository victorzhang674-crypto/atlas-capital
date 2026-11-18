"""Fixtures for the Stage 29 research-agent tests (the world lives in `fundamental_world`)."""

from pathlib import Path

import pytest
from fundamental_world import FundamentalWorld, build_fundamental_world


@pytest.fixture(scope="module")
def fworld(tmp_path_factory: pytest.TempPathFactory) -> FundamentalWorld:
    """The Stage 29 world (clean and dirty lakes), built once per test module."""
    root: Path = tmp_path_factory.mktemp("fundamental-world")
    return build_fundamental_world(root)
