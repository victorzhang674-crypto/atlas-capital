"""Macro quadrant, credit stress and the stress flag (Stage 21).

Inputs are point-in-time FRED series (`DataView.macro` columns: a float `pd.Series` indexed by
an ascending, tz-naive `DatetimeIndex` of observation dates, values in FRED units; rows of
other series in a joint frame are NaN and are dropped here). Every function reads only the
past of the series it is given, so a value computed on a snapshot taken at `as_of` uses nothing
unknown then.

- **Growth votes** (+1 up, -1 down, NaN missing): `INDPRO` and `PAYEMS` acceleration = the
  3-month annualised % change minus the year-over-year % change (percentage points; > 0 = up);
  `ICSA` claims trend = mean of the last `claims_short_weeks` weekly claims minus the mean of
  the last `claims_long_weeks` (claims, a count; rising claims = growth down). A zero counts as
  down (fail safe).
- **Inflation gap** = core CPI (`CPILFESL`) 3-month annualised % minus YoY % (pp); ≥ 0 counts
  as rising (fail safe).
- **Quadrant** (`classify_quadrant`): growth up ⇔ mean vote > 0; up & falling → Goldilocks,
  up & rising → Reflation, down & rising → Stagflation, down & falling → Deflation.
- **Credit proxy** (`credit_proxy`): duration-hedged HY excess log return
  `x = ln(L_t/L_{t-1}) - h · ln(H_t/H_{t-1})` (`L` HY ETF, `H` Treasury ETF, adjusted closes);
  `change = -x` (log-return units, positive = spreads widening) and `level` = its running sum.
- **Credit z-score** (`credit_zscore`) of the HY OAS level (percent) or of the proxy level over
  the trailing `zscore_window` observations; **stress flag** = z > `zscore_threshold` or NFCI >
  `nfci_threshold` (`stress_flag`).
"""

import math
from collections.abc import Mapping
from typing import Literal

import numpy as np
import pandas as pd

from atlas.core.config import MacroConfig, RegimeConfig, RegimeCreditConfig, RegimeMacroConfig
from atlas.data.macro.transforms import annualized_3m_pct, yoy_pct

Quadrant = Literal["Goldilocks", "Reflation", "Stagflation", "Deflation"]
"""Macro quadrant label."""

QUADRANTS: tuple[Quadrant, ...] = ("Goldilocks", "Reflation", "Stagflation", "Deflation")
"""Quadrant labels."""

CreditSource = Literal["oas", "proxy"]
"""Where a credit z-score came from."""

StressInput = Literal["credit", "nfci"]
"""Inputs of the stress flag (reported when missing)."""

GROWTH_SERIES: tuple[str, ...] = ("INDPRO", "PAYEMS", "ICSA")
"""FRED ids of the growth votes, in vote order."""

INFLATION_SERIES = "CPILFESL"
"""FRED id of the inflation input (core CPI)."""

NFCI_SERIES = "NFCI"
"""FRED id of the Chicago Fed National Financial Conditions Index."""


def _clean(series: pd.Series | None) -> pd.Series:
    if series is None:
        return pd.Series(dtype="float64")
    return series.dropna().astype("float64")


def acceleration(series: pd.Series | None, config: MacroConfig | None = None) -> float:
    """The latest 3-month annualised % change minus the year-over-year % change of a monthly
    index (percentage points), from Stage 10's `annualized_3m_pct` / `yoy_pct`; NaN without
    the lagged values."""
    s = _clean(series)
    if len(s) < 2:
        return math.nan
    gap = annualized_3m_pct(s, config).iloc[-1] - yoy_pct(s, config).iloc[-1]
    return float(gap) if np.isfinite(gap) else math.nan


def claims_trend(claims: pd.Series | None, short_weeks: int, long_weeks: int) -> float:
    """Mean of the last `short_weeks` weekly initial claims minus the mean of the last
    `long_weeks` (claims, a count); NaN with fewer than `long_weeks` observations."""
    s = _clean(claims)
    if len(s) < long_weeks:
        return math.nan
    return float(s.iloc[-short_weeks:].mean() - s.iloc[-long_weeks:].mean())


def _vote(value: float, up_when_positive: bool = True) -> float:
    if not math.isfinite(value):
        return math.nan
    up = value > 0 if up_when_positive else value < 0
    return 1.0 if up else -1.0


def growth_votes(
    indpro: pd.Series | None,
    payems: pd.Series | None,
    claims: pd.Series | None,
    config: RegimeMacroConfig,
    macro_config: MacroConfig | None = None,
) -> dict[str, float]:
    """The growth votes {`INDPRO`, `PAYEMS`, `ICSA`} → +1 (up), -1 (down) or NaN (missing):
    production and payrolls up when accelerating, claims up when their trend is falling; a
    zero counts as down."""
    return {
        "INDPRO": _vote(acceleration(indpro, macro_config)),
        "PAYEMS": _vote(acceleration(payems, macro_config)),
        "ICSA": _vote(
            claims_trend(claims, config.claims_short_weeks, config.claims_long_weeks),
            up_when_positive=False,
        ),
    }


def growth_score(votes: Mapping[str, float]) -> float:
    """The mean of the available growth votes (in [-1, 1]); NaN when none is available."""
    values = [v for v in votes.values() if math.isfinite(v)]
    return float(np.mean(values)) if values else math.nan


def inflation_gap(core_cpi: pd.Series | None, config: MacroConfig | None = None) -> float:
    """Core CPI 3-month annualised % minus YoY % (percentage points; ≥ 0 = rising); NaN when
    missing."""
    return acceleration(core_cpi, config)


def classify_quadrant(growth: float, inflation: float) -> Quadrant | None:
    """The quadrant of a growth score (mean vote; up when > 0) and an inflation gap (rising
    when ≥ 0); `None` when either is missing."""
    if not (math.isfinite(growth) and math.isfinite(inflation)):
        return None
    up = growth > 0
    rising = inflation >= 0
    if up:
        return "Reflation" if rising else "Goldilocks"
    return "Stagflation" if rising else "Deflation"


def credit_proxy(long_close: pd.Series, hedge_close: pd.Series, hedge_ratio: float) -> pd.DataFrame:
    """The duration-hedged credit-spread proxy per session (see the module docstring) from the
    adjusted closes of the HY ETF (`long_close`) and the Treasury ETF (`hedge_close`): columns
    `change` (-excess log return; NaN where either leg lacks this or the previous close) and
    `level` (running sum of `change`: 0 on the session before the first change, NaN before
    that, flat over gaps)."""
    if hedge_ratio < 0:
        raise ValueError(f"hedge_ratio must be non-negative, got {hedge_ratio}")
    long_close, hedge_close = long_close.align(hedge_close, join="outer")
    with np.errstate(divide="ignore", invalid="ignore"):
        excess = np.log(long_close).diff() - hedge_ratio * np.log(hedge_close).diff()
    change = (-excess).astype("float64")
    level = change.fillna(0.0).cumsum()
    first = change.first_valid_index()
    if first is None:
        level[:] = np.nan
    else:  # the level starts at 0 on the session before the first change
        level.iloc[: max(change.index.get_loc(first) - 1, 0)] = np.nan
    return pd.DataFrame({"change": change, "level": level})


def trailing_zscore(series: pd.Series, window: int) -> pd.Series:
    """(x - mean) / std (ddof 1) of each value against the trailing `window` non-missing
    observations (itself included); NaN before `window` observations or without dispersion."""
    if window < 2:
        raise ValueError(f"window must be at least 2, got {window}")
    s = _clean(series)
    roll = s.rolling(window, min_periods=window)
    std = roll.std(ddof=1)
    z = (s - roll.mean()) / std.where(std > 0)
    return z.astype("float64")


def _last(series: pd.Series) -> float:
    return float(series.iloc[-1]) if len(series) and math.isfinite(series.iloc[-1]) else math.nan


def credit_zscore(
    oas: pd.Series | None, proxy_level: pd.Series | None, config: RegimeCreditConfig
) -> tuple[float, CreditSource | None]:
    """The latest credit z-score and its source. `source` `auto`: the HY OAS level (percent)
    when at least `zscore_window` OAS observations are given, else the proxy level; `oas` /
    `proxy` force one (NaN when it is too short). (NaN, None) when neither input is given."""
    oas_clean = _clean(oas)
    use_oas = config.source == "oas" or (
        config.source == "auto" and len(oas_clean) >= config.zscore_window
    )
    if use_oas:
        return _last(trailing_zscore(oas_clean, config.zscore_window)), "oas"
    if proxy_level is None:
        return math.nan, None
    return _last(trailing_zscore(proxy_level, config.zscore_window)), "proxy"


def stress_flag(
    credit_z: float, nfci: float, config: RegimeConfig
) -> tuple[bool, tuple[StressInput, ...]]:
    """The stress flag and the missing inputs (`credit`, `nfci`): credit z-score >
    `credit.zscore_threshold` or NFCI > `stress.nfci_threshold`; a missing input is left out;
    both missing → False."""
    inputs: tuple[tuple[StressInput, float], ...] = (("credit", credit_z), ("nfci", nfci))
    missing = tuple(name for name, v in inputs if not math.isfinite(v))
    flag = (math.isfinite(credit_z) and credit_z > config.credit.zscore_threshold) or (
        math.isfinite(nfci) and nfci > config.stress.nfci_threshold
    )
    return bool(flag), missing
