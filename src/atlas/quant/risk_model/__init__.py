"""Covariance estimators, PSD repair, volatility forecasts (Stage 18) and the factor risk
model (Stage 19).

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

Factor risk model (Stage 19; Barra style, daily units, weights = fractions of NAV):
- `fit_factor_history(cross_sections, factors, config)` runs the daily constrained WLS
  (`constrained_wls`: weights sqrt(cap), cap-weighted sector returns sum to zero) over
  `CrossSection`s; `build_risk_model(history, raw_styles, sectors, caps, as_of, config)` gives a
  `RiskModel` with `cov() = X F X' + D` (`factor_covariance`: EWMA + Newey-West;
  `specific_variance`: EWMA shrunk to the sector median).
- `portfolio_risk(model, w)`, `factor_exposures`, `marginal_contribution`, `bias_statistic`.
- `etf_exposures` / `extend_model`: ETF time-series betas; `RiskModelStore`: lake persistence;
  `load_cross_sections` / `style_inputs` / `build_model_from_view` /
  `etf_exposures_from_view`: real-data inputs through `DataView`; `build_and_store` fits a
  window and stores its factor returns and month-end snapshots.
"""

from atlas.quant.risk_model.analytics import (
    PortfolioRisk,
    bias_statistic,
    factor_exposures,
    marginal_contribution,
    portfolio_risk,
)
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
from atlas.quant.risk_model.etf_mapping import EtfExposures, etf_exposures, extend_model
from atlas.quant.risk_model.factor_model import (
    MARKET,
    STYLE_NAMES,
    CrossSection,
    Exposures,
    FactorCovariance,
    FactorHistory,
    FactorSet,
    RiskModel,
    build_risk_model,
    factor_covariance,
    fit_factor_history,
    newey_west_covariance,
    prepare_exposures,
    standardize_styles,
)
from atlas.quant.risk_model.loader import (
    StyleInputs,
    build_and_store,
    build_factor_history,
    build_model_from_view,
    etf_exposures_from_view,
    load_cross_sections,
    style_inputs,
)
from atlas.quant.risk_model.psd import (
    condition_number,
    floor_condition,
    is_psd,
    nearest_correlation,
    nearest_psd,
)
from atlas.quant.risk_model.regression import (
    RegressionResult,
    constrained_wls,
    sector_constraint,
)
from atlas.quant.risk_model.specific import (
    SpecificResult,
    ewma_specific_variance,
    shrink_to_sector_median,
    specific_variance,
)
from atlas.quant.risk_model.store import (
    FACTOR_RETURNS,
    RISK_MODEL_SNAPSHOTS,
    RiskModelStore,
    risk_model_config_hash,
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
    "FACTOR_RETURNS",
    "MARKET",
    "METHODS",
    "RISK_MODEL_SNAPSHOTS",
    "STYLE_NAMES",
    "VOL_METHODS",
    "CovResult",
    "CovarianceEstimator",
    "CrossSection",
    "EWMACovariance",
    "EtfExposures",
    "Exposures",
    "FactorCovariance",
    "FactorHistory",
    "FactorSet",
    "LedoitWolfCovariance",
    "OASCovariance",
    "PortfolioRisk",
    "RegressionResult",
    "RiskModel",
    "RiskModelStore",
    "SampleCovariance",
    "SpecificResult",
    "StyleInputs",
    "VolForecast",
    "annualise_variance",
    "annualise_vol",
    "bias_statistic",
    "blend_vol",
    "build_and_store",
    "build_factor_history",
    "build_model_from_view",
    "build_risk_model",
    "condition_number",
    "constrained_wls",
    "corr_from_cov",
    "cov_from_corr",
    "covariance_estimator",
    "deannualise_vol",
    "etf_exposures",
    "etf_exposures_from_view",
    "ewma_lambda",
    "ewma_specific_variance",
    "ewma_variance_path",
    "ewma_vol",
    "extend_model",
    "factor_covariance",
    "factor_exposures",
    "fit_factor_history",
    "floor_condition",
    "forecast_vol",
    "forecast_vols",
    "garch_vol",
    "is_psd",
    "load_cross_sections",
    "marginal_contribution",
    "nearest_correlation",
    "nearest_psd",
    "newey_west_covariance",
    "portfolio_risk",
    "prepare_exposures",
    "realised_vol",
    "risk_model_config_hash",
    "sector_constraint",
    "shrink_to_sector_median",
    "specific_variance",
    "standardize_styles",
    "style_inputs",
]
