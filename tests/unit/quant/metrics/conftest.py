"""Shared fixtures of the Stage 20 metrics tests."""

import pytest
from metrics_reference import Reference, load_reference


@pytest.fixture(scope="session")
def reference() -> Reference:
    """The empyrical reference series and values."""
    return load_reference()
