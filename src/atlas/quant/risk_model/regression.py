"""One daily cross-sectional regression of the factor risk model (Stage 19).

`constrained_wls` fits `r = X f + e` by weighted least squares subject to one linear
constraint `c . f = 0` (for the risk model: the cap-weighted sector returns sum to zero, which
makes the design identifiable and lets the market factor play the role of the cap-weighted
market return; with `sqrt(cap)` regression weights it equals that return only approximately,
exactly only when the regression weights are proportional to cap). The constraint is solved
exactly by the null-space method: `f = R g`, where `R` (K x K-1) has the identity in every
column but the pivot row, which is `-c_i / c_pivot`; then an ordinary weighted least squares on
`X R`. A factor whose exposure column is all zero in the cross-section (a sector without names
that day) is left out and reported as NaN.

Units: `returns` simple returns of one session, `exposures` dimensionless, `weights` the
regression weights on squared residuals (the risk model passes `sqrt(cap)`); factor returns and
residuals are simple returns (fractions).
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from atlas.core.errors import DataError


@dataclass(frozen=True)
class RegressionResult:
    """`factor_returns` (index = factor names, NaN for a factor without exposure that day),
    `residuals` (index = names), weighted `r2`, `n_names` and the `rank` of the constrained
    design (number of free parameters estimated)."""

    factor_returns: pd.Series
    residuals: pd.Series
    r2: float
    n_names: int
    rank: int


def sector_constraint(
    exposures: pd.DataFrame, caps: pd.Series, sector_factors: Sequence[str]
) -> pd.Series:
    """The constraint coefficients over the columns of `exposures`: for each sector factor
    the share of `caps` held by the names exposed to it (cap-weighted sector weights summing
    to 1 over the non-empty sectors), 0 for every other factor."""
    if not exposures.index.equals(caps.index):
        raise ValueError("exposures and caps are not aligned on the same names")
    total = float(caps.sum())
    if not total > 0:
        raise ValueError("caps must have a positive sum")
    out = pd.Series(0.0, index=exposures.columns, name="constraint")
    for sector in sector_factors:
        if sector in exposures.columns:
            out[sector] = float(caps[exposures[sector] != 0].sum()) / total
    return out


def constrained_wls(
    returns: pd.Series, exposures: pd.DataFrame, weights: pd.Series, constraint: pd.Series
) -> RegressionResult:
    """WLS of `returns` on `exposures` (weights on squared residuals) subject to
    `constraint . f = 0`.

    Raises `ValueError` for misaligned or non-finite input or non-positive weights, and
    `DataError` when the cross-section has too few names for the number of free factors or
    the constrained design is rank-deficient (never a guessed fit).
    """
    if not (returns.index.equals(exposures.index) and weights.index.equals(exposures.index)):
        raise ValueError("returns, exposures and weights are not aligned on the same names")
    if not constraint.index.equals(exposures.columns):
        raise ValueError("constraint must be indexed by the exposure columns (factors)")
    y = returns.to_numpy(dtype="float64")
    x = exposures.to_numpy(dtype="float64")
    w = weights.to_numpy(dtype="float64")
    c = constraint.to_numpy(dtype="float64")
    if not (np.isfinite(y).all() and np.isfinite(x).all() and np.isfinite(c).all()):
        raise ValueError("returns, exposures and constraint must be finite")
    if not (np.isfinite(w).all() and (w > 0).all()):
        raise ValueError("weights must be positive and finite")
    active = np.abs(x).sum(axis=0) > 0
    k = int(active.sum())
    n = len(y)
    if n <= k:
        raise DataError(f"{n} names cannot fit {k} factors: too few names in the cross-section")
    xa, ca = x[:, active], c[active]
    if np.any(ca != 0):
        pivot = int(np.argmax(np.abs(ca)))
        basis = np.eye(k)
        basis[pivot, :] = -ca / ca[pivot]
        basis[pivot, pivot] = 0.0
        basis = np.delete(basis, pivot, axis=1)  # k x (k-1)
    else:
        basis = np.eye(k)
    root = np.sqrt(w)
    design = (xa @ basis) * root[:, None]
    g, _, rank, _ = np.linalg.lstsq(design, y * root, rcond=None)
    if rank < basis.shape[1]:
        raise DataError(
            f"the constrained design is rank-deficient (rank {rank} < {basis.shape[1]}): "
            "collinear exposures in the cross-section"
        )
    f = np.full(len(c), np.nan)
    f[active] = basis @ g
    resid = y - xa @ (basis @ g)
    centre = float(w @ y / w.sum())
    total = float(w @ (y - centre) ** 2)
    r2 = 1.0 - float(w @ resid**2) / total if total > 0 else 0.0
    return RegressionResult(
        factor_returns=pd.Series(f, index=exposures.columns, name="factor_returns"),
        residuals=pd.Series(resid, index=exposures.index, name="residual"),
        r2=r2,
        n_names=n,
        rank=int(rank),
    )
