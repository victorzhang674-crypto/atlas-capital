"""Performance and books-and-records contracts: predictions, lessons, NAV and ledger entries.

Money is `Decimal` in the base currency; exposures are fractions of NAV; returns are simple;
horizons are in trading days; timestamps are UTC.
"""

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from atlas.core.types.base import AtlasModel, NonEmptyStr, UnitInterval, UtcDatetime
from atlas.core.types.ids import LedgerId, LessonId, PredictionId
from atlas.core.types.instruments import Symbol
from atlas.core.types.research import Direction, HorizonDays, ReturnDistribution

_PositiveDecimal = Annotated[Decimal, Field(gt=0)]


class Forecast(AtlasModel):
    """What a prediction says: the `direction`, the probability in [0, 1] that the direction
    is right (`confidence`), and the distribution of the simple return over the horizon."""

    direction: Direction
    confidence: UnitInterval
    returns: ReturnDistribution


class Prediction(AtlasModel):
    """A scored forecast about `target` over `horizon_days` trading days (spec §7, §10).

    It is open until settled: `settled_at` (UTC, after `made_at`) and `outcome` (the realised
    simple return over the horizon) are set together, and `score` only once settled.
    """

    id: PredictionId
    source_agent: NonEmptyStr
    made_at: UtcDatetime
    target: Symbol
    horizon_days: HorizonDays
    forecast: Forecast
    settled_at: UtcDatetime | None = None
    outcome: float | None = None
    score: float | None = None

    @model_validator(mode="after")
    def _settlement_consistent(self) -> Self:
        if (self.settled_at is None) != (self.outcome is None):
            raise ValueError("settled_at and outcome must be set together (settle both or none)")
        if self.score is not None and self.settled_at is None:
            raise ValueError("score can only be set once the prediction is settled")
        if self.settled_at is not None and self.settled_at <= self.made_at:
            raise ValueError("settled_at must be after made_at")
        return self

    @property
    def is_settled(self) -> bool:
        """True once the outcome is known."""
        return self.settled_at is not None


class Lesson(AtlasModel):
    """A short reflection stored point-in-time (fields per Stage 27): `agent` is the agent it
    applies to or `"global"`; `created_at` (UTC) is when it became known; `quality_score` in
    [0, 1] if rated."""

    id: LessonId
    agent: NonEmptyStr
    created_at: UtcDatetime
    text: NonEmptyStr
    tags: tuple[str, ...] = ()
    source_episode_ids: tuple[str, ...] = ()
    quality_score: UnitInterval | None = None


class NAVRecord(AtlasModel):
    """The fund's net asset value at the close of trading day `date`, in the base currency.

    `long_market_value` is >= 0 and `short_market_value` <= 0 (shorts are negative);
    `nav_per_share` is set when investor units are tracked.
    """

    date: dt.date
    nav: _PositiveDecimal
    cash: Decimal
    long_market_value: Annotated[Decimal, Field(ge=0)]
    short_market_value: Annotated[Decimal, Field(le=0)]
    nav_per_share: _PositiveDecimal | None = None

    def gross_exposure(self) -> float:
        """(long - short market value) / NAV, as a fraction of NAV."""
        return float((self.long_market_value - self.short_market_value) / self.nav)

    def net_exposure(self) -> float:
        """(long + short market value) / NAV, as a fraction of NAV."""
        return float((self.long_market_value + self.short_market_value) / self.nav)


class EntrySide(StrEnum):
    """Which side of a double-entry ledger an entry posts to."""

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class LedgerEntry(AtlasModel):
    """One double-entry ledger line posted on trading day `as_of`: a positive `amount` in the
    base currency on `side` of `account`; `ref` points at what caused it (a fill or order id,
    a fee accrual). Balancing and posting rules are Stage 52's."""

    id: LedgerId
    as_of: dt.date
    account: NonEmptyStr
    side: EntrySide
    amount: _PositiveDecimal
    ref: NonEmptyStr
