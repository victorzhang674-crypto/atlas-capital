"""Shared input handling of `atlas.quant.metrics` (Stage 20).

Units: returns are simple per-session (daily) returns as fractions (0.01 = 1%); a P&L series
is in currency. `periods` is the number of sessions a year (`quant.features.sessions_per_year`,
252). Rates named `_annual` are annual fractions; everything else is per session.
"""

import functools
import math
from collections.abc import Hashable, Sequence

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError

ReturnsLike = pd.Series | np.ndarray | Sequence[float]
"""A 1-D series of per-session values: a `pd.Series` indexed by session (ascending) or an
array / sequence (positional)."""

RiskFree = float | pd.Series | None
"""A per-session (daily) risk-free rate: a scalar, a Series by session, or `None` for the
default `quant.metrics.risk_free_annual` converted to a daily rate."""


@functools.cache
def default_config() -> AtlasConfig:
    """The code-default configuration, built once (`AtlasConfig` is immutable)."""
    return AtlasConfig()


def config_or_default(config: AtlasConfig | None) -> AtlasConfig:
    """`config`, or the code defaults when it is `None`."""
    return config if config is not None else default_config()


def periods_per_year(periods: int | None, config: AtlasConfig | None = None) -> int:
    """Sessions a year: `periods` if given (a positive int), else
    `quant.features.sessions_per_year`."""
    if periods is None:
        return config_or_default(config).quant.features.sessions_per_year
    if isinstance(periods, bool) or not isinstance(periods, int) or periods < 1:
        raise ValueError(f"periods must be a positive int (sessions a year), got {periods!r}")
    return periods


def annual_to_daily(rate_annual: float, periods: int) -> float:
    """An annual rate (fraction) as the per-session rate that compounds to it over `periods`
    sessions: `(1 + R)^(1/P) - 1`."""
    if not math.isfinite(rate_annual) or rate_annual <= -1.0:
        raise ValueError(f"an annual rate must be finite and > -1, got {rate_annual!r}")
    return float((1.0 + rate_annual) ** (1.0 / periods) - 1.0)


def as_series(
    values: ReturnsLike, name: str = "returns", *, pnl: bool = False, min_obs: int = 1
) -> pd.Series:
    """`values` as a float Series with missing values (NaN) dropped.

    A Series must have a unique, ascending index (sessions); an array or sequence gets a
    positional index. Infinite values raise `ValueError`; so does a return below -1 (more than
    -100%) unless `pnl` (a P&L series in currency). Fewer than `min_obs` values left after
    dropping NaN raise `DataError` (no number without data)."""
    if isinstance(values, pd.DataFrame):
        raise ValueError(f"{name} must be one series, got a DataFrame")
    if isinstance(values, pd.Series):
        series = values
        if series.index.has_duplicates:
            raise ValueError(f"{name} has duplicate index labels")
        if not series.index.is_monotonic_increasing:
            raise ValueError(f"{name} must be sorted by session (ascending index)")
    else:
        array = np.asarray(values)
        if array.ndim != 1:
            raise ValueError(f"{name} must be one-dimensional, got shape {array.shape}")
        series = pd.Series(array)
    try:
        out = series.astype("float64")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric: {exc}") from exc
    out = out.dropna()
    data = out.to_numpy()
    if np.isinf(data).any():
        raise ValueError(f"{name} contains infinite values")
    if not pnl and (data < -1.0).any():
        raise ValueError(f"{name} has a return below -1 (more than -100%): {data.min()!r}")
    if len(out) < min_obs:
        raise DataError(f"{name} needs at least {min_obs} values, got {len(out)}")
    return out.rename(name)


def risk_free_daily(
    risk_free: RiskFree,
    index: pd.Index,
    periods: int,
    config: AtlasConfig | None = None,
) -> np.ndarray:
    """The per-session risk-free rate on `index` as an array. `None` → the annual default
    `quant.metrics.risk_free_annual` converted with `annual_to_daily`; a scalar is already a
    per-session rate; a Series is aligned on `index` and must cover every session
    (`DataError` otherwise: no excess return without a rate)."""
    if risk_free is None:
        annual = config_or_default(config).quant.metrics.risk_free_annual
        return np.full(len(index), annual_to_daily(annual, periods))
    if isinstance(risk_free, pd.Series):
        aligned = risk_free.astype("float64").reindex(index)
        missing = aligned.isna()
        if missing.any():
            first = list(index[missing.to_numpy()])[:5]
            raise DataError(f"no risk-free rate for sessions {first}")
        values = aligned.to_numpy()
        if np.isinf(values).any():
            raise ValueError("risk_free contains infinite values")
        return values
    if isinstance(risk_free, bool) or not isinstance(risk_free, int | float):
        raise ValueError(f"risk_free must be a number, a Series or None, got {risk_free!r}")
    rate = float(risk_free)
    if not math.isfinite(rate) or rate <= -1.0:
        raise ValueError(f"risk_free must be finite and > -1, got {rate!r}")
    return np.full(len(index), rate)


def align(
    returns: ReturnsLike, benchmark: ReturnsLike, *, min_obs: int = 2
) -> tuple[pd.Series, pd.Series]:
    """`returns` and `benchmark` on the sessions where both have a value (inner join). Both
    must be Series when either is (aligned by index); arrays must have equal lengths. Fewer
    than `min_obs` common sessions → `DataError`."""
    r = as_series(returns, "returns")
    b = as_series(benchmark, "benchmark")
    both_series = isinstance(returns, pd.Series) and isinstance(benchmark, pd.Series)
    if not both_series and isinstance(returns, pd.Series) != isinstance(benchmark, pd.Series):
        raise ValueError("returns and benchmark must both be Series (aligned by session) or arrays")
    if not both_series and len(np.asarray(returns)) != len(np.asarray(benchmark)):
        raise ValueError("returns and benchmark arrays must have the same length")
    common = r.index.intersection(b.index, sort=False)
    common = common.sort_values()
    if len(common) < min_obs:
        raise DataError(f"returns and benchmark need {min_obs} common sessions, got {len(common)}")
    return r.loc[common], b.loc[common]


_CONSTANT_RTOL = 1e-12  # spread below this share of the largest |value| is rounding noise


def is_constant(values: np.ndarray) -> bool:
    """True when the values are equal up to floating-point rounding (or there are none): their
    range is at most 1e-12 x the largest absolute value. A variance test is not enough:
    `x - mean(x)` of 100 x 0.001 leaves ~1e-19, and `(b + 0.001) - b` is not exactly 0.001,
    which would turn an undefined ratio (Sharpe, beta, IR) into a number like 1e16."""
    if values.size == 0:
        return True
    spread = float(np.ptp(values))
    return spread <= _CONSTANT_RTOL * float(np.max(np.abs(values)))


def sample_std(values: np.ndarray) -> float:
    """Sample standard deviation (ddof 1) in the units of `values`; exactly 0 for a constant
    series (see `is_constant`)."""
    if is_constant(values):
        return 0.0
    return float(np.std(values, ddof=1))


def nan_if_zero(numerator: float, denominator: float) -> float:
    """`numerator / denominator`, NaN when the denominator is 0 (an undefined ratio, never
    ±inf)."""
    if denominator == 0.0 or not math.isfinite(denominator):
        return math.nan
    return float(numerator / denominator)


def window_length(window: int | None, config: AtlasConfig | None = None) -> int:
    """A rolling window in sessions: `window` (an int >= 2) or `quant.metrics.rolling_window`."""
    w = config_or_default(config).quant.metrics.rolling_window if window is None else window
    if isinstance(w, bool) or not isinstance(w, int) or w < 2:
        raise ValueError(f"window must be an int >= 2 (sessions), got {w!r}")
    return w


def as_weights(weights: pd.Series, name: str = "weights") -> pd.Series:
    """`weights` (fractions of NAV, or currency values, by symbol) as a float Series: unique
    index, every value finite (`ValueError` otherwise)."""
    if not isinstance(weights, pd.Series):
        raise ValueError(f"{name} must be a pandas Series indexed by symbol")
    if weights.index.has_duplicates:
        raise ValueError(f"duplicate symbols in {name}")
    try:
        out = weights.astype("float64")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric: {exc}") from exc
    if not np.isfinite(out.to_numpy()).all():
        raise ValueError(f"{name} must be finite")
    return out


_SYMMETRY_RTOL = 1e-8
_PSD_RTOL = 1e-10


def as_covariance(cov: pd.DataFrame | np.ndarray) -> tuple[np.ndarray, list[Hashable]]:
    """A covariance as `(matrix, names)`: a DataFrame must have the same labels on both axes
    (names = its index), an array is square (names = positions 0..N-1). The matrix must be
    finite, symmetric (relative tolerance 1e-8 of its largest entry) and positive
    semi-definite (smallest eigenvalue >= -1e-10 x the largest); it is returned symmetrised.
    Daily units (return^2 per session)."""
    if isinstance(cov, pd.DataFrame):
        if cov.index.has_duplicates or list(cov.index) != list(cov.columns):
            raise ValueError("cov must have the same unique labels on its index and columns")
        names: list[Hashable] = list(cov.index)
        matrix = cov.to_numpy(dtype="float64")
    elif isinstance(cov, np.ndarray):
        matrix = np.asarray(cov, dtype="float64")
        if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
            raise ValueError(f"cov must be a square matrix, got shape {matrix.shape}")
        names = list(range(matrix.shape[0]))
    else:
        raise ValueError("cov must be a DataFrame (names x names) or a square ndarray")
    if matrix.shape[0] == 0:
        raise ValueError("cov is empty")
    if not np.isfinite(matrix).all():
        raise ValueError("cov must be finite")
    scale = float(np.abs(matrix).max())
    if not np.allclose(matrix, matrix.T, rtol=0.0, atol=_SYMMETRY_RTOL * max(scale, 1e-300)):
        raise ValueError("cov must be symmetric")
    matrix = (matrix + matrix.T) / 2.0
    eig = np.linalg.eigvalsh(matrix)
    if eig[0] < -_PSD_RTOL * max(float(eig[-1]), 0.0):
        raise ValueError(f"cov is not positive semi-definite (smallest eigenvalue {eig[0]:.3e})")
    return matrix, names


def weights_on(
    names: list[Hashable],
    weights: pd.Series | np.ndarray,
    *,
    name: str = "weights",
    strict: bool = True,
) -> np.ndarray:
    """`weights` as a vector over `names`. A Series is aligned by label (absent names → 0); with
    `strict`, a nonzero value on a name outside `names` raises `DataError` (no risk without an
    estimate), otherwise such values are ignored. An array must have one finite value per name.
    """
    if isinstance(weights, pd.Series):
        w = as_weights(weights, name)
        known = set(names)
        unknown = [str(s) for s, v in w.items() if s not in known and v != 0.0]
        if unknown and strict:
            raise DataError(f"{name}: nonzero values on names without an estimate: {unknown[:10]}")
        return w.reindex(names).fillna(0.0).to_numpy(dtype="float64")
    array = np.asarray(weights, dtype="float64")
    if array.shape != (len(names),):
        raise ValueError(f"{name} must have one value per name ({len(names)}), got {array.shape}")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} must be finite")
    return array
