"""Model base and field types shared by every contract in `atlas.core.types`.

Units used across the package: weights are fractions of NAV (0.05 = 5%, shorts negative);
returns are simple, not log; probabilities and confidences are in [0, 1]; timestamps are
tz-aware UTC `datetime`s and trading days are `date`s.
"""

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    WrapSerializer,
)


class AtlasModel(BaseModel):
    """Base of every Atlas contract: immutable, rejects unknown keys and NaN/infinite numbers.

    Sequence fields are tuples and mapping fields are `FrozenDict`s, so a shared contract can
    never be changed in place. `model_copy(update=...)` re-validates, so a changed copy obeys
    the same rules as a new instance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """A copy of this contract; with `update`, the changed fields are validated like a
        new instance (pydantic's default copy would skip validation)."""
        if not update:
            return super().model_copy(deep=deep)
        return self.model_validate({**dict(self), **update})


class FrozenDict[K, V](Mapping[K, V]):
    """Read-only mapping (unlike `MappingProxyType` it pickles and deep-copies)."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[K, V]) -> None:
        self._data = dict(data)

    def __getitem__(self, key: K) -> V:
        return self._data[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._data!r})"

    def __hash__(self) -> int:
        return hash(frozenset(self._data.items()))

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self._data,))


def freeze[K, V](value: Mapping[K, V]) -> FrozenDict[K, V]:
    """Validator step: store a validated mapping as a `FrozenDict`."""
    return FrozenDict(value)


def thaw(value: Mapping[Any, Any], handler: SerializerFunctionWrapHandler) -> Any:
    """Serializer step: dump a `FrozenDict` as a plain dict."""
    return handler(dict(value))


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def reject_bool(value: object) -> object:
    """Validator step: refuse `True`/`False` where a number is expected (pydantic would
    otherwise read them as 1 and 0)."""
    if isinstance(value, bool):
        raise ValueError("a bool is not a number")
    return value


type FrozenMap[K, V] = Annotated[Mapping[K, V], AfterValidator(freeze), WrapSerializer(thaw)]
"""A read-only mapping field: validated item by item, stored as a `FrozenDict`, dumped as a
plain dict."""

Real = Annotated[float, BeforeValidator(reject_bool)]
"""A finite float that refuses `True`/`False`."""

UnitInterval = Annotated[float, BeforeValidator(reject_bool), Field(ge=0, le=1)]
"""A probability, confidence or strength in [0, 1]."""

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
"""Text with surrounding whitespace stripped; must not be empty afterwards."""

UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
"""A tz-aware timestamp, converted to UTC; naive datetimes are rejected."""
