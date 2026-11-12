"""Systematic alpha models (Stage 22): deterministic signals with calibrated expected returns.

`base` holds the `AlphaModel` protocol and the alpha-frame contract (`score`,
`exp_return_ann` as an annualised fraction, `confidence` in [0, 1], one row per scored
symbol); `calibration` the Grinold score-to-return rule and the point-in-time IC bookkeeping.
"""

from atlas.quant.alpha.base import (
    ALPHA_COLUMNS,
    AlphaModel,
    alpha_frame,
    check_alpha_frame,
    check_horizon,
    direction,
    empty_alpha_frame,
)
from atlas.quant.alpha.calibration import (
    CalibrationFit,
    WeightSource,
    calibration_slope,
    grinold_alpha,
    horizon_scale,
    ic_history,
    ic_weights,
    known_ic,
    specific_vol,
    trailing_ic,
)
from atlas.quant.alpha.multifactor import (
    COMPOSITE,
    CombineResult,
    ComponentPanel,
    MultiFactorModel,
    clean_component,
    combine,
    component_frame,
    member_sectors,
    style_scores,
)
from atlas.quant.alpha.pead import PEADModel, pead_decay, pead_signal
from atlas.quant.alpha.tsmom import TSMOMModel, trend_blend

__all__ = [
    "ALPHA_COLUMNS",
    "COMPOSITE",
    "AlphaModel",
    "CalibrationFit",
    "CombineResult",
    "ComponentPanel",
    "MultiFactorModel",
    "PEADModel",
    "TSMOMModel",
    "WeightSource",
    "alpha_frame",
    "calibration_slope",
    "check_alpha_frame",
    "check_horizon",
    "clean_component",
    "combine",
    "component_frame",
    "direction",
    "empty_alpha_frame",
    "grinold_alpha",
    "horizon_scale",
    "ic_history",
    "ic_weights",
    "known_ic",
    "member_sectors",
    "pead_decay",
    "pead_signal",
    "specific_vol",
    "style_scores",
    "trailing_ic",
    "trend_blend",
]
