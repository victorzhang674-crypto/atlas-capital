"""Pairs trading signals (Stage 23): spread z-scores, the per-pair state machine, breakdown
checks, `PairSignal` and dollar-neutral target weights.

With `cfg = quant.statarb` and a selected `Pair` (hedge ratio β, half-life HL in sessions):

- **Spread and z-score.** `s = log y - β log x`; `z_t = (s_t - mean) / std` (ddof 1) over the
  last `L = max(round(lookback_mult x HL), min_lookback)` values including `s_t`.
- **State machine** (`step`, one transition per session):
  `FLAT` → `LONG_SPREAD` (long y, short x) if `-stop_z ≤ z < -entry_z`, → `SHORT_SPREAD` if
  `entry_z < z ≤ stop_z`; a locked pair never enters, a broken pair is locked. In a position,
  in this order: **exit** (long: `z > -exit_z`; short: `z < exit_z`, which includes an
  overshoot through zero) → **stop loss** (`|z| > stop_z`) → **breakdown** (the rolling
  Engle-Granger p-value above `breakdown_pvalue`) → **time stop** (sessions held ≥
  `time_stop_mult x HL`). An exit leaves the pair free; a stop loss, breakdown or time stop
  locks it until the next formation. A NaN z never enters or exits on z; the breakdown and
  time stop still apply.
- **`PairSignal`** for a pair in a position: `long` / `short` legs, `hedge_ratio` β,
  `z`, `exp_return` (annualised fraction of the pair's gross notional: the OU reversion
  `(|z| - exit_z) x sigma_s / (1 + β)` over `HL x log2(|z| / exit_z)` sessions, times
  `periods / that time`) and `confidence = clip(1 - p / breakdown_pvalue, 0, 1)`.
- **Weights** (`target_weights`): each active pair holds `pair_gross` of NAV, `+pair_gross/2`
  on its long leg and `-pair_gross/2` on its short leg (dollar-neutral per pair, not
  β-neutral); a name in several pairs gets the sum.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Annotated, Self

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas.core.config import StatArbConfig
from atlas.data.view import DataView
from atlas.quant.alpha.multifactor import member_sectors
from atlas.quant.statarb.selection import (
    Pair,
    coint_pvalue,
    log_price_panel,
    pair_id,
    select_pairs,
)


class SpreadState(StrEnum):
    """A pair's position: flat, long the spread (long y, short x) or short it."""

    FLAT = "flat"
    LONG = "long_spread"
    SHORT = "short_spread"


class Event(StrEnum):
    """A state-machine transition (or, `DESELECTED`, a close forced by reselection)."""

    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT = "exit"
    STOP_LOSS = "stop_loss"
    BREAKDOWN = "breakdown"
    TIME_STOP = "time_stop"
    DESELECTED = "deselected"


@dataclass(frozen=True)
class PairState:
    """`state`; `held` = sessions held since entry (0 on the entry session and when flat);
    `locked` = no entry until the next formation (only when flat)."""

    state: SpreadState = SpreadState.FLAT
    held: int = 0
    locked: bool = False

    def __post_init__(self) -> None:
        if self.held < 0:
            raise ValueError(f"held must be >= 0, got {self.held}")
        if self.state is SpreadState.FLAT and self.held != 0:
            raise ValueError("a flat pair holds nothing: held must be 0")
        if self.state is not SpreadState.FLAT and self.locked:
            raise ValueError("only a flat pair can be locked")


_LOCKED = PairState(locked=True)


def zscore_lookback(half_life: float, config: StatArbConfig) -> int:
    """`max(round(lookback_mult x half_life), min_lookback)` sessions (round half up)."""
    return max(math.floor(config.lookback_mult * half_life + 0.5), config.min_lookback)


def spread(log_y: pd.Series, log_x: pd.Series, hedge_ratio: float) -> pd.Series:
    """The log spread `log y - β log x`."""
    return (log_y - hedge_ratio * log_x).rename("spread")


def zscore(values: pd.Series, lookback: int) -> pd.Series:
    """Rolling z-score of `values` over `lookback` values including the current one (mean,
    std with ddof 1); NaN until the window is full and where the std is 0."""
    if lookback < 2:
        raise ValueError(f"lookback must be >= 2, got {lookback}")
    roll = values.rolling(lookback, min_periods=lookback)
    std = roll.std(ddof=1)
    return ((values - roll.mean()) / std.where(std > 0)).rename("z")


def latest_zscore(values: np.ndarray, lookback: int) -> tuple[float, float]:
    """`(z, sigma)` of the last value of `values` (the last `lookback` of them, oldest first):
    the last row of `zscore` / `rolling_std`; NaN when there are fewer than `lookback` values,
    a NaN among them, or no variation."""
    if lookback < 2:
        raise ValueError(f"lookback must be >= 2, got {lookback}")
    window = np.asarray(values, dtype="float64")[-lookback:]
    if len(window) < lookback or not np.isfinite(window).all():
        return math.nan, math.nan
    sigma = float(np.std(window, ddof=1))
    if not sigma > 0:
        return math.nan, sigma
    return float((window[-1] - window.mean()) / sigma), sigma


def rolling_std(values: pd.Series, lookback: int) -> pd.Series:
    """The z-score window's std (ddof 1): sigma_s in the expected return."""
    return values.rolling(lookback, min_periods=lookback).std(ddof=1)


def step(
    state: PairState,
    z: float,
    *,
    broken: bool,
    half_life: float,
    config: StatArbConfig,
) -> tuple[PairState, Event | None]:
    """One session of the state machine (module docstring): the new state and the event
    (`None` when nothing happens)."""
    if not half_life > 0:
        raise ValueError(f"half_life must be > 0, got {half_life}")
    cfg = config
    finite = math.isfinite(z)
    if state.state is SpreadState.FLAT:
        if broken:
            return (_LOCKED, None) if state.locked else (_LOCKED, Event.BREAKDOWN)
        if state.locked or not finite:
            return state, None
        if -cfg.stop_z <= z < -cfg.entry_z:
            return PairState(SpreadState.LONG), Event.ENTER_LONG
        if cfg.entry_z < z <= cfg.stop_z:
            return PairState(SpreadState.SHORT), Event.ENTER_SHORT
        return state, None
    held = state.held + 1
    if finite:
        long = state.state is SpreadState.LONG
        if (long and z > -cfg.exit_z) or (not long and z < cfg.exit_z):
            return PairState(), Event.EXIT
        if abs(z) > cfg.stop_z:
            return _LOCKED, Event.STOP_LOSS
    if broken:
        return _LOCKED, Event.BREAKDOWN
    if held >= cfg.time_stop_mult * half_life:
        return _LOCKED, Event.TIME_STOP
    return PairState(state.state, held), None


def run_pair(
    z: pd.Series,
    broken: pd.Series,
    *,
    half_life: float,
    config: StatArbConfig,
    initial: PairState | None = None,
) -> pd.DataFrame:
    """The state machine over a z path (`broken`: the breakdown flag per session, same
    index): one row per session with `z`, `state`, `event`, `held`, `locked` after it."""
    if not z.index.equals(broken.index):
        raise ValueError("z and broken must share one index")
    st = initial or PairState()
    states, events, held, locked = [], [], [], []
    values = z.to_numpy(dtype="float64")
    for value, flag in zip(values, broken.to_numpy(dtype=bool), strict=True):
        st, ev = step(st, float(value), broken=bool(flag), half_life=half_life, config=config)
        states.append(st.state)
        events.append(ev)
        held.append(st.held)
        locked.append(st.locked)
    return pd.DataFrame(
        {
            "z": values,
            "state": pd.Series(states, index=z.index, dtype="object"),
            "event": pd.Series(events, index=z.index, dtype="object"),
            "held": held,
            "locked": locked,
        },
        index=z.index,
    )


def rolling_coint_pvalue(
    log_y: pd.Series, log_x: pd.Series, *, start: int, config: StatArbConfig
) -> pd.Series:
    """The order-free Engle-Granger p-value over the `formation` values ending at positions
    `start`, `start + breakdown_every`, …, carried forward in between (a test that cannot
    run, a gap in its window, also carries the last value forward); indexed by the
    positions `start..` of `log_y`. `ValueError` if `start` has fewer than `formation`
    values before it (inclusive)."""
    cfg = config
    if start < cfg.formation - 1:
        raise ValueError(f"start {start} has fewer than formation = {cfg.formation} values")
    y, x = log_y.to_numpy(dtype="float64"), log_x.to_numpy(dtype="float64")
    out = np.full(len(y) - start, np.nan)
    for i in range(start, len(y), cfg.breakdown_every):
        lo = i - cfg.formation + 1
        wy, wx = y[lo : i + 1], x[lo : i + 1]
        ok = np.isfinite(wy).all() and np.isfinite(wx).all()
        out[i - start] = coint_pvalue(wy, wx) if ok else np.nan
    # Untested sessions carry the last test forward; so does a test that could not run (a
    # gap in the window): the last known p-value is kept rather than none.
    return pd.Series(out, index=log_y.index[start:], name="pvalue").ffill()


def expected_return(
    z: float,
    *,
    sigma: float,
    hedge_ratio: float,
    half_life: float,
    config: StatArbConfig,
    periods: int,
) -> float:
    """Annualised expected return (fraction of the pair's gross notional) of reverting from
    `z` to the exit band (module docstring); 0 inside the band or for a NaN input."""
    exit_z = config.exit_z
    values = (z, sigma, hedge_ratio, half_life)
    if not all(math.isfinite(v) for v in values) or abs(z) <= exit_z:
        return 0.0
    move = (abs(z) - exit_z) * sigma / (1.0 + abs(hedge_ratio))
    sessions = half_life * math.log2(abs(z) / exit_z)
    return float(move * periods / sessions)


def confidence(pvalue: float, config: StatArbConfig) -> float:
    """`clip(1 - pvalue / breakdown_pvalue, 0, 1)`; 0 for NaN."""
    if not math.isfinite(pvalue):
        return 0.0
    return float(min(max(1.0 - pvalue / config.breakdown_pvalue, 0.0), 1.0))


_Finite = Annotated[float, Field(allow_inf_nan=False)]


class PairSignal(BaseModel):
    """An active pair's signal (crosses to the Stat-Arb pod, Stage 39): `pair_id` (`y~x`),
    the `long` and `short` legs, `hedge_ratio` (β > 0, log-price units), `z` (the spread's
    z-score), `exp_return` (annualised fraction of the pair's gross notional, ≥ 0) and
    `confidence` ∈ [0, 1]."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair_id: Annotated[str, Field(min_length=3)]
    long: Annotated[str, Field(min_length=1)]
    short: Annotated[str, Field(min_length=1)]
    hedge_ratio: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    z: _Finite
    exp_return: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    confidence: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def _legs(self) -> Self:
        if self.long == self.short:
            raise ValueError("long and short must be different symbols")
        if pair_id(self.long, self.short) != self.pair_id:
            raise ValueError(f"legs {self.long}, {self.short} do not form pair {self.pair_id}")
        return self


def to_signal(
    pair: Pair,
    state: SpreadState,
    *,
    z: float,
    sigma: float,
    pvalue: float,
    config: StatArbConfig,
    periods: int,
) -> PairSignal | None:
    """The `PairSignal` of `pair` in `state` (`None` when flat): long y / short x for
    `LONG_SPREAD`, the reverse for `SHORT_SPREAD`."""
    if state is SpreadState.FLAT:
        return None
    long, short = (pair.y, pair.x) if state is SpreadState.LONG else (pair.x, pair.y)
    return PairSignal(
        pair_id=pair.pair_id,
        long=long,
        short=short,
        hedge_ratio=pair.hedge_ratio,
        z=z,
        exp_return=expected_return(
            z,
            sigma=sigma,
            hedge_ratio=pair.hedge_ratio,
            half_life=pair.half_life,
            config=config,
            periods=periods,
        ),
        confidence=confidence(pvalue, config),
    )


def target_weights(signals: Iterable[PairSignal], *, pair_gross: float) -> pd.Series:
    """Per-symbol target weights (fractions of NAV, shorts negative, index sorted): each
    pair `+pair_gross/2` long and `-pair_gross/2` short, summed over pairs."""
    if not pair_gross > 0:
        raise ValueError(f"pair_gross must be > 0, got {pair_gross}")
    half = pair_gross / 2.0
    out: dict[str, float] = {}
    seen: set[str] = set()
    for sig in signals:
        if sig.pair_id in seen:
            raise ValueError(f"pair {sig.pair_id} appears twice")
        seen.add(sig.pair_id)
        out[sig.long] = out.get(sig.long, 0.0) + half
        out[sig.short] = out.get(sig.short, 0.0) - half
    return pd.Series(out, dtype="float64").sort_index().rename("weight")


SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "pair_id",
    "sector",
    "y",
    "x",
    "hedge_ratio",
    "half_life",
    "hurst",
    "pvalue",
    "corr",
    "lookback",
    "z",
    "signal",
    "exp_return",
    "confidence",
)


@dataclass(frozen=True)
class PairsSnapshot:
    """The pairs selected at `as_of`, a `table` (`SNAPSHOT_COLUMNS`, one row per pair) and
    the `signals` of the pairs that would enter now (a stateless snapshot: every pair starts
    flat; the live state is the Stat-Arb pod's, Stage 39)."""

    as_of: date
    pairs: list[Pair]
    table: pd.DataFrame
    signals: list[PairSignal] = field(default_factory=list)


def pairs_snapshot(view: DataView, config: StatArbConfig | None = None) -> PairsSnapshot:
    """Select the pairs at `view.as_of` and score their current z (closes through the
    session before the as-of)."""
    cfg = config or view.config.quant.statarb
    periods = view.config.quant.features.sessions_per_year
    members = sorted(view.universe(cfg.universe))
    empty = PairsSnapshot(view.day, [], pd.DataFrame(columns=list(SNAPSHOT_COLUMNS)))
    if len(members) < 2:
        return empty
    lp = log_price_panel(view.returns(members, cfg.formation))
    sectors = member_sectors(view, list(lp.columns))
    pairs = select_pairs(lp, sectors, config=cfg, formed=view.day)
    rows, signals = [], []
    for p in pairs:
        n = zscore_lookback(p.half_life, cfg)
        z, sigma = latest_zscore(spread(lp[p.y], lp[p.x], p.hedge_ratio).to_numpy(), n)
        state, _ = step(PairState(), z, broken=False, half_life=p.half_life, config=cfg)
        sig = to_signal(
            p, state.state, z=z, sigma=sigma, pvalue=p.pvalue, config=cfg, periods=periods
        )
        if sig is not None:
            signals.append(sig)
        rows.append(
            {
                "pair_id": p.pair_id,
                "sector": p.sector,
                "y": p.y,
                "x": p.x,
                "hedge_ratio": p.hedge_ratio,
                "half_life": p.half_life,
                "hurst": p.hurst,
                "pvalue": p.pvalue,
                "corr": p.corr,
                "lookback": n,
                "z": z,
                "signal": str(state.state),
                "exp_return": sig.exp_return if sig else 0.0,
                "confidence": confidence(p.pvalue, cfg),
            }
        )
    if not rows:
        return PairsSnapshot(view.day, pairs, empty.table)
    table = pd.DataFrame(rows, columns=list(SNAPSHOT_COLUMNS))
    return PairsSnapshot(view.day, pairs, table, signals)


__all__: Sequence[str] = [
    "SNAPSHOT_COLUMNS",
    "Event",
    "PairSignal",
    "PairState",
    "PairsSnapshot",
    "SpreadState",
    "confidence",
    "expected_return",
    "latest_zscore",
    "pairs_snapshot",
    "rolling_coint_pvalue",
    "rolling_std",
    "run_pair",
    "spread",
    "step",
    "target_weights",
    "to_signal",
    "zscore",
    "zscore_lookback",
]
