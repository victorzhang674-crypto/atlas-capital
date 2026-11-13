"""Fundamental factor library (Stage 16).

`fundamental` defines the raw factors and computes them from what `DataView` returns at an
as-of (point-in-time fundamentals, split-adjusted share counts, prices); `cleaning` turns them
into cross-sectional exposures (winsorise, z-score, sector-median fill, sector and optional
size neutralisation); `composite` combines exposures; `fundamental.factor_panel(view)` runs the
whole pipeline; `store` caches exposures in the lake's `factor_exposures` table for monthly
rebalance dates.
"""

from atlas.quant.factors.cleaning import (
    SIZE_FACTOR,
    CleanedFactors,
    clean_factors,
    fill_missing,
    neutralize,
    winsorize,
    zscore,
)
from atlas.quant.factors.composite import composite, style_composites
from atlas.quant.factors.fundamental import (
    FACTOR_DEFINITIONS_VERSION,
    FACTOR_NAMES,
    FACTORS,
    GROUPS,
    HISTORY_CONCEPTS,
    MARKET_COLUMNS,
    SHARES_COLUMNS,
    FactorGroup,
    FactorPanel,
    FactorSpec,
    adjusted_shares,
    average_shares,
    compute_market,
    factor_config_hash,
    factor_panel,
    implied_shares,
    raw_factors,
)
from atlas.quant.factors.store import (
    FACTOR_EXPOSURES,
    FactorStore,
    StoredExposures,
    build_factor_cache,
    monthly_rebalance_dates,
)

__all__ = [
    "FACTORS",
    "FACTOR_DEFINITIONS_VERSION",
    "FACTOR_EXPOSURES",
    "FACTOR_NAMES",
    "GROUPS",
    "HISTORY_CONCEPTS",
    "MARKET_COLUMNS",
    "SHARES_COLUMNS",
    "SIZE_FACTOR",
    "CleanedFactors",
    "FactorGroup",
    "FactorPanel",
    "FactorSpec",
    "FactorStore",
    "StoredExposures",
    "adjusted_shares",
    "average_shares",
    "build_factor_cache",
    "clean_factors",
    "composite",
    "compute_market",
    "factor_config_hash",
    "factor_panel",
    "fill_missing",
    "implied_shares",
    "monthly_rebalance_dates",
    "neutralize",
    "raw_factors",
    "style_composites",
    "winsorize",
    "zscore",
]
