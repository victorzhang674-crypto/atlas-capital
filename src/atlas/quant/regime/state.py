"""The regime contract (`RegimeState`) and the deterministic `risk_scalar` mapping (Stage 21).

`RegimeState` is what pods, the CIO and risk condition on: one per decision day `as_of`,
computed at that day's decision time from data through `data_date` (the previous session).

`risk_scalar` (a fraction of normal risk in [0.25, 1]) is the product of the multipliers in
`quant.regime.risk_scalar` (`config/fund.yaml`):

| component | multiplier |
|---|---|
| vol regime | LOW 1.0 · NORMAL 1.0 · HIGH 0.7 · CRISIS 0.4 |
| trend regime | UP 1.0 · NEUTRAL 0.9 · DOWN 0.75 |
| stress flag | 0.6 when set |
| HMM | 0.85 when P(turbulent) ≥ `hmm.turbulent_prob` (0.5) |
| macro quadrant | Goldilocks 1.0 · Reflation 1.0 · Stagflation 0.85 · Deflation 0.9 |

A missing component multiplies by 1; the product is clipped to [`floor` 0.25, `cap` 1.0].
Vol-targeting (Stage 43) scales `limits.target_vol_annual` by it.
"""

import math
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas.core.config import RegimeConfig
from atlas.quant.regime.hmm import state_labels
from atlas.quant.regime.macro_quadrant import QUADRANTS, CreditSource, Quadrant
from atlas.quant.regime.vol_trend import TREND_LABELS, VOL_LABELS, TrendRegime, VolRegime

MIN_RISK_SCALAR = 0.25
"""The lowest possible `risk_scalar` (the brief's lower bound)."""

MAX_RISK_SCALAR = 1.0
"""The highest possible `risk_scalar`."""

Component = Literal["vix", "breadth", "momentum", "hmm", "macro_quadrant", "credit", "nfci"]
"""Names that can appear in `RegimeState.missing`."""


def risk_scalar(
    vol_regime: VolRegime,
    trend_regime: TrendRegime,
    *,
    stress: bool,
    quadrant: Quadrant | None,
    p_turbulent: float | None,
    config: RegimeConfig,
) -> float:
    """The deterministic risk scalar (module docstring): a fraction in [`floor`, `cap`] ⊂
    [0.25, 1]. `quadrant` / `p_turbulent` (a probability) `None` = missing (multiplier 1).
    Unknown labels or a probability outside [0, 1] raise `ValueError`."""
    table = config.risk_scalar
    if vol_regime not in VOL_LABELS:
        raise ValueError(f"unknown vol regime {vol_regime!r}")
    if trend_regime not in TREND_LABELS:
        raise ValueError(f"unknown trend regime {trend_regime!r}")
    if quadrant is not None and quadrant not in QUADRANTS:
        raise ValueError(f"unknown macro quadrant {quadrant!r}")
    if p_turbulent is not None and not 0.0 <= p_turbulent <= 1.0:
        raise ValueError(f"p_turbulent must be a probability, got {p_turbulent}")
    value = getattr(table.vol, vol_regime.lower()) * getattr(table.trend, trend_regime.lower())
    if stress:
        value *= table.stress
    if p_turbulent is not None and p_turbulent >= config.hmm.turbulent_prob:
        value *= table.hmm_turbulent
    if quadrant is not None:
        value *= getattr(table.quadrant, quadrant.lower())
    return float(min(table.cap, max(table.floor, value)))


class RegimeState(BaseModel):
    """The market regime on decision day `as_of` (see the module docstring).

    Labels: `vol_regime` LOW / NORMAL / HIGH / CRISIS; `trend_regime` UP / NEUTRAL / DOWN;
    `macro_quadrant` Goldilocks / Reflation / Stagflation / Deflation or `None` (missing);
    `hmm_probs` the filtered HMM state probabilities (fractions summing to 1) in the order of
    `hmm_labels` (calm → turbulent), both empty / `None` before the first fit; `stress_flag`
    credit or financial-conditions stress; `risk_scalar` the deterministic multiplier of
    target risk in [0.25, 1]; `credit_source` `oas` / `proxy` (what the credit z-score used).

    Readings (`None` when missing): `realised_vol` (benchmark 21-session realised vol,
    annualised fraction), `vix` (index points), `vol_score` (percentile fraction),
    `price_to_sma_200` (ratio), `momentum_12_1` (fraction), `breadth` (fraction of members
    above their 200-session SMA), `credit_z` (z-score), `nfci` (index), `growth_score` (mean
    growth vote in [-1, 1]), `inflation_gap` (core CPI 3m annualised - YoY, percentage
    points). `missing` lists the components that could not be computed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    as_of: date
    data_date: date
    vol_regime: VolRegime
    trend_regime: TrendRegime
    hmm_probs: tuple[float, ...] | None
    hmm_labels: tuple[str, ...]
    macro_quadrant: Quadrant | None
    stress_flag: bool
    risk_scalar: float = Field(ge=MIN_RISK_SCALAR, le=MAX_RISK_SCALAR)
    credit_source: CreditSource | None = None
    realised_vol: float | None = None
    vix: float | None = None
    vol_score: float | None = None
    price_to_sma_200: float | None = None
    momentum_12_1: float | None = None
    breadth: float | None = None
    credit_z: float | None = None
    nfci: float | None = None
    growth_score: float | None = None
    inflation_gap: float | None = None
    missing: tuple[Component, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.data_date >= self.as_of:
            raise ValueError(
                f"data_date ({self.data_date}) must be before the decision day ({self.as_of})"
            )
        if self.hmm_probs is None:
            if self.hmm_labels:
                raise ValueError("hmm_labels given without hmm_probs")
            return self
        if self.hmm_labels != state_labels(len(self.hmm_labels)):
            raise ValueError(
                f"hmm_labels must be one of the state label sets, got {self.hmm_labels}"
            )
        if len(self.hmm_probs) != len(self.hmm_labels):
            raise ValueError("hmm_probs and hmm_labels differ in length")
        if any(not 0.0 <= p <= 1.0 for p in self.hmm_probs):
            raise ValueError("hmm_probs must be probabilities in [0, 1]")
        if not math.isclose(sum(self.hmm_probs), 1.0, abs_tol=1e-6):
            raise ValueError(f"hmm_probs must sum to 1, got {sum(self.hmm_probs)}")
        return self

    @property
    def p_turbulent(self) -> float | None:
        """P(turbulent state) — the last HMM probability — or `None` without an HMM read."""
        return None if self.hmm_probs is None else self.hmm_probs[-1]
