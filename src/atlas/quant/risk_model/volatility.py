"""Volatility forecasts and annualisation helpers (Stage 18).

Units: daily simple returns in; variances in return² per session and vols as daily standard
deviations out, unless a function says `annual`. Annualisation uses
`quant.features.sessions_per_year` (252) sessions a year: vol x √P, variance x P.

Next-session forecasts for one name (`VolForecast`), all assuming a zero daily mean:

- `ewma_vol`: RiskMetrics EWMA, `sigma²_{t+1} = Σ_k λ^k r²_{t-k} / Σ_k λ^k` over the returns
  present (the same weights as `EWMACovariance`); `ewma_variance_path` gives the forecast made
  after every session (value at `t` = forecast for the session after `t`, from returns up to
  `t` only).
- `realised_vol`: root mean square of the last `realised_window` returns.
- `garch_vol`: GARCH(1,1), zero mean, normal errors, fitted by `arch` on the returns in
  percent (NaN dropped); the forecast is `ω + alpha r_T² + β sigma_T²`. It **falls back to EWMA**
  (`fallback` = the reason, `method` = `ewma`, a warning logged) when there are fewer than
  `garch_min_obs` returns, the optimiser raises, it does not converge, or the parameters are
  invalid (`ω ≤ 0`, `alpha < 0`, `β < 0`, `alpha + β ≥ 1`, or not finite).
- `blend_vol`: `w_ewma sigma²_ewma + w_garch sigma²_garch + w_realised sigma²_realised` with
  `quant.risk_model.volatility.blend_weights` (summing to 1); a GARCH fallback is carried.
- `forecast_vol(returns, method)` dispatches (default `quant.risk_model.volatility.method`);
  `forecast_vols(frame, method)` forecasts each column and leaves out (and logs) the names
  without a forecast.

Fail safe (spec §3.6): a series with fewer than `quant.risk_model.min_history` returns, or a
zero variance forecast, raises `DataError` (no forecast, never a guess).
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig, BlendWeights
from atlas.core.errors import DataError
from atlas.core.logging import get_logger

_log = get_logger(__name__)

# --- annualisation --------------------------------------------------------------------------


def _periods(periods: int | None, config: AtlasConfig | None) -> int:
    if periods is None:
        cfg = config if config is not None else AtlasConfig()
        return cfg.quant.features.sessions_per_year
    if isinstance(periods, bool) or not isinstance(periods, int) or periods < 1:
        raise ValueError(f"periods must be a positive int (sessions a year), got {periods!r}")
    return periods


def annualise_vol[V: (float, np.ndarray, pd.Series, pd.DataFrame)](
    values: V, periods: int | None = None, *, config: AtlasConfig | None = None
) -> V:
    """Daily vol(s) → annual: `values · √P` (`P` = `quant.features.sessions_per_year`)."""
    return values * math.sqrt(_periods(periods, config))


def annualise_variance[V: (float, np.ndarray, pd.Series, pd.DataFrame)](
    values: V, periods: int | None = None, *, config: AtlasConfig | None = None
) -> V:
    """Daily variance(s) or covariance → annual: `values · P`."""
    return values * _periods(periods, config)


def deannualise_vol[V: (float, np.ndarray, pd.Series, pd.DataFrame)](
    values: V, periods: int | None = None, *, config: AtlasConfig | None = None
) -> V:
    """Annual vol(s) → daily: `values / √P`."""
    return values / math.sqrt(_periods(periods, config))


# --- EWMA decay -----------------------------------------------------------------------------


def ewma_lambda(
    lam: float | None = None, half_life: float | None = None, *, config: AtlasConfig | None = None
) -> float:
    """The EWMA decay per session: `lam` if given, else `0.5 ** (1 / half_life)` (the weight
    halves every `half_life` sessions), else `quant.risk_model.ewma_lambda` (RiskMetrics daily
    0.94). `ValueError` when both are given or the result is not in (0, 1)."""
    if lam is not None and half_life is not None:
        raise ValueError("give lam or half_life, not both")
    if half_life is not None:
        if not (math.isfinite(half_life) and half_life > 0):
            raise ValueError(f"half_life must be a positive number of sessions, got {half_life}")
        lam = 0.5 ** (1.0 / half_life)
    elif lam is None:
        lam = (config if config is not None else AtlasConfig()).quant.risk_model.ewma_lambda
    if not (math.isfinite(lam) and 0.0 < lam < 1.0):
        raise ValueError(f"the EWMA decay must be in (0, 1), got {lam}")
    return float(lam)


# --- forecasts ------------------------------------------------------------------------------


@dataclass(frozen=True)
class VolForecast:
    """A next-session volatility forecast for one name.

    `variance` is in return² per session (`vol` its square root, a daily fraction); `n_obs`
    is the number of returns the forecast saw; `params` holds the method's parameters (read
    only); `fallback` is the reason a GARCH forecast fell back to EWMA, else `None`.
    """

    variance: float
    method: str
    n_obs: int
    params: Mapping[str, float] = field(default_factory=dict)
    fallback: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    @property
    def vol(self) -> float:
        """Daily volatility (standard deviation of the next session's return)."""
        return math.sqrt(self.variance)

    def annual_vol(self, periods: int | None = None, *, config: AtlasConfig | None = None) -> float:
        """Annualised volatility, `vol · √P`."""
        return annualise_vol(self.vol, periods, config=config)


def _series(returns: object) -> pd.Series:
    """`returns` as a float Series; `ValueError` unless numeric, without ±inf, with a unique,
    strictly ascending index."""
    if not isinstance(returns, pd.Series):
        raise ValueError(f"returns must be a Series, got {type(returns).__name__}")
    if pd.api.types.is_bool_dtype(returns) or not pd.api.types.is_numeric_dtype(returns):
        raise ValueError(f"returns must be numeric, got dtype {returns.dtype}")
    if not (returns.index.is_unique and returns.index.is_monotonic_increasing):
        raise ValueError("returns must be indexed by session, unique and strictly ascending")
    out = returns.astype("float64")
    if np.isinf(out.to_numpy()).any():
        raise ValueError("returns has infinite values")
    return out


def _checked(returns: object, min_history: int | None, config: AtlasConfig | None) -> pd.Series:
    """The validated series, if it has at least `min_history` returns (default
    `quant.risk_model.min_history`); `DataError` otherwise."""
    r = _series(returns)
    need = (
        (config if config is not None else AtlasConfig()).quant.risk_model.min_history
        if min_history is None
        else min_history
    )
    n = int(r.notna().sum())
    if n < need:
        raise DataError(f"{n} returns, fewer than min_history ({need}): no vol forecast")
    return r


def _forecast(
    variance: float,
    method: str,
    n_obs: int,
    params: Mapping[str, float],
    fallback: str | None = None,
) -> VolForecast:
    if not (math.isfinite(variance) and variance > 0.0):
        raise DataError(f"{method} forecast has zero variance ({variance}): no vol forecast")
    return VolForecast(float(variance), method, n_obs, params, fallback)


def ewma_variance_path(
    returns: pd.Series,
    lam: float | None = None,
    *,
    half_life: float | None = None,
    config: AtlasConfig | None = None,
) -> pd.Series:
    """The EWMA variance forecast made after each session: value at `t` = forecast for the
    session after `t`, from returns up to `t` only. `S_t = λ S_{t-1} + r_t²`,
    `W_t = λ W_{t-1} + 1` (a missing return decays the weights but adds nothing),
    forecast `S_t / W_t`; NaN before the first return."""
    r = _series(returns)
    decay = ewma_lambda(lam, half_life, config=config)
    values = r.to_numpy()
    out = np.full(len(values), np.nan)
    num = den = 0.0
    for i, x in enumerate(values):
        num *= decay
        den *= decay
        if not np.isnan(x):
            num += x * x
            den += 1.0
        if den > 0.0:
            out[i] = num / den
    return pd.Series(out, index=r.index, name=r.name)


def ewma_vol(
    returns: pd.Series,
    lam: float | None = None,
    *,
    half_life: float | None = None,
    config: AtlasConfig | None = None,
    min_history: int | None = None,
) -> VolForecast:
    """Next-session EWMA vol (zero mean; `λ` from `lam`, `half_life` or
    `quant.risk_model.ewma_lambda`)."""
    r = _checked(returns, min_history, config)
    decay = ewma_lambda(lam, half_life, config=config)
    path = ewma_variance_path(r, decay)
    return _forecast(float(path.iloc[-1]), "ewma", int(r.notna().sum()), {"lambda": decay})


def realised_vol(
    returns: pd.Series,
    window: int | None = None,
    *,
    config: AtlasConfig | None = None,
    min_history: int | None = None,
) -> VolForecast:
    """Next-session realised vol: root mean square of the returns in the last `window`
    sessions (default `quant.risk_model.volatility.realised_window`; missing returns skipped)."""
    cfg = config if config is not None else AtlasConfig()
    size = cfg.quant.risk_model.volatility.realised_window if window is None else window
    if isinstance(size, bool) or not isinstance(size, int) or size < 2:
        raise ValueError(f"window must be an int >= 2, got {size!r}")
    r = _checked(returns, min_history, cfg)
    last = r.to_numpy()[-size:]
    last = last[~np.isnan(last)]
    if len(last) < 2:
        raise DataError(f"{len(last)} returns in the last {size} sessions: no realised vol")
    return _forecast(
        float(np.mean(last**2)), "realised", int(r.notna().sum()), {"window": float(size)}
    )


def _fit_garch(pct: np.ndarray) -> object:
    """Fit a zero-mean GARCH(1,1) with normal errors to `pct` (daily returns in percent) with
    `arch`; the fitted result (`params`, `convergence_flag`, `forecast`)."""
    from arch import arch_model  # imported here: it pulls in scipy and statsmodels

    model = arch_model(pct, mean="Zero", vol="GARCH", p=1, q=1, dist="normal", rescale=False)
    return model.fit(disp="off", show_warning=False)


def garch_vol(
    returns: pd.Series,
    *,
    config: AtlasConfig | None = None,
    min_history: int | None = None,
    min_obs: int | None = None,
) -> VolForecast:
    """Next-session GARCH(1,1) vol (zero mean, normal errors), or the EWMA forecast with the
    reason in `fallback` (module docstring). `params` (daily fractions): `omega` (return²),
    `alpha`, `beta`, `persistence` (`alpha + β`), `long_run_variance` (`ω / (1 - alpha - β)`).
    `min_obs` defaults to `quant.risk_model.volatility.garch_min_obs`."""
    cfg = config if config is not None else AtlasConfig()
    r = _checked(returns, min_history, cfg)
    clean = r.dropna()
    need = cfg.quant.risk_model.volatility.garch_min_obs if min_obs is None else min_obs

    def fall_back(reason: str) -> VolForecast:
        _log.warning("garch_fallback", reason=reason, n_obs=len(clean), series=str(r.name))
        ewma = ewma_vol(r, config=cfg, min_history=1)
        return VolForecast(ewma.variance, "ewma", ewma.n_obs, ewma.params, fallback=reason)

    if len(clean) < need:
        return fall_back(f"{len(clean)} returns, fewer than garch_min_obs ({need})")
    pct = clean.to_numpy() * 100.0
    try:
        res: Any = _fit_garch(pct)
    except (ValueError, RuntimeError, ArithmeticError, np.linalg.LinAlgError) as exc:
        return fall_back(f"fit failed: {type(exc).__name__}: {exc}")
    if res.convergence_flag != 0:
        return fall_back(f"did not converge (convergence_flag {res.convergence_flag})")
    omega, alpha, beta = (float(res.params[k]) for k in ("omega", "alpha[1]", "beta[1]"))
    if not (
        all(math.isfinite(v) for v in (omega, alpha, beta))
        and omega > 0.0
        and alpha >= 0.0
        and beta >= 0.0
        and alpha + beta < 1.0
    ):
        return fall_back(f"invalid parameters (omega {omega}, alpha {alpha}, beta {beta})")
    variance = float(res.forecast(horizon=1, reindex=False).variance.iloc[-1, 0]) / 1e4
    params = {
        "omega": omega / 1e4,
        "alpha": alpha,
        "beta": beta,
        "persistence": alpha + beta,
        "long_run_variance": omega / (1.0 - alpha - beta) / 1e4,
    }
    return _forecast(variance, "garch", len(clean), params)


def blend_vol(
    returns: pd.Series,
    *,
    weights: BlendWeights | None = None,
    config: AtlasConfig | None = None,
    min_history: int | None = None,
) -> VolForecast:
    """Next-session blended vol: the variance-weighted blend of the EWMA, GARCH(1,1) and
    realised forecasts (`weights` default `quant.risk_model.volatility.blend_weights`; a
    component with weight 0 is not computed). `params` holds each computed component's
    variance and every weight; `fallback` carries GARCH's fallback reason."""
    cfg = config if config is not None else AtlasConfig()
    w = cfg.quant.risk_model.volatility.blend_weights if weights is None else weights
    r = _checked(returns, min_history, cfg)
    parts: dict[str, VolForecast] = {}
    if w.ewma > 0:
        parts["ewma"] = ewma_vol(r, config=cfg, min_history=min_history)
    if w.garch > 0:
        parts["garch"] = garch_vol(r, config=cfg, min_history=min_history)
    if w.realised > 0:
        parts["realised"] = realised_vol(r, config=cfg, min_history=min_history)
    weight = {"ewma": w.ewma, "garch": w.garch, "realised": w.realised}
    variance = sum(weight[k] * f.variance for k, f in parts.items())
    params = {f"{k}_variance": f.variance for k, f in parts.items()}
    params.update({f"w_{k}": v for k, v in weight.items()})
    fallback = parts["garch"].fallback if "garch" in parts else None
    return _forecast(variance, "blend", int(r.notna().sum()), params, fallback)


VOL_METHODS: tuple[str, ...] = ("ewma", "garch", "realised", "blend")


def forecast_vol(
    returns: pd.Series, method: str | None = None, *, config: AtlasConfig | None = None
) -> VolForecast:
    """Next-session vol of one name by `method` (`ewma`, `garch`, `realised`, `blend`;
    default `quant.risk_model.volatility.method`)."""
    cfg = config if config is not None else AtlasConfig()
    name = cfg.quant.risk_model.volatility.method if method is None else method
    if name == "ewma":
        return ewma_vol(returns, config=cfg)
    if name == "garch":
        return garch_vol(returns, config=cfg)
    if name == "realised":
        return realised_vol(returns, config=cfg)
    if name == "blend":
        return blend_vol(returns, config=cfg)
    raise ValueError(f"unknown vol method {name!r}; expected one of {VOL_METHODS}")


def forecast_vols(
    returns: pd.DataFrame, method: str | None = None, *, config: AtlasConfig | None = None
) -> dict[str, VolForecast]:
    """`forecast_vol` for each column of `returns` (names in column order). A name without a
    forecast (`DataError`: too little history, zero variance) is left out and logged: no
    forecast, never a guess."""
    if not isinstance(returns, pd.DataFrame):
        raise ValueError(f"returns must be a DataFrame, got {type(returns).__name__}")
    if not returns.columns.is_unique:
        raise ValueError("returns has duplicate column names")
    out: dict[str, VolForecast] = {}
    for column in returns.columns:
        try:
            out[str(column)] = forecast_vol(returns[column], method, config=config)
        except DataError as exc:
            _log.warning("vol_forecast_skipped", symbol=str(column), reason=str(exc))
    return out
