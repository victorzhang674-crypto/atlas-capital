"""Regression tests for the Stage 04 whole-branch review findings."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from atlas.core.types import (
    CapitalAllocation,
    Direction,
    FrozenDict,
    RiskDecision,
    RiskStatus,
    Signal,
    TargetPortfolio,
    View,
)

AS_OF = date(2024, 1, 2)


def test_model_copy_update_revalidates() -> None:
    tp = TargetPortfolio(as_of=AS_OF, weights={"AAPL": 0.01})
    copy = tp.model_copy(update={"weights": {"brk-b": 0.1}})
    assert copy.weights == {"BRK.B": 0.1}
    assert isinstance(copy.weights, FrozenDict)
    with pytest.raises(ValidationError):
        tp.model_copy(update={"weights": {"AAPL": float("nan")}})
    with pytest.raises(ValidationError):
        tp.model_copy(update={"unknown_field": 1})
    decision = RiskDecision(status=RiskStatus.RESIZE, adjusted_weights={})
    with pytest.raises(ValidationError):
        decision.model_copy(update={"status": "MAYBE"})


def test_model_copy_without_update_is_equal() -> None:
    tp = TargetPortfolio(as_of=AS_OF, weights={"AAPL": 0.01})
    assert tp.model_copy() == tp
    assert tp.model_copy(deep=True) == tp


def test_models_with_maps_are_hashable() -> None:
    a = TargetPortfolio(as_of=AS_OF, weights={"AAPL": 0.01, "MSFT": -0.02})
    b = TargetPortfolio(as_of=AS_OF, weights={"MSFT": -0.02, "AAPL": 0.01})
    assert hash(a) == hash(b)
    assert len({a, b}) == 1
    alloc = CapitalAllocation(
        as_of=AS_OF, pod_weights={"x": 1.0}, risk_budgets={"x": 1.0}, rationale="r"
    )
    assert isinstance(hash(alloc), int)
    assert hash(FrozenDict({"a": 1})) == hash(FrozenDict({"a": 1}))


_BOOL_CASES: dict[str, Any] = {
    "unit_interval": lambda: Signal(name="s", direction=Direction.LONG, strength=True),
    "horizon_days": lambda: View(ticker="A", exp_return=0.1, confidence=0.5, horizon_days=True),
    "view_return": lambda: View(ticker="A", exp_return=True, confidence=0.5, horizon_days=5),
    "weights": lambda: TargetPortfolio(as_of=AS_OF, weights={"A": True}),
}


@pytest.mark.parametrize("case", sorted(_BOOL_CASES))
def test_bool_is_not_a_number(case: str) -> None:
    with pytest.raises(ValidationError, match="bool"):
        _BOOL_CASES[case]()
