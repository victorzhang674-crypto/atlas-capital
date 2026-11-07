"""Calibration of alpha scores into expected returns, and point-in-time IC bookkeeping
(Stage 22).

**Grinold.** For a cross-sectional score `z` (mean 0, population std 1) whose information
coefficient with the next `h`-session residual return is `IC`, the expected residual return
over those `h` sessions is `alpha_h = IC * sigma_h * z` (Grinold & Kahn), `sigma_h` the
residual vol over `h` sessions. With the annualised vol `sigma = sigma_h * sqrt(P / h)` and
`alpha_ann = alpha_h * P / h` (P sessions a year):

    exp_return_ann = IC * sigma * z * sqrt(P / h)

At `h = P` this is the plain `IC * sigma * z`. Units: annualised fractions.

**IC history.** An IC dated `d` measures the signal of decision day `d` against the label
close(`d`) -> close(`d + h`) (Stage 17), so it is known only from the decision day after
`d + h` sessions: `known_ic` keeps exactly those rows. `trailing_ic` summarises the known ICs
of the last months (mean, Newey-West t), `ic_weights` turns positive ones into composite
weights.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.pit import guard_not_label
from atlas.quant.research import ic_series, newey_west_tstat, nw_lags
from atlas.quant.risk_model import RiskModel

WeightSource = Literal["ic", "fixed"]


def horizon_scale(horizon: int, periods: int) -> float:
    """`sqrt(periods / horizon)`: the factor that annualises a Grinold alpha of an
    `horizon`-session IC (`periods` sessions a year)."""
    if horizon < 1 or periods < 1:
        raise ValueError(f"horizon and periods must be >= 1, got {horizon}, {periods}")
    return math.sqrt(periods / horizon)


def grinold_alpha(
    z: pd.Series, ic: float, sigma_ann: pd.Series, *, horizon: int, periods: int
) -> pd.Series:
    """The annualised expected residual return `ic * sigma_ann * z * sqrt(periods / horizon)`
    (module docstring) of each name of `z`; `sigma_ann` (annualised residual vol, fraction)
    is aligned on `z`'s index; NaN where either is missing. `ic` must be finite with
    |ic| < 1 and `sigma_ann` non-negative (`ValueError`)."""
    if not math.isfinite(ic) or abs(ic) >= 1:
        raise ValueError(f"ic must be finite with |ic| < 1, got {ic}")
    sigma = sigma_ann.reindex(z.index).astype("float64")
    if (sigma < 0).any():
        raise ValueError("sigma_ann must be non-negative")
    scale = horizon_scale(horizon, periods)
    return (ic * sigma * z.astype("float64") * scale).rename("exp_return_ann")


@dataclass(frozen=True)
class CalibrationFit:
    """OLS fit `realised = intercept + slope * predicted` over `n_obs` pairs; a calibrated
    forecast has slope ~1 (NaN when undefined)."""

    slope: float
    intercept: float
    n_obs: int


def calibration_slope(predicted: pd.Series, realised: pd.Series) -> CalibrationFit:
    """The OLS fit of `realised` on `predicted` (aligned positionally; pairs with a NaN
    dropped). Both in the same units (e.g. annualised fractions)."""
    x = np.asarray(predicted, dtype="float64")
    y = np.asarray(realised, dtype="float64")
    if x.shape != y.shape:
        raise ValueError(f"predicted and realised differ in length: {x.shape} vs {y.shape}")
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    n = len(x)
    if n < 2 or float(np.var(x)) == 0.0:
        return CalibrationFit(slope=math.nan, intercept=math.nan, n_obs=n)
    slope = float(np.cov(x, y, ddof=0)[0, 1] / np.var(x))
    return CalibrationFit(slope=slope, intercept=float(y.mean() - slope * x.mean()), n_obs=n)


def ic_history(
    components: Mapping[date, pd.DataFrame],
    labels: pd.DataFrame,
    *,
    method: Literal["spearman", "pearson"] = "spearman",
    min_names: int | None = None,
) -> pd.DataFrame:
    """The IC of every column of the per-date component frames (`components[d]`: index
    symbol, one column per component) with `labels` (dates x symbols, one horizon; Stage 17
    `ForwardReturns.horizon(h)`): index `date` (the decision days, ascending), one column per
    component. Uses Stage 17 `ic_series` (`min_names` default `quant.research.min_names`);
    a label passed as a component raises `LookAheadError`."""
    days = sorted(components)
    if not days:
        raise ValueError("components must hold at least one date")
    for frame in components.values():
        guard_not_label(frame, "component frame")
    names = list(dict.fromkeys(c for d in days for c in components[d].columns))
    index = pd.DatetimeIndex(pd.to_datetime(days), name="date")
    out = {}
    for name in names:
        panel = pd.DataFrame(
            {
                ts: components[d][name] if name in components[d].columns else pd.Series()
                for ts, d in zip(index, days, strict=True)
            }
        ).T.reindex(index)
        out[name] = ic_series(panel.astype("float64"), labels, method=method, min_names=min_names)
    return pd.DataFrame(out, index=index)


def known_ic(
    history: pd.DataFrame, day: date, horizon: int, calendar: TradingCalendar
) -> pd.DataFrame:
    """The rows of `history` (IC dated by decision day) whose `horizon`-session label is
    known at the 08:00 decision of `day`: dated `d` with `offset(d, horizon) < day`."""
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    if history.empty:
        return history.copy()
    keep = [calendar.offset(pd.Timestamp(d).date(), horizon) < day for d in history.index]
    return history.loc[np.asarray(keep, dtype=bool)].copy()


def trailing_ic(
    history: pd.DataFrame,
    day: date,
    horizon: int,
    *,
    window_months: int,
    min_obs: int,
    calendar: TradingCalendar,
) -> pd.DataFrame:
    """Per column of `history`: `mean`, Newey-West `t_stat` (lags `nw_lags(horizon, dates)`)
    and `n_obs` of the ICs known at `day` (`known_ic`) dated within the last
    `window_months` months before `day`; `mean` and `t_stat` NaN with fewer than `min_obs`."""
    known = known_ic(history, day, horizon, calendar)
    start = pd.Timestamp(day) - pd.DateOffset(months=window_months)
    window = known.loc[pd.DatetimeIndex(known.index) > start]
    lags = nw_lags(horizon, list(window.index), calendar) if len(window) else 0
    rows = {}
    for col in history.columns:
        series = window[col].dropna() if col in window.columns else pd.Series(dtype="float64")
        n = len(series)
        enough = n >= min_obs
        rows[col] = {
            "mean": float(series.mean()) if enough else math.nan,
            "t_stat": newey_west_tstat(series, lags) if enough else math.nan,
            "n_obs": n,
        }
    out = pd.DataFrame.from_dict(rows, orient="index", columns=["mean", "t_stat", "n_obs"])
    return out.astype({"mean": "float64", "t_stat": "float64", "n_obs": "int64"})


def ic_weights(
    trailing: pd.DataFrame, fixed: Mapping[str, float]
) -> tuple[dict[str, float], WeightSource]:
    """Composite weights over the names of `fixed`: proportional to the positive trailing
    mean ICs (`trailing_ic` output; a NaN or non-positive mean gets 0) and source `ic`; when
    none is positive, `fixed` normalised to sum 1 and source `fixed`."""
    means = trailing["mean"].reindex(list(fixed)).astype("float64")
    positive = means.where(means > 0, 0.0).fillna(0.0)
    total = float(positive.sum())
    if total > 0:
        return {str(k): float(v) / total for k, v in positive.items()}, "ic"
    norm = math.fsum(abs(w) for w in fixed.values())
    if norm <= 0:
        raise ValueError("fixed weights must not all be zero")
    return {k: abs(w) / norm for k, w in fixed.items()}, "fixed"


def specific_vol(model: RiskModel, *, periods: int) -> pd.Series:
    """The annualised specific (residual) vol of each name of a Stage 19 `RiskModel`:
    `sqrt(specific_var * periods)` (`specific_var` is daily), named `resid_vol`."""
    var = model.specific_var.astype("float64")
    out: pd.Series = (var.clip(lower=0.0) * periods) ** 0.5
    return out.rename("resid_vol")
