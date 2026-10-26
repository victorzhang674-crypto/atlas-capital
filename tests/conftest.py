"""Shared pytest fixtures for the Atlas test suite."""

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path of the repository root."""
    return Path(__file__).resolve().parents[1]
