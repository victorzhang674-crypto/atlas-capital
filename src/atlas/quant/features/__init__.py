"""Technical feature library (Stage 15): registry, the features, per-symbol snapshots.

Importing this package registers every technical feature in `REGISTRY`.
"""

from atlas.quant.features import technical as technical
from atlas.quant.features.registry import (
    REGISTRY,
    FeatureKind,
    FeatureRegistry,
    FeatureSpec,
    FeatureUnit,
    PricePanel,
    compute_features,
    compute_panel,
    feature,
    normalize_symbols,
)
from atlas.quant.features.snapshot import (
    FeatureReading,
    TechnicalSnapshot,
    build_snapshots,
    percentile_rank,
)

__all__ = [
    "REGISTRY",
    "FeatureKind",
    "FeatureReading",
    "FeatureRegistry",
    "FeatureSpec",
    "FeatureUnit",
    "PricePanel",
    "TechnicalSnapshot",
    "build_snapshots",
    "compute_features",
    "compute_panel",
    "feature",
    "normalize_symbols",
    "percentile_rank",
]
