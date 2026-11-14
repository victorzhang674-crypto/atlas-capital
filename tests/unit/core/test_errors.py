"""Tests for the Atlas exception hierarchy."""

from itertools import permutations

import pytest

from atlas.core.errors import (
    AtlasError,
    BacktestError,
    BudgetExceeded,
    CacheMiss,
    ComplianceError,
    ConfigError,
    DataError,
    LLMError,
    LLMTransientError,
    LookAheadError,
    RiskLimitError,
)

SUBCLASSES = [
    ConfigError,
    LookAheadError,
    DataError,
    LLMError,
    BacktestError,
    RiskLimitError,
    ComplianceError,
]


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


def test_llm_refinements_are_llm_errors() -> None:  # Stage 24 D2
    # A caller that fails safe on LLMError also holds on a budget stop or an exhausted retry.
    budget = BudgetExceeded("daily budget", scope="daily_usd", limit=5.0, spent=5.01)
    assert isinstance(budget, LLMError)
    assert (budget.scope, budget.limit, budget.spent) == ("daily_usd", 5.0, 5.01)
    assert str(budget) == "daily budget"
    transient = LLMTransientError("429", retry_after=2.5)
    assert isinstance(transient, LLMError)
    assert transient.retry_after == 2.5
    assert LLMTransientError("timeout").retry_after is None
    assert not issubclass(BudgetExceeded, LLMTransientError)
    assert not issubclass(LLMTransientError, BudgetExceeded)
    for cls in (BudgetExceeded, LLMTransientError):
        assert cls.__doc__ and cls.__doc__.strip()


def test_cache_miss_is_an_llm_error_with_its_key() -> None:  # Stage 25 D3
    miss = CacheMiss("no recorded answer", key="abc123")
    assert isinstance(miss, LLMError)  # callers that fail safe on LLMError hold on a replay miss
    assert miss.key == "abc123"
    assert str(miss) == "no recorded answer"
    assert not issubclass(CacheMiss, (BudgetExceeded, LLMTransientError))
    assert CacheMiss.__doc__ and CacheMiss.__doc__.strip()
