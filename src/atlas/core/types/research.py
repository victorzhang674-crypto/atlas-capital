"""Research contracts: analyst reports, macro outlooks, debate turns and investment theses.

Probabilities, confidences, convictions and strengths are in [0, 1]. Returns are simple
returns over the stated horizon; horizons are in trading days.
"""

from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BeforeValidator, Field, model_validator

from atlas.core.types.base import (
    AtlasModel,
    NameMap,
    NonEmptyStr,
    Real,
    UnitInterval,
    UtcDatetime,
    reject_bool,
)
from atlas.core.types.ids import ThesisId
from atlas.core.types.instruments import Symbol

HorizonDays = Annotated[int, BeforeValidator(reject_bool), Field(ge=1)]
"""A forecast or holding horizon in trading days (at least 1)."""


class Direction(StrEnum):
    """Which way a view points."""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"

    @property
    def sign(self) -> int:
        """+1 for LONG, -1 for SHORT, 0 for FLAT."""
        return {"LONG": 1, "SHORT": -1, "FLAT": 0}[self.value]


class Signal(AtlasModel):
    """One named signal inside an analyst report (for example `valuation`); `strength` in
    [0, 1]; `evidence_ids` cite `Evidence.id`s."""

    name: NonEmptyStr
    direction: Direction
    strength: UnitInterval
    evidence_ids: tuple[NonEmptyStr, ...] = ()


class Evidence(AtlasModel):
    """A citable piece of source material. `id` is a stable key from the source (for example
    `news:<article_id>`), not a minted id; `as_of` is when it became public (UTC)."""

    id: NonEmptyStr
    source: NonEmptyStr
    as_of: UtcDatetime
    text: NonEmptyStr
    url: str | None = None


class AnalystReport(AtlasModel):
    """One analyst's view of `ticker` on trading day `as_of`; `confidence` in [0, 1]."""

    ticker: Symbol
    as_of: date
    analyst: NonEmptyStr
    summary: NonEmptyStr
    signals: tuple[Signal, ...]
    risks: tuple[NonEmptyStr, ...]
    confidence: UnitInterval


class AssetView(AtlasModel):
    """The macro economist's view on one ETF; `conviction` in [0, 1]; horizon in trading
    days."""

    etf: Symbol
    direction: Direction
    conviction: UnitInterval
    horizon_days: HorizonDays


class MacroOutlook(AtlasModel):
    """The macro briefing for trading day `as_of` (fields per Stage 33's brief).

    `growth`, `inflation`, `policy` and `credit` are short narrative assessments;
    `sector_tilts` maps a sector (unique ignoring case) to a tilt in [-1, 1];
    `risk_appetite` is in [0, 1];
    `evidence` cites `Evidence.id`s.
    """

    as_of: date
    regime_summary: NonEmptyStr
    growth: NonEmptyStr
    inflation: NonEmptyStr
    policy: NonEmptyStr
    credit: NonEmptyStr
    risks: tuple[NonEmptyStr, ...]
    asset_views: tuple[AssetView, ...]
    sector_tilts: NameMap[Annotated[Real, Field(ge=-1, le=1)]]
    risk_appetite: UnitInterval
    evidence: tuple[NonEmptyStr, ...]


class DebateRole(StrEnum):
    """Who speaks in a research debate."""

    BULL = "BULL"
    BEAR = "BEAR"
    RED_TEAM = "RED_TEAM"


class Claim(AtlasModel):
    """One argued point in a debate turn, citing evidence; `strength` in [0, 1]."""

    text: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...]
    strength: UnitInterval


class DebateTurn(AtlasModel):
    """One speaker's turn in the bull / bear / red-team debate (fields per Stage 35)."""

    speaker: DebateRole
    claims: tuple[Claim, ...]
    concessions: tuple[NonEmptyStr, ...] = ()
    questions_for_other_side: tuple[NonEmptyStr, ...] = ()


class ReturnDistribution(AtlasModel):
    """Quantiles of the simple return over a horizon (0.05 = +5%); each >= -1 and ordered
    `p10 <= p50 <= p90`."""

    p10: Annotated[Real, Field(ge=-1)]
    p50: Annotated[Real, Field(ge=-1)]
    p90: Annotated[Real, Field(ge=-1)]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.p10 <= self.p50 <= self.p90:
            raise ValueError(
                f"quantiles must satisfy p10 <= p50 <= p90, got {self.p10}, {self.p50}, {self.p90}"
            )
        return self


class Catalyst(AtlasModel):
    """An event expected to move the stock, with its expected date if known."""

    description: NonEmptyStr
    expected_date: date | None = None


class ThesisStatus(StrEnum):
    """Lifecycle state of an investment thesis (transitions are Stage 36's)."""

    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class InvestmentThesis(AtlasModel):
    """The Research Director's structured thesis on `ticker` (spec §7).

    `exp_return` is the distribution of the simple return over `horizon_days` trading days;
    `conviction` is in [0, 1]; `kill_criteria` are checkable conditions in Stage 36's DSL (for
    example `price_below(300)`); `evidence` holds cited `Evidence.id`s.
    """

    id: ThesisId
    ticker: Symbol
    as_of: date
    direction: Direction
    exp_return: ReturnDistribution
    horizon_days: HorizonDays
    conviction: UnitInterval
    catalysts: tuple[Catalyst, ...] = ()
    kill_criteria: tuple[NonEmptyStr, ...]
    evidence: tuple[NonEmptyStr, ...]
    status: ThesisStatus = ThesisStatus.ACTIVE
