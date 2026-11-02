"""Trading contracts: orders, fills and positions.

Quantities are in shares and prices are per share in the base currency, all as exact
`Decimal`s. Order lifecycle transitions are enforced by the OMS (Stage 50); these records only
check that their own fields agree.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from atlas.core.types.base import AtlasModel, NonEmptyStr, UtcDatetime
from atlas.core.types.ids import FillId, OrderId
from atlas.core.types.instruments import Symbol

_PositiveDecimal = Annotated[Decimal, Field(gt=0)]
_NonNegDecimal = Annotated[Decimal, Field(ge=0)]


class Side(StrEnum):
    """Order side. Selling more than is held opens a short."""

    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        """+1 for BUY, -1 for SELL."""
        return 1 if self is Side.BUY else -1


class OrderType(StrEnum):
    """How an order is priced: at market, at a limit, or market-on-open."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    MOO = "MOO"


class TimeInForce(StrEnum):
    """How long an order stays working: the day, until cancelled, immediate-or-cancel, or the
    opening auction (OPG)."""

    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"
    OPG = "OPG"


class OrderStatus(StrEnum):
    """Order lifecycle states (Stage 50 owns the allowed transitions)."""

    NEW = "NEW"
    ACCEPTED = "ACCEPTED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        """True once the order can no longer change (filled, cancelled, rejected, expired)."""
        return self in _TERMINAL


_TERMINAL = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
)
_NO_FILLS = frozenset(
    {OrderStatus.NEW, OrderStatus.ACCEPTED, OrderStatus.SUBMITTED, OrderStatus.REJECTED}
)


class Order(AtlasModel):
    """An order for `quantity` shares (always positive; `side` gives the sign).

    `limit_price` (per share) is set exactly when `order_type` is LIMIT; a MOO order uses
    time-in-force OPG. `filled_quantity` (shares) agrees with `status`: zero before any fill
    and when REJECTED, strictly between zero and `quantity` when PARTIALLY_FILLED, equal to it
    when FILLED. `pods` tags the pod(s) the order trades for (no pod twice, ignoring case);
    `as_of` is the decision's trading day, `created_at` the UTC creation time; `parent_id`
    links a child slice to its parent order (never the order itself).
    """

    id: OrderId
    symbol: Symbol
    side: Side
    quantity: _PositiveDecimal
    order_type: OrderType
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: _PositiveDecimal | None = None
    status: OrderStatus = OrderStatus.NEW
    filled_quantity: _NonNegDecimal = Decimal("0")
    pods: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]
    as_of: date
    created_at: UtcDatetime
    parent_id: OrderId | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.order_type is OrderType.LIMIT) != (self.limit_price is not None):
            raise ValueError("limit_price must be set for LIMIT orders and only for them")
        if self.order_type is OrderType.MOO and self.time_in_force is not TimeInForce.OPG:
            raise ValueError("a MOO order must use time_in_force OPG")
        filled, qty = self.filled_quantity, self.quantity
        if filled > qty:
            raise ValueError(f"filled_quantity {filled} exceeds quantity {qty}")
        if self.status in _NO_FILLS and filled != 0:
            raise ValueError(f"a {self.status} order cannot have fills")
        if self.status is OrderStatus.PARTIALLY_FILLED and not 0 < filled < qty:
            raise ValueError("a PARTIALLY_FILLED order needs 0 < filled_quantity < quantity")
        if self.status is OrderStatus.FILLED and filled != qty:
            raise ValueError("a FILLED order needs filled_quantity == quantity")
        if len({pod.casefold() for pod in self.pods}) != len(self.pods):
            raise ValueError(f"duplicate pod in pods {self.pods}")
        if self.parent_id == self.id:
            raise ValueError("an order cannot be its own parent (parent_id == id)")
        return self

    @property
    def remaining_quantity(self) -> Decimal:
        """Shares still to fill."""
        return self.quantity - self.filled_quantity

    @property
    def signed_quantity(self) -> Decimal:
        """Order size in shares with the side's sign (sells negative)."""
        return self.quantity * self.side.sign


class Fill(AtlasModel):
    """One execution of `quantity` shares at `price` per share; `commission` in the base
    currency; `ts` is the UTC execution time and `as_of` its trading day."""

    id: FillId
    order_id: OrderId
    symbol: Symbol
    side: Side
    quantity: _PositiveDecimal
    price: _PositiveDecimal
    commission: _NonNegDecimal = Decimal("0")
    ts: UtcDatetime
    as_of: date

    @property
    def notional(self) -> Decimal:
        """`quantity * price` in the base currency (unsigned, before commission)."""
        return self.quantity * self.price


class Position(AtlasModel):
    """A holding of `quantity` shares (negative for shorts) at `avg_cost` per share, optionally
    tagged with the owning pod."""

    symbol: Symbol
    quantity: Decimal
    avg_cost: _NonNegDecimal
    pod: NonEmptyStr | None = None

    @property
    def is_long(self) -> bool:
        """True for a positive share count."""
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        """True for a negative share count."""
        return self.quantity < 0

    def market_value(self, price: Decimal) -> Decimal:
        """Signed value at `price` per share (negative for shorts), in the base currency."""
        return self.quantity * price
