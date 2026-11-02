"""The technical feature library (Stage 15): 40 features registered in `REGISTRY`.

Every feature is a function of a `PricePanel` (adjusted OHLC in USD, raw dollar volume in USD,
simple daily returns), vectorised across symbols. Notation in the docstrings: `C`, `O`, `H`,
`L` adjusted close/open/high/low, `r` the daily simple return of `C`, `m` the benchmark's,
`DV` = raw close x raw volume (USD). A feature at session `t` uses bars at or before `t` only.

Missing data: rows are trading sessions, so a missing bar is NaN. A window statistic needs
every value in its window (NaN until the gap leaves the window); point-to-point returns need
their two end bars only. Recursive smoothers (EMA, Wilder) are seeded with the mean of their
first `n` consecutive inputs, keep their state across a missing input and give NaN there; their
values depend (decaying geometrically) on where the loaded window starts, which is why
`compute_features` loads `quant.features.buffer_sessions` extra sessions as warm-up.

Conventions follow TA-Lib where one exists (Wilder ATR and RSI, EMA seeded with an SMA,
MACD 12/26/9, Bollinger bands with the population standard deviation).
"""

import math
from collections.abc import Callable
from typing import cast

import numpy as np
import pandas as pd

from atlas.quant.features.registry import FeatureUnit, PricePanel, feature

FLAT_EPS = 1e-12
"""A standard deviation, band width or sum of squares at or below this (relative to the level
where one applies) counts as zero: the feature is undefined there (NaN), never infinite."""


# --- building blocks -----------------------------------------------------------------------------


def _frame(values: np.ndarray, like: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(values, index=like.index, columns=like.columns)


def smooth(x: pd.DataFrame, n: int, alpha: float) -> pd.DataFrame:
    """Recursive smoother per column: seeded at the first session where the last `n` inputs are
    all present (their mean), then `s_t = s_{t-1} + alpha * (x_t - s_{t-1})`. A missing input
    keeps the state and gives NaN on that session. One loop over sessions, vectorised across
    columns."""
    values = x.to_numpy(dtype="float64")
    seeds = x.rolling(n, min_periods=n).mean().to_numpy()
    rows, cols = values.shape
    seeded = ~np.isnan(seeds)
    first = np.where(seeded.any(axis=0), seeded.argmax(axis=0), rows)
    out = np.full((rows, cols), np.nan)
    state = np.full(cols, np.nan)
    start = int(first.min()) if cols else rows
    for t in range(start, rows):
        seeding = first == t
        state[seeding] = seeds[t, seeding]
        current = values[t]
        update = (first < t) & ~np.isnan(current)
        state[update] += alpha * (current[update] - state[update])
        out[t] = np.where(seeding | update, state, np.nan)
    return _frame(out, x)


def ema(x: pd.DataFrame, n: int) -> pd.DataFrame:
    """Exponential moving average with span `n` (alpha = 2 / (n + 1)), SMA-seeded."""
    return smooth(x, n, 2.0 / (n + 1))


def wilder(x: pd.DataFrame, n: int) -> pd.DataFrame:
    """Wilder's smoothing (alpha = 1 / n), SMA-seeded."""
    return smooth(x, n, 1.0 / n)


def _rolling(x: pd.DataFrame, n: int) -> "pd.api.typing.Rolling":
    return x.rolling(n, min_periods=n)


def _annualise(panel: PricePanel) -> float:
    return math.sqrt(panel.sessions_per_year)


def _ema_close(panel: PricePanel, n: int) -> pd.DataFrame:
    return panel.memo(("ema", n), lambda: ema(panel.close, n))


def _macd(panel: PricePanel) -> pd.DataFrame:
    return panel.memo("macd", lambda: _ema_close(panel, 12) - _ema_close(panel, 26))


def _macd_signal(panel: PricePanel) -> pd.DataFrame:
    return panel.memo("macd_signal", lambda: ema(_macd(panel), 9))


def _gaps(panel: PricePanel) -> pd.DataFrame:
    return panel.memo("gaps", lambda: panel.open / panel.close.shift(1) - 1)


def _register(
    name: str,
    lookback: int,
    unit: FeatureUnit,
    description: str | None = None,
    *,
    benchmark: bool = False,
) -> Callable[[Callable[[PricePanel], pd.DataFrame]], Callable[[PricePanel], pd.DataFrame]]:
    return feature(name, lookback, unit=unit, description=description, benchmark=benchmark)


# --- returns and momentum ------------------------------------------------------------------------


def _ret(n: int) -> Callable[[PricePanel], pd.DataFrame]:
    def compute(panel: PricePanel) -> pd.DataFrame:
        return panel.close / panel.close.shift(n) - 1

    return compute


for _n in (1, 5, 21, 63, 126, 252):
    _register(
        f"ret_{_n}d",
        _n + 1,
        "fraction",
        f"Total return over the last {_n} sessions: C_t / C_(t-{_n}) - 1 (simple, fraction).",
    )(_ret(_n))


@_register("momentum_12_1", 253, "fraction")
def momentum_12_1(panel: PricePanel) -> pd.DataFrame:
    """12-1 momentum: C_(t-21) / C_(t-252) - 1, the 12-month return skipping the last month."""
    return panel.close.shift(21) / panel.close.shift(252) - 1


@_register("reversal_5d", 6, "fraction")
def reversal_5d(panel: PricePanel) -> pd.DataFrame:
    """Short-term reversal: minus the 5-session return (high = recent losers)."""
    return -(panel.close / panel.close.shift(5) - 1)


# --- volatility ----------------------------------------------------------------------------------


def _vol(n: int) -> Callable[[PricePanel], pd.DataFrame]:
    def compute(panel: PricePanel) -> pd.DataFrame:
        return _rolling(panel.returns, n).std(ddof=1) * _annualise(panel)

    return compute


for _n in (21, 63):
    _register(
        f"vol_{_n}d",
        _n + 1,
        "annual_fraction",
        f"Realised volatility: sample std (ddof 1) of the last {_n} daily returns, annualised.",
    )(_vol(_n))


@_register("downside_vol_63d", 64, "annual_fraction")
def downside_vol_63d(panel: PricePanel) -> pd.DataFrame:
    """Downside volatility: sqrt(mean(min(r, 0)^2)) over the last 63 daily returns, annualised."""
    downside = panel.returns.clip(upper=0.0) ** 2
    return np.sqrt(_rolling(downside, 63).mean()) * _annualise(panel)


def _true_range(panel: PricePanel) -> pd.DataFrame:
    def compute() -> pd.DataFrame:
        prev = panel.close.shift(1).to_numpy()
        high, low = panel.high.to_numpy(), panel.low.to_numpy()
        tr = np.maximum(np.maximum(high - low, np.abs(high - prev)), np.abs(low - prev))
        return _frame(tr, panel.close)

    return panel.memo("true_range", compute)


@_register("atr_14", 15, "usd")
def atr_14(panel: PricePanel) -> pd.DataFrame:
    """Average true range (Wilder, 14): TR = max(H - L, |H - C_(t-1)|, |L - C_(t-1)|), USD."""
    return panel.memo("atr_14", lambda: wilder(_true_range(panel), 14))


@_register("atr_pct_14", 15, "fraction")
def atr_pct_14(panel: PricePanel) -> pd.DataFrame:
    """ATR(14) as a fraction of the close: atr_14 / C."""
    return atr_14(panel) / panel.close


# --- oscillators ---------------------------------------------------------------------------------


@_register("rsi_14", 15, "index_0_100")
def rsi_14(panel: PricePanel) -> pd.DataFrame:
    """Relative strength index (Wilder, 14) on 0-100: 100 with no losses, 50 with no moves."""
    delta = panel.close.diff()
    gain = wilder(delta.clip(lower=0.0), 14).to_numpy()
    loss = wilder((-delta).clip(lower=0.0), 14).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = 100.0 - 100.0 / (1.0 + gain / loss)
    rsi = np.where(loss == 0.0, np.where(gain > 0.0, 100.0, 50.0), rsi)
    rsi = np.where(np.isnan(gain) | np.isnan(loss), np.nan, rsi)
    return _frame(rsi, panel.close)


@_register("macd", 26, "usd")
def macd(panel: PricePanel) -> pd.DataFrame:
    """MACD line: EMA(12) - EMA(26) of the close, USD."""
    return _macd(panel)


@_register("macd_signal", 34, "usd")
def macd_signal(panel: PricePanel) -> pd.DataFrame:
    """MACD signal line: EMA(9) of the MACD line, USD."""
    return _macd_signal(panel)


@_register("macd_hist", 34, "usd")
def macd_hist(panel: PricePanel) -> pd.DataFrame:
    """MACD histogram: MACD line - signal line, USD."""
    return _macd(panel) - _macd_signal(panel)


@_register("bb_pctb_20", 20, "ratio")
def bb_pctb_20(panel: PricePanel) -> pd.DataFrame:
    """Bollinger %B: (C - lower) / (upper - lower), bands SMA(20) +/- 2 population std; 0 = lower
    band, 1 = upper band; NaN when the band is flat."""
    window = _rolling(panel.close, 20)
    mid, sd = window.mean(), window.std(ddof=0)
    width = 4.0 * sd
    pctb = (panel.close - (mid - 2.0 * sd)) / width
    return pctb.mask(width <= FLAT_EPS * mid.abs())


# --- moving averages -----------------------------------------------------------------------------


def _sma(n: int) -> Callable[[PricePanel], pd.DataFrame]:
    def compute(panel: PricePanel) -> pd.DataFrame:
        return panel.memo(("sma", n), lambda: _rolling(panel.close, n).mean())

    return compute


def _ema(n: int) -> Callable[[PricePanel], pd.DataFrame]:
    def compute(panel: PricePanel) -> pd.DataFrame:
        return _ema_close(panel, n)

    return compute


def _price_to(
    average: Callable[[PricePanel], pd.DataFrame],
) -> Callable[[PricePanel], pd.DataFrame]:
    def compute(panel: PricePanel) -> pd.DataFrame:
        return panel.close / average(panel)

    return compute


for _n in (20, 50, 200):
    _register(f"sma_{_n}", _n, "usd", f"Simple moving average of the close over {_n} sessions.")(
        _sma(_n)
    )
    _register(
        f"ema_{_n}",
        _n,
        "usd",
        f"Exponential moving average of the close, span {_n} (alpha 2/{_n + 1}), SMA-seeded.",
    )(_ema(_n))
for _n in (20, 50, 200):
    _register(
        f"price_to_sma_{_n}", _n, "ratio", f"Close over its {_n}-session SMA (1 = at the average)."
    )(_price_to(_sma(_n)))
    _register(
        f"price_to_ema_{_n}", _n, "ratio", f"Close over its {_n}-session EMA (1 = at the average)."
    )(_price_to(_ema(_n)))


# --- range and drawdown --------------------------------------------------------------------------


@_register("dist_52w_high", 252, "fraction")
def dist_52w_high(panel: PricePanel) -> pd.DataFrame:
    """Distance from the 52-week high: C / max(H over 252 sessions) - 1 (at most 0)."""
    return panel.close / _rolling(panel.high, 252).max() - 1


@_register("dist_52w_low", 252, "fraction")
def dist_52w_low(panel: PricePanel) -> pd.DataFrame:
    """Distance from the 52-week low: C / min(L over 252 sessions) - 1 (at least 0)."""
    return panel.close / _rolling(panel.low, 252).min() - 1


@_register("max_dd_252d", 252, "fraction")
def max_dd_252d(panel: PricePanel) -> pd.DataFrame:
    """Maximum drawdown of the close inside the last 252 sessions: the worst C_s / running peak - 1
    within the window (at most 0)."""
    window = 252
    close = panel.close.to_numpy(dtype="float64")
    rows = close.shape[0]
    out = np.full(close.shape, np.nan)
    if rows >= window:
        count = rows - window + 1  # windows end at sessions window-1 .. rows-1
        peak = close[:count].copy()
        worst = np.zeros_like(peak)
        worst[np.isnan(peak)] = np.nan
        for j in range(1, window):
            current = close[j : j + count]
            peak = np.maximum(peak, current)
            worst = np.minimum(worst, current / peak - 1)
        out[window - 1 :] = worst
    return _frame(out, panel.close)


# --- market: beta and idiosyncratic volatility ---------------------------------------------------


def _market(panel: PricePanel) -> tuple[pd.DataFrame, pd.DataFrame]:
    def compute() -> pd.DataFrame:
        window = 252
        r, m = panel.returns, panel.benchmark_returns
        cov = _rolling(r, window).cov(m)
        var_m = _rolling(m.to_frame(), window).var().iloc[:, 0]
        var_r = _rolling(r, window).var()
        sxx = (window - 1) * var_m
        beta = cov.div(var_m, axis=0).mask((sxx <= FLAT_EPS).to_numpy()[:, None] | cov.isna())
        ssr = (window - 1) * (var_r - beta**2 * pd.DataFrame({c: var_m for c in r.columns}))
        idio = np.sqrt(ssr.clip(lower=0.0) / (window - 2)) * _annualise(panel)
        return pd.concat({"beta": beta, "idio": idio.mask(beta.isna())}, axis=1)

    both = panel.memo("market", compute)
    return cast(pd.DataFrame, both["beta"]), cast(pd.DataFrame, both["idio"])


@_register("beta_252d", 253, "ratio", benchmark=True)
def beta_252d(panel: PricePanel) -> pd.DataFrame:
    """Beta to the benchmark: cov(r, m) / var(m) over the last 252 daily returns (OLS slope)."""
    return _market(panel)[0]


@_register("idio_vol_252d", 253, "annual_fraction", benchmark=True)
def idio_vol_252d(panel: PricePanel) -> pd.DataFrame:
    """Idiosyncratic volatility: std of the OLS residuals of r on m (with intercept, ddof 2) over
    the last 252 daily returns, annualised."""
    return _market(panel)[1]


# --- liquidity and gaps --------------------------------------------------------------------------


@_register("volume_z_21d", 22, "zscore")
def volume_z_21d(panel: PricePanel) -> pd.DataFrame:
    """Volume z-score: (ln DV_t - mean) / std (ddof 1) of ln DV over the previous 21 sessions."""
    dv = panel.dollar_volume
    log_dv = _frame(np.log(dv.where(dv > 0).to_numpy()), dv)
    past = _rolling(log_dv.shift(1), 21)
    mean, sd = past.mean(), past.std(ddof=1)
    return ((log_dv - mean) / sd).mask(sd <= FLAT_EPS)


@_register("amihud_21d", 22, "return_per_usd_mm")
def amihud_21d(panel: PricePanel) -> pd.DataFrame:
    """Amihud illiquidity: mean of |r| / DV over the last 21 sessions, per USD 1M traded."""
    dv = panel.dollar_volume
    illiq = panel.returns.abs() / dv.where(dv > 0) * 1e6
    return _rolling(illiq, 21).mean()


@_register("gap_1d", 2, "fraction")
def gap_1d(panel: PricePanel) -> pd.DataFrame:
    """Overnight gap: O_t / C_(t-1) - 1 (adjusted prices)."""
    return _gaps(panel)


@_register("gap_mean_21d", 22, "fraction")
def gap_mean_21d(panel: PricePanel) -> pd.DataFrame:
    """Mean overnight gap over the last 21 sessions."""
    return _rolling(_gaps(panel), 21).mean()


@_register("gap_std_63d", 64, "fraction")
def gap_std_63d(panel: PricePanel) -> pd.DataFrame:
    """Standard deviation (ddof 1) of the overnight gap over the last 63 sessions (daily)."""
    return _rolling(_gaps(panel), 63).std(ddof=1)
