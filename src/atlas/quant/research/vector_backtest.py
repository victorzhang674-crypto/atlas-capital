"""A simple vectorised long-short backtest of a signal (Stage 17; the full event-driven
backtester is Stage 56).

On each rebalance date (the first evaluation date of each `rebalance` period: `signal` =
every evaluation date, `weekly`, `monthly`, `quarterly`) the book goes long the top quantile
and short the bottom quantile of the signal (`quantile_buckets`), each leg `gross / 2` of NAV:
`equal` weights, or `signal` weights proportional to the distance of the name's average rank
from the median rank. Either leg empty (too few names, a constant signal) means no position.

Timing: a decision on day `t` trades at the close of `t`, so the book earns the daily returns
of `t+1` onwards; the return of the rebalance session itself is earned by the previous book.
Between rebalances the weights drift with returns, `w <- w (1 + r) / (1 + R)` (a missing daily
return counts as 0: the position is held at its last price). Turnover = sum |target - drifted
weight| (the first book counts in full, from cash); its cost, `turnover * cost_bps / 10^4`, is
charged on the rebalance session. Weights are fractions of NAV (shorts negative); returns are
simple daily fractions; no financing or borrow costs.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, get_args

import numpy as np
import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.core.pit import guard_not_label
from atlas.quant.research.quantiles import quantile_buckets

Rebalance = Literal["signal", "weekly", "monthly", "quarterly"]
Weighting = Literal["equal", "signal"]
REBALANCES: tuple[str, ...] = get_args(Rebalance)
WEIGHTINGS: tuple[str, ...] = get_args(Weighting)


def rebalance_dates(dates: Sequence[date | pd.Timestamp], rebalance: Rebalance) -> pd.DatetimeIndex:
    """The first of `dates` (ascending) in each `rebalance` period (`signal`: all of them)."""
    if rebalance not in REBALANCES:
        raise ValueError(f"rebalance must be one of {REBALANCES}, got {rebalance!r}")
    index = pd.DatetimeIndex(pd.to_datetime(list(dates)), name="date")
    if rebalance == "signal":
        return index

    def period(d: pd.Timestamp) -> tuple[int, int]:
        if rebalance == "weekly":
            iso = d.isocalendar()
            return int(iso[0]), int(iso[1])
        if rebalance == "monthly":
            return d.year, d.month
        return d.year, (d.month - 1) // 3

    keep, seen = [], set()
    for d in index:
        key = period(d)
        if key not in seen:
            seen.add(key)
            keep.append(d)
    return pd.DatetimeIndex(keep, name="date")


def long_short_weights(
    row: pd.Series, *, n_quantiles: int, weighting: Weighting, gross: float, min_names: int
) -> pd.Series:
    """Target weights (fractions of NAV) for one date's signal `row`: the top quantile long,
    the bottom short, each leg summing to `gross / 2` in absolute value; 0 elsewhere (and
    everywhere when either leg is empty)."""
    if weighting not in WEIGHTINGS:
        raise ValueError(f"weighting must be one of {WEIGHTINGS}, got {weighting!r}")
    frame = row.astype("float64").to_frame().T
    buckets = quantile_buckets(frame, n_quantiles, min_names=min_names).iloc[0].to_numpy()
    out = np.zeros(len(row))
    long, short = buckets == n_quantiles, buckets == 1
    if long.any() and short.any():
        if weighting == "equal":
            size = np.ones(len(row))
        else:
            ranks = frame.rank(axis=1, method="average").iloc[0].to_numpy(dtype="float64")
            n = (~np.isnan(ranks)).sum()
            size = np.abs(ranks - (n + 1) / 2.0)
        half = gross / 2.0
        if size[long].sum() > 0 and size[short].sum() > 0:
            out[long] = half * size[long] / size[long].sum()
            out[short] = -half * size[short] / size[short].sum()
    return pd.Series(out, index=row.index, dtype="float64")


@dataclass(frozen=True, eq=False)
class BacktestResult:
    """Daily `returns` (net of costs), `gross_returns`, `costs` (fractions of NAV, from the
    first rebalance session to the last session), `turnover` (per rebalance date), `weights`
    (rebalance date x symbol targets) and `summary` (module docstring of `vector_backtest`)."""

    returns: pd.Series
    gross_returns: pd.Series
    costs: pd.Series
    turnover: pd.Series
    weights: pd.DataFrame
    summary: dict[str, Any]


def _summary(
    net: np.ndarray, turnover: np.ndarray, costs: np.ndarray, periods: int
) -> dict[str, Any]:
    n = len(net)
    mean = float(net.mean()) if n else float("nan")
    std = float(net.std(ddof=1)) if n >= 2 else float("nan")
    wealth = np.cumprod(1.0 + net)
    peak = np.maximum.accumulate(wealth) if n else wealth
    years = n / periods if n else float("nan")
    return {
        "n_sessions": n,
        "ann_return": mean * periods,
        "ann_vol": std * np.sqrt(periods),
        "sharpe": mean / std * np.sqrt(periods) if std > 0 else float("nan"),
        "total_return": float(wealth[-1] - 1.0) if n else float("nan"),
        "max_drawdown": float(-(wealth / peak - 1.0).min()) if n else float("nan"),
        "n_rebalances": len(turnover),
        "mean_turnover": float(turnover.mean()) if len(turnover) else float("nan"),
        "annual_turnover": float(turnover.sum() / years) if n else float("nan"),
        "total_cost": float(costs.sum()),
    }


def vector_backtest(
    signal: pd.DataFrame,
    daily: pd.DataFrame,
    *,
    rebalance: Rebalance | None = None,
    weighting: Weighting | None = None,
    cost_bps: float | None = None,
    gross: float | None = None,
    n_quantiles: int | None = None,
    min_names: int | None = None,
    sessions_per_year: int | None = None,
    config: AtlasConfig | None = None,
) -> BacktestResult:
    """Backtest the top-minus-bottom quantile book of `signal` (evaluation dates x symbols)
    on `daily` (sessions x symbols, simple daily returns; every rebalance date must be one of
    its sessions). Defaults: `quant.research.backtest` (`rebalance`, `weighting`, `cost_bps`,
    `gross`), `quant.research` (`n_quantiles`, `min_names`) and
    `quant.features.sessions_per_year`. Summary: `ann_return` (mean x P), `ann_vol` (std x
    sqrt P), `sharpe` (mean / std x sqrt P, risk-free 0), `total_return`, `max_drawdown`
    (positive fraction), `n_rebalances`, `mean_turnover` (per rebalance), `annual_turnover`,
    `total_cost`, `n_sessions` and the settings used. A label as `signal` raises
    `LookAheadError`."""
    guard_not_label(signal, "signal")
    cfg = config if config is not None else AtlasConfig()
    bt, rc = cfg.quant.research.backtest, cfg.quant.research
    reb = bt.rebalance if rebalance is None else rebalance
    wgt = bt.weighting if weighting is None else weighting
    bps = bt.cost_bps if cost_bps is None else cost_bps
    grs = bt.gross if gross is None else gross
    q = rc.n_quantiles if n_quantiles is None else n_quantiles
    k = rc.min_names if min_names is None else min_names
    periods = (
        cfg.quant.features.sessions_per_year if sessions_per_year is None else sessions_per_year
    )
    if bps < 0 or not grs > 0:
        raise ValueError(f"cost_bps must be >= 0 and gross > 0, got {bps}, {grs}")
    dates = rebalance_dates(list(signal.index), reb)
    sessions = pd.DatetimeIndex(daily.index)
    positions = sessions.get_indexer(dates)
    if (positions < 0).any():
        missing = [str(d.date()) for d, p in zip(dates, positions, strict=True) if p < 0]
        raise ValueError(f"rebalance dates {missing} are not sessions of the daily returns")
    symbols = signal.columns
    rows = pd.DatetimeIndex(signal.index).get_indexer(dates)
    targets = np.vstack(
        [
            long_short_weights(
                signal.iloc[int(i)], n_quantiles=q, weighting=wgt, gross=grs, min_names=k
            ).to_numpy()
            for i in rows
        ]
    )
    r = np.nan_to_num(daily.reindex(columns=symbols).to_numpy(dtype="float64"), nan=0.0)
    first = int(positions[0])
    span = sessions[first:]
    gross_ret = np.zeros(len(span))
    cost = np.zeros(len(span))
    turnover = np.zeros(len(dates))
    target_at = {int(p): i for i, p in enumerate(positions)}
    w = np.zeros(len(symbols))
    for j, session in enumerate(range(first, len(sessions))):
        if j > 0:
            day_ret = float(w @ r[session])
            gross_ret[j] = day_ret
            w = w * (1.0 + r[session]) / (1.0 + day_ret)
        if session in target_at:
            i = target_at[session]
            turnover[i] = float(np.abs(targets[i] - w).sum())
            cost[j] = turnover[i] * bps / 1e4
            w = targets[i].copy()
    net = gross_ret - cost
    summary = _summary(net, turnover, cost, periods)
    summary.update({"rebalance": reb, "weighting": wgt, "cost_bps": bps, "gross": grs})
    return BacktestResult(
        returns=pd.Series(net, index=span, name="net_return"),
        gross_returns=pd.Series(gross_ret, index=span, name="gross_return"),
        costs=pd.Series(cost, index=span, name="cost"),
        turnover=pd.Series(turnover, index=dates, name="turnover"),
        weights=pd.DataFrame(targets, index=dates, columns=symbols),
        summary=summary,
    )
