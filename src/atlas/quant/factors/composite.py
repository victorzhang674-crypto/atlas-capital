"""Composite factors: weighted combinations of exposures (Stage 16).

`composite(factors, weights)` is the generic builder (Stage 22 picks its own weights);
`style_composites(exposures)` gives one equal-weight composite per factor group (value,
quality, growth, leverage, size, issuance), the style exposures Stage 19 can use.
"""

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

from atlas.quant.factors.cleaning import zscore
from atlas.quant.factors.fundamental import FACTORS, GROUPS


def composite(
    factors: pd.DataFrame, weights: Mapping[str, float], *, standardize: bool = True
) -> pd.Series:
    """A weighted combination of the columns of `factors` (index symbol), named
    `composite`.

    For each row: sum(w_i * x_i) / sum(|w_i|) over the factors with a value in that row (a
    missing factor's weight is left out rather than counted as 0); NaN when none has one.
    With `standardize` the result is z-scored across rows (mean 0, population std 1).
    `weights` must name known columns, be finite and not all zero (`ValueError`).
    """
    if not weights:
        raise ValueError("weights must name at least one factor")
    unknown = [name for name in weights if name not in factors.columns]
    if unknown:
        raise ValueError(f"unknown factor(s) {unknown}; columns: {list(factors.columns)}")
    if not all(math.isfinite(w) for w in weights.values()):
        raise ValueError(f"weights must be finite, got {dict(weights)}")
    if all(w == 0 for w in weights.values()):
        raise ValueError("weights must not all be zero")
    used = {name: float(w) for name, w in weights.items() if w != 0}
    values = factors[list(used)].astype("float64").to_numpy()
    w = np.array(list(used.values()))
    known = ~np.isnan(values)
    num = np.where(known, values, 0.0) @ w
    den = known.astype("float64") @ np.abs(w)
    with np.errstate(invalid="ignore", divide="ignore"):
        combined = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)
    out = pd.Series(combined, index=factors.index, name="composite", dtype="float64")
    if standardize:
        out = zscore(out.to_frame())["composite"]
    return out


def style_composites(exposures: pd.DataFrame) -> pd.DataFrame:
    """One equal-weight, standardised `composite` per factor group (columns `GROUPS`) of the
    factors of that group present in `exposures`; a group with none present is all NaN."""
    out = {}
    for group in GROUPS:
        members = [f.name for f in FACTORS if f.group == group and f.name in exposures.columns]
        if members:
            out[group] = composite(exposures, dict.fromkeys(members, 1.0))
        else:
            out[group] = pd.Series(np.nan, index=exposures.index, dtype="float64")
    return pd.DataFrame(out, index=exposures.index)
