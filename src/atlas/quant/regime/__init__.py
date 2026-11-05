"""Market-regime detection (Stage 21): a deterministic, point-in-time regime read that pods, the
CIO and risk condition on.

- `vol_trend` — volatility regime (LOW / NORMAL / HIGH / CRISIS from the benchmark's realised
  vol and VIX percentiles) and trend regime (UP / NEUTRAL / DOWN from the 200-session average,
  12-1 momentum and breadth).
- `hmm` — Gaussian HMM (2-3 states ordered by volatility: calm / [normal] / turbulent) on an
  expanding window refitted monthly; forward-filtered probabilities.
- `macro_quadrant` — Goldilocks / Reflation / Stagflation / Deflation from growth and core
  inflation trends; credit z-score (HY OAS, or the HYG/IEF proxy where FRED's 3-year OAS is
  short) and the stress flag (credit z > 2 or NFCI > 0).
- `state` — `RegimeState` and the deterministic `risk_scalar` ∈ [0.25, 1] that vol-targeting
  multiplies into `limits.target_vol_annual`.
- `loader` — `regime_at(view)` / `regime_history(lake, start, end)`; `store` — the persisted
  daily history (`regime_history` table, `RegimeStore`).

Settings: `quant.regime` in `config/fund.yaml`. See the module docstrings for units.
"""

from atlas.quant.regime.hmm import (
    HMMFit,
    expanding_probabilities,
    filter_probabilities,
    fit_hmm,
    state_labels,
)
from atlas.quant.regime.loader import regime_at, regime_history
from atlas.quant.regime.macro_quadrant import (
    QUADRANTS,
    Quadrant,
    acceleration,
    claims_trend,
    classify_quadrant,
    credit_proxy,
    credit_zscore,
    growth_score,
    growth_votes,
    inflation_gap,
    stress_flag,
    trailing_zscore,
)
from atlas.quant.regime.state import MAX_RISK_SCALAR, MIN_RISK_SCALAR, RegimeState, risk_scalar
from atlas.quant.regime.store import (
    REGIME_HISTORY,
    RegimeStore,
    build_and_store,
    regime_config_hash,
)
from atlas.quant.regime.vol_trend import (
    TREND_LABELS,
    VOL_LABELS,
    TrendRegime,
    VolRegime,
    breadth,
    classify_trend,
    classify_vol,
    trailing_percentile,
    trend_votes,
    vol_score,
)

__all__ = [
    "MAX_RISK_SCALAR",
    "MIN_RISK_SCALAR",
    "QUADRANTS",
    "REGIME_HISTORY",
    "TREND_LABELS",
    "VOL_LABELS",
    "HMMFit",
    "Quadrant",
    "RegimeState",
    "RegimeStore",
    "TrendRegime",
    "VolRegime",
    "acceleration",
    "breadth",
    "build_and_store",
    "claims_trend",
    "classify_quadrant",
    "classify_trend",
    "classify_vol",
    "credit_proxy",
    "credit_zscore",
    "expanding_probabilities",
    "filter_probabilities",
    "fit_hmm",
    "growth_score",
    "growth_votes",
    "inflation_gap",
    "regime_at",
    "regime_config_hash",
    "regime_history",
    "risk_scalar",
    "state_labels",
    "stress_flag",
    "trailing_percentile",
    "trailing_zscore",
    "trend_votes",
    "vol_score",
]
