"""Benchmark-relative metrics (Stage 20).

Units: `returns` and `benchmark` are simple per-session (daily) returns, fractions, as Series
indexed by session (or 1-D arrays of equal length). They are compared on the sessions where
both have a value (inner join, NaN dropped); fewer than 2 common sessions raise `DataError`.
`periods` = sessions a year (default `quant.features.sessions_per_year`). `risk_free` is a
per-session rate (scalar or Series; `None` = `quant.metrics.risk_free_annual` converted).

Definitions (empyrical-compatible):

- `beta` = `cov(r - rf, b - rf) / var(b - rf)` (ddof 1); `alpha` (CAPM, annual fraction) =
  `(1 + mean((r - rf) - beta (b - rf)))^P - 1`.
- `tracking_error` = `std(r - b, ddof=1) * sqrt(P)` (annual fraction); `information_ratio` =
  `mean(r - b) * P / tracking_error` (unitless); `active_return` = CAGR(r) - CAGR(b) on the
  common sessions (annual fraction).
- `up_capture` / `down_capture` = CAGR of the portfolio over the sessions where the benchmark
  is > 0 / < 0, divided by the benchmark's CAGR over the same sessions (unitless);
  `up_down_capture` = up / down; `correlation` = Pearson.
- `rolling_beta`, `rolling_correlation`: the same over trailing windows of `window` common
  sessions (default `quant.metrics.rolling_window`), on the window's last session.

A ratio with a zero denominator (a constant benchmark, no up or down session) is NaN.
"""

import math
from typing import Any

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.quant.metrics._common import (
    ReturnsLike,
    RiskFree,
    align,
    nan_if_zero,
    periods_per_year,
    risk_free_daily,
    window_length,
)
from atlas.quant.metrics.performance import annualised_return


def _excess(
    returns: ReturnsLike,
    benchmark: ReturnsLike,
    risk_free: RiskFree,
    periods: int | None,
    config: AtlasConfig | None,
) -> tuple[np.ndarray, np.ndarray, int]:
    r, b = align(returns, benchmark)
    p = periods_per_year(periods, config)
    rf = risk_free_daily(risk_free, r.index, p, config)
    return r.to_numpy() - rf, b.to_numpy() - rf, p


def _beta(re: np.ndarray, be: np.ndarray) -> float:
    var_b = float(np.var(be, ddof=1))
    cov = float(np.sum((re - re.mean()) * (be - be.mean())) / (len(re) - 1))
    return nan_if_zero(cov, var_b)


def beta(
    returns: ReturnsLike,
    benchmark: ReturnsLike,
    *,
    risk_free: RiskFree = None,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> float:
    """CAPM beta of the excess returns on the benchmark's excess returns (unitless); a
    constant benchmark → NaN."""
    re, be, _ = _excess(returns, benchmark, risk_free, periods, config)
    return _beta(re, be)


def alpha(
    returns: ReturnsLike,
    benchmark: ReturnsLike,
    *,
    risk_free: RiskFree = None,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> float:
    """CAPM alpha, annualised geometrically: `(1 + mean(r_e - beta b_e))^P - 1` (annual
    fraction); NaN when beta is undefined."""
    re, be, p = _excess(returns, benchmark, risk_free, periods, config)
    b = _beta(re, be)
    if math.isnan(b):
        return math.nan
    daily = float(np.mean(re - b * be))
    return float((1.0 + daily) ** p - 1.0)


def tracking_error(
    returns: ReturnsLike,
    benchmark: ReturnsLike,
    *,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> float:
    """Annualised tracking error, `std(r - b, ddof=1) * sqrt(P)` (annual fraction)."""
    r, b = align(returns, benchmark)
    active = r.to_numpy() - b.to_numpy()
    return float(active.std(ddof=1) * math.sqrt(periods_per_year(periods, config)))


def information_ratio(
    returns: ReturnsLike,
    benchmark: ReturnsLike,
    *,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> float:
    """Information ratio, `mean(r - b) * P / tracking_error` (unitless); zero tracking error
    → NaN."""
    r, b = align(returns, benchmark)
    p = periods_per_year(periods, config)
    active = r.to_numpy() - b.to_numpy()
    return nan_if_zero(float(active.mean()) * math.sqrt(p), float(active.std(ddof=1)))


def active_return(
    returns: ReturnsLike,
    benchmark: ReturnsLike,
    *,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> float:
    """CAGR of the returns minus CAGR of the benchmark on the common sessions (annual
    fraction)."""
    r, b = align(returns, benchmark)
    p = periods_per_year(periods, config)
    return annualised_return(r, periods=p) - annualised_return(b, periods=p)


def _capture(r: pd.Series, b: pd.Series, mask: np.ndarray, p: int) -> float:
    if not mask.any():
        return math.nan
    return nan_if_zero(annualised_return(r[mask], periods=p), annualised_return(b[mask], periods=p))


def up_capture(
    returns: ReturnsLike,
    benchmark: ReturnsLike,
    *,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> float:
    """Up-market capture: CAGR of the returns over the sessions with a positive benchmark
    return / the benchmark's CAGR over them (unitless); no such session → NaN."""
    r, b = align(returns, benchmark)
    return _capture(r, b, b.to_numpy() > 0.0, periods_per_year(periods, config))


def down_capture(
    returns: ReturnsLike,
    benchmark: ReturnsLike,
    *,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> float:
    """Down-market capture: as `up_capture` over the sessions with a negative benchmark return
    (unitless; below 1 = the portfolio lost less than the benchmark)."""
    r, b = align(returns, benchmark)
    return _capture(r, b, b.to_numpy() < 0.0, periods_per_year(periods, config))


def up_down_capture(
    returns: ReturnsLike,
    benchmark: ReturnsLike,
    *,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> float:
    """`up_capture / down_capture` (unitless); NaN when either is undefined or down is 0."""
    up = up_capture(returns, benchmark, periods=periods, config=config)
    down = down_capture(returns, benchmark, periods=periods, config=config)
    if math.isnan(up) or math.isnan(down):
        return math.nan
    return nan_if_zero(up, down)


def correlation(returns: ReturnsLike, benchmark: ReturnsLike) -> float:
    """Pearson correlation of the returns with the benchmark on the common sessions
    (unitless, in [-1, 1]); a constant series → NaN."""
    r, b = align(returns, benchmark)
    x, y = r.to_numpy(), b.to_numpy()
    dx, dy = x - x.mean(), y - y.mean()
    denom = math.sqrt(float(np.sum(dx**2)) * float(np.sum(dy**2)))
    value = nan_if_zero(float(np.sum(dx * dy)), denom)
    return value if math.isnan(value) else float(min(1.0, max(-1.0, value)))


def rolling_beta(
    returns: ReturnsLike,
    benchmark: ReturnsLike,
    window: int | None = None,
    *,
    risk_free: RiskFree = None,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> pd.Series:
    """`beta` over each trailing window of `window` common sessions (unitless), vectorised; a
    constant benchmark in a window → NaN."""
    r, b = align(returns, benchmark)
    w = window_length(window, config)
    p = periods_per_year(periods, config)
    rf = risk_free_daily(risk_free, r.index, p, config)
    re, be = r - rf, b - rf
    cov = re.rolling(w).cov(be, ddof=1)
    var = be.rolling(w).var(ddof=1)
    return (cov / var.where(var > 0.0)).rename("rolling_beta")


def rolling_correlation(
    returns: ReturnsLike,
    benchmark: ReturnsLike,
    window: int | None = None,
    *,
    config: AtlasConfig | None = None,
) -> pd.Series:
    """`correlation` over each trailing window of `window` common sessions (unitless)."""
    r, b = align(returns, benchmark)
    w = window_length(window, config)
    cov = r.rolling(w).cov(b, ddof=1)
    sd = r.rolling(w).std(ddof=1) * b.rolling(w).std(ddof=1)
    out = (cov / sd.where(sd > 0.0)).clip(-1.0, 1.0)
    return out.rename("rolling_correlation")


def relative_summary(
    returns: ReturnsLike,
    benchmark: ReturnsLike,
    *,
    risk_free: RiskFree = None,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> dict[str, Any]:
    """Every benchmark-relative metric in one dict (units as each function: annual fractions
    for alpha, tracking error and active return, unitless ratios otherwise): `n_common`,
    `beta`, `alpha`, `tracking_error`, `information_ratio`, `active_return`, `up_capture`,
    `down_capture`, `up_down_capture`, `correlation`."""
    r, b = align(returns, benchmark)
    p = periods_per_year(periods, config)
    kw: dict[str, Any] = {"periods": p}
    return {
        "n_common": len(r),
        "beta": beta(r, b, risk_free=risk_free, config=config, **kw),
        "alpha": alpha(r, b, risk_free=risk_free, config=config, **kw),
        "tracking_error": tracking_error(r, b, **kw),
        "information_ratio": information_ratio(r, b, **kw),
        "active_return": active_return(r, b, **kw),
        "up_capture": up_capture(r, b, **kw),
        "down_capture": down_capture(r, b, **kw),
        "up_down_capture": up_down_capture(r, b, **kw),
        "correlation": correlation(r, b),
    }
