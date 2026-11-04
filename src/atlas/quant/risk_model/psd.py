"""Positive semi-definite repair and conditioning of covariance matrices (Stage 18).

- `nearest_psd(matrix)`: Higham (1988), "Computing a nearest symmetric positive semidefinite
  matrix": the nearest symmetric PSD matrix in Frobenius norm is the symmetric part with its
  negative eigenvalues set to zero. With `keep_diagonal=True` the variances are kept: the
  matrix is scaled to a correlation matrix, replaced by its nearest correlation matrix
  (`nearest_correlation`) and scaled back.
- `nearest_correlation(matrix)`: Higham (2002), "Computing the nearest correlation matrix — a
  problem from finance": alternating projections onto the PSD matrices and the unit-diagonal
  matrices with Dykstra's correction. The result is the PSD projection of the last iterate
  rescaled to a unit diagonal (a congruence, so it stays PSD and its diagonal is exactly 1).
- `floor_condition(matrix, max_condition)`: eigenvalues below `λmax / max_condition` are
  raised to it, so the condition number is at most `max_condition`.

Matrices are numpy arrays or square DataFrames (labels are kept); entries must be finite.
"""

import numpy as np
import pandas as pd

from atlas.core.logging import get_logger

_log = get_logger(__name__)


def _as_array(matrix: np.ndarray | pd.DataFrame) -> np.ndarray:
    """`matrix` as a float array; `ValueError` unless square, non-empty and finite."""
    raw = matrix.to_numpy(dtype="float64") if isinstance(matrix, pd.DataFrame) else matrix
    arr = np.asarray(raw, dtype="float64")
    if arr.ndim != 2 or arr.shape[0] != arr.shape[1] or arr.shape[0] == 0:
        raise ValueError(f"expected a non-empty square matrix, got shape {arr.shape}")
    if not np.isfinite(arr).all():
        raise ValueError("matrix has NaN or infinite entries")
    return arr


def _like[M: (np.ndarray, pd.DataFrame)](values: np.ndarray, template: M) -> M:
    """`values` with the labels of `template` when it is a DataFrame."""
    if isinstance(template, pd.DataFrame):
        return pd.DataFrame(values, index=template.index, columns=template.columns)
    return values


def _sym(a: np.ndarray) -> np.ndarray:
    return (a + a.T) / 2.0


def _clip(a: np.ndarray, floor: float) -> np.ndarray:
    """Symmetric `a` with eigenvalues below `floor` raised to `floor`."""
    w, v = np.linalg.eigh(a)
    return _sym((v * np.maximum(w, floor)) @ v.T)


def is_psd(matrix: np.ndarray | pd.DataFrame, *, tol: float = 1e-12) -> bool:
    """True when the symmetric part of `matrix` has no eigenvalue below `-tol · max|λ|`
    (rounding below zero is tolerated)."""
    eig = np.linalg.eigvalsh(_sym(_as_array(matrix)))
    scale = float(np.abs(eig).max())
    return bool(eig.min() >= -tol * scale)


def condition_number(matrix: np.ndarray | pd.DataFrame) -> float:
    """`λmax / λmin` of the symmetric part of `matrix`; `inf` when it is not positive definite
    (numerically: `λmin <= n · eps · |λmax|`)."""
    arr = _sym(_as_array(matrix))
    eig = np.linalg.eigvalsh(arr)
    top = float(np.abs(eig).max())
    if eig.min() <= arr.shape[0] * np.finfo("float64").eps * top:
        return float("inf")
    return float(eig.max() / eig.min())


def nearest_correlation[M: (np.ndarray, pd.DataFrame)](
    matrix: M, *, max_iter: int = 200, tol: float = 1e-9
) -> M:
    """The nearest correlation matrix (PSD, unit diagonal) to the symmetric part of `matrix`
    in Frobenius norm (Higham 2002, alternating projections with Dykstra's correction).

    Stops when the relative changes of both iterates and their distance are below `tol`; after
    `max_iter` iterations the result is still PSD with a unit diagonal (a warning is logged).
    """
    a = _sym(_as_array(matrix))
    y = a.copy()
    correction = np.zeros_like(a)
    x = y
    converged = False
    for _ in range(max_iter):
        r = y - correction
        x = _clip(r, 0.0)
        correction = x - r
        y_new = x.copy()
        np.fill_diagonal(y_new, 1.0)
        size = max(float(np.linalg.norm(y_new)), np.finfo("float64").tiny)
        change = max(
            float(np.linalg.norm(y_new - y)) / size, float(np.linalg.norm(x - y_new)) / size
        )
        y = y_new
        if change < tol:
            converged = True
            break
    if not converged:
        _log.warning("nearest_correlation_not_converged", max_iter=max_iter, tol=tol)
    d = np.sqrt(np.clip(np.diag(x), 0.0, None))
    dead = d < 1e-8  # a zero row of x: keep it zero, the unit diagonal keeps the result PSD
    d[dead] = 1.0
    out = x / np.outer(d, d)
    out[dead, :] = 0.0
    out[:, dead] = 0.0
    out = _sym(out)
    np.fill_diagonal(out, 1.0)
    return _like(out, matrix)


def nearest_psd[M: (np.ndarray, pd.DataFrame)](
    matrix: M, *, keep_diagonal: bool = False, max_iter: int = 200, tol: float = 1e-9
) -> M:
    """The nearest symmetric PSD matrix to `matrix` (Higham 1988: eigenvalues clipped at 0).

    With `keep_diagonal=True` the diagonal (the variances) is kept: the nearest correlation
    matrix (Higham 2002) of `D^-1/2 A D^-1/2`, scaled back by `D^1/2`; the diagonal must be
    positive (`ValueError`).
    """
    a = _sym(_as_array(matrix))
    if not keep_diagonal:
        return _like(_clip(a, 0.0), matrix)
    diag = np.diag(a)
    if not (diag > 0).all():
        raise ValueError("keep_diagonal needs a positive diagonal (variances)")
    s = np.sqrt(diag)
    corr = nearest_correlation(a / np.outer(s, s), max_iter=max_iter, tol=tol)
    out = _sym(corr * np.outer(s, s))
    np.fill_diagonal(out, diag)
    return _like(out, matrix)


def floor_condition[M: (np.ndarray, pd.DataFrame)](matrix: M, max_condition: float) -> M:
    """`matrix` (symmetrised) with eigenvalues below `λmax / max_condition` raised to it, so its
    condition number is at most `max_condition`; unchanged when it already is. `ValueError`
    when `max_condition <= 1` or the matrix has no positive eigenvalue."""
    if not (np.isfinite(max_condition) and max_condition > 1):
        raise ValueError(f"max_condition must be finite and > 1, got {max_condition}")
    a = _sym(_as_array(matrix))
    eig = np.linalg.eigvalsh(a)
    top = float(eig.max())
    if top <= 0:
        raise ValueError("matrix has no positive eigenvalue; it cannot be conditioned")
    floor = top / max_condition
    if eig.min() >= floor:
        return _like(a, matrix)
    return _like(_clip(a, floor), matrix)
