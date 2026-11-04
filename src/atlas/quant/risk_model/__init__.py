"""Covariance estimators, PSD repair and volatility forecasts (Stage 18).

Units: daily simple returns in (fractions, one column per name, sessions ascending);
covariances in return² per session and vols as daily standard deviations out. Annualise
explicitly with `annualise_vol` / `annualise_variance` (`quant.features.sessions_per_year`).

- `covariance_estimator(method).estimate(returns) -> CovResult` (`sample`, `ewma`,
  `ledoit_wolf`, `oas`): every result is symmetric, positive definite and has a condition
  number of at most `quant.risk_model.max_condition`; names without enough history are
  dropped and reported, never guessed.
- `forecast_vol(returns, method) -> VolForecast` (`ewma`, `garch`, `realised`, `blend`):
  next-session vol of one name; GARCH(1,1) falls back to EWMA with the reason recorded.
- `nearest_psd`, `nearest_correlation` (Higham), `condition_number`, `floor_condition`.
"""

from atlas.quant.risk_model.covariance import (
    METHODS,
    CovarianceEstimator,
    CovResult,
    EWMACovariance,
    LedoitWolfCovariance,
    OASCovariance,
    SampleCovariance,
    corr_from_cov,
    cov_from_corr,
    covariance_estimator,
)
from atlas.quant.risk_model.psd import (
    condition_number,
    floor_condition,
    is_psd,
    nearest_correlation,
    nearest_psd,
)
from atlas.quant.risk_model.volatility import (
    VOL_METHODS,
    VolForecast,
    annualise_variance,
    annualise_vol,
    blend_vol,
    deannualise_vol,
    ewma_lambda,
    ewma_variance_path,
    ewma_vol,
    forecast_vol,
    forecast_vols,
    garch_vol,
    realised_vol,
)

__all__ = [
    "METHODS",
    "VOL_METHODS",
    "CovResult",
    "CovarianceEstimator",
    "EWMACovariance",
    "LedoitWolfCovariance",
    "OASCovariance",
    "SampleCovariance",
    "VolForecast",
    "annualise_variance",
    "annualise_vol",
    "blend_vol",
    "condition_number",
    "corr_from_cov",
    "cov_from_corr",
    "covariance_estimator",
    "deannualise_vol",
    "ewma_lambda",
    "ewma_variance_path",
    "ewma_vol",
    "floor_condition",
    "forecast_vol",
    "forecast_vols",
    "garch_vol",
    "is_psd",
    "nearest_correlation",
    "nearest_psd",
    "realised_vol",
]
