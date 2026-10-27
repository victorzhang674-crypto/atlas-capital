"""Fixtures shared by the storage tests."""

from pathlib import Path

import pytest
from storage_helpers import PricesFrame, make_prices_frame, make_prices_schema

from atlas.core.storage import Lake, SchemaRegistry, TableSchema


@pytest.fixture
def prices_schema() -> TableSchema:
    """The `prices_daily` test schema (partitioned by symbol)."""
    return make_prices_schema()


@pytest.fixture
def prices_frame() -> PricesFrame:
    """Factory: `prices_frame(symbols=..., days=..., close=...)` → a valid `prices_daily` batch."""
    return make_prices_frame


@pytest.fixture
def lake(tmp_path: Path, prices_schema: TableSchema) -> Lake:
    """A lake under `tmp_path/"lake"` whose registry holds only the `prices_daily` test schema.

    Not a copy of `DEFAULT_REGISTRY`: data stages register real tables there (Stage 07's
    `prices_daily` has more columns than this test schema), and the storage mechanics tested
    here must not depend on which data tables happen to be imported.
    """
    return Lake(tmp_path / "lake", registry=SchemaRegistry([prices_schema]))
