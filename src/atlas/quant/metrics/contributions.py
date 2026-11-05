"""Risk contributions: marginal, component and percent (Euler) (Stage 20).

Units: weights are fractions of NAV (shorts negative); the covariance is daily (return^2 per
session), so `vol` and the contributions are daily standard deviations (`annual_vol()`
annualises with `sqrt(P)`, `P = quant.features.sessions_per_year`).

With `sigma = sqrt(w' Sigma w)`: `marginal = Sigma w / sigma` (d sigma / d w, daily vol per unit
of weight); `component = w * marginal` (sums to `sigma`, Euler's theorem for the 1-homogeneous
`sigma(w)`); `percent = component / sigma` (sums to 1; a short or a hedge can be negative).
A zero portfolio has zero vol and zero marginal and component contributions; its percent
contributions are undefined (NaN).

The covariance may be a DataFrame (names x names, aligned with the weights by symbol), an
array (aligned by position) or a Stage 19 `RiskModel`, decomposed through `portfolio_risk`
without building the N x N matrix (same numbers as `risk_contributions(w, model.cov())`). A
nonzero weight on a name outside the covariance raises `DataError` (no risk without an
estimate).
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.quant.metrics._common import as_covariance, weights_on
from atlas.quant.risk_model.analytics import portfolio_risk
from atlas.quant.risk_model.factor_model import RiskModel
from atlas.quant.risk_model.volatility import annualise_vol


@dataclass(frozen=True)
class RiskContributions:
    """Euler decomposition of a portfolio's daily vol (module docstring). `marginal`,
    `component` and `percent` are indexed by every name of the covariance (zero weight → zero
    component)."""

    vol: float
    marginal: pd.Series
    component: pd.Series
    percent: pd.Series

    def annual_vol(self, periods: int | None = None, *, config: AtlasConfig | None = None) -> float:
        """`vol` annualised (`periods` sessions a year; default from the config)."""
        return float(annualise_vol(self.vol, periods, config=config))


def _result(vol: float, marginal: np.ndarray, w: np.ndarray, index: pd.Index) -> RiskContributions:
    component = w * marginal
    percent = component / vol if vol > 0.0 else np.full(len(w), np.nan)
    return RiskContributions(
        vol=vol,
        marginal=pd.Series(marginal, index=index, name="marginal"),
        component=pd.Series(component, index=index, name="component"),
        percent=pd.Series(percent, index=index, name="percent"),
    )


def risk_contributions(
    weights: pd.Series | np.ndarray, cov: pd.DataFrame | np.ndarray | RiskModel
) -> RiskContributions:
    """Marginal, component and percent contributions of each name to the portfolio's daily
    vol (module docstring)."""
    if isinstance(cov, RiskModel):
        if not isinstance(weights, pd.Series):
            raise ValueError("weights must be a Series by symbol with a RiskModel")
        risk = portfolio_risk(cov, weights)
        w = weights.reindex(list(cov.symbols)).fillna(0.0).to_numpy(dtype="float64")
        return _result(
            risk.total_vol,
            risk.mctr.to_numpy(),
            w,
            pd.Index(cov.symbols, name="symbol"),
        )
    matrix, names = as_covariance(cov)
    w = weights_on(names, weights)
    sigma_w = matrix @ w
    vol = math.sqrt(max(float(w @ sigma_w), 0.0))
    marginal = sigma_w / vol if vol > 0.0 else np.zeros(len(w))
    return _result(vol, marginal, w, pd.Index(names, name="symbol"))
