"""Liquidity metrics: days to liquidate at a share of ADV (Stage 20).

Units: `positions` are signed position values in currency (USD; weight x NAV), `adv` the
average daily traded value in the same currency (`DataView.adv(...)["adv_usd"]`, the mean of
close x volume over `limits.adv_window_days` sessions); `participation` is the fraction of ADV
traded per session (default `limits.liquidation_adv_frac`, 0.10). Days are sessions (float).

- `days_to_liquidate` = `|position| / (participation * ADV)`; 0 for a zero position. A held
  name with no ADV (missing, NaN or <= 0) cannot be sold at any participation: `inf` days
  (fail safe: it never counts as liquid) and it is listed in `LiquidityProfile.no_adv`.
- `liquidity_profile` adds, for each horizon `h` (default `quant.metrics.liquidity_horizons`,
  1/3/5 sessions): `names_liquid[h]` = share of held positions (by count) that liquidate within
  `h` sessions, and `gross_liquid[h]` = share of gross position value that can be sold within
  `h` sessions at the participation rate (`sum(min(|pos|, h * participation * ADV)) /
  sum(|pos|)`, partial liquidation counted).
"""

import math
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.core.logging import get_logger
from atlas.data.view import DataView
from atlas.quant.metrics._common import as_weights, config_or_default

_log = get_logger(__name__)


@dataclass(frozen=True)
class LiquidityProfile:
    """Liquidity of a book (module docstring). `days` holds every position (sessions; `inf`
    without ADV); `names_liquid` / `gross_liquid` map each horizon (sessions) to a fraction;
    `max_days` is the slowest held position (0 for an empty book); `no_adv` lists held names
    without a usable ADV."""

    days: pd.Series
    participation: float
    names_liquid: Mapping[int, float]
    gross_liquid: Mapping[int, float]
    max_days: float
    no_adv: tuple[Hashable, ...]


def _participation(value: float | None, cfg: AtlasConfig) -> float:
    p = cfg.limits.liquidation_adv_frac if value is None else value
    if isinstance(p, bool) or not isinstance(p, int | float) or not 0.0 < float(p) <= 1.0:
        raise ValueError(f"participation must be a fraction of ADV in (0, 1], got {p!r}")
    return float(p)


def _horizons(values: Sequence[int] | None, cfg: AtlasConfig) -> tuple[int, ...]:
    hs = tuple(cfg.quant.metrics.liquidity_horizons if values is None else values)
    if not hs or any(isinstance(h, bool) or not isinstance(h, int) or h < 1 for h in hs):
        raise ValueError(f"horizons must be positive ints (sessions), got {hs!r}")
    return hs


def _adv_on(positions: pd.Series, adv: pd.Series) -> np.ndarray:
    if not isinstance(adv, pd.Series):
        raise ValueError("adv must be a Series by symbol (currency per session)")
    if adv.index.has_duplicates:
        raise ValueError("adv has duplicate symbols")
    values = adv.reindex(positions.index).astype("float64").to_numpy()
    if np.isinf(values).any():
        raise ValueError("adv contains infinite values")
    return values


def days_to_liquidate(
    positions: pd.Series,
    adv: pd.Series,
    *,
    participation: float | None = None,
    config: AtlasConfig | None = None,
) -> pd.Series:
    """Sessions to sell each position at `participation` of ADV (module docstring); `inf`
    for a held name without a usable ADV."""
    cfg = config_or_default(config)
    pos = as_weights(positions, "positions")
    rate = _participation(participation, cfg)
    capacity = rate * _adv_on(pos, adv)
    size = np.abs(pos.to_numpy())
    usable = np.isfinite(capacity) & (capacity > 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        days = np.where(usable, size / np.where(usable, capacity, 1.0), math.inf)
    days = np.where(size == 0.0, 0.0, days)
    return pd.Series(days, index=pos.index, name="days_to_liquidate")


def liquidity_profile(
    positions: pd.Series,
    adv: pd.Series,
    *,
    participation: float | None = None,
    horizons: Sequence[int] | None = None,
    config: AtlasConfig | None = None,
) -> LiquidityProfile:
    """Days to liquidate (sessions) and the share (fraction) of positions / gross value liquid
    within each horizon (module docstring); `positions` and `adv` in the same currency."""
    cfg = config_or_default(config)
    pos = as_weights(positions, "positions")
    rate = _participation(participation, cfg)
    hs = _horizons(horizons, cfg)
    days = days_to_liquidate(pos, adv, participation=rate, config=cfg)
    size = np.abs(pos.to_numpy())
    held = size > 0.0
    capacity = rate * np.nan_to_num(_adv_on(pos, adv), nan=0.0)
    capacity = np.where(capacity > 0.0, capacity, 0.0)
    gross = float(size.sum())
    names: dict[int, float] = {}
    value: dict[int, float] = {}
    for h in hs:
        names[h] = float(np.mean(days.to_numpy()[held] <= h)) if held.any() else 1.0
        value[h] = float(np.minimum(size, h * capacity).sum() / gross) if gross > 0 else 1.0
    no_adv = tuple(days.index[np.isinf(days.to_numpy())])
    if no_adv:
        _log.warning("liquidity_no_adv", names=[str(s) for s in no_adv[:10]], count=len(no_adv))
    return LiquidityProfile(
        days=days,
        participation=rate,
        names_liquid=MappingProxyType(names),
        gross_liquid=MappingProxyType(value),
        max_days=float(days.to_numpy()[held].max()) if held.any() else 0.0,
        no_adv=no_adv,
    )


def liquidity_from_view(
    view: DataView,
    positions: pd.Series,
    *,
    participation: float | None = None,
    horizons: Sequence[int] | None = None,
    lookback: int | None = None,
) -> LiquidityProfile:
    """`liquidity_profile` with the ADV read point-in-time from `view` (`DataView.adv`,
    `adv_usd` over `lookback` sessions, default `limits.adv_window_days`); `positions` in USD."""
    pos = as_weights(positions, "positions")
    adv = view.adv([str(s) for s in pos.index], lookback)["adv_usd"]
    return liquidity_profile(
        pos, adv, participation=participation, horizons=horizons, config=view.config
    )
