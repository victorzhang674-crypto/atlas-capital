"""Rule-based volatility and trend regimes (Stage 21).

Every function is vectorised over a time series indexed by trading session (ascending) and only
looks backwards along it: the value at session `s` uses rows `<= s`, so a series computed on a
truncated history equals the full one on the common rows (point in time).

Units: realised volatility is an annualised fraction (0.2 = 20%); VIX is in index points;
percentiles and shares are fractions in [0, 1]; `price_to_sma_200` is a ratio (1 = at the
average); momentum is a simple return fraction. Labels:

- volatility: `LOW` / `NORMAL` / `HIGH` / `CRISIS` (`classify_vol`, thresholds in
  `quant.regime.vol`);
- trend: `UP` / `NEUTRAL` / `DOWN` (`classify_trend`, thresholds in `quant.regime.trend`).

A label that cannot be computed is `None`.
"""

from typing import Literal

import numpy as np
import pandas as pd

from atlas.core.config import RegimeTrendConfig, RegimeVolConfig

VolRegime = Literal["LOW", "NORMAL", "HIGH", "CRISIS"]
"""Volatility regime label."""

TrendRegime = Literal["UP", "NEUTRAL", "DOWN"]
"""Trend regime label."""

VOL_LABELS: tuple[VolRegime, ...] = ("LOW", "NORMAL", "HIGH", "CRISIS")
"""Volatility labels, calmest first."""

TREND_LABELS: tuple[TrendRegime, ...] = ("UP", "NEUTRAL", "DOWN")
"""Trend labels, most bullish first."""

TREND_VOTES: tuple[str, ...] = ("ma", "momentum", "breadth")
"""Columns of `trend_votes`."""


def _labels(values: np.ndarray, valid: np.ndarray, index: pd.Index) -> pd.Series:
    out = pd.Series(values, index=index, dtype="object")
    return out.where(pd.Series(valid, index=index), None)


def trailing_percentile(series: pd.Series, window: int, min_obs: int) -> pd.Series:
    """The empirical percentile of each value within its trailing window: the share of the
    non-missing values among the last `window` rows (this row included) that are at or below
    it (ties count as below), a fraction in (0, 1]. NaN where the value is missing or the
    window holds fewer than `min_obs` values."""
    if window < 1 or min_obs < 1 or min_obs > window:
        raise ValueError(f"need 1 <= min_obs <= window, got window={window}, min_obs={min_obs}")
    values = series.astype("float64")
    ranked = values.rolling(window, min_periods=min_obs).rank(method="max", pct=True)
    return ranked.where(values.notna())


def vol_score(rv_percentile: pd.Series, vix_percentile: pd.Series) -> pd.Series:
    """The volatility score: the larger of the realised-vol and VIX percentiles (fractions);
    the realised percentile alone where VIX is missing (and vice versa); NaN when both are."""
    both = pd.concat([rv_percentile, vix_percentile], axis=1)
    return both.max(axis=1, skipna=True).astype("float64")


def classify_vol(
    score: pd.Series, vix: pd.Series, realised_vol: pd.Series, config: RegimeVolConfig
) -> pd.Series:
    """The volatility regime per session from the score (`vol_score`), VIX (index points) and
    realised vol (annualised fraction): score < `low_below` → LOW, < `high_from` → NORMAL,
    < `crisis_from` → HIGH, else CRISIS when VIX ≥ `crisis_vix_floor` or realised vol ≥
    `crisis_rv_floor`, otherwise HIGH. `None` where the score is missing."""
    s = score.to_numpy(dtype="float64")
    v = vix.reindex(score.index).to_numpy(dtype="float64")
    r = realised_vol.reindex(score.index).to_numpy(dtype="float64")
    with np.errstate(invalid="ignore"):
        floor = (v >= config.crisis_vix_floor) | (r >= config.crisis_rv_floor)
        labels = np.select(
            [s < config.low_below, s < config.high_from, s < config.crisis_from, floor],
            ["LOW", "NORMAL", "HIGH", "CRISIS"],
            default="HIGH",
        )
    return _labels(labels, ~np.isnan(s), score.index)


def breadth(ratio: pd.DataFrame, members: pd.DataFrame, min_names: int) -> pd.Series:
    """Market breadth per session: among the names that are members (`members`, boolean) and
    have a `price_to_sma_200` value (`ratio`), the share whose ratio is above 1 (a fraction).
    NaN with fewer than `min_names` such names. The frames must share index and columns."""
    if not (ratio.index.equals(members.index) and ratio.columns.equals(members.columns)):
        raise ValueError("ratio and members must be aligned (same index and columns)")
    eligible = members.fillna(False).astype(bool) & ratio.notna()
    count = eligible.sum(axis=1)
    above = (eligible & (ratio > 1.0)).sum(axis=1)
    share = above / count.where(count > 0)
    return share.where(count >= min_names).astype("float64")


def trend_votes(
    price_to_sma: pd.Series,
    momentum: pd.Series,
    breadth_share: pd.Series,
    config: RegimeTrendConfig,
) -> pd.DataFrame:
    """The three trend votes per session (columns `ma`, `momentum`, `breadth`): 1.0 bullish,
    0.0 bearish, NaN missing. Bullish: benchmark close above its 200-session SMA
    (`price_to_sma` > 1); 12-1 momentum > 0; breadth > `breadth_bull_above`."""

    def vote(series: pd.Series, threshold: float) -> pd.Series:
        values = series.reindex(price_to_sma.index).astype("float64")
        return (values > threshold).astype("float64").where(values.notna())

    return pd.DataFrame(
        {
            "ma": vote(price_to_sma, 1.0),
            "momentum": vote(momentum, 0.0),
            "breadth": vote(breadth_share, config.breadth_bull_above),
        },
        index=price_to_sma.index,
    )


def classify_trend(votes: pd.DataFrame, config: RegimeTrendConfig) -> pd.Series:
    """The trend regime per session from `trend_votes`: the bullish share of the available
    votes ≥ `up_share` → UP, ≤ `down_share` → DOWN, else NEUTRAL. `None` without the
    benchmark's moving-average vote (`ma`)."""
    share = votes[list(TREND_VOTES)].mean(axis=1, skipna=True).to_numpy(dtype="float64")
    labels = np.select(
        [share >= config.up_share, share <= config.down_share], ["UP", "DOWN"], default="NEUTRAL"
    )
    return _labels(labels, votes["ma"].notna().to_numpy(), votes.index)
