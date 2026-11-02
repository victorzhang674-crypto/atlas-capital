"""Shared pytest fixtures for the Atlas test suite."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from atlas.core.clock import SimClock, use_clock
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from tiny_universe import TinyUniverse, build_tiny_universe


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


@pytest.fixture
def tiny_universe(tmp_lake: Lake) -> TinyUniverse:
    """5 tickers (AAA, BBB, CCC, DDD, SPY) x 300 sessions to 2024-02-29 with known betas to SPY
    (0.5, 1.0, 1.3, 1.8) and daily vols, in `tmp_lake`; `.view()` reads it at 2024-03-01."""
    return build_tiny_universe(tmp_lake)
