"""Returns-based performance and risk metrics (Stage 20).

Units: `returns` are simple per-session (daily) returns, fractions (0.01 = 1%), a Series
indexed by session in ascending order (or a 1-D array); missing values (NaN) are dropped.
`periods` = sessions a year (default `quant.features.sessions_per_year`, 252). Names with
`annualised` are annual fractions; ratios are unitless; durations are counts of sessions.

Definitions (empyrical-compatible where empyrical has the metric):

- `cumulative_return` = `prod(1 + r) - 1`; `annualised_return` (CAGR) =
  `prod(1 + r)^(P/n) - 1`; `annualised_mean` = `mean(r) * P`; `annualised_vol` =
  `std(r, ddof=1) * sqrt(P)`.
- `sharpe_ratio` = `mean(r - rf) / std(r - rf, ddof=1) * sqrt(P)` with `rf` the per-session
  risk-free rate (default `quant.metrics.risk_free_annual`, converted geometrically).
- `downside_risk` = `sqrt(mean(min(r - req, 0)^2)) * sqrt(P)` (every session in the mean);
  `sortino_ratio` = `mean(r - req) * P / downside_risk`; `omega_ratio` =
  `sum((r - tau)+) / sum((tau - r)+)` with `tau` the per-session equivalent of the annual
  threshold (default `quant.metrics.omega_threshold_annual`).
- Drawdowns use the wealth index `W_0 = 1` (before the first return), `W_t = prod(1 + r)`;
  `drawdown_series` = `W_t / max(W_0..W_t) - 1` (<= 0); `max_drawdown` is the **positive**
  magnitude of its minimum (0.2 = -20%); `calmar_ratio` = CAGR / max drawdown;
  `ulcer_index` = `sqrt(mean(drawdown_t^2))`; `drawdown_periods` lists each episode and
  `max_drawdown_duration` is the longest one, in sessions from the peak to the recovery (or
  to the last session when not recovered).
- `skewness` / `excess_kurtosis` are population (biased) moments, as `scipy.stats.skew` /
  `kurtosis`; `hit_rate` = share of sessions with `r > 0`; `profit_factor` =
  `sum(r+) / sum(r-)`; `tail_ratio` = `|q(1 - t)| / |q(t)|` (numpy linear quantiles,
  `t = quant.metrics.tail_quantile`).
- Rolling versions: `rolling_metric(r, fn, window)` applies any scalar metric to each trailing
  window of `window` returns (default `quant.metrics.rolling_window`); the value sits on the
  window's last session (NaN before the first full window). `rolling_sharpe`, `rolling_vol`
  and `rolling_max_drawdown` are vectorised equivalents.

Fail safe: an empty input, or fewer than 2 returns where a standard deviation is needed, raise
`DataError`. A ratio with a zero denominator (no volatility, no drawdown, no losses) is
undefined and returned as NaN, never +-inf.
"""

import math
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.quant.metrics._common import (
    ReturnsLike,
    RiskFree,
    annual_to_daily,
    as_series,
    config_or_default,
    is_constant,
    nan_if_zero,
    periods_per_year,
    risk_free_daily,
    sample_std,
    window_length,
)

# --- level and dispersion ---------------------------------------------------------------------


def cumulative_return(returns: ReturnsLike) -> float:
    """Total compounded return, `prod(1 + r) - 1` (fraction)."""
    r = as_series(returns).to_numpy()
    return float(np.prod(1.0 + r) - 1.0)


def annualised_return(
    returns: ReturnsLike, *, periods: int | None = None, config: AtlasConfig | None = None
) -> float:
    """Compound annual growth rate, `prod(1 + r)^(P/n) - 1` (annual fraction)."""
    r = as_series(returns).to_numpy()
    p = periods_per_year(periods, config)
    growth = float(np.prod(1.0 + r))
    return float(growth ** (p / len(r)) - 1.0)


def annualised_mean(
    returns: ReturnsLike, *, periods: int | None = None, config: AtlasConfig | None = None
) -> float:
    """Arithmetic annualised return, `mean(r) * P` (annual fraction)."""
    r = as_series(returns).to_numpy()
    return float(r.mean() * periods_per_year(periods, config))


def annualised_vol(
    returns: ReturnsLike, *, periods: int | None = None, config: AtlasConfig | None = None
) -> float:
    """Annualised volatility, `std(r, ddof=1) * sqrt(P)` (annual fraction)."""
    r = as_series(returns, min_obs=2).to_numpy()
    return float(sample_std(r) * math.sqrt(periods_per_year(periods, config)))


# --- risk-adjusted ratios ---------------------------------------------------------------------


def sharpe_ratio(
    returns: ReturnsLike,
    *,
    risk_free: RiskFree = None,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> float:
    """Annualised Sharpe ratio, `mean(r - rf) / std(r - rf, ddof=1) * sqrt(P)` (unitless).
    `risk_free` is a per-session rate (scalar or Series by session); `None` uses
    `quant.metrics.risk_free_annual`. Zero volatility → NaN."""
    r = as_series(returns, min_obs=2)
    p = periods_per_year(periods, config)
    excess = r.to_numpy() - risk_free_daily(risk_free, r.index, p, config)
    return nan_if_zero(float(excess.mean()) * math.sqrt(p), sample_std(excess))


def downside_risk(
    returns: ReturnsLike,
    *,
    required_return: float = 0.0,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> float:
    """Annualised downside deviation below the per-session `required_return`:
    `sqrt(mean(min(r - req, 0)^2)) * sqrt(P)` (annual fraction)."""
    r = as_series(returns).to_numpy()
    shortfall = np.minimum(r - _finite("required_return", required_return), 0.0)
    return float(
        math.sqrt(float(np.mean(shortfall**2))) * math.sqrt(periods_per_year(periods, config))
    )


def sortino_ratio(
    returns: ReturnsLike,
    *,
    required_return: float = 0.0,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> float:
    """Annualised Sortino ratio, `mean(r - req) * P / downside_risk` (unitless); no session
    below `required_return` (per session) → NaN."""
    r = as_series(returns).to_numpy()
    p = periods_per_year(periods, config)
    req = _finite("required_return", required_return)
    down = downside_risk(r, required_return=req, periods=p)
    return nan_if_zero(float(np.mean(r - req)) * p, down)


def omega_ratio(
    returns: ReturnsLike,
    *,
    threshold_annual: float | None = None,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> float:
    """Omega ratio at an annual threshold `R` (default `quant.metrics.omega_threshold_annual`):
    with `tau = (1 + R)^(1/P) - 1`, `sum((r - tau)+) / sum((tau - r)+)` (unitless); no session
    below the threshold → NaN."""
    r = as_series(returns).to_numpy()
    p = periods_per_year(periods, config)
    annual = (
        config_or_default(config).quant.metrics.omega_threshold_annual
        if threshold_annual is None
        else threshold_annual
    )
    tau = annual_to_daily(annual, p)
    gains = float(np.clip(r - tau, 0.0, None).sum())
    losses = float(np.clip(tau - r, 0.0, None).sum())
    return nan_if_zero(gains, losses)


def calmar_ratio(
    returns: ReturnsLike, *, periods: int | None = None, config: AtlasConfig | None = None
) -> float:
    """Calmar ratio, CAGR / max drawdown (unitless); no drawdown → NaN."""
    r = as_series(returns)
    return nan_if_zero(annualised_return(r, periods=periods, config=config), max_drawdown(r))


# --- drawdowns -------------------------------------------------------------------------------


def wealth_index(returns: ReturnsLike) -> pd.Series:
    """Growth of 1 unit of NAV after each session, `prod(1 + r)` up to that session (the
    starting value 1 sits before the first session and is not in the Series)."""
    r = as_series(returns)
    return pd.Series(np.cumprod(1.0 + r.to_numpy()), index=r.index, name="wealth")


def _drawdowns(r: np.ndarray) -> np.ndarray:
    """Drawdowns at positions 0..n of the wealth path that starts at 1 (position 0 = before
    the first return), as fractions <= 0."""
    wealth = np.concatenate(([1.0], np.cumprod(1.0 + r)))
    peak = np.maximum.accumulate(wealth)  # >= 1: the path starts at 1, so never a zero peak
    return wealth / peak - 1.0


def drawdown_series(returns: ReturnsLike) -> pd.Series:
    """Drawdown after each session, `W_t / max(W_0..W_t) - 1` (fraction, <= 0); the initial
    NAV `W_0 = 1` counts as a peak, so a loss on the first session is a drawdown."""
    r = as_series(returns)
    return pd.Series(_drawdowns(r.to_numpy())[1:], index=r.index, name="drawdown")


def max_drawdown(returns: ReturnsLike) -> float:
    """Largest peak-to-trough loss as a **positive** fraction (0.2 = -20%); 0 if none."""
    r = as_series(returns).to_numpy()
    return float(-_drawdowns(r).min()) + 0.0


@dataclass(frozen=True)
class DrawdownPeriod:
    """One drawdown episode. `peak` is the session whose close set the high-water mark (`None`
    when it is the initial NAV, before the first session), `trough` the session of the lowest
    point, `recovery` the first session back at the high-water mark (`None` if not
    recovered). `depth` is the positive loss from the peak to the trough (fraction);
    `to_trough` the sessions from the peak to the trough; `duration` the sessions from the
    peak to the recovery, or to the last session when not recovered."""

    peak: Hashable | None
    trough: Hashable
    recovery: Hashable | None
    depth: float
    to_trough: int
    duration: int

    @property
    def recovered(self) -> bool:
        """Whether the NAV came back to the high-water mark."""
        return self.recovery is not None


def drawdown_periods(returns: ReturnsLike) -> list[DrawdownPeriod]:
    """Every drawdown episode, in time order (see `DrawdownPeriod`: depth a positive fraction
    of the peak NAV, lengths in sessions)."""
    r = as_series(returns)
    labels = list(r.index)
    dd = _drawdowns(r.to_numpy())  # positions 0..n; position i > 0 is session labels[i - 1]
    n = len(dd) - 1
    out: list[DrawdownPeriod] = []
    pos = 1
    while pos <= n:
        if dd[pos] >= 0.0:
            pos += 1
            continue
        peak = pos - 1
        end = pos
        while end <= n and dd[end] < 0.0:
            end += 1
        recovered = end <= n
        trough = peak + 1 + int(np.argmin(dd[peak + 1 : end]))
        out.append(
            DrawdownPeriod(
                peak=None if peak == 0 else labels[peak - 1],
                trough=labels[trough - 1],
                recovery=labels[end - 1] if recovered else None,
                depth=float(-dd[trough]),
                to_trough=trough - peak,
                duration=(end if recovered else n) - peak,
            )
        )
        pos = end + 1
    return out


def max_drawdown_duration(returns: ReturnsLike) -> int:
    """The longest drawdown episode, in sessions from the peak to the recovery (or to the last
    session when not recovered); 0 if the NAV never fell below a high-water mark."""
    return max((p.duration for p in drawdown_periods(returns)), default=0)


def ulcer_index(returns: ReturnsLike) -> float:
    """Ulcer index, `sqrt(mean(drawdown_t^2))` over the sessions (fraction)."""
    dd = _drawdowns(as_series(returns).to_numpy())[1:]
    return float(math.sqrt(float(np.mean(dd**2))))


# --- distribution ----------------------------------------------------------------------------


def skewness(returns: ReturnsLike) -> float:
    """Population skewness `m3 / m2^1.5` of the returns (unitless; biased, as
    `scipy.stats.skew`); constant → NaN."""
    r = as_series(returns, min_obs=2).to_numpy()
    if is_constant(r):
        return math.nan
    d = r - r.mean()
    m2 = float(np.mean(d**2))
    return nan_if_zero(float(np.mean(d**3)), m2**1.5)


def excess_kurtosis(returns: ReturnsLike) -> float:
    """Population excess kurtosis `m4 / m2^2 - 3` of the returns (unitless; biased, Fisher, as
    `scipy.stats.kurtosis`); constant → NaN."""
    r = as_series(returns, min_obs=2).to_numpy()
    if is_constant(r):
        return math.nan
    d = r - r.mean()
    m2 = float(np.mean(d**2))
    ratio = nan_if_zero(float(np.mean(d**4)), m2**2)
    return ratio - 3.0


def hit_rate(returns: ReturnsLike) -> float:
    """Share of sessions with a positive return (a zero return is not a hit), fraction."""
    r = as_series(returns).to_numpy()
    return float(np.mean(r > 0.0))


def profit_factor(returns: ReturnsLike) -> float:
    """Sum of gains over the magnitude of the sum of losses (unitless); no loss → NaN."""
    r = as_series(returns).to_numpy()
    return nan_if_zero(float(r[r > 0].sum()), float(-r[r < 0].sum()))


def tail_ratio(
    returns: ReturnsLike, *, quantile: float | None = None, config: AtlasConfig | None = None
) -> float:
    """`|q(1 - t)| / |q(t)|` (unitless) with `t = quantile` (default
    `quant.metrics.tail_quantile`, 0.05) and numpy linear quantiles; `q(t) = 0` → NaN."""
    r = as_series(returns).to_numpy()
    t = config_or_default(config).quant.metrics.tail_quantile if quantile is None else quantile
    if isinstance(t, bool) or not isinstance(t, int | float) or not 0.0 < t < 0.5:
        raise ValueError(f"quantile must be in (0, 0.5), got {t!r}")
    upper, lower = np.quantile(r, [1.0 - t, t])
    return nan_if_zero(abs(float(upper)), abs(float(lower)))


# --- rolling ---------------------------------------------------------------------------------


def rolling_metric(
    returns: ReturnsLike,
    metric: Callable[[pd.Series], float],
    window: int | None = None,
    *,
    config: AtlasConfig | None = None,
) -> pd.Series:
    """`metric` of each trailing window of `window` returns (default
    `quant.metrics.rolling_window`), on the window's last session; NaN before the first full
    window. Units: those of `metric`."""
    r = as_series(returns)
    w = window_length(window, config)
    values = np.full(len(r), np.nan)
    for end in range(w, len(r) + 1):
        values[end - 1] = metric(r.iloc[end - w : end])
    return pd.Series(values, index=r.index, name="rolling")


def rolling_vol(
    returns: ReturnsLike,
    window: int | None = None,
    *,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> pd.Series:
    """Rolling `annualised_vol` (annual fraction), vectorised."""
    r = as_series(returns)
    w = window_length(window, config)
    p = periods_per_year(periods, config)
    out = r.rolling(w).std(ddof=1) * math.sqrt(p)
    return out.rename("rolling_vol")


def rolling_sharpe(
    returns: ReturnsLike,
    window: int | None = None,
    *,
    risk_free: RiskFree = None,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> pd.Series:
    """Rolling `sharpe_ratio` (unitless), vectorised; zero volatility in a window → NaN."""
    r = as_series(returns)
    w = window_length(window, config)
    p = periods_per_year(periods, config)
    excess = r - risk_free_daily(risk_free, r.index, p, config)
    mean = excess.rolling(w).mean()
    std = excess.rolling(w).std(ddof=1)
    out = (mean / std.where(std > 0.0)) * math.sqrt(p)
    return out.rename("rolling_sharpe")


def rolling_max_drawdown(
    returns: ReturnsLike, window: int | None = None, *, config: AtlasConfig | None = None
) -> pd.Series:
    """Rolling `max_drawdown` (positive fraction) of each trailing window, the window starting
    from a NAV of 1."""
    r = as_series(returns)
    w = window_length(window, config)
    out = r.rolling(w).apply(lambda x: float(-_drawdowns(x).min()) + 0.0, raw=True)
    return out.rename("rolling_max_drawdown")


# --- summary ---------------------------------------------------------------------------------


def performance_summary(
    returns: ReturnsLike,
    *,
    risk_free: RiskFree = None,
    periods: int | None = None,
    config: AtlasConfig | None = None,
) -> dict[str, Any]:
    """Every returns-based metric of this module in one dict (units as each function):
    `n_sessions`, `start`, `end`, `cumulative_return`, `annualised_return`, `annualised_mean`,
    `annualised_vol`, `sharpe`, `sortino`, `calmar`, `omega`, `max_drawdown`,
    `max_drawdown_duration`, `ulcer_index`, `skewness`, `excess_kurtosis`, `hit_rate`,
    `profit_factor`, `tail_ratio`. Needs at least 2 returns."""
    r = as_series(returns, min_obs=2)
    p = periods_per_year(periods, config)
    return {
        "n_sessions": len(r),
        "start": r.index[0],
        "end": r.index[-1],
        "cumulative_return": cumulative_return(r),
        "annualised_return": annualised_return(r, periods=p),
        "annualised_mean": annualised_mean(r, periods=p),
        "annualised_vol": annualised_vol(r, periods=p),
        "sharpe": sharpe_ratio(r, risk_free=risk_free, periods=p, config=config),
        "sortino": sortino_ratio(r, periods=p),
        "calmar": calmar_ratio(r, periods=p),
        "omega": omega_ratio(r, periods=p, config=config),
        "max_drawdown": max_drawdown(r),
        "max_drawdown_duration": max_drawdown_duration(r),
        "ulcer_index": ulcer_index(r),
        "skewness": skewness(r),
        "excess_kurtosis": excess_kurtosis(r),
        "hit_rate": hit_rate(r),
        "profit_factor": profit_factor(r),
        "tail_ratio": tail_ratio(r, config=config),
    }


def _finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)
