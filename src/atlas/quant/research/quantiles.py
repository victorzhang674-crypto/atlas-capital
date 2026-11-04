"""Quantile analysis of a signal (Stage 17).

On each evaluation date the names with a signal are split into `n_quantiles` buckets by
average rank: `bucket = floor((rank - 1) * q / n) + 1` (1 = lowest signal, q = highest; equal
sizes up to one name; equal signals share a bucket, so a constant signal puts every name in
one bucket and has no spread). Buckets use the signal only, never the labels.

For a horizon: the mean forward return of each bucket per date (names without a label left
out), averaged over dates; the monotonicity score = Spearman correlation between bucket number
and mean return (+1: returns rise steadily with the signal, -1: they fall); the long-short
spread = top bucket minus bottom bucket per date, with its mean and Newey-West t. The
cumulative spread chart data is daily: each date's buckets are held from the next session to
the next evaluation date, each bucket an equal-weight average of its names' daily returns,
and the spread compounded: `prod(1 + top - bottom) - 1` (a session without both buckets adds 0).
Units: returns as fractions.
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.core.pit import guard_not_label
from atlas.quant.research.ic import newey_west_tstat, nw_lags
from atlas.quant.research.labels import ForwardReturns


def quantile_buckets(
    signal: pd.DataFrame,
    n_quantiles: int | None = None,
    *,
    min_names: int | None = None,
    config: AtlasConfig | None = None,
) -> pd.DataFrame:
    """The bucket (1..`n_quantiles`, as floats) of each name with a signal on each date; NaN
    for missing names and on dates with fewer than `min_names` names. Defaults:
    `quant.research.n_quantiles` and `min_names`."""
    guard_not_label(signal, "signal")
    cfg = config if config is not None else AtlasConfig()
    q = cfg.quant.research.n_quantiles if n_quantiles is None else n_quantiles
    k = cfg.quant.research.min_names if min_names is None else min_names
    if q < 2:
        raise ValueError(f"n_quantiles must be at least 2, got {q}")
    ranks = signal.astype("float64").rank(axis=1, method="average").to_numpy(dtype="float64")
    n = (~np.isnan(ranks)).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        buckets = np.floor((ranks - 1.0) * q / n[:, None]) + 1.0
    buckets[n < max(k, q)] = np.nan
    return pd.DataFrame(buckets, index=signal.index, columns=signal.columns)


def quantile_returns(buckets: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Mean of `labels` (aligned by date and name) per bucket per date: index = the dates of
    `buckets`, columns 1..q (ints); NaN where a bucket has no name with a label."""
    y = labels.reindex(index=buckets.index, columns=buckets.columns).to_numpy(dtype="float64")
    b = buckets.to_numpy(dtype="float64")
    top = int(np.nanmax(b)) if (~np.isnan(b)).any() else 0
    out = {}
    for bucket in range(1, top + 1):
        mask = (b == bucket) & ~np.isnan(y)
        count = mask.sum(axis=1)
        total = np.where(mask, y, 0.0).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[bucket] = np.where(count > 0, total / count, np.nan)
    return pd.DataFrame(out, index=buckets.index, dtype="float64")


def monotonicity(means: pd.Series) -> float:
    """Spearman correlation between bucket number (the index order) and the mean returns:
    +1 when the means rise with the bucket, -1 when they fall; NaN if a mean is missing or
    all are equal."""
    values = means.to_numpy(dtype="float64")
    if len(values) < 2 or np.isnan(values).any() or values.max() == values.min():
        return math.nan
    ranks = pd.Series(values).rank(method="average").to_numpy()
    order = np.arange(1, len(values) + 1, dtype="float64")
    return float(np.corrcoef(order, ranks)[0, 1])


def held_rows(dates: pd.DatetimeIndex, sessions: pd.DatetimeIndex) -> np.ndarray:
    """For each session, the position in `dates` (ascending) of the latest date strictly
    before it (-1 when none): the decision whose positions earn that session's return."""
    return np.asarray(dates.searchsorted(sessions, side="left"), dtype="int64") - 1


def daily_quantile_returns(buckets: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Equal-weight daily return of each bucket (columns 1..q), for the sessions of `daily`
    after the first evaluation date: a session uses the buckets of the latest evaluation date
    strictly before it."""
    sessions = pd.DatetimeIndex(daily.index)
    held = buckets.reindex(columns=daily.columns).to_numpy(dtype="float64")
    latest = held_rows(pd.DatetimeIndex(buckets.index), sessions)
    start = latest >= 0
    b = held[latest[start]]
    r = daily.to_numpy(dtype="float64")[start]
    top = int(np.nanmax(buckets.to_numpy(dtype="float64"))) if buckets.notna().any().any() else 0
    out = {}
    for bucket in range(1, top + 1):
        mask = (b == bucket) & ~np.isnan(r)
        count = mask.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[bucket] = np.where(count > 0, np.where(mask, r, 0.0).sum(axis=1) / count, np.nan)
    return pd.DataFrame(out, index=sessions[start], dtype="float64")


@dataclass(frozen=True, eq=False)
class QuantileAnalysis:
    """Quantile analysis of one horizon (module docstring): `by_date` (date x bucket mean
    label), `mean` (bucket -> mean over dates), `counts` (date x bucket names with a signal),
    `spread` (top - bottom per date), `spread_mean`, `spread_t` (Newey-West, `lags`),
    `monotonicity`, `daily` (session x bucket daily return) and `cumulative_spread`."""

    horizon: int
    n_quantiles: int
    by_date: pd.DataFrame
    mean: pd.Series
    counts: pd.DataFrame
    spread: pd.Series
    spread_mean: float
    spread_t: float
    lags: int
    monotonicity: float
    daily: pd.DataFrame
    cumulative_spread: pd.Series


def quantile_analysis(
    signal: pd.DataFrame,
    labels: ForwardReturns,
    horizon: int,
    *,
    n_quantiles: int | None = None,
    min_names: int | None = None,
    lags: int | None = None,
    config: AtlasConfig | None = None,
) -> QuantileAnalysis:
    """`QuantileAnalysis` of `signal` against the `horizon` labels of `labels` (and its
    daily returns for the cumulative spread). `lags` defaults to `nw_lags(horizon, dates)`."""
    guard_not_label(signal, "signal")
    target = labels.horizon(horizon)
    cfg = config if config is not None else AtlasConfig()
    q = cfg.quant.research.n_quantiles if n_quantiles is None else n_quantiles
    buckets = quantile_buckets(signal, q, min_names=min_names, config=cfg)
    by_date = quantile_returns(buckets, target).reindex(columns=range(1, q + 1))
    counts = pd.DataFrame(
        {b: (buckets == b).sum(axis=1) for b in range(1, q + 1)}, index=buckets.index
    )
    spread = (by_date[q] - by_date[1]).rename("spread")
    lag = nw_lags(horizon, list(signal.index)) if lags is None else lags
    daily = daily_quantile_returns(buckets, labels.daily).reindex(columns=range(1, q + 1))
    daily_spread = (daily[q] - daily[1]).fillna(0.0)
    mean = by_date.mean()
    return QuantileAnalysis(
        horizon=horizon,
        n_quantiles=q,
        by_date=by_date,
        mean=mean,
        counts=counts,
        spread=spread,
        spread_mean=float(spread.mean()),
        spread_t=newey_west_tstat(spread, lag),
        lags=lag,
        monotonicity=monotonicity(mean),
        daily=daily,
        cumulative_spread=((1.0 + daily_spread).cumprod() - 1.0).rename("cumulative_spread"),
    )
