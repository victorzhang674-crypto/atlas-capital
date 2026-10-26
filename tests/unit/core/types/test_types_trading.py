"""Tests for orders, fills and positions."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from atlas.core.types.ids import IdPrefix, new_id
from atlas.core.types.trading import (
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    TimeInForce,
)

TS = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)


def _order(**kw: Any) -> Order:
    base: dict[str, Any] = {
        "id": new_id(IdPrefix.ORDER),
        "symbol": "aapl",
        "side": Side.BUY,
        "quantity": Decimal("100"),
        "order_type": OrderType.MARKET,
        "pods": ("systematic",),
        "as_of": date(2024, 1, 2),
        "created_at": TS,
    }
    return Order(**{**base, **kw})


def _fill(**kw: Any) -> Fill:
    base: dict[str, Any] = {
        "id": new_id(IdPrefix.FILL),
        "order_id": new_id(IdPrefix.ORDER),
        "symbol": "AAPL",
        "side": Side.SELL,
        "quantity": Decimal("40"),
        "price": Decimal("185.25"),
        "commission": Decimal("0.40"),
        "ts": TS,
        "as_of": date(2024, 1, 3),
    }
    return Fill(**{**base, **kw})


def test_enum_values() -> None:
    assert [s.value for s in Side] == ["BUY", "SELL"]
    assert [t.value for t in OrderType] == ["MARKET", "LIMIT", "MOO"]
    assert [t.value for t in TimeInForce] == ["DAY", "GTC", "IOC", "OPG"]
    assert (Side.BUY.sign, Side.SELL.sign) == (1, -1)


def test_order_status_terminal_set() -> None:
    assert [s.value for s in OrderStatus] == [
        "NEW",
        "ACCEPTED",
        "SUBMITTED",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELED",
        "REJECTED",
        "EXPIRED",
    ]
    assert {s for s in OrderStatus if s.is_terminal} == {
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }


def test_order_defaults_and_helpers() -> None:
    order = _order(side=Side.SELL)
    assert order.symbol == "AAPL"
    assert order.status is OrderStatus.NEW
    assert order.time_in_force is TimeInForce.DAY
    assert order.filled_quantity == Decimal("0")
    assert order.remaining_quantity == Decimal("100")
    assert order.signed_quantity == Decimal("-100")


def test_limit_price_rules() -> None:
    limit = _order(order_type=OrderType.LIMIT, limit_price=Decimal("180.5"))
    assert limit.limit_price == Decimal("180.5")
    with pytest.raises(ValidationError, match="limit_price"):
        _order(order_type=OrderType.LIMIT)
    with pytest.raises(ValidationError, match="limit_price"):
        _order(limit_price=Decimal("180"))
    with pytest.raises(ValidationError):
        _order(order_type=OrderType.LIMIT, limit_price=Decimal("0"))


def test_moo_requires_opg() -> None:
    assert _order(order_type=OrderType.MOO, time_in_force=TimeInForce.OPG).order_type is (
        OrderType.MOO
    )
    with pytest.raises(ValidationError, match="OPG"):
        _order(order_type=OrderType.MOO)


@pytest.mark.parametrize(
    ("status", "filled", "ok"),
    [
        (OrderStatus.NEW, "0", True),
        (OrderStatus.NEW, "1", False),
        (OrderStatus.ACCEPTED, "1", False),
        (OrderStatus.SUBMITTED, "1", False),
        (OrderStatus.PARTIALLY_FILLED, "0", False),
        (OrderStatus.PARTIALLY_FILLED, "40", True),
        (OrderStatus.PARTIALLY_FILLED, "100", False),
        (OrderStatus.FILLED, "100", True),
        (OrderStatus.FILLED, "99", False),
        (OrderStatus.CANCELED, "40", True),
        (OrderStatus.EXPIRED, "0", True),
        (OrderStatus.CANCELED, "101", False),
        (OrderStatus.REJECTED, "-1", False),
    ],
)
def test_fill_quantity_consistency(status: OrderStatus, filled: str, ok: bool) -> None:
    if ok:
        assert _order(status=status, filled_quantity=Decimal(filled)).status is status
    else:
        with pytest.raises(ValidationError):
            _order(status=status, filled_quantity=Decimal(filled))


def test_order_requires_pod() -> None:
    with pytest.raises(ValidationError):
        _order(pods=())
    with pytest.raises(ValidationError):
        _order(pods=(" ",))


def test_order_rejects_bad_quantity_and_naive_time() -> None:
    with pytest.raises(ValidationError):
        _order(quantity=Decimal("0"))
    with pytest.raises(ValidationError):
        _order(created_at=datetime(2024, 1, 2, 21, 0))
    with pytest.raises(ValidationError):
        _order(parent_id=new_id(IdPrefix.FILL))


def test_quantities_are_decimal_exact() -> None:
    order = _order(quantity="0.1")
    assert order.quantity == Decimal("0.1")
    reloaded = Order.model_validate_json(order.model_dump_json())
    assert reloaded.quantity == Decimal("0.1")
    assert isinstance(reloaded.quantity, Decimal)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_decimal_fields_reject_nan(bad: str) -> None:
    with pytest.raises(ValidationError):
        _order(quantity=Decimal(bad))
    with pytest.raises(ValidationError):
        _fill(price=Decimal(bad))
    with pytest.raises(ValidationError):
        Position(symbol="AAPL", quantity=Decimal(bad), avg_cost=Decimal("1"))


def test_fill_notional() -> None:
    assert _fill().notional == Decimal("7410.00")
    with pytest.raises(ValidationError):
        _fill(commission=Decimal("-0.01"))


def test_position_market_value_short() -> None:
    short = Position(symbol="tsla", quantity=Decimal("-10"), avg_cost=Decimal("250"))
    assert short.symbol == "TSLA"
    assert short.is_short and not short.is_long
    assert short.market_value(Decimal("240.5")) == Decimal("-2405.0")
    flat = Position(symbol="TSLA", quantity=Decimal("0"), avg_cost=Decimal("0"))
    assert not flat.is_long and not flat.is_short
    with pytest.raises(ValidationError):
        Position(symbol="TSLA", quantity=Decimal("1"), avg_cost=Decimal("-1"))
