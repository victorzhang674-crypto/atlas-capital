"""Vectorised pairs backtest (Stage 23) on the Stage 17 weights engine (`weights_backtest`).

Over the sessions of `[start, end]` of a simple daily `returns` panel (log total-return
indices from `log_price_panel`), with `cfg = quant.statarb`:

- **Formation** on the first session of each month (`rebalance_dates(…, "monthly")`, so
  `start` is one): `select_pairs` on the `formation` sessions *before* it, over the names
  `members(day)` (default: every column). Too little history → no pairs that month. Open
  pairs not reselected close at once (`deselected`); reselected open pairs keep the
  parameters they entered with until flat; locks reset.
- **Each session t**: the z-score from closes through `t - 1` (the pair's own lookback),
  the breakdown p-value (`coint_pvalue` on the `formation` sessions through `t - 1`) re-tested
  every `breakdown_every` sessions after the formation (the formation p-value before; a NaN
  p-value is not a breakdown), one `step` of the state machine, and the targets of the pairs
  in a position: `target_weights` (dollar-neutral, `pair_gross` per pair).
- **P&L**: targets of session t trade at its close and earn from `t + 1`
  (`weights_backtest`: drift, daily rebalance to target, `cost_bps` per unit of turnover).

Trades (`TRADE_COLUMNS`): `gross_return` per unit of the pair's gross notional,
`(R_long - R_short) / 2` from the entry close to the exit close (no costs, no intra-trade
rebalancing); `held` = sessions earned. Open trades at `end` have no exit.
"""

import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, StatArbConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.pit import guard_not_label
from atlas.data.view import DataView
from atlas.quant.alpha.multifactor import member_sectors
from atlas.quant.research.vector_backtest import (
    BacktestResult,
    rebalance_dates,
    weights_backtest,
)
from atlas.quant.statarb.selection import Pair, log_price_panel, select_pairs
from atlas.quant.statarb.signals import (
    Event,
    PairSignal,
    PairState,
    SpreadState,
    latest_zscore,
    rolling_coint_pvalue,
    step,
    target_weights,
    to_signal,
    zscore_lookback,
)

_log = get_logger(__name__)

TRADE_COLUMNS: tuple[str, ...] = (
    "pair_id",
    "sector",
    "y",
    "x",
    "side",
    "long",
    "short",
    "formed",
    "hedge_ratio",
    "half_life",
    "entry",
    "entry_z",
    "exit",
    "exit_event",
    "exit_z",
    "held",
    "gross_return",
)
SELECTION_COLUMNS: tuple[str, ...] = (
    "formed",
    "pair_id",
    "sector",
    "y",
    "x",
    "hedge_ratio",
    "half_life",
    "hurst",
    "pvalue",
    "corr",
)
DAILY_COLUMNS: tuple[str, ...] = ("selected", "open_pairs", "max_pairs_per_name", "gross")


@dataclass(frozen=True, eq=False)
class PairsBacktestResult:
    """`backtest` (Stage 17 `BacktestResult`: net / gross returns, costs, turnover, daily
    target weights), `trades` (`TRADE_COLUMNS`), `selections` (`SELECTION_COLUMNS`, one row
    per pair and formation), `daily` (`DAILY_COLUMNS` per session) and `summary` (the
    backtest summary plus trade statistics)."""

    backtest: BacktestResult
    trades: pd.DataFrame
    selections: pd.DataFrame
    daily: pd.DataFrame
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Open:
    """A pair's live bookkeeping: the parameters in use, its state, the open trade."""

    params: Pair
    state: PairState
    pvalue: float
    trade: dict[str, Any] | None = None
    params_next: Pair | None = None  # reselected while open: used once flat
    pvalues: pd.Series | None = None  # this formation's rolling p-values, by session


def _trade_return(lp: np.ndarray, col: dict[str, int], trade: dict[str, Any], k_exit: int) -> float:
    k0 = trade["_k"]
    r_long = math.expm1(lp[k_exit, col[trade["long"]]] - lp[k0, col[trade["long"]]])
    r_short = math.expm1(lp[k_exit, col[trade["short"]]] - lp[k0, col[trade["short"]]])
    return 0.5 * (r_long - r_short)


def pairs_backtest(
    returns: pd.DataFrame,
    sectors: pd.Series,
    *,
    start: date,
    end: date,
    members: Callable[[date], Sequence[str]] | None = None,
    config: StatArbConfig | None = None,
    sessions_per_year: int | None = None,
    progress: Callable[[date, int], None] | None = None,
) -> PairsBacktestResult:
    """Backtest the pairs strategy (module docstring) on `returns` (sessions x symbols,
    simple daily, ascending) with `sectors` (symbol → GICS sector) over `[start, end]`.
    Defaults: `quant.statarb` and `quant.features.sessions_per_year` of `AtlasConfig()`.
    `progress(day, n_selected)` is called after each formation that had enough history."""
    guard_not_label(returns, "returns")
    base = AtlasConfig()
    cfg = config or base.quant.statarb
    periods = sessions_per_year or base.quant.features.sessions_per_year
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    sessions = pd.DatetimeIndex(returns.index)
    inside = np.flatnonzero((sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end)))
    if len(inside) == 0:
        raise ValueError(f"no session of the returns between {start} and {end}")
    first, last = int(inside[0]), int(inside[-1])
    lp_frame = log_price_panel(returns)
    symbols = list(lp_frame.columns)
    col = {s: i for i, s in enumerate(symbols)}
    lp = lp_frame.to_numpy(dtype="float64")
    formations = sorted(
        int(k)
        for k in sessions.get_indexer(rebalance_dates(list(sessions[first : last + 1]), "monthly"))
    )

    book: dict[str, _Open] = {}
    k_form = first
    trades: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    target_rows: list[dict[str, float]] = []
    daily_rows: list[dict[str, Any]] = []
    n_formations = 0

    def close(pid: str, k: int, event: Event, z: float) -> None:
        trade = book[pid].trade
        if trade is None:
            return
        trade.update(
            exit=sessions[k].date(),
            exit_event=str(event),
            exit_z=z,
            held=k - trade["_k"],
            gross_return=_trade_return(lp, col, trade, k),
        )
        book[pid].trade = None

    for k in range(first, last + 1):
        day = sessions[k].date()
        if k in formations:
            k_form = k
            new: list[Pair] = []
            if k >= cfg.formation:
                names = list(members(day)) if members is not None else symbols
                cands = [s for s in symbols if s in set(names)]
                window = lp_frame.iloc[k - cfg.formation : k][cands]
                new = select_pairs(window, sectors, config=cfg, formed=day)
                n_formations += 1
                if progress is not None:
                    progress(day, len(new))
            else:
                _log.info("statarb.backtest_short_history", day=day.isoformat(), sessions=k)
            fresh = {p.pair_id: p for p in new}
            later = [f for f in formations if f > k]
            seg_end = later[0] if later else last + 1  # sessions k .. seg_end - 1
            for pid in list(book):
                if pid not in fresh:
                    close(pid, k, Event.DESELECTED, math.nan)
                    del book[pid]
            for pid, pair in fresh.items():
                held = book.get(pid)
                if held is not None and held.state.state is not SpreadState.FLAT:
                    held.params_next = pair
                else:
                    book[pid] = _Open(params=pair, state=PairState(), pvalue=pair.pvalue)
                # Breakdown p-values for session t use the window ending t - 1: positions
                # k - 1 .. seg_end - 2, re-tested every `breakdown_every` sessions.
                book[pid].pvalues = rolling_coint_pvalue(
                    lp_frame[pair.y].iloc[: seg_end - 1],
                    lp_frame[pair.x].iloc[: seg_end - 1],
                    start=k - 1,
                    config=cfg,
                )
                selections.append(
                    {
                        "formed": day,
                        "pair_id": pid,
                        "sector": pair.sector,
                        "y": pair.y,
                        "x": pair.x,
                        "hedge_ratio": pair.hedge_ratio,
                        "half_life": pair.half_life,
                        "hurst": pair.hurst,
                        "pvalue": pair.pvalue,
                        "corr": pair.corr,
                    }
                )
        signals: list[PairSignal] = []
        for pid in sorted(book):
            entry = book[pid]
            p = entry.params
            if entry.pvalues is not None:
                entry.pvalue = float(entry.pvalues.iloc[k - k_form])
            n = zscore_lookback(p.half_life, cfg)
            lo = max(k - n, 0)
            z, sigma = latest_zscore(lp[lo:k, col[p.y]] - p.hedge_ratio * lp[lo:k, col[p.x]], n)
            broken = math.isfinite(entry.pvalue) and entry.pvalue > cfg.breakdown_pvalue
            before = entry.state.state
            entry.state, event = step(
                entry.state, z, broken=broken, half_life=p.half_life, config=cfg
            )
            if event in (Event.ENTER_LONG, Event.ENTER_SHORT):
                side = entry.state.state
                long, short = (p.y, p.x) if side is SpreadState.LONG else (p.x, p.y)
                entry.trade = {
                    "pair_id": pid, "sector": p.sector, "y": p.y, "x": p.x,
                    "side": str(side), "long": long, "short": short, "formed": p.formed,
                    "hedge_ratio": p.hedge_ratio, "half_life": p.half_life, "entry": day,
                    "entry_z": z, "exit": None, "exit_event": None, "exit_z": math.nan,
                    "held": 0, "gross_return": math.nan, "_k": k,
                }  # fmt: skip
                trades.append(entry.trade)
            elif event is not None and before is not SpreadState.FLAT:
                close(pid, k, event, z)
            if entry.state.state is SpreadState.FLAT and entry.params_next is not None:
                entry.params, entry.params_next = entry.params_next, None
            sig = to_signal(
                p,
                entry.state.state,
                z=z,
                sigma=sigma,
                pvalue=entry.pvalue,
                config=cfg,
                periods=periods,
            )
            if sig is not None:
                signals.append(sig)
        weights = target_weights(signals, pair_gross=cfg.pair_gross)
        target_rows.append({str(s): float(w) for s, w in weights.items()})
        uses = Counter(s for sig in signals for s in (sig.long, sig.short))
        daily_rows.append(
            {
                "selected": len(book),
                "open_pairs": len(signals),
                "max_pairs_per_name": max(uses.values(), default=0),
                "gross": float(weights.abs().sum()),
            }
        )

    index = pd.DatetimeIndex(sessions[first : last + 1], name="date")
    involved = sorted({s for row in target_rows for s in row})
    targets = pd.DataFrame(target_rows, index=index).reindex(columns=involved).fillna(0.0)
    bt = weights_backtest(
        targets, returns.iloc[: last + 1], cost_bps=cfg.cost_bps, sessions_per_year=periods
    )
    for t in trades:
        if t["exit"] is None:
            t["held"] = last - t["_k"]
    trade_frame = pd.DataFrame(
        [{c: t[c] for c in TRADE_COLUMNS} for t in trades], columns=list(TRADE_COLUMNS)
    )
    for c in ("entry", "exit", "formed", "exit_event"):
        trade_frame[c] = trade_frame[c].astype("object")
    closed = trade_frame[trade_frame["exit"].notna()]
    events = Counter(closed["exit_event"])
    summary = dict(bt.summary)
    summary.update(
        {
            "start": index[0].date().isoformat(),
            "end": index[-1].date().isoformat(),
            "n_formations": n_formations,
            "mean_pairs_selected": (float(len(selections) / n_formations) if n_formations else 0.0),
            "n_trades": len(trade_frame),
            "n_closed": len(closed),
            "hit_rate": float((closed["gross_return"] > 0).mean()) if len(closed) else math.nan,
            "mean_trade_return": float(closed["gross_return"].mean()) if len(closed) else math.nan,
            "mean_held": float(closed["held"].mean()) if len(closed) else math.nan,
            "exits": {str(e): int(n) for e, n in sorted(events.items())},
            "mean_open_pairs": float(np.mean([r["open_pairs"] for r in daily_rows])),
            "mean_gross": float(np.mean([r["gross"] for r in daily_rows])),
            "pair_gross": cfg.pair_gross,
        }
    )
    return PairsBacktestResult(
        backtest=bt,
        trades=trade_frame,
        selections=pd.DataFrame(selections, columns=list(SELECTION_COLUMNS)),
        daily=pd.DataFrame(daily_rows, index=index, columns=list(DAILY_COLUMNS)),
        summary=summary,
    )


__all__ = [
    "DAILY_COLUMNS",
    "SELECTION_COLUMNS",
    "TRADE_COLUMNS",
    "PairsBacktestResult",
    "pairs_backtest",
]


def pairs_backtest_from_view(
    view: DataView,
    start: date,
    end: date,
    *,
    statarb: StatArbConfig | None = None,
    progress: Callable[[date, int], None] | None = None,
) -> PairsBacktestResult:
    """`pairs_backtest` over [`start`, `end`] with its inputs read point in time from `view`.

    `view` must be the view of the session after the last session on or before `end`
    (`DataView(lake, calendar.next_session(last), config)`): its latest known bar is that
    session, so nothing later is read; another view raises `ValueError`. Returns of every
    name that is a member of `statarb.universe` on a session of the window, over the window
    plus `statarb.formation` sessions of history; members per formation
    (`DataView.members_on`); sectors from the instruments table (today's values: a known
    limitation). `statarb` defaults to the view's `quant.statarb`. `ValueError` for a window
    without a session; `DataError` when the universe has no member in it."""
    cfg_all = view.config
    cfg = statarb if statarb is not None else cfg_all.quant.statarb
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    cal = TradingCalendar(config=cfg_all.calendar)
    window = cal.sessions(start, end)
    if not window:
        raise ValueError(f"no trading session between {start} and {end}")
    last = window[-1]
    if view.day != cal.next_session(last):
        raise ValueError(
            f"the view is at {view.day}; the backtest to {last} needs the view of "
            f"{cal.next_session(last)} (its latest known bar is {last})"
        )
    names = sorted({m for day in window for m in view.members_on(day, cfg.universe)})
    if not names:
        raise DataError(f"universe {cfg.universe!r} has no member between {start} and {last}")
    n = len(window) + cfg.formation + 1
    returns = view.returns(names, n - 1)
    returns = returns.loc[:, returns.notna().any()]
    if returns.empty or returns.index[-1].date() != last:
        raise DataError(f"the lake has no bars of the universe's members up to {last}")
    _log.info(
        "statarb.backtest_inputs",
        start=start.isoformat(),
        end=last.isoformat(),
        names=int(returns.shape[1]),
        sessions=int(returns.shape[0]),
    )
    return pairs_backtest(
        returns,
        member_sectors(view, list(returns.columns)),
        start=start,
        end=last,
        members=lambda day: view.members_on(day, cfg.universe),
        config=cfg,
        sessions_per_year=cfg_all.quant.features.sessions_per_year,
        progress=progress,
    )
