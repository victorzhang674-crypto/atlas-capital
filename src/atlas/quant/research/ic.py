"""Information coefficients and signal decay (Stage 17).

The IC of a date is the cross-sectional correlation between the signal and the forward
return over the names that have both: `pearson` (linear) or `spearman` (Pearson of the
average ranks, ranked over those names only). A date with fewer than `min_names` such names,
or a constant signal or label, has no IC (NaN, dropped from summaries).

`summarize_ic` reports the mean IC, its standard deviation (ddof 1), the IR (mean / std, per
evaluation period, not annualised), the hit rate (share of dates with IC > 0) and a
Newey-West t-statistic of the mean: overlapping forward windows (a horizon longer than the
spacing of the evaluation dates) make consecutive ICs correlated, so the variance of the mean
uses Bartlett-weighted autocovariances up to `nw_lags(horizon, dates)` lags. The tear sheet's
t-statistics keep a date without an IC in its place (`keep_gaps=True`): the rows are the
evaluation dates, so only ICs that really are `l` dates apart are paired at lag `l`.

Decay: `ic_decay` gives the mean IC per horizon; `rank_autocorrelation` the mean Spearman
correlation between the signal on a date and `k` evaluation dates earlier (a persistence and
turnover proxy: 1 - lag-1 autocorrelation is the share of the ranking that changes).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.pit import guard_not_label
from atlas.quant.research.labels import ForwardReturns

Method = Literal["pearson", "spearman"]
METHODS: tuple[Method, ...] = ("pearson", "spearman")

SUMMARY_FIELDS = ("mean", "std", "ir", "t_stat", "lags", "hit_rate", "n_dates")
"""Fields of `ICSummary` (and columns of the by-group / by-regime tables)."""


def _min_names(min_names: int | None, config: AtlasConfig | None) -> int:
    if min_names is not None:
        if min_names < 2:
            raise ValueError(f"min_names must be at least 2, got {min_names}")
        return min_names
    cfg = config if config is not None else AtlasConfig()
    return cfg.quant.research.min_names


def _row_corr(x: np.ndarray, y: np.ndarray, min_names: int) -> np.ndarray:
    """Row-wise Pearson correlation over the entries where both are finite (NaN when fewer
    than `min_names` such entries or either side is constant)."""
    mask = ~np.isnan(x) & ~np.isnan(y)
    n = mask.sum(axis=1)
    xs = np.where(mask, x, 0.0)
    ys = np.where(mask, y, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mx = xs.sum(axis=1) / n
        my = ys.sum(axis=1) / n
        dx = np.where(mask, x - mx[:, None], 0.0)
        dy = np.where(mask, y - my[:, None], 0.0)
        corr = (dx * dy).sum(axis=1) / np.sqrt((dx * dx).sum(axis=1) * (dy * dy).sum(axis=1))
    spread_x = np.where(mask, x, -np.inf).max(axis=1) > np.where(mask, x, np.inf).min(axis=1)
    spread_y = np.where(mask, y, -np.inf).max(axis=1) > np.where(mask, y, np.inf).min(axis=1)
    valid = (n >= min_names) & spread_x & spread_y
    return np.where(valid, corr, np.nan)


def _ranks(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Average ranks (1..n) of each row over the masked entries (NaN elsewhere)."""
    frame = pd.DataFrame(np.where(mask, values, np.nan))
    return frame.rank(axis=1, method="average").to_numpy(dtype="float64")


def _corr(x: np.ndarray, y: np.ndarray, method: Method, min_names: int) -> np.ndarray:
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}, got {method!r}")
    if method == "spearman":
        mask = ~np.isnan(x) & ~np.isnan(y)
        x, y = _ranks(x, mask), _ranks(y, mask)
    return _row_corr(x, y, min_names)


def ic_series(
    signal: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    method: Method = "spearman",
    min_names: int | None = None,
    config: AtlasConfig | None = None,
) -> pd.Series:
    """The IC of `signal` (dates x symbols) with `labels` (forward returns, aligned by date
    and symbol; dates or names missing from `labels` count as missing) on each date of
    `signal`: a Series named `ic_<method>` (NaN where undefined, see the module docstring).
    `min_names` defaults to `quant.research.min_names`. A label as `signal` raises
    `LookAheadError`."""
    guard_not_label(signal, "signal")
    k = _min_names(min_names, config)
    x = signal.to_numpy(dtype="float64")
    y = labels.reindex(index=signal.index, columns=signal.columns).to_numpy(dtype="float64")
    out = _corr(x, y, method, k)
    return pd.Series(out, index=signal.index, name=f"ic_{method}", dtype="float64")


def newey_west_tstat(series: pd.Series, lags: int, *, keep_gaps: bool = False) -> float:
    """t-statistic of the mean of `series` (NaNs dropped) with the Newey-West (Bartlett)
    long-run variance: `gamma_0 + 2 * sum_{l=1..L} (1 - l/(L+1)) gamma_l`, autocovariances
    divided by n. `lags=0` gives `mean / (std_pop / sqrt(n))`. NaN with fewer than 2 values, a
    constant series or a non-positive variance.

    `keep_gaps`: the rows of `series` are equally spaced and a NaN is a missing observation
    that keeps its place, so `gamma_l` sums only the pairs of values exactly `l` rows apart
    (the estimator for series with missing observations: deviations from the mean, zero where
    missing, then the usual formula with n = the number of values). Without it the NaNs are
    squeezed out first and values on either side of a gap count as neighbours. The two agree
    when the values are consecutive."""
    if isinstance(lags, bool) or not isinstance(lags, int) or lags < 0:
        raise ValueError(f"lags must be a non-negative int, got {lags!r}")
    raw = series.to_numpy(dtype="float64")
    x = raw[~np.isnan(raw)]
    n = len(x)
    if n < 2 or x.max() == x.min():  # constant: rounding in the mean would fake a variance
        return math.nan
    mean = float(x.mean())
    d = np.where(np.isnan(raw), 0.0, raw - mean) if keep_gaps else x - mean
    variance = float(d @ d) / n
    for lag in range(1, min(lags, len(d) - 1) + 1):
        weight = 1.0 - lag / (lags + 1)
        variance += 2.0 * weight * float(d[lag:] @ d[:-lag]) / n
    if not variance > 0:
        return math.nan
    return mean / math.sqrt(variance / n)


def session_step(
    dates: Sequence[date | pd.Timestamp], calendar: TradingCalendar | None = None
) -> int:
    """The median number of sessions between consecutive `dates` (trading sessions,
    ascending), rounded, at least 1 (1 with fewer than two dates)."""
    days = [pd.Timestamp(d).date() for d in dates]
    if len(days) < 2:
        return 1
    cal = calendar if calendar is not None else TradingCalendar()
    grid = pd.DatetimeIndex(pd.to_datetime(list(cal.sessions(days[0], days[-1]))))
    positions = grid.get_indexer(pd.DatetimeIndex(pd.to_datetime(days)))
    if (positions < 0).any():
        raise ValueError("dates must be trading sessions")
    return max(round(float(np.median(np.diff(positions)))), 1)


def nw_lags(
    horizon: int, dates: Sequence[date | pd.Timestamp], calendar: TradingCalendar | None = None
) -> int:
    """Newey-West lags for ICs of `horizon`-session labels on evaluation `dates`:
    `max(ceil(horizon / step) - 1, 0)`, `step` = `session_step(dates)`."""
    return max(math.ceil(horizon / session_step(dates, calendar)) - 1, 0)


@dataclass(frozen=True)
class ICSummary:
    """Summary of an IC series: `mean`, `std` (ddof 1), `ir` = mean / std (per evaluation
    period), `t_stat` (Newey-West with `lags`), `hit_rate` (share of dates with IC > 0) over
    `n_dates` dates with an IC. NaN when undefined."""

    mean: float
    std: float
    ir: float
    t_stat: float
    lags: int
    hit_rate: float
    n_dates: int

    def to_dict(self) -> dict[str, float | int]:
        """The fields as a plain dict (NaN kept)."""
        return {name: getattr(self, name) for name in SUMMARY_FIELDS}


def summarize_ic(series: pd.Series, lags: int, *, keep_gaps: bool = False) -> ICSummary:
    """`ICSummary` of `series` (NaNs dropped) with `lags` Newey-West lags; `keep_gaps` as in
    `newey_west_tstat` (it changes the t-statistic only)."""
    valid = series.dropna().to_numpy(dtype="float64")
    n = len(valid)
    mean = float(valid.mean()) if n else math.nan
    std = float(valid.std(ddof=1)) if n >= 2 else math.nan
    ir = mean / std if n >= 2 and std > 0 else math.nan
    return ICSummary(
        mean=mean,
        std=std,
        ir=ir,
        t_stat=newey_west_tstat(series, lags, keep_gaps=keep_gaps),
        lags=lags,
        hit_rate=float((valid > 0).mean()) if n else math.nan,
        n_dates=n,
    )


def ic_by_group(
    signal: pd.DataFrame,
    labels: pd.DataFrame,
    groups: pd.Series,
    *,
    lags: int,
    method: Method = "spearman",
    min_names: int | None = None,
    config: AtlasConfig | None = None,
) -> pd.DataFrame:
    """The IC summary inside each group (e.g. sector) of `groups` (symbol -> label), each
    computed on that group's own cross-section per date (`min_names` defaults to
    `quant.research.min_group_names`). Symbols with no group (None/NaN) are left out, never
    pooled. One row per group (sorted): `SUMMARY_FIELDS` + `names` (symbols in the group)."""
    guard_not_label(signal, "signal")
    if min_names is None:
        cfg = config if config is not None else AtlasConfig()
        min_names = cfg.quant.research.min_group_names
    mapped = groups.reindex(signal.columns)
    rows = {}
    for group in sorted({g for g in mapped if isinstance(g, str)}):
        cols = [c for c, g in mapped.items() if g == group]
        series = ic_series(signal[cols], labels, method=method, min_names=min_names)
        summary = summarize_ic(series, lags, keep_gaps=True)  # rows are evaluation dates
        rows[group] = {**summary.to_dict(), "names": len(cols)}
    columns = [*SUMMARY_FIELDS, "names"]
    return pd.DataFrame.from_dict(rows, orient="index", columns=columns).rename_axis("group")


def ic_by_regime(series: pd.Series, regimes: pd.Series, *, lags: int) -> pd.DataFrame:
    """The IC summary of the dates of each regime (`regimes`: date -> label, aligned with the
    IC `series`' index; dates without a regime are left out). One row per regime (sorted),
    columns `SUMMARY_FIELDS`. A regime's dates are not consecutive, so its t-statistic keeps
    the gaps (`newey_west_tstat(keep_gaps=True)`): only ICs that are really `l` evaluation
    dates apart enter the lag-`l` autocovariance."""
    mapped = regimes.reindex(series.index)
    rows = {}
    for regime in sorted({r for r in mapped if isinstance(r, str)}):
        rows[regime] = summarize_ic(series.where(mapped == regime), lags, keep_gaps=True).to_dict()
    return pd.DataFrame.from_dict(rows, orient="index", columns=list(SUMMARY_FIELDS)).rename_axis(
        "regime"
    )


def ic_decay(
    signal: pd.DataFrame,
    labels: ForwardReturns | Mapping[int, pd.DataFrame],
    *,
    method: Method = "spearman",
    min_names: int | None = None,
    config: AtlasConfig | None = None,
) -> pd.Series:
    """Mean IC of `signal` per horizon of `labels` (a `ForwardReturns` or horizon -> label
    frame): index `horizon` (ascending)."""
    frames = (
        {h: labels.horizon(h) for h in labels.horizons}
        if isinstance(labels, ForwardReturns)
        else dict(labels)
    )
    out = {
        h: ic_series(signal, frames[h], method=method, min_names=min_names, config=config).mean()
        for h in sorted(frames)
    }
    return pd.Series(out, name=f"ic_{method}", dtype="float64").rename_axis("horizon")


def rank_autocorrelation(
    signal: pd.DataFrame,
    lags: Sequence[int],
    *,
    min_names: int | None = None,
    config: AtlasConfig | None = None,
) -> pd.Series:
    """Mean over dates of the Spearman correlation between the signal on a date and `lag`
    rows (evaluation dates) earlier, over the names present on both: index `lag`."""
    guard_not_label(signal, "signal")
    k = _min_names(min_names, config)
    values = signal.to_numpy(dtype="float64")
    out = {}
    for lag in lags:
        if isinstance(lag, bool) or not isinstance(lag, int) or lag < 1:
            raise ValueError(f"lags must be positive ints, got {lag!r}")
        if lag >= len(values):
            out[lag] = math.nan
            continue
        corr = _corr(values[lag:], values[:-lag], "spearman", k)
        out[lag] = float(np.mean(corr[~np.isnan(corr)])) if (~np.isnan(corr)).any() else math.nan
    return pd.Series(out, name="rank_autocorrelation", dtype="float64").rename_axis("lag")
