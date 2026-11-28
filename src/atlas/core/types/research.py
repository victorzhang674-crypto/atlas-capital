"""Research contracts: analyst reports, macro outlooks, debate turns and investment theses.

Probabilities, confidences, convictions and strengths are in [0, 1]. Returns are simple
returns over the stated horizon; horizons are in trading days.
"""

import datetime as dt
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


class Timing(StrEnum):
    """When to act on a technical signal's direction (Stage 30): enter `NOW`, `WAIT_FOR_PULLBACK`
    to a better level, or `AVOID` entering (the only timing of a FLAT signal)."""

    NOW = "NOW"
    WAIT_FOR_PULLBACK = "WAIT_FOR_PULLBACK"
    AVOID = "AVOID"


StopFraction = Annotated[Real, Field(gt=0, lt=1)]
"""A stop distance as a fraction of the last close, strictly between 0 and 1 (0.08 = 8%)."""


class TechnicalSignal(Signal):
    """A signal of the technical analyst (Stage 30): a `Signal` plus its `timing` and
    `suggested_stop_pct`, the stop distance as a fraction of the last close (0.08 = a stop 8%
    below it for a LONG view, 8% above it for a SHORT view; `None` for a FLAT view)."""

    timing: Timing
    suggested_stop_pct: StopFraction | None = None


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


class TechnicalReport(AnalystReport):
    """The technical analyst's report (Stage 30): an `AnalystReport` whose signals are
    `TechnicalSignal`s, so every consumer of an `AnalystReport` takes it."""

    signals: tuple[TechnicalSignal, ...]


class Impact(StrEnum):
    """The direction of a catalyst's expected effect on the stock's price (Stage 31):
    `POSITIVE`, `NEGATIVE`, or `MIXED` (could go either way, or both)."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"


class NewsCatalyst(AtlasModel):
    """An event or story expected to move the stock, as the news analyst reports it
    (Stage 31): its expected `date` if known (a day on or after the report's `as_of`), a
    `description`, the one `evidence_id` it rests on (an `Evidence.id` the run showed) and the
    `expected_impact` on the price. Distinct from the thesis's `Catalyst` (Stage 36's shape)."""

    date: dt.date | None = None
    description: NonEmptyStr
    evidence_id: NonEmptyStr
    expected_impact: Impact


class NewsReport(AnalystReport):
    """The news analyst's report (Stage 31): an `AnalystReport` plus its `catalysts`, so every
    consumer of an `AnalystReport` takes it."""

    catalysts: tuple[NewsCatalyst, ...] = ()


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

    `exp_return` is the distribution of the simple return over `horizon_days` trading days,
    not annualised (unlike `View.exp_return`, which is); `conviction` is in [0, 1];
    `kill_criteria` are checkable conditions in Stage 36's DSL (for example
    `price_below(300)`); `evidence` holds cited `Evidence.id`s.
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
