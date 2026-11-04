"""Specific (residual) risk of the factor risk model (Stage 19).

`ewma_specific_variance` is the exponentially weighted mean of squared regression residuals
(zero-mean, half-life in sessions, weights over the sessions the name has a residual);
`shrink_to_sector_median` pulls each name's vol towards its sector's median vol (Barra USE4
Bayesian form): with `g = |sigma_n - sigma_S|`, `delta_S` the std (ddof 0) of the sector's vols
and `q` the strength, `v = q g / (delta_S + q g)` and `sigma_n' = v sigma_S + (1 - v) sigma_n`
(`v = 0` when the denominator is 0); `specific_variance` runs both, floors the vol and drops
names without enough history or a sector (never a guess).

Units: residuals are daily simple returns; variances are daily return**2, vols daily.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from atlas.core.errors import DataError
from atlas.core.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class SpecificResult:
    """`variance` (daily, after shrinkage and floor), `raw_variance` (EWMA only), `shrinkage`
    (`v` per name), `n_obs` (residuals used), `floored` (names raised to the vol floor) and
    `dropped` (name -> reason: `history` or `sector`). All Series are indexed by the kept
    names."""

    variance: pd.Series
    raw_variance: pd.Series
    shrinkage: pd.Series
    n_obs: pd.Series
    floored: tuple[str, ...]
    dropped: dict[str, str]


def ewma_specific_variance(
    residuals: pd.DataFrame, half_life: float
) -> tuple[pd.Series, pd.Series]:
    """EWMA of squared `residuals` (sessions ascending, one column per name, NaN = no residual
    that session): weights `0.5 ** ((T - 1 - t) / half_life)` over the observed sessions.
    Returns `(variance, n_obs)`; a name without residuals has NaN variance and 0 obs."""
    if not half_life > 0:
        raise ValueError(f"half_life must be positive, got {half_life}")
    values = residuals.to_numpy(dtype="float64")
    if np.isinf(values).any():
        raise ValueError("residuals must be finite or NaN")
    seen = ~np.isnan(values)
    squares = np.where(seen, values, 0.0) ** 2
    t = len(values)
    w = 0.5 ** ((t - 1 - np.arange(t)) / half_life)
    num = w @ squares
    den = w @ seen
    with np.errstate(invalid="ignore", divide="ignore"):
        var = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)
    return (
        pd.Series(var, index=residuals.columns, name="specific_variance"),
        pd.Series(seen.sum(axis=0), index=residuals.columns, name="n_obs"),
    )


def shrink_to_sector_median(
    vol: pd.Series, sectors: pd.Series, q: float
) -> tuple[pd.Series, pd.Series]:
    """`vol` (daily, positive) shrunk towards the median of its sector with strength `q`
    (0 = none). Returns `(shrunk_vol, v)`. A single-name sector, or a name at the median, has
    `v = 0`."""
    if not (np.isfinite(q) and q >= 0):
        raise ValueError(f"q must be a finite non-negative number, got {q}")
    if not ((vol > 0) & np.isfinite(vol)).all():
        raise ValueError("vols must be positive and finite")
    missing = vol.index.difference(sectors.index)
    if len(missing):
        raise ValueError(f"sector missing for {list(missing)[:5]}")
    labels = sectors.reindex(vol.index)
    median = vol.groupby(labels).transform("median")
    delta = vol.groupby(labels).transform(lambda s: float(np.std(s.to_numpy())))
    gap = (vol - median).abs()
    denom = delta + q * gap
    with np.errstate(invalid="ignore", divide="ignore"):
        v = pd.Series(
            np.where(denom > 0, q * gap / denom.where(denom > 0, 1.0), 0.0), index=vol.index
        )
    shrunk = v * median + (1.0 - v) * vol
    return shrunk.rename("specific_vol"), v.rename("shrinkage")


def specific_variance(
    residuals: pd.DataFrame,
    sectors: pd.Series,
    *,
    half_life: float,
    q: float,
    floor_vol: float,
    min_history: int,
) -> SpecificResult:
    """Specific variance per name from `residuals`: EWMA, shrink the vol to the sector median,
    floor at `floor_vol`. Names with fewer than `min_history` residuals (`history`) or no entry
    in `sectors` (`sector`) are dropped and reported; nothing left -> `DataError`."""
    var, n_obs = ewma_specific_variance(residuals, half_life)
    dropped: dict[str, str] = {}
    for name in residuals.columns:
        if name not in sectors.index or pd.isna(sectors[name]):
            dropped[name] = "sector"
        elif n_obs[name] < min_history:
            dropped[name] = "history"
    keep = [name for name in residuals.columns if name not in dropped]
    if not keep:
        sample = dict(list(dropped.items())[:5])
        raise DataError(f"no name has {min_history} residuals (history) and a sector: {sample}")
    var, n_obs = var[keep], n_obs[keep]
    vol = np.sqrt(var)
    positive = vol > 0
    shrunk = vol.copy()
    v = pd.Series(0.0, index=keep, name="shrinkage")
    if positive.any():
        s, vv = shrink_to_sector_median(vol[positive], sectors, q)
        shrunk[positive], v[positive] = s, vv
    below = shrunk < floor_vol
    floored = tuple(shrunk.index[below])
    if floored:
        _log.warning("risk_model.specific_floor", n=len(floored), floor_vol=floor_vol)
    final = shrunk.clip(lower=floor_vol)
    return SpecificResult(
        variance=(final**2).rename("specific_variance"),
        raw_variance=var,
        shrinkage=v,
        n_obs=n_obs,
        floored=floored,
        dropped=dropped,
    )
