"""Shared pytest fixtures for the Atlas test suite."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from atlas.core.clock import SimClock, use_clock
from atlas.core.storage import DEFAULT_REGISTRY, Lake


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path of the repository root."""
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def sim_clock() -> Iterator[SimClock]:
    """A `SimClock` at the 2024-03-01 session's decision time, installed with `use_clock`."""
    clock = SimClock(start=date(2024, 3, 1))
    with use_clock(clock):
        yield clock


@pytest.fixture
def tmp_lake(tmp_path: Path) -> Lake:
    """An empty `Lake` under `tmp_path/"lake"`, with its own copy of `DEFAULT_REGISTRY`."""
    return Lake(tmp_path / "lake", registry=DEFAULT_REGISTRY.copy())
