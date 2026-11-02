"""Portfolio decision contracts: views, pod proposals, capital allocation, target portfolios,
risk decisions and compliance results.

Weights are fractions of NAV (0.05 = 5%; shorts negative). Expected returns are simple and
annualised unless a field says otherwise; horizons are in trading days.
"""

import math
from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from atlas.core.types.base import AtlasModel, NameMap, NonEmptyStr, Real, UnitInterval
from atlas.core.types.ids import OrderId, ProposalId
from atlas.core.types.instruments import Symbol, SymbolWeights
from atlas.core.types.research import HorizonDays
from atlas.core.types.trading import Order

_SUM_TOLERANCE = 1e-9
_Fraction = Annotated[float, Field(ge=0, le=1)]


class View(AtlasModel):
    """A pod's expected return on `ticker`: `exp_return` is the annualised simple return,
    `confidence` in [0, 1], over `horizon_days` trading days."""

    ticker: Symbol
    exp_return: Real
    confidence: UnitInterval
    horizon_days: HorizonDays


class PodProposal(AtlasModel):
    """What a pod wants to hold on `as_of`: views for the optimiser and/or direct
    `target_weights` (fractions of NAV; the stat-arb pod sends weights instead of views)."""

    id: ProposalId
    pod: NonEmptyStr
    as_of: date
    views: tuple[View, ...] = ()
    target_weights: SymbolWeights | None = None
    rationale: NonEmptyStr


class CapitalAllocation(AtlasModel):
    """The CIO's split of the fund across pods: `pod_weights` (capital) and `risk_budgets`
    (risk), each a fraction in [0, 1] per pod, each summing to 1, over the same pods (pod
    names unique ignoring case)."""

    as_of: date
    pod_weights: NameMap[_Fraction]
    risk_budgets: NameMap[_Fraction]
    rationale: NonEmptyStr

    @model_validator(mode="after")
    def _budgets_add_up(self) -> Self:
        for name in ("pod_weights", "risk_budgets"):
            total = math.fsum(getattr(self, name).values())
            if abs(total - 1.0) > _SUM_TOLERANCE:
                raise ValueError(f"{name} must sum to 1, got {total}")
        if set(self.pod_weights) != set(self.risk_budgets):
            raise ValueError("pod_weights and risk_budgets must name the same pods")
        return self


class TargetPortfolio(AtlasModel):
    """The book the fund wants to hold on `as_of`, in fractions of NAV.

    `hedges` (index/sector ETF overlays) are kept apart from `weights` but are part of the
    book: every helper works on `weights + hedges`. `expected_vol` is the annualised ex-ante
    volatility (fraction); `expected_beta` the ex-ante beta to the benchmark.
    """

    as_of: date
    weights: SymbolWeights
    hedges: SymbolWeights = Field(default_factory=dict, validate_default=True)
    expected_vol: Annotated[float, Field(ge=0)] | None = None
    expected_beta: float | None = None

    def book(self) -> dict[str, float]:
        """Total weight per symbol: `weights` plus `hedges` (fractions of NAV)."""
        total = dict(self.weights)
        for symbol, weight in self.hedges.items():
            total[symbol] = total.get(symbol, 0.0) + weight
        return total

    def gross(self) -> float:
        """Gross exposure Σ|w| of the whole book, as a fraction of NAV."""
        return math.fsum(abs(w) for w in self.book().values())

    def net(self) -> float:
        """Net exposure Σw of the whole book, as a fraction of NAV."""
        return math.fsum(self.book().values())

    def long_leg(self) -> dict[str, float]:
        """Symbols with a positive total weight."""
        return {s: w for s, w in self.book().items() if w > 0}

    def short_leg(self) -> dict[str, float]:
        """Symbols with a negative total weight (values stay negative)."""
        return {s: w for s, w in self.book().items() if w < 0}

    def diff(self, other: "TargetPortfolio") -> dict[str, float]:
        """Weight change per symbol to go from `other` to this book (`self - other`), over
        both books' symbols; unchanged symbols are left out."""
        mine, theirs = self.book(), other.book()
        changes = {s: mine.get(s, 0.0) - theirs.get(s, 0.0) for s in mine.keys() | theirs.keys()}
        return {s: d for s, d in changes.items() if d != 0.0}


class RiskStatus(StrEnum):
    """The risk engine's verdict on a target portfolio."""

    APPROVE = "APPROVE"
    RESIZE = "RESIZE"
    REJECT = "REJECT"


class Breach(AtlasModel):
    """One hard limit the proposal breaks: the measured `value` against its `threshold`, in
    the limit's own units (see `LimitsConfig`)."""

    limit: NonEmptyStr
    value: float
    threshold: float
    detail: str = ""


class RiskDecision(AtlasModel):
    """The risk engine's answer: `adjusted_weights` (fractions of NAV) is the book it allows;
    `breaches` lists the limits that forced a resize or rejection (an APPROVE has none);
    `metrics` holds the computed risk numbers by name."""

    status: RiskStatus
    adjusted_weights: SymbolWeights
    breaches: tuple[Breach, ...] = ()
    metrics: NameMap[float] = Field(default_factory=dict, validate_default=True)

    @model_validator(mode="after")
    def _approve_has_no_breach(self) -> Self:
        if self.status is RiskStatus.APPROVE and self.breaches:
            raise ValueError("an APPROVE decision cannot carry breaches")
        return self


class Severity(StrEnum):
    """How a compliance rule hit is handled: BLOCK stops the order, WARN only records it."""

    BLOCK = "BLOCK"
    WARN = "WARN"


class Violation(AtlasModel):
    """One compliance rule hit, optionally naming the symbol and order it concerns."""

    rule: NonEmptyStr
    severity: Severity
    message: NonEmptyStr
    symbol: Symbol | None = None
    order_id: OrderId | None = None


class ComplianceResult(AtlasModel):
    """The pre-trade compliance outcome: orders allowed through, blocking violations and
    warnings (fields per Stage 48)."""

    approved_orders: tuple[Order, ...] = ()
    blocked: tuple[Violation, ...] = ()
    warnings: tuple[Violation, ...] = ()

    @model_validator(mode="after")
    def _severity_buckets(self) -> Self:
        if any(v.severity is not Severity.BLOCK for v in self.blocked):
            raise ValueError("blocked may only hold BLOCK violations")
        if any(v.severity is not Severity.WARN for v in self.warnings):
            raise ValueError("warnings may only hold WARN violations")
        return self

    @property
    def passed(self) -> bool:
        """True when nothing was blocked."""
        return not self.blocked
