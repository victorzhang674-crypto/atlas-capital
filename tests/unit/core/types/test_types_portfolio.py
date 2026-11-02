"""Tests for portfolio contracts and the `TargetPortfolio` helpers."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from atlas.core.types.ids import IdPrefix, new_id
from atlas.core.types.portfolio import (
    Breach,
    CapitalAllocation,
    ComplianceResult,
    PodProposal,
    RiskDecision,
    RiskStatus,
    Severity,
    TargetPortfolio,
    View,
    Violation,
)
from atlas.core.types.trading import Order, OrderType, Side

AS_OF = date(2024, 1, 2)


def _tp(weights: dict[str, float], hedges: dict[str, float] | None = None) -> TargetPortfolio:
    return TargetPortfolio(as_of=AS_OF, weights=weights, hedges=hedges or {})


def test_gross_net_legs_known_answer() -> None:
    tp = _tp({"A": 0.05, "B": -0.03}, {"SPY": -0.02})
    assert tp.gross() == pytest.approx(0.10)
    assert tp.net() == pytest.approx(0.0)
    assert tp.long_leg() == {"A": 0.05}
    assert tp.short_leg() == {"B": -0.03, "SPY": -0.02}
    assert tp.book() == {"A": 0.05, "B": -0.03, "SPY": -0.02}


def test_hedges_merge_with_weights_on_same_symbol() -> None:
    tp = _tp({"SPY": 0.03, "A": 0.02}, {"spy": -0.05})
    assert tp.book() == pytest.approx({"SPY": -0.02, "A": 0.02})
    assert tp.gross() == pytest.approx(0.04)
    assert tp.short_leg() == pytest.approx({"SPY": -0.02})


def test_zero_weight_is_in_neither_leg() -> None:
    tp = _tp({"A": 0.0, "B": 0.01})
    assert tp.long_leg() == {"B": 0.01}
    assert tp.short_leg() == {}


def test_diff_known_answer() -> None:
    new = _tp({"A": 0.05, "B": -0.02})
    old = _tp({"A": 0.03, "C": 0.04, "B": -0.02})
    assert new.diff(old) == pytest.approx({"A": 0.02, "C": -0.04})
    assert new.diff(new) == {}


_weights = st.dictionaries(
    st.sampled_from([f"S{i}" for i in range(30)]),
    st.floats(min_value=-0.2, max_value=0.2, allow_nan=False, allow_infinity=False),
    max_size=20,
)


@settings(derandomize=True, max_examples=300)
@given(_weights, _weights, _weights)
def test_gross_ge_abs_net(w: dict[str, float], h: dict[str, float], o: dict[str, float]) -> None:
    tp, other = _tp(w, h), _tp(o)
    assert tp.gross() >= abs(tp.net()) - 1e-12
    assert tp.gross() == pytest.approx(sum(tp.long_leg().values()) - sum(tp.short_leg().values()))
    assert tp.net() == pytest.approx(sum(tp.book().values()))
    diff = tp.diff(other)
    for symbol in set(tp.book()) | set(other.book()):
        rebuilt = other.book().get(symbol, 0.0) + diff.get(symbol, 0.0)
        assert rebuilt == pytest.approx(tp.book().get(symbol, 0.0), abs=1e-12)
    assert 0.0 not in diff.values()


def _proposal(target_weights: dict[str, float]) -> PodProposal:
    return PodProposal(
        id=new_id(IdPrefix.PROPOSAL),
        pod="statarb",
        as_of=AS_OF,
        target_weights=target_weights,
        rationale="pairs",
    )


_WEIGHT_FIELDS = {
    "target.weights": lambda w: _tp(w),
    "target.hedges": lambda w: _tp({}, w),
    "proposal.target_weights": _proposal,
    "risk.adjusted_weights": lambda w: RiskDecision(status=RiskStatus.RESIZE, adjusted_weights=w),
}


@pytest.mark.parametrize("field", sorted(_WEIGHT_FIELDS))
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_weights_reject_nan_and_inf(field: str, bad: float) -> None:
    with pytest.raises(ValidationError):
        _WEIGHT_FIELDS[field]({"AAPL": 0.01, "MSFT": bad})


@pytest.mark.parametrize("field", sorted(_WEIGHT_FIELDS))
def test_weight_maps_are_frozen(field: str) -> None:
    model = _WEIGHT_FIELDS[field]({"AAPL": 0.01})
    mapping = next(v for v in model.__dict__.values() if hasattr(v, "keys") and "AAPL" in v)
    with pytest.raises(TypeError):
        mapping["AAPL"] = 1.0


def test_target_portfolio_optional_fields() -> None:
    tp = TargetPortfolio(as_of=AS_OF, weights={}, expected_vol=0.1, expected_beta=-0.05)
    assert tp.gross() == 0.0
    assert tp.hedges == {}
    with pytest.raises(ValidationError):
        TargetPortfolio(as_of=AS_OF, weights={}, expected_vol=-0.1)


def test_view_and_proposal() -> None:
    view = View(ticker="msft", exp_return=0.15, confidence=0.6, horizon_days=63)
    assert view.ticker == "MSFT"
    proposal = PodProposal(
        id=new_id(IdPrefix.PROPOSAL),
        pod="fundamental_ls",
        as_of=AS_OF,
        views=(view,),
        rationale="r",
    )
    assert proposal.target_weights is None
    with pytest.raises(ValidationError):
        View(ticker="MSFT", exp_return=0.1, confidence=1.2, horizon_days=63)
    with pytest.raises(ValidationError):
        PodProposal(id=new_id(IdPrefix.THESIS), pod="x", as_of=AS_OF, rationale="r")


def _alloc(**kw: Any) -> CapitalAllocation:
    base: dict[str, Any] = {
        "as_of": AS_OF,
        "pod_weights": {"a": 0.6, "b": 0.4},
        "risk_budgets": {"a": 0.7, "b": 0.3},
        "rationale": "risk parity",
    }
    return CapitalAllocation(**{**base, **kw})


def test_capital_allocation_sums_to_one() -> None:
    assert _alloc().pod_weights == {"a": 0.6, "b": 0.4}
    _alloc(pod_weights={"a": 0.1, "b": 0.2, "c": 0.7}, risk_budgets={"a": 1.0, "b": 0, "c": 0})
    with pytest.raises(ValidationError, match="sum"):
        _alloc(pod_weights={"a": 0.6, "b": 0.399})
    with pytest.raises(ValidationError, match="sum"):
        _alloc(risk_budgets={"a": 0.7, "b": 0.31})
    with pytest.raises(ValidationError, match="pods"):
        _alloc(risk_budgets={"a": 0.7, "c": 0.3})
    with pytest.raises(ValidationError):
        _alloc(pod_weights={"a": 1.2, "b": -0.2})


def test_approve_with_breach_rejected() -> None:
    breach = Breach(limit="max_gross", value=2.1, threshold=2.0)
    resize = RiskDecision(
        status=RiskStatus.RESIZE, adjusted_weights={"A": 0.01}, breaches=(breach,)
    )
    assert resize.breaches[0].detail == ""
    assert RiskDecision(status=RiskStatus.APPROVE, adjusted_weights={}).metrics == {}
    with pytest.raises(ValidationError, match="APPROVE"):
        RiskDecision(status=RiskStatus.APPROVE, adjusted_weights={}, breaches=(breach,))
    with pytest.raises(ValidationError):
        RiskDecision(status=RiskStatus.REJECT, adjusted_weights={}, metrics={"var": float("nan")})


def _order() -> Order:
    return Order(
        id=new_id(IdPrefix.ORDER),
        symbol="AAPL",
        side=Side.BUY,
        quantity=Decimal("10"),
        order_type=OrderType.MARKET,
        pods=("systematic",),
        as_of=AS_OF,
        created_at=datetime(2024, 1, 2, 21, tzinfo=UTC),
    )


def test_compliance_result_severity_buckets_and_passed() -> None:
    block = Violation(rule="restricted_list", severity=Severity.BLOCK, message="m", symbol="tsla")
    warn = Violation(rule="wash_sale", severity=Severity.WARN, message="m")
    ok = ComplianceResult(approved_orders=(_order(),), warnings=(warn,))
    assert ok.passed
    assert not ComplianceResult(blocked=(block,)).passed
    assert block.symbol == "TSLA"
    with pytest.raises(ValidationError, match="BLOCK"):
        ComplianceResult(blocked=(warn,))
    with pytest.raises(ValidationError, match="WARN"):
        ComplianceResult(warnings=(block,))
    with pytest.raises(ValidationError):
        Violation(rule="r", severity=Severity.BLOCK, message="m", order_id=new_id(IdPrefix.FILL))


def test_pod_and_metric_names_non_empty_and_unique() -> None:  # Phase 0 checkpoint, Tier 2
    alloc = _alloc(pod_weights={" a ": 0.6, "b": 0.4})
    assert alloc.pod_weights == {"a": 0.6, "b": 0.4}
    empty = {"": 0.6, "b": 0.4}
    with pytest.raises(ValidationError, match="at least 1 character"):
        _alloc(pod_weights=empty, risk_budgets=empty)
    twice = {"a": 0.6, "A": 0.4}
    with pytest.raises(ValidationError, match="same entry"):
        _alloc(pod_weights=twice, risk_budgets=twice)
    with pytest.raises(ValidationError, match="at least 1 character"):
        RiskDecision(status=RiskStatus.RESIZE, adjusted_weights={}, metrics={"": 1.0})
