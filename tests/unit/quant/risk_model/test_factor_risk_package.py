"""The Stage 19 public API of `atlas.quant.risk_model`."""

import atlas.quant.risk_model as rm

STAGE_19 = {
    "CrossSection", "EtfExposures", "Exposures", "FACTOR_RETURNS", "FactorCovariance",
    "FactorHistory", "FactorSet", "MARKET", "PortfolioRisk", "RISK_MODEL_SNAPSHOTS",
    "RegressionResult", "RiskModel", "RiskModelStore", "STYLE_NAMES", "SpecificResult",
    "StyleInputs", "bias_statistic", "build_and_store", "etf_exposures_from_view",
    "build_factor_history", "build_model_from_view",
    "build_risk_model", "constrained_wls", "etf_exposures", "ewma_specific_variance",
    "extend_model", "factor_covariance", "factor_exposures", "fit_factor_history",
    "load_cross_sections", "marginal_contribution", "newey_west_covariance",
    "portfolio_risk", "prepare_exposures", "risk_model_config_hash", "sector_constraint",
    "shrink_to_sector_median", "specific_variance", "standardize_styles", "style_inputs",
}  # fmt: skip


def test_stage_19_names_are_exported() -> None:
    assert set(rm.__all__) >= STAGE_19
    for name in STAGE_19:
        assert getattr(rm, name) is not None
    assert len(rm.__all__) == len(set(rm.__all__))
