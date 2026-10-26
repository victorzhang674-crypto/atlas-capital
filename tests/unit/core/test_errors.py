"""Tests for the Atlas exception hierarchy."""

from itertools import permutations

import pytest

from atlas.core.errors import (
    AtlasError,
    ComplianceError,
    ConfigError,
    DataError,
    LLMError,
    LookAheadError,
    RiskLimitError,
)

SUBCLASSES = [ConfigError, LookAheadError, DataError, LLMError, RiskLimitError, ComplianceError]


@pytest.mark.parametrize("cls", SUBCLASSES, ids=lambda c: c.__name__)
def test_every_error_is_an_atlas_error(cls: type[AtlasError]) -> None:
    assert issubclass(cls, AtlasError)
    assert issubclass(AtlasError, Exception)
    with pytest.raises(AtlasError):
        raise cls("msg")


def test_errors_are_distinct_siblings() -> None:
    # A handler for one kind (e.g. bad data) must never swallow another (e.g. look-ahead).
    for a, b in permutations(SUBCLASSES, 2):
        assert not issubclass(a, b), f"{a.__name__} must not subclass {b.__name__}"


@pytest.mark.parametrize("cls", [AtlasError, *SUBCLASSES], ids=lambda c: c.__name__)
def test_errors_have_docstrings(cls: type[AtlasError]) -> None:
    assert cls.__doc__ and cls.__doc__.strip()


def test_error_keeps_message() -> None:
    assert str(RiskLimitError("gross 2.1 > 2.0")) == "gross 2.1 > 2.0"
