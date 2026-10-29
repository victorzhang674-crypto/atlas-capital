"""Derived macro series: YoY %, 3-month annualised %, z-score, curve slope, real rate.

Every function takes one series of a point-in-time frame (`macro_pit`: a float `pd.Series`
indexed by an ascending, tz-naive `DatetimeIndex` of observation dates) and only looks
backwards along it, so computing on a snapshot taken at `as_of` uses nothing unknown then.
Results are in percent or percentage points (3.0 = 3%), so they combine with FRED's yields,
which are in percent. Thresholds come from `MacroConfig` (`config=None` → the defaults).
"""

import numpy as np
import pandas as pd

from atlas.core.config import MacroConfig


def _check(series: pd.Series, what: str = "series") -> None:
    if not isinstance(series.index, pd.DatetimeIndex):
        raise ValueError(f"{what} must be indexed by a DatetimeIndex of observation dates")
    if not series.index.is_monotonic_increasing:
        raise ValueError(f"{what}: observation dates must be ascending")


def _lagged(series: pd.Series, months: int, tolerance_days: int) -> pd.Series:
    """For each date t, the value at the latest observation on or before t - `months` months,
    if it is at most `tolerance_days` days before that target; else NaN."""
    dates = pd.DatetimeIndex(series.index).as_unit("ns")
    targets = pd.DataFrame({"target": dates - pd.DateOffset(months=months)})
    values = pd.DataFrame({"target": dates, "lagged": series.to_numpy(dtype="float64")})
    found = pd.merge_asof(
        targets,
        values,
        on="target",
        direction="backward",
        tolerance=pd.Timedelta(days=tolerance_days),
    )
    return pd.Series(found["lagged"].to_numpy(), index=series.index)


def _settings(config: MacroConfig | None) -> MacroConfig:
    return config if config is not None else MacroConfig()


def yoy_pct(series: pd.Series, config: MacroConfig | None = None) -> pd.Series:
    """Year-over-year change in percent: 100·(x_t / x_{t-12 months} - 1).

    The year-ago value is the latest observation on or before t - 12 months, at most
    `transform_lag_tolerance_days` early (a Saturday target uses Friday); NaN without one.
    """
    _check(series)
    lag = _lagged(series, 12, _settings(config).transform_lag_tolerance_days)
    return 100.0 * (series / lag - 1.0)


def annualized_3m_pct(series: pd.Series, config: MacroConfig | None = None) -> pd.Series:
    """Three-month change annualised, in percent: 100·((x_t / x_{t-3 months})⁴ - 1), with the
    lagged value found as in `yoy_pct`."""
    _check(series)
    lag = _lagged(series, 3, _settings(config).transform_lag_tolerance_days)
    return 100.0 * ((series / lag) ** 4 - 1.0)


def zscore(series: pd.Series, config: MacroConfig | None = None) -> pd.Series:
    """Z-score against the trailing `zscore_window_years` years (window of
    round(365.25 · years) days ending at t, sample std): (x_t - mean) / std.

    NaN until the series spans `zscore_min_years` years from its first observation, and where
    the window has no dispersion.
    """
    _check(series)
    settings = _settings(config)
    window = f"{round(365.25 * settings.zscore_window_years)}D"
    rolling = series.rolling(window, min_periods=2)
    z = (series - rolling.mean()) / rolling.std(ddof=1)
    span = series.index - series.index[0] if len(series) else series.index
    enough = np.asarray(span >= pd.Timedelta(days=365.25 * settings.zscore_min_years))
    return z.where(enough & np.isfinite(z.to_numpy()))


def curve_slope(long: pd.Series, short: pd.Series) -> pd.Series:
    """Long minus short yield on the dates both have, in percentage points (e.g. DGS10 - DGS2,
    FRED's T10Y2Y)."""
    _check(long, "long")
    _check(short, "short")
    both = long.index.intersection(short.index)
    return (long.loc[both] - short.loc[both]).rename(None)


def real_rate(nominal: pd.Series, inflation_pct: pd.Series) -> pd.Series:
    """Nominal yield minus inflation, in percentage points (brief: DGS10 - core PCE YoY).

    Each nominal date uses the latest inflation observation dated on or before it (a monthly
    YoY dated the 1st covers its month); NaN before the first one.
    """
    _check(nominal, "nominal")
    _check(inflation_pct, "inflation_pct")
    left = pd.DataFrame(
        {
            "obs_date": pd.DatetimeIndex(nominal.index).as_unit("ns"),
            "nominal": nominal.to_numpy(dtype="float64"),
        }
    )
    right = pd.DataFrame(
        {
            "obs_date": pd.DatetimeIndex(inflation_pct.index).as_unit("ns"),
            "inflation": inflation_pct.to_numpy(dtype="float64"),
        }
    )
    joined = pd.merge_asof(left, right, on="obs_date", direction="backward")
    return pd.Series((joined["nominal"] - joined["inflation"]).to_numpy(), index=nominal.index)
