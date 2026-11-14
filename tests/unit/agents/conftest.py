"""Fixtures for the Stage 26 agent tests (helpers live in `agent_world` and `agent_helpers`)."""

from pathlib import Path

import pytest
from agent_world import World, build_agent_world


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    """The Stage 13 test world (clean and dirty lakes), built once per test module."""
    root: Path = tmp_path_factory.mktemp("agents-world")
    return build_agent_world(root)
