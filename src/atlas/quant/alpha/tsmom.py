"""Time-series momentum for ETFs (Stage 22).

For each ETF of `quant.alpha.tsmom.universe` and each lookback `L` of `lookbacks` (sessions;
about 1, 3, 6 and 12 months), the trend signal is the sign of the Stage 15 total return
`ret_<L>d` (bars through the previous session; 0 for an exactly flat return). The blend
`b = mean of the available signs` (equal weights, at least `min_lookbacks` of them) is in
[-1, 1]. With the annualised volatility `vol` (Stage 15 `vol_feature`, default `vol_63d`):

- `score = clip(b * vol_target / vol, -max_position, max_position)`: the inverse-vol-scaled
  position (NAV fraction of a book run at `vol_target` per asset);
- `exp_return_ann = sharpe_prior * vol * b`: the asset's own expected annualised return when
  held along a full trend earns a Sharpe ratio of `sharpe_prior` (a prior, checked in-sample by
  the Stage 22 research run), 0 for no trend;
- `confidence = |b|`: the agreement of the lookbacks. The direction is the sign of `b`.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from atlas.core.config import AlphaTSMOMConfig
from atlas.data.view import DataView
from atlas.quant.alpha.base import alpha_frame, empty_alpha_frame
from atlas.quant.features import REGISTRY, compute_features


def trend_blend(returns: pd.DataFrame, *, min_lookbacks: int) -> pd.Series:
    """The equal-weight mean of the signs of `returns` (index symbol, one column per
    lookback; total returns as fractions) over each row's available values; NaN for a row
    with fewer than `min_lookbacks` values."""
    if min_lookbacks < 1:
        raise ValueError(f"min_lookbacks must be >= 1, got {min_lookbacks}")
    values = returns.astype("float64")
    signs = values.apply(np.sign)
    count = values.notna().sum(axis=1)
    return signs.mean(axis=1).where(count >= min_lookbacks).rename("blend")


def _feature_names(cfg: AlphaTSMOMConfig) -> list[str]:
    names = [f"ret_{n}d" for n in cfg.lookbacks]
    unknown = [n for n in [*names, cfg.vol_feature] if n not in REGISTRY]
    if unknown:
        raise ValueError(
            f"no Stage 15 feature {unknown} for quant.alpha.tsmom (each lookback needs a "
            "registered ret_<L>d feature)"
        )
    return names


@dataclass(frozen=True)
class TSMOMModel:
    """The time-series momentum `AlphaModel` (module docstring); `config` overrides the
    view's `quant.alpha.tsmom`."""

    config: AlphaTSMOMConfig | None = None

    @property
    def name(self) -> str:
        """`tsmom`."""
        return "tsmom"

    @property
    def horizon(self) -> int:
        """The forecast horizon in sessions (`config.horizon`; default 21)."""
        return (self.config or AlphaTSMOMConfig()).horizon

    def predict(self, view: DataView) -> pd.DataFrame:
        """The alpha frame of the universe's ETFs at `view.as_of`; an ETF without enough
        lookbacks or without a positive vol is absent."""
        cfg = self.config or view.config.quant.alpha.tsmom
        names = _feature_names(cfg)
        members = view.universe(cfg.universe)
        if not members:
            return empty_alpha_frame()
        feats = compute_features(view, members, [*names, cfg.vol_feature], history=1)
        rets = pd.DataFrame(
            {n: feats[name].iloc[-1] for n, name in zip(cfg.lookbacks, names, strict=True)}
        )
        blend = trend_blend(rets, min_lookbacks=cfg.min_lookbacks)
        vol = feats[cfg.vol_feature].iloc[-1].astype("float64")
        vol = vol.where(vol > 0)
        score = (blend * cfg.vol_target / vol).clip(-cfg.max_position, cfg.max_position)
        exp = cfg.sharpe_prior * vol * blend
        return alpha_frame(score, exp, blend.abs())
