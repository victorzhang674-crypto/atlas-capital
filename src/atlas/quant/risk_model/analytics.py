"""Portfolio analytics on a `RiskModel` (Stage 19): risk decomposition, factor exposures,
marginal contributions and the calibration (bias) statistic.

Units: weights are fractions of NAV (0.05 = 5%, shorts negative); variances are daily
return**2 and vols daily (`PortfolioRisk.annual_vol` annualises with `sqrt(periods)`,
default `quant.features.sessions_per_year`). With `x = X'w`, `F` the factor covariance and
`D` the specific variances: `factor_var = x'Fx`, `specific_var = sum w**2 D`, `total_var =
factor_var + specific_var = w' Sigma w`; `by_factor[k] = x_k (Fx)_k` sums to `factor_var`;
`mctr = Sigma w / sigma_p` is the gradient of `sigma_p` in `w`; `contribution = w * mctr` sums
to `sigma_p` (Euler).
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.quant.risk_model.factor_model import RiskModel
from atlas.quant.risk_model.volatility import annualise_vol


@dataclass(frozen=True)
class PortfolioRisk:
    """Daily risk of a portfolio under a `RiskModel`. `by_factor` and `exposures` are indexed
    by factor; `mctr` and `contribution` by every name of the model (zero weight -> zero
    contribution). A zero portfolio has zero risk and zero `mctr`."""

    total_var: float
    factor_var: float
    specific_var: float
    total_vol: float
    factor_vol: float
    specific_vol: float
    by_factor: pd.Series
    exposures: pd.Series
    mctr: pd.Series
    contribution: pd.Series

    def annual_vol(self, periods: int | None = None, *, config: AtlasConfig | None = None) -> float:
        """`total_vol` annualised (`periods` sessions a year; default from the config)."""
        return float(annualise_vol(self.total_vol, periods, config=config))


def _aligned(model: RiskModel, weights: pd.Series) -> np.ndarray:
    """`weights` as a vector over `model.symbols`: validated, unknown names allowed only with
    zero weight (`DataError` otherwise: no risk without a risk estimate)."""
    if not isinstance(weights, pd.Series):
        raise ValueError("weights must be a pandas Series indexed by symbol")
    if weights.index.has_duplicates:
        raise ValueError("duplicate symbols in weights")
    values = weights.to_numpy(dtype="float64")
    if not np.isfinite(values).all():
        raise ValueError("weights must be finite")
    known = set(model.symbols)
    unknown = [str(s) for s, v in weights.items() if s not in known and v != 0.0]
    if unknown:
        raise DataError(f"not in the risk model (no risk estimate), nonzero weight: {unknown[:10]}")
    return weights.reindex(list(model.symbols)).fillna(0.0).to_numpy(dtype="float64")


def portfolio_risk(model: RiskModel, weights: pd.Series) -> PortfolioRisk:
    """The risk decomposition of `weights` (Series of NAV fractions indexed by symbol)."""
    w = _aligned(model, weights)
    x = model.x.T @ w
    fx = model.f @ x
    factor_var = float(x @ fx)
    specific_var = float(w**2 @ model.d)
    total_var = factor_var + specific_var
    total_vol = math.sqrt(total_var)
    sigma_w = model.x @ fx + model.d * w
    mctr = sigma_w / total_vol if total_vol > 0 else np.zeros_like(w)
    names = pd.Index(model.symbols, name="symbol")
    return PortfolioRisk(
        total_var=total_var,
        factor_var=factor_var,
        specific_var=specific_var,
        total_vol=total_vol,
        factor_vol=math.sqrt(factor_var),
        specific_vol=math.sqrt(specific_var),
        by_factor=pd.Series(x * fx, index=list(model.factors), name="variance_contribution"),
        exposures=pd.Series(x, index=list(model.factors), name="exposure"),
        mctr=pd.Series(mctr, index=names, name="mctr"),
        contribution=pd.Series(w * mctr, index=names, name="contribution"),
    )


def factor_exposures(model: RiskModel, weights: pd.Series) -> pd.Series:
    """The portfolio's exposure to each factor, `X'w` (index = factors)."""
    return pd.Series(
        model.x.T @ _aligned(model, weights), index=list(model.factors), name="exposure"
    )


def marginal_contribution(model: RiskModel, weights: pd.Series) -> pd.Series:
    """Marginal contribution to risk of every name, `(Sigma w)_n / sigma_p` (daily vol per
    unit weight)."""
    return portfolio_risk(model, weights).mctr


def bias_statistic(realised: pd.Series, predicted_vol: pd.Series) -> float:
    """Calibration of a risk forecast: `sqrt(mean((r_t / sigma_t) ** 2))` over the sessions
    with both a realised return and a positive predicted vol. About 1 for a calibrated model;
    above 1 the risk was under-predicted. Fewer than 20 usable sessions -> `DataError`."""
    both = pd.concat([realised.rename("r"), predicted_vol.rename("s")], axis=1).dropna()
    both = both[both["s"] > 0]
    if len(both) < 20:
        raise DataError(f"bias statistic needs at least 20 usable sessions, got {len(both)}")
    return float(np.sqrt(np.mean((both["r"] / both["s"]) ** 2)))
