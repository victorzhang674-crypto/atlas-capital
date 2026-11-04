"""Cross-sectional cleaning of raw factor values into exposures (Stage 16).

All functions are pure and work column by column on a frame indexed by symbol (one row per
name of the cross-section, one column per factor). NaN means unknown and stays NaN unless a
step says otherwise. The pipeline (`clean_factors`):

1. a factor with fewer than `min_names` valid raw values is left all NaN;
2. `winsorize` at the cross-sectional `winsor_lower`/`winsor_upper` quantiles;
3. `zscore` (population std, ddof 0);
4. `fill_missing`: NaN → the median z-score of the name's GICS sector (0 when the sector has
   none), recorded in a boolean `filled` frame;
5. `neutralize`: demean within sector, or with `size_neutral` take the OLS residual on sector
   dummies plus the size exposure (the `size` factor itself is only sector-demeaned);
6. `zscore` again over the names with a known sector, so those exposures have mean 0 and
   std 1 and sum to 0 within every sector.

Names with no sector (`None`) are never grouped with each other: they are filled with the
median of the whole cross-section and neutralised against it (market demeaning, or the
residual of a market-wide fit on [1, size]), then scored on the same scale. On the real lake
every former S&P 500 member lacks a sector (the instruments table holds today's reference
data), so a shared "unknown" group would compare future index leavers with each other:
information from after the as-of (Stage 16 audit). Exposures are in standard deviations.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from atlas.core.config import FactorsConfig
from atlas.core.logging import get_logger

SIZE_FACTOR = "size"
"""The factor used (and never neutralised) by size neutralisation."""

_log = get_logger(__name__)


def _labels(sectors: pd.Series, index: pd.Index) -> np.ndarray:
    """Sector labels aligned to `index` (object array, `None` where unknown)."""
    missing = index.difference(sectors.index)
    if len(missing):
        raise ValueError(f"sectors lack {len(missing)} symbol(s), e.g. {list(missing[:3])}")
    labels = sectors.reindex(index).astype("object")
    return labels.where(labels.notna(), None).to_numpy(dtype="object")


def _known(labels: np.ndarray) -> np.ndarray:
    return np.array([label is not None for label in labels], dtype=bool)


def winsorize(frame: pd.DataFrame, lower: float, upper: float) -> pd.DataFrame:
    """Each column clipped to its own `lower`/`upper` quantiles (fractions in [0, 1], linear
    interpolation over the valid values); NaN stays NaN."""
    if not 0 <= lower < upper <= 1:
        raise ValueError(f"need 0 <= lower < upper <= 1, got lower={lower}, upper={upper}")
    out = frame.astype("float64").copy()
    for col in out.columns:
        x = out[col]
        if x.notna().any():
            out[col] = x.clip(lower=float(x.quantile(lower)), upper=float(x.quantile(upper)))
    return out


def zscore(frame: pd.DataFrame, basis: np.ndarray | None = None) -> pd.DataFrame:
    """Each column minus its mean, over its population std (ddof 0), both taken over the valid
    values of the rows in `basis` (a boolean mask; default every row, and every row when no
    basis row is valid). A column with no dispersion (constant, or a single value) becomes 0
    where valid; NaN stays NaN."""
    out = frame.astype("float64").copy()
    rows = np.ones(len(out), dtype=bool) if basis is None else np.asarray(basis, dtype=bool)
    for col in out.columns:
        x = out[col]
        valid = x.notna().to_numpy()
        if not valid.any():
            continue
        ref = x[valid & rows] if (valid & rows).any() else x[valid]
        std = float(ref.std(ddof=0))
        centred = x - float(ref.mean())
        out[col] = centred / std if std > 0 else centred.where(~valid, 0.0)
    return out


def fill_missing(frame: pd.DataFrame, sectors: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`frame` with each NaN replaced by the median of its column within the name's sector
    (0.0 when the sector has no value: the cross-sectional mean of a z-score) or, for a name
    with no sector, by the median of the whole column; and a boolean frame that is `True`
    where a value was filled. A column with no valid value is left all NaN and not flagged."""
    labels = _labels(sectors, frame.index)
    known = _known(labels)
    out = frame.astype("float64").copy()
    flags = pd.DataFrame(False, index=frame.index, columns=frame.columns)
    for col in out.columns:
        x = out[col]
        missing = x.isna()
        if not missing.any() or missing.all():
            continue
        fill = pd.Series(float(x.median()), index=x.index)
        if known.any():
            sector = x[known].groupby(labels[known]).transform("median").fillna(0.0)
            fill[known] = sector.to_numpy()
        out[col] = x.where(~missing, fill)
        flags[col] = missing.to_numpy()
    return out, flags.astype("bool")


def _residual(y: np.ndarray, design: np.ndarray) -> np.ndarray:
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    return y - design @ coef


def neutralize(
    frame: pd.DataFrame,
    sectors: pd.Series,
    *,
    size: pd.Series | None = None,
    exclude: Sequence[str] = (),
) -> pd.DataFrame:
    """Each column with its sector means removed (demeaned within sector), or, when `size`
    is given, the residual of an OLS fit on sector dummies plus `size` (columns in `exclude`
    are only demeaned). Either way the names with a known sector sum to 0 within every
    sector; with `size` they are also orthogonal to it. A name with no sector is neutralised
    against the whole cross-section instead: minus the mean of every valid value, or the
    residual of a fit on [1, size] over every valid name. NaN rows stay NaN and are left out
    of the fits; `size` must be known wherever a fitted column is (`ValueError` otherwise)."""
    labels = _labels(sectors, frame.index)
    known = _known(labels)
    out = frame.astype("float64").copy()
    for col in out.columns:
        x = out[col].to_numpy(dtype="float64")
        valid = ~np.isnan(x)
        if not valid.any():
            continue
        result = np.full(len(x), np.nan)
        in_sector, no_sector = valid & known, valid & ~known
        if size is None or col in exclude:
            if in_sector.any():
                demeaned = pd.Series(x[in_sector]).groupby(labels[in_sector]).transform("mean")
                result[in_sector] = x[in_sector] - demeaned.to_numpy()
            result[no_sector] = x[no_sector] - x[valid].mean()
        else:
            s = size.reindex(frame.index).to_numpy(dtype="float64")
            if np.isnan(s[valid]).any():
                raise ValueError(f"size is missing for names where {col!r} is known")
            if in_sector.any():
                dummies = pd.get_dummies(pd.Series(labels[in_sector]), dtype="float64")
                design = np.column_stack([dummies.to_numpy(), s[in_sector]])
                result[in_sector] = _residual(x[in_sector], design)
            if no_sector.any():
                market = np.column_stack([np.ones(int(valid.sum())), s[valid]])
                fitted = x[valid] - _residual(x[valid], market)
                result[no_sector] = x[no_sector] - fitted[no_sector[valid]]
        out[col] = result
    return out


@dataclass(frozen=True)
class CleanedFactors:
    """The result of `clean_factors`: `exposures` (z-scores, sector-neutral; index symbol,
    one column per factor) and `filled` (bool, `True` where a missing raw value was filled
    with its sector median)."""

    exposures: pd.DataFrame
    filled: pd.DataFrame


def clean_factors(raw: pd.DataFrame, sectors: pd.Series, config: FactorsConfig) -> CleanedFactors:
    """Run the cleaning pipeline (module docstring) on `raw` (index symbol, one column per
    factor) with each name's GICS sector in `sectors` (index symbol; `None` = unknown).
    Settings from `config` (`quant.factors`): winsor quantiles, `min_names`,
    `size_neutral`."""
    known = _known(_labels(sectors, raw.index))  # also fails early on a misaligned sector map
    frame = raw.astype("float64").copy()
    thin = [c for c in frame.columns if int(frame[c].notna().sum()) < config.min_names]
    if thin:
        _log.info("factors.too_few_names", factors=thin, min_names=config.min_names)
        frame[thin] = np.nan
    z = zscore(winsorize(frame, config.winsor_lower, config.winsor_upper))
    filled, flags = fill_missing(z, sectors)
    size = None
    if config.size_neutral:
        if SIZE_FACTOR in filled.columns and filled[SIZE_FACTOR].notna().any():
            size = filled[SIZE_FACTOR]
        else:
            _log.warning("factors.size_neutral_skipped", reason="no size exposure")
    neutral = neutralize(filled, sectors, size=size, exclude=(SIZE_FACTOR,))
    return CleanedFactors(exposures=zscore(neutral, basis=known), filled=flags)
