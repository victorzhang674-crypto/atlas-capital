"""Value at Risk and Conditional VaR (expected shortfall) (Stage 20).

Units: VaR and CVaR are **positive losses** in the units of the input: a fraction of NAV for a
return series or a portfolio (weights are fractions of NAV), currency for a P&L series. A
negative VaR means a gain at that confidence. `VaR_a = -q_{1-a}` (the loss exceeded with
probability `1 - a`); `CVaR_a` = the mean loss in that tail (never below the VaR at the same
confidence). Defaults: VaR at `limits.var_confidence` (0.95) and CVaR at
`limits.cvar_confidence` (0.975) — the fund limits' own confidence levels; `method` =
`quant.metrics.var_method`; `horizon` in sessions (default 1).

Methods on a series (`var_cvar`, `value_at_risk`, `conditional_value_at_risk`):

- `historical`: `q` = numpy linear quantile of the observations; CVaR = -mean of the
  observations `<= q`. Horizon `h` > 1: `quant.metrics.horizon_method` = `sqrt` (the 1-session
  numbers x sqrt(h), square-root-of-time) or `overlapping` (the same estimators on the
  overlapping h-session returns: compounded for returns, summed for P&L).
- `normal`: `VaR = -(mu_h + z sigma_h)`, `CVaR = -mu_h + sigma_h phi(z) / (1 - a)` with
  `z = Phi^-1(1 - a)`, `mu_h = h mu`, `sigma_h = sqrt(h) sigma` (sample std, ddof 1); `mu = 0`
  when `quant.metrics.zero_mean` (default, as Stage 18's zero-mean forecasts), else the
  sample mean.
- `cornish_fisher`: the normal quantile adjusted for the sample skewness `S` and excess
  kurtosis `K` (population moments; under i.i.d. aggregation `S / sqrt(h)` and `K / h` at
  horizon h): `z_cf = z + (z^2 - 1) S/6 + (z^3 - 3z) K/24 - (2z^3 - 5z) S^2/36`,
  `VaR = -(mu_h + z_cf sigma_h)`; CVaR = the expected loss of the Cornish-Fisher-transformed
  normal below the VaR quantile, in closed form
  `-mu_h + sigma_h phi(z)/(1 - a) [1 + S z/6 + K (z^2 - 1)/24 - S^2 (2 z^2 - 1)/36]`. The
  expansion is not monotone for extreme (S, K): a CVaR below the VaR is floored at the VaR
  (PerformanceAnalytics' convention) and recorded in `adjustments`.

Portfolios (`portfolio_var`): with a returns history, the historical simulation of today's
weights, `portfolio_returns(w, R) = R w` (a session where a held name has no return is
dropped), then any series method. With a covariance (daily, names x names): `normal` uses
`sigma_p = sqrt(w' Sigma w)` (and `w' mu`); `monte_carlo` draws `quant.metrics.mc_simulations`
multivariate-normal scenarios (mean 0 or the given daily mean, scaled to the horizon:
`h mu`, `h Sigma`) with an explicit `seed` or `rng` and applies the historical estimators to
the simulated portfolio P&L. A nonzero weight on a name without data raises `DataError`.

Fail safe: fewer than `quant.metrics.var_min_obs` observations raise `DataError` — never a
VaR from too little data.
"""

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal, get_args

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.quant.metrics._common import (
    ReturnsLike,
    as_covariance,
    as_series,
    as_weights,
    config_or_default,
    is_constant,
    sample_std,
    weights_on,
)

_log = get_logger(__name__)
_N = NormalDist()
_MC_BATCH = 4096  # simulated scenarios drawn per batch (bounds memory for large universes)

VarMethod = Literal["historical", "normal", "cornish_fisher", "monte_carlo"]
SERIES_METHODS: tuple[str, ...] = ("historical", "normal", "cornish_fisher")
"""Methods that work on a return / P&L series."""
COVARIANCE_METHODS: tuple[str, ...] = ("normal", "monte_carlo")
"""Methods that work on weights and a covariance."""
METHODS: tuple[str, ...] = get_args(VarMethod)


@dataclass(frozen=True)
class VaRResult:
    """VaR and CVaR as positive losses (input units; fractions of NAV for portfolios) at
    `var_confidence` / `cvar_confidence` over `horizon` sessions, by `method`, from `n_obs`
    observations (simulated scenarios for Monte Carlo). `adjustments` records any correction
    (for example a floored Cornish-Fisher CVaR)."""

    var: float
    cvar: float
    var_confidence: float
    cvar_confidence: float
    horizon: int
    method: str
    n_obs: int
    adjustments: tuple[str, ...] = ()


# --- validation ------------------------------------------------------------------------------


def _confidence(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number in (0.5, 1), got {value!r}")
    if not 0.5 < float(value) < 1.0:
        raise ValueError(f"{name} must be in (0.5, 1), got {value!r}")
    return float(value)


def _horizon(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"horizon must be a positive int (sessions), got {value!r}")
    return value


def _levels(
    var_confidence: float | None, cvar_confidence: float | None, cfg: AtlasConfig
) -> tuple[float, float]:
    vc = cfg.limits.var_confidence if var_confidence is None else var_confidence
    cc = cfg.limits.cvar_confidence if cvar_confidence is None else cvar_confidence
    return _confidence("var_confidence", vc), _confidence("cvar_confidence", cc)


# --- estimators on arrays --------------------------------------------------------------------


def _hist_var(x: np.ndarray, conf: float) -> float:
    return float(-np.quantile(x, 1.0 - conf))


def _hist_cvar(x: np.ndarray, conf: float) -> float:
    """-mean of the observations at or below the `1 - conf` quantile; the tail holds at least
    the smallest observation by construction (never an empty mean)."""
    q = np.quantile(x, 1.0 - conf)
    ordered = np.sort(x)
    k = max(int(np.searchsorted(ordered, q, side="right")), 1)
    return float(-ordered[:k].mean())


def normal_var(mu: float, sigma: float, confidence: float) -> float:
    """Normal VaR (positive loss, units of `mu` / `sigma`): `-(mu + z sigma)`,
    `z = Phi^-1(1 - confidence)`."""
    z = _N.inv_cdf(1.0 - _confidence("confidence", confidence))
    return float(-(mu + z * sigma))


def normal_cvar(mu: float, sigma: float, confidence: float) -> float:
    """Normal CVaR (positive loss): `-mu + sigma phi(z) / (1 - confidence)`."""
    conf = _confidence("confidence", confidence)
    z = _N.inv_cdf(1.0 - conf)
    return float(-mu + sigma * _N.pdf(z) / (1.0 - conf))


def cornish_fisher_quantile(z: float, skew: float, kurt: float) -> float:
    """Cornish-Fisher adjusted standard quantile for skewness `skew` and excess kurtosis
    `kurt` (unitless)."""
    return float(
        z
        + (z**2 - 1.0) * skew / 6.0
        + (z**3 - 3.0 * z) * kurt / 24.0
        - (2.0 * z**3 - 5.0 * z) * skew**2 / 36.0
    )


def cornish_fisher_var(
    mu: float, sigma: float, skew: float, kurt: float, confidence: float
) -> float:
    """Cornish-Fisher VaR (positive loss): `-(mu + z_cf sigma)`."""
    z = _N.inv_cdf(1.0 - _confidence("confidence", confidence))
    return float(-(mu + cornish_fisher_quantile(z, skew, kurt) * sigma))


def cornish_fisher_cvar(
    mu: float, sigma: float, skew: float, kurt: float, confidence: float
) -> tuple[float, bool]:
    """Cornish-Fisher CVaR (positive loss) in closed form and whether it was floored at the
    VaR at the same confidence (the expansion is not monotone for extreme moments)."""
    conf = _confidence("confidence", confidence)
    z = _N.inv_cdf(1.0 - conf)
    factor = 1.0 + skew * z / 6.0 + kurt * (z**2 - 1.0) / 24.0 - skew**2 * (2.0 * z**2 - 1.0) / 36.0
    cvar = float(-mu + sigma * _N.pdf(z) / (1.0 - conf) * factor)
    var = cornish_fisher_var(mu, sigma, skew, kurt, conf)
    if cvar < var:
        return var, True
    return cvar, False


def scale_to_horizon(value: float, horizon: int) -> float:
    """A 1-session VaR / CVaR / vol scaled to `horizon` sessions by the square-root-of-time
    rule, `value * sqrt(h)` (zero-mean, i.i.d. returns)."""
    return float(value * math.sqrt(_horizon(horizon)))


def _overlapping(x: pd.Series, h: int, pnl: bool) -> np.ndarray:
    agg = x.rolling(h).sum() if pnl else (1.0 + x).rolling(h).apply(np.prod, raw=True) - 1.0
    return agg.dropna().to_numpy()


def _moments(x: np.ndarray) -> tuple[float, float]:
    if is_constant(x):
        return 0.0, 0.0
    d = x - x.mean()
    m2 = float(np.mean(d**2))
    return float(np.mean(d**3)) / m2**1.5, float(np.mean(d**4)) / m2**2 - 3.0


# --- series ----------------------------------------------------------------------------------


def var_cvar(
    values: ReturnsLike,
    *,
    method: str | None = None,
    var_confidence: float | None = None,
    cvar_confidence: float | None = None,
    horizon: int = 1,
    pnl: bool = False,
    config: AtlasConfig | None = None,
) -> VaRResult:
    """VaR and CVaR of a return series (fractions) or, with `pnl=True`, a P&L series
    (currency), by `method` in `SERIES_METHODS` (default `quant.metrics.var_method`) over
    `horizon` sessions. Needs `quant.metrics.var_min_obs` observations."""
    cfg = config_or_default(config)
    mc = cfg.quant.metrics
    how = mc.var_method if method is None else method
    if how not in SERIES_METHODS:
        raise ValueError(f"method must be one of {SERIES_METHODS} for a series, got {how!r}")
    vc, cc = _levels(var_confidence, cvar_confidence, cfg)
    h = _horizon(horizon)
    x = as_series(values, "pnl" if pnl else "returns", pnl=pnl)
    if len(x) < mc.var_min_obs:
        raise DataError(f"VaR needs at least {mc.var_min_obs} observations, got {len(x)}")
    data = x.to_numpy()
    adjustments: list[str] = []
    if how == "historical":
        if h > 1 and mc.horizon_method == "overlapping":
            agg = _overlapping(x, h, pnl)
            if len(agg) < mc.var_min_obs:
                raise DataError(
                    f"VaR needs at least {mc.var_min_obs} overlapping {h}-session observations, "
                    f"got {len(agg)}"
                )
            var, cvar = _hist_var(agg, vc), _hist_cvar(agg, cc)
            adjustments.append(f"overlapping {h}-session returns")
        else:
            var, cvar = _hist_var(data, vc), _hist_cvar(data, cc)
            if h > 1:
                var, cvar = scale_to_horizon(var, h), scale_to_horizon(cvar, h)
                adjustments.append(f"sqrt-of-time scaling to {h} sessions")
        return VaRResult(var, cvar, vc, cc, h, how, len(data), tuple(adjustments))
    mu = 0.0 if mc.zero_mean else float(data.mean())
    sigma = sample_std(data)
    mu_h, sigma_h = mu * h, sigma * math.sqrt(h)
    if how == "normal":
        var, cvar = normal_var(mu_h, sigma_h, vc), normal_cvar(mu_h, sigma_h, cc)
        return VaRResult(var, cvar, vc, cc, h, how, len(data), ())
    skew, kurt = _moments(data)
    skew_h, kurt_h = skew / math.sqrt(h), kurt / h
    var = cornish_fisher_var(mu_h, sigma_h, skew_h, kurt_h, vc)
    cvar, floored = cornish_fisher_cvar(mu_h, sigma_h, skew_h, kurt_h, cc)
    if floored:
        adjustments.append("cornish-fisher CVaR floored at the VaR (non-monotone expansion)")
        _log.warning("cornish_fisher_cvar_floored", skew=skew_h, kurtosis=kurt_h)
    return VaRResult(var, cvar, vc, cc, h, how, len(data), tuple(adjustments))


def value_at_risk(
    values: ReturnsLike,
    *,
    method: str | None = None,
    confidence: float | None = None,
    horizon: int = 1,
    pnl: bool = False,
    config: AtlasConfig | None = None,
) -> float:
    """VaR (positive loss, input units) at `confidence` (default `limits.var_confidence`)."""
    return var_cvar(
        values,
        method=method,
        var_confidence=confidence,
        horizon=horizon,
        pnl=pnl,
        config=config,
    ).var


def conditional_value_at_risk(
    values: ReturnsLike,
    *,
    method: str | None = None,
    confidence: float | None = None,
    horizon: int = 1,
    pnl: bool = False,
    config: AtlasConfig | None = None,
) -> float:
    """CVaR (positive loss, input units) at `confidence` (default `limits.cvar_confidence`)."""
    return var_cvar(
        values,
        method=method,
        cvar_confidence=confidence,
        horizon=horizon,
        pnl=pnl,
        config=config,
    ).cvar


# --- portfolios ------------------------------------------------------------------------------


def portfolio_returns(weights: pd.Series, returns: pd.DataFrame) -> pd.Series:
    """Historical simulation of today's `weights` (fractions of NAV by symbol) on past
    `returns` (sessions x symbols, simple daily): `R w` per session (fraction of NAV). Sessions
    where a held name has no return are dropped (and logged); a nonzero weight on a name
    without a returns column raises `DataError`; duplicate symbols or sessions, or an unsorted
    index, raise `ValueError`."""
    w = as_weights(weights)
    if not isinstance(returns, pd.DataFrame):
        raise ValueError("returns must be a DataFrame (sessions x symbols)")
    if returns.columns.has_duplicates or returns.index.has_duplicates:
        raise ValueError("returns has duplicate symbols or sessions")
    if not returns.index.is_monotonic_increasing:
        raise ValueError("returns must be sorted by session (ascending index)")
    held = w[w != 0.0]
    missing = [str(s) for s in held.index if s not in returns.columns]
    if missing:
        raise DataError(f"no returns for held names: {missing[:10]}")
    frame = returns.loc[:, list(held.index)].astype("float64")
    complete = frame.notna().all(axis=1)
    dropped = int((~complete).sum())
    if dropped:
        _log.info("portfolio_returns_dropped_sessions", sessions=dropped)
    values = frame[complete].to_numpy() @ held.to_numpy()
    return pd.Series(values, index=frame.index[complete], name="portfolio_return")


def simulate_portfolio_returns(
    weights: pd.Series | np.ndarray,
    cov: pd.DataFrame | np.ndarray,
    *,
    n_sims: int,
    mean: pd.Series | np.ndarray | None = None,
    horizon: int = 1,
    seed: int | None = None,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """`n_sims` simulated portfolio returns over `horizon` sessions (fractions of NAV) from
    multivariate-normal scenarios `N(h mu, h Sigma)` (`mu` daily, default 0; `Sigma` the daily
    covariance, factorised by eigen-decomposition so a PSD rank-deficient matrix works).
    Exactly one of `seed` / `rng` is required."""
    if (seed is None) == (rng is None):
        raise ValueError("pass exactly one of seed or rng (Monte Carlo must be reproducible)")
    gen = rng if rng is not None else np.random.default_rng(seed)
    if isinstance(n_sims, bool) or not isinstance(n_sims, int) or n_sims < 1:
        raise ValueError(f"n_sims must be a positive int, got {n_sims!r}")
    h = _horizon(horizon)
    matrix, names = as_covariance(cov)
    w = weights_on(names, weights)
    mu = np.zeros(len(w)) if mean is None else weights_on(names, mean, name="mean", strict=False)
    vals, vecs = np.linalg.eigh(matrix)
    loading = vecs @ (np.sqrt(np.clip(vals, 0.0, None)) * (vecs.T @ w))
    # loading = Sigma^(1/2) w, so each scenario's portfolio return is mu'w + z'loading
    out = np.empty(n_sims)
    done = 0
    while done < n_sims:
        size = min(_MC_BATCH, n_sims - done)
        z = gen.standard_normal((size, len(w)))
        out[done : done + size] = z @ loading
        done += size
    return float(mu @ w) * h + math.sqrt(h) * out


def portfolio_var(
    weights: pd.Series,
    *,
    returns: pd.DataFrame | None = None,
    cov: pd.DataFrame | np.ndarray | None = None,
    method: str | None = None,
    var_confidence: float | None = None,
    cvar_confidence: float | None = None,
    horizon: int = 1,
    mean: pd.Series | np.ndarray | None = None,
    n_sims: int | None = None,
    seed: int | None = None,
    rng: np.random.Generator | None = None,
    config: AtlasConfig | None = None,
) -> VaRResult:
    """VaR and CVaR of a portfolio (`weights`: fractions of NAV by symbol), as fractions of
    NAV. Give exactly one of `returns` (historical simulation, any series method) or `cov`
    (daily covariance: `normal` or `monte_carlo`; default `monte_carlo` needs `seed` / `rng`
    and draws `n_sims` (default `quant.metrics.mc_simulations`) scenarios). `mean` is a daily
    mean vector for the covariance methods (default 0)."""
    if (returns is None) == (cov is None):
        raise ValueError("pass exactly one of returns (historical simulation) or cov")
    cfg = config_or_default(config)
    if returns is not None:
        series = portfolio_returns(weights, returns)
        return var_cvar(
            series,
            method=method,
            var_confidence=var_confidence,
            cvar_confidence=cvar_confidence,
            horizon=horizon,
            config=cfg,
        )
    assert cov is not None
    how = "monte_carlo" if method is None else method
    if how not in COVARIANCE_METHODS:
        raise ValueError(f"method must be one of {COVARIANCE_METHODS} with a cov, got {how!r}")
    vc, cc = _levels(var_confidence, cvar_confidence, cfg)
    h = _horizon(horizon)
    matrix, names = as_covariance(cov)
    w = weights_on(names, weights)
    if how == "normal":
        mu = 0.0 if mean is None else float(weights_on(names, mean, name="mean", strict=False) @ w)
        sigma = math.sqrt(max(float(w @ matrix @ w), 0.0))
        mu_h, sigma_h = mu * h, sigma * math.sqrt(h)
        return VaRResult(
            normal_var(mu_h, sigma_h, vc), normal_cvar(mu_h, sigma_h, cc), vc, cc, h, how, 0
        )
    sims = cfg.quant.metrics.mc_simulations if n_sims is None else n_sims
    # align a by-symbol mean to the covariance's names first: `w` and `matrix` are positional
    mean_vec = None if mean is None else weights_on(names, mean, name="mean", strict=False)
    pnl = simulate_portfolio_returns(
        w, matrix, n_sims=sims, mean=mean_vec, horizon=h, seed=seed, rng=rng
    )
    return VaRResult(_hist_var(pnl, vc), _hist_cvar(pnl, cc), vc, cc, h, how, len(pnl), ())
