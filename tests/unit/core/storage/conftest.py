"""Fixtures shared by the storage tests."""

import pytest
from storage_helpers import PricesFrame, make_prices_frame, make_prices_schema

from atlas.core.storage import Lake, TableSchema


@pytest.fixture
def prices_schema() -> TableSchema:
    """The `prices_daily` test schema (partitioned by symbol)."""
    return make_prices_schema()


@pytest.fixture
def prices_frame() -> PricesFrame:
    """Factory: `prices_frame(symbols=..., days=..., close=...)` → a valid `prices_daily` batch."""
    return make_prices_frame


@pytest.fixture
def lake(tmp_lake: Lake, prices_schema: TableSchema) -> Lake:
    """`tmp_lake` with the `prices_daily` test schema registered."""
    tmp_lake.registry.register(prices_schema)
    return tmp_lake
