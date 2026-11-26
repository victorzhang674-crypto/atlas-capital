"""The technical analyst's tools (Stage 30): `get_technical_snapshot` and `get_price_summary`
(`TECHNICAL_TOOLS`), the market-regime item and `prefetch`. The analyst interprets these
numbers; it never computes them: every value comes from the Stage 15 feature library, the
Stage 21 regime or the bars themselves, through the context's `DataView` (point in time).

Evidence, one item per group of numbers the analyst may cite, with a stable id:

- `pxs:<SYM>:<last bar>:<ohlc | range | ma | levels | stops>` (`get_price_summary`): the
  recent OHLC table, the 52-week range, the key moving averages, support and resistance
  candidates and stop references. Prices are USD, split- and dividend-adjusted with the
  actions known today (`DataView.prices`, Stage 15's `PricePanel` convention), so every level
  is on today's share basis and comparable with the last close (a split known after the last
  bar rescales that bar too); volume is in today's shares. The windows end at the symbol's
  last bar, which may be older than the latest session (said so in the text). The items' time
  is the latest `available_at` of the bars read.
- `tsnap:<SYM>:<session>:<group>` (`get_technical_snapshot`): every Stage 15 feature of
  `FEATURE_GROUPS` with its percentile against the symbol's own recent history and against
  the index cross-section (Stage 15 `build_snapshots`).
- `regime:<day>:market` (`get_technical_snapshot`): the fund's market regime (Stage 21
  `RegimeState`), given by the caller (`inputs["regime"]`, the fund graph's
  `FundState.regime`) or computed with `regime_at` from the view.
- `gap:<SYM>:<day>:<get_technical_snapshot | market_regime>`: what could not be gathered, and
  why.

Snapshot, regime and gap items are timed one microsecond before the decision cutoff:
computed at the decision moment from data known then (Stage 29's convention). Percentiles are
shown as 0-100; returns, distances and stops as percentages of the last close. Windows:
`agents.technical` (`config/fund.yaml`). A value that cannot be computed reads "n/a", never a
guess.
"""

import math
from collections.abc import Sequence
from datetime import date, datetime
from typing import Annotated

import numpy as np
import pandas as pd
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from atlas.agents.context import AgentContext
from atlas.agents.tools.fundamental_tools import gap_item
from atlas.agents.tools.registry import Tool
from atlas.core.config import TechnicalAnalystConfig
from atlas.core.errors import ConfigError, DataError
from atlas.core.types.instruments import normalize_symbol
from atlas.core.types.research import Evidence
from atlas.data.view import DataView
from atlas.quant.features import build_snapshots, compute_features
from atlas.quant.features.registry import FeatureUnit
from atlas.quant.regime import RegimeState, regime_at


class TechnicalArgs(BaseModel):
    """Arguments of the technical tools: one ticker (normalised; a malformed one fails
    validation, so the model gets an error text)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Annotated[
        str,
        Field(min_length=1, max_length=20, description="Ticker symbol, for example AAPL"),
        AfterValidator(normalize_symbol),
    ]


FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "trend": (
        "price_to_sma_20",
        "price_to_sma_50",
        "price_to_sma_200",
        "price_to_ema_20",
        "price_to_ema_50",
        "price_to_ema_200",
        "sma_20",
        "sma_50",
        "sma_200",
        "ema_20",
        "ema_50",
        "ema_200",
        "macd",
        "macd_signal",
        "macd_hist",
    ),
    "momentum": (
        "ret_1d",
        "ret_5d",
        "ret_21d",
        "ret_63d",
        "ret_126d",
        "ret_252d",
        "momentum_12_1",
        "reversal_5d",
        "rsi_14",
    ),
    "volatility": (
        "vol_21d",
        "vol_63d",
        "downside_vol_63d",
        "atr_14",
        "atr_pct_14",
        "beta_252d",
        "idio_vol_252d",
    ),
    "levels": ("dist_52w_high", "dist_52w_low", "max_dd_252d", "bb_pctb_20"),
    "participation": (
        "volume_z_21d",
        "share_volume_z_21d",
        "amihud_21d",
        "gap_1d",
        "gap_mean_21d",
        "gap_std_63d",
    ),
}
"""The snapshot's evidence groups, in report order; together they hold every registered
Stage 15 feature exactly once (tested)."""

SUMMARY_PARTS = ("ohlc", "range", "ma", "levels", "stops")
"""The `get_price_summary` items, in order."""

MA_FEATURES = (("sma_20", "SMA20"), ("sma_50", "SMA50"), ("sma_200", "SMA200"))
"""The key moving averages of the price summary: Stage 15 feature and label."""

NA = "n/a"

_EPSILON = pd.Timedelta(microseconds=1).to_pytimedelta()


# --- formatting -------------------------------------------------------------------------------


def _finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)


def fmt_feature(value: float | None, unit: FeatureUnit) -> str:
    """A feature value in its unit (`n/a` when undefined): fractions as signed percentages,
    annualised fractions as percentages, USD with two decimals, ratios with three, 0-100
    oscillators with one, z-scores in standard deviations."""
    if value is None or not _finite(value):
        return NA
    match unit:
        case "fraction":
            return f"{value * 100:+.2f}%"
        case "annual_fraction":
            return f"{value * 100:.2f}% annualised"
        case "usd":
            return f"USD {value:.2f}"
        case "ratio":
            return f"{value:.3f}"
        case "index_0_100":
            return f"{value:.1f}"
        case "zscore":
            return f"{value:+.2f} sd"
        case "return_per_usd_mm":
            return f"{value:.3g} per USD 1M"


def _pct(fraction: float | None) -> str:
    """A percentile fraction as 0-100 (`n/a` when undefined)."""
    value = _value(fraction)
    return f"{value * 100:.0f}" if value is not None else NA


def _value(x: float | None) -> float | None:
    """`x` when it is a finite number, else `None`."""
    return x if x is not None and math.isfinite(x) else None


def _at(series: pd.Series, which: str) -> int:
    """The position of the highest (`which="max"`) or lowest value of `series`, NaN skipped."""
    values = series.to_numpy(dtype="float64")
    return int(np.nanargmax(values) if which == "max" else np.nanargmin(values))


def _signed(x: float) -> str:
    return f"{x * 100:+.2f}%"


def _computed_at(view: DataView) -> datetime:
    """The time of a computed evidence item: just before the decision cutoff."""
    return view.as_of.cutoff - _EPSILON


def _settings(view: DataView) -> TechnicalAnalystConfig:
    return view.config.agents.technical


def _require(view: DataView, symbol: str) -> None:
    if symbol in view.blocked:  # `prices` and the snapshots would silently leave it out
        raise DataError(f"symbol {symbol} is blocked by a critical data-quality issue")


def _day(value: object) -> date:
    return pd.Timestamp(value).date()  # type: ignore[arg-type]


# --- get_price_summary ------------------------------------------------------------------------


def _adjusted_bars(view: DataView, symbol: str, sessions: int) -> pd.DataFrame:
    """`symbol`'s bars over the last `sessions` sessions: OHLC adjusted (x `adj_close /
    close`), volume in today's shares (raw / split factor), oldest first."""
    _require(view, symbol)
    bars = view.prices([symbol], sessions)
    if bars.empty:
        raise DataError(
            f"no bars known for {symbol} in the {sessions} sessions to {view.day.isoformat()}"
        )
    bars = bars.sort_values("date").reset_index(drop=True)
    factor = bars["adj_close"] / bars["close"]
    splits = view.split_factors([symbol], sessions)[symbol]
    split_on = pd.to_datetime(bars["date"]).map(splits).fillna(1.0).to_numpy()
    return pd.DataFrame(
        {
            "date": [_day(d) for d in bars["date"]],
            "open": bars["open"] * factor,
            "high": bars["high"] * factor,
            "low": bars["low"] * factor,
            "close": bars["adj_close"],
            "volume": bars["volume"].astype("float64") / split_on,
            "available_at": bars["available_at"],
        }
    )


def _sessions_behind(view: DataView, last: date, window: int) -> int:
    """Sessions after `last` up to the view's latest session (`last` lies in the last
    `window` sessions)."""
    sessions = view.sessions(window)
    return len([d for d in sessions if d > last])


def _feature_values(
    view: DataView, symbol: str, names: Sequence[str], behind: int
) -> dict[str, float | None]:
    """Stage 15 features `names` of `symbol` on its last bar, `behind` sessions before the
    view's latest session (`None` where undefined)."""
    frames = compute_features(view, [symbol], list(names), history=behind + 1)
    out: dict[str, float | None] = {}
    for name in names:
        value = float(frames[name][symbol].iloc[0])
        out[name] = value if math.isfinite(value) else None
    return out


def _ohlc_text(symbol: str, bars: pd.DataFrame, latest: date, behind: int) -> str:
    rows = "; ".join(
        f"{d} O {o:.2f} H {h:.2f} L {lo:.2f} C {c:.2f} V {v / 1e6:.2f}M"
        for d, o, h, lo, c, v in zip(
            bars["date"],
            bars["open"].astype(float),
            bars["high"].astype(float),
            bars["low"].astype(float),
            bars["close"].astype(float),
            bars["volume"].astype(float),
            strict=True,
        )
    )
    stale = ""
    if behind:
        stale = (
            f" Note: last bar {bars['date'].iloc[-1]} is {behind} sessions before the latest "
            f"session {latest}; no newer bar is known."
        )
    return (
        f"{symbol} last {len(bars)} sessions (USD, adjusted for splits and dividends known "
        f"today, so comparable with the last close; volume in today's shares, M = 1e6):"
        f"{stale} {rows}."
    )


def _range_text(symbol: str, bars: pd.DataFrame, want: int, window_start: date) -> str:
    """The 52-week range over `bars`, the symbol's bars in the `want` sessions that end at
    its last bar (from `window_start`): fewer bars are a short history (the first bar is
    after `window_start`) or sessions without a bar."""
    hi, lo = _at(bars["high"], "max"), _at(bars["low"], "min")
    high, low = float(bars["high"].iloc[hi]), float(bars["low"].iloc[lo])
    close = float(bars["close"].iloc[-1])
    n = len(bars)
    first = bars["date"].iloc[0]
    short = ""
    if first > window_start:
        short = f" (only {n} sessions of history, first bar {first})"
    elif n < want:
        short = f" ({want - n} sessions in the window without a bar)"
    position = (close - low) / (high - low) if high > low else math.nan
    where = f"at {position * 100:.1f}% of the range" if _finite(position) else "range n/a"
    return (
        f"{symbol} 52-week range ({n} sessions {first}..{bars['date'].iloc[-1]}){short}: "
        f"high {high:.2f} on {bars['date'].iloc[hi]}, low {low:.2f} on {bars['date'].iloc[lo]} "
        f"(adjusted USD). The last close {close:.2f} is {_signed(close / high - 1)} from the "
        f"high and {_signed(close / low - 1)} from the low, {where}."
    )


def _ma_text(symbol: str, close: float, mas: dict[str, float | None]) -> str:
    parts = []
    for name, label in MA_FEATURES:
        value = _value(mas[name])
        parts.append(
            f"{label} {value:.2f} (close {_signed(close / value - 1)} vs it)"
            if value is not None
            else f"{label} n/a"
        )
    s50, s200 = _value(mas["sma_50"]), _value(mas["sma_200"])
    if s50 is not None and s200 is not None:
        side = "above" if s50 >= s200 else "below"
        cross = f"SMA50 is {abs(s50 / s200 - 1) * 100:.2f}% {side} SMA200"
    else:
        cross = "SMA50 vs SMA200 n/a"
    return (
        f"{symbol} simple moving averages of the adjusted close (USD, Stage 15 features): "
        f"{'; '.join(parts)}. {cross}."
    )


def _levels_text(
    symbol: str,
    bars: pd.DataFrame,
    mas: dict[str, float | None],
    settings: TechnicalAnalystConfig,
    window_start: date,
) -> str:
    close = float(bars["close"].iloc[-1])
    n = len(bars)
    year = f"52-week ({n}-session)" if bars["date"].iloc[0] > window_start else "52-week"
    candidates: list[tuple[str, float, date | None]] = []
    notes = []
    for window in settings.swing_sessions:
        if n < window:
            notes.append(f"no {window}-session level (only {n} sessions)")
            continue
        tail = bars.tail(window)
        lo, hi = _at(tail["low"], "min"), _at(tail["high"], "max")
        candidates.append(
            (f"{window}-session low", float(tail["low"].iloc[lo]), tail["date"].iloc[lo])
        )
        candidates.append(
            (f"{window}-session high", float(tail["high"].iloc[hi]), tail["date"].iloc[hi])
        )
    lo, hi = _at(bars["low"], "min"), _at(bars["high"], "max")
    candidates.append((f"{year} low", float(bars["low"].iloc[lo]), bars["date"].iloc[lo]))
    candidates.append((f"{year} high", float(bars["high"].iloc[hi]), bars["date"].iloc[hi]))
    for name, label in MA_FEATURES:
        value = _value(mas[name])
        if value is not None:
            candidates.append((label, value, None))

    def show(label: str, level: float, day: date | None) -> str:
        on = f" on {day}" if day is not None else ""
        return f"{label} {level:.2f}{on} ({_signed(level / close - 1)})"

    below = sorted((c for c in candidates if c[1] <= close), key=lambda c: -c[1])
    above = sorted((c for c in candidates if c[1] > close), key=lambda c: c[1])
    support = "; ".join(show(*c) for c in below) or "none"
    resistance = "; ".join(show(*c) for c in above) or "none"
    extra = f" ({'; '.join(notes)})" if notes else ""
    return (
        f"{symbol} support and resistance candidates (adjusted USD; distance from the last "
        f"close {close:.2f}){extra}. Support below: {support}. Resistance above: {resistance}."
    )


def _stops_text(
    symbol: str, bars: pd.DataFrame, atr: float | None, settings: TechnicalAnalystConfig
) -> str:
    close = float(bars["close"].iloc[-1])
    atr = _value(atr)
    if atr is not None:
        multiples = ", ".join(
            f"{k:g} x ATR = {k * atr / close * 100:.2f}%" for k in settings.atr_stop_multiples
        )
        head = f"ATR(14) {atr:.2f} USD = {atr / close * 100:.2f}% of the last close {close:.2f}"
    else:
        multiples = "ATR multiples n/a"
        head = f"ATR(14) n/a (last close {close:.2f})"
    swings = []
    for window in settings.swing_sessions:
        if len(bars) < window:
            continue
        tail = bars.tail(window)
        low, high = float(tail["low"].min()), float(tail["high"].max())
        swings.append(
            f"to the {window}-session low {(1 - low / close) * 100:.2f}% (LONG), "
            f"to the {window}-session high {(high / close - 1) * 100:.2f}% (SHORT)"
        )
    return (
        f"{symbol} stop references: {head}. Stop distances as a percentage of the last close "
        f"(below it for a LONG view, above it for a SHORT view): {multiples}; "
        f"{'; '.join(swings) or 'no swing levels'}."
    )


def price_summary(view: DataView, symbol: str) -> list[Evidence]:
    """The `get_price_summary` evidence of `symbol` (module docstring): one item per
    `SUMMARY_PARTS`. `DataError` for a blocked symbol or one without bars."""
    symbol = normalize_symbol(symbol)
    settings = _settings(view)
    want = settings.range_sessions
    bars = _adjusted_bars(view, symbol, want)
    latest = view.sessions(1)[-1]
    last = bars["date"].iloc[-1]
    behind = _sessions_behind(view, last, want)
    if behind:  # a stale last bar: the window is the `want` sessions that end at it
        bars = _adjusted_bars(view, symbol, want + behind)
    window_start = view.sessions(want + behind)[0]
    feats = _feature_values(view, symbol, [name for name, _ in MA_FEATURES] + ["atr_14"], behind)
    close = float(bars["close"].iloc[-1])
    at = pd.Timestamp(bars["available_at"].max()).tz_convert("UTC").to_pydatetime()
    texts = {
        "ohlc": _ohlc_text(symbol, bars.tail(settings.ohlc_sessions), latest, behind),
        "range": _range_text(symbol, bars, want, window_start),
        "ma": _ma_text(symbol, close, feats),
        "levels": _levels_text(symbol, bars, feats, settings, window_start),
        "stops": _stops_text(symbol, bars, feats["atr_14"], settings),
    }
    return [
        Evidence(
            id=f"pxs:{symbol}:{last.isoformat()}:{part}",
            source="prices",
            as_of=at,
            text=texts[part],
        )
        for part in SUMMARY_PARTS
    ]


# --- get_technical_snapshot -------------------------------------------------------------------


def technical_snapshot(view: DataView, symbol: str) -> list[Evidence]:
    """The snapshot evidence of `symbol`: one item per `FEATURE_GROUPS` group (Stage 15
    `build_snapshots`). `DataError` for a blocked symbol or one with no feature value (no bars
    in the window: `build_snapshots` would return a snapshot of n/a values)."""
    symbol = normalize_symbol(symbol)
    _require(view, symbol)
    names = [name for group in FEATURE_GROUPS.values() for name in group]
    snapshots = build_snapshots(view, [symbol], names=names)
    if symbol not in snapshots:
        raise DataError(f"no technical snapshot for {symbol} on {view.day.isoformat()}")
    snap = snapshots[symbol]
    if all(r.value is None for r in snap.readings):  # no bar in the window: nothing to show
        raise DataError(
            f"no technical features for {symbol} on {snap.session.isoformat()}: no bars known "
            "in the window"
        )
    settings = view.config.quant.features.snapshot
    at = _computed_at(view)
    head = (
        f"{symbol} technical features on {snap.session} (percentiles 0-100: own = against "
        f"its own last {snap.history_sessions} sessions, universe = against {snap.universe_size} "
        f"names, the {settings.universe} members and {symbol}; USD levels have no universe "
        "percentile)"
    )
    items = []
    for group, group_names in FEATURE_GROUPS.items():
        lines = []
        for name in group_names:
            r = snap.reading(name)
            lines.append(
                f"{name} {fmt_feature(r.value, r.unit)} (own {_pct(r.pct_own)}, "
                f"universe {_pct(r.pct_universe)})"
            )
        items.append(
            Evidence(
                id=f"tsnap:{symbol}:{snap.session.isoformat()}:{group}",
                source="technical_features",
                as_of=at,
                text=f"{head}, {group}: {'; '.join(lines)}.",
            )
        )
    return items


# --- the market regime ------------------------------------------------------------------------


def _num(x: float | None, spec: str) -> str:
    return format(x, spec) if _finite(x) else NA


def _frac(x: float | None, spec: str = "+.2f") -> str:
    value = _value(x)
    return f"{format(value * 100, spec)}%" if value is not None else NA


def regime_item(view: DataView, regime: RegimeState | None) -> Evidence:
    """The `regime:<day>:market` item: `regime` (computed with `regime_at(view)` when `None`).
    `ConfigError` for a regime of another decision day; `regime_at`'s `DataError` passes."""
    state = regime if regime is not None else regime_at(view)
    if state.as_of != view.day:
        raise ConfigError(
            f"a regime for {state.as_of.isoformat()} cannot inform a decision on "
            f"{view.day.isoformat()}"
        )
    bench = view.config.fund.benchmark
    if state.hmm_probs is None:
        hmm = "HMM state probabilities n/a"
    else:
        probs = ", ".join(
            f"{label} {p:.2f}" for label, p in zip(state.hmm_labels, state.hmm_probs, strict=True)
        )
        hmm = f"HMM state probabilities {probs}"
    credit = _num(state.credit_z, "+.2f")
    if state.credit_source is not None and _finite(state.credit_z):
        credit += f" from {state.credit_source}"
    gap = f"{state.inflation_gap:+.2f} points" if _finite(state.inflation_gap) else NA
    missing = f" Missing components: {', '.join(state.missing)}." if state.missing else ""
    text = (
        f"Market regime for decisions on {state.as_of} (data through {state.data_date}; "
        f"benchmark {bench}): volatility regime {state.vol_regime} (vol score "
        f"{_pct(state.vol_score)}, {bench} 21-session realised vol "
        f"{_frac(state.realised_vol, '.2f')} annualised, VIX {_num(state.vix, '.1f')}); "
        f"trend regime {state.trend_regime} ({bench} / its 200-session average "
        f"{_num(state.price_to_sma_200, '.3f')}, {bench} 12-1 momentum "
        f"{_frac(state.momentum_12_1)}, breadth {_frac(state.breadth, '.0f')} of members above "
        f"their 200-session average); {hmm}; macro quadrant {state.macro_quadrant or NA} "
        f"(growth score {_num(state.growth_score, '+.2f')}, inflation gap {gap}); stress flag "
        f"{'ON' if state.stress_flag else 'off'} (credit z-score {credit}, NFCI "
        f"{_num(state.nfci, '+.2f')}); risk scalar {state.risk_scalar:.2f} (fraction of normal "
        f"fund risk).{missing}"
    )
    return Evidence(
        id=f"regime:{view.day.isoformat()}:market",
        source="regime",
        as_of=_computed_at(view),
        text=text,
    )


def given_regime(ctx: AgentContext) -> RegimeState | None:
    """`ctx.inputs["regime"]` (the caller's `RegimeState`) or `None`; `ConfigError` for
    anything else."""
    value = ctx.inputs.get("regime")
    if value is None or isinstance(value, RegimeState):
        return value
    raise ConfigError(f"inputs['regime'] must be a RegimeState, not {type(value).__name__}")


def _regime_or_gap(view: DataView, symbol: str, regime: RegimeState | None) -> Evidence:
    try:
        return regime_item(view, regime)
    except DataError as exc:
        return gap_item(view, symbol, "market_regime", exc)


# --- prefetch and the tools -------------------------------------------------------------------


def prefetch(view: DataView, symbol: str, regime: RegimeState | None = None) -> list[Evidence]:
    """Both tools' evidence for `symbol`: the price summary (its `DataError` propagates: a
    blocked symbol or one without bars gets no report), the snapshot (a `DataError` becomes a
    `gap:` item) and the market regime (`regime`, else computed; a `DataError` becomes a
    `gap:` item)."""
    symbol = normalize_symbol(symbol)
    items = price_summary(view, symbol)
    try:
        items += technical_snapshot(view, symbol)
    except DataError as exc:
        items.append(gap_item(view, symbol, "get_technical_snapshot", exc))
    items.append(_regime_or_gap(view, symbol, regime))
    return items


def _snapshot_tool(ctx: AgentContext, args: TechnicalArgs) -> list[Evidence]:
    return [
        *technical_snapshot(ctx.view, args.symbol),
        _regime_or_gap(ctx.view, args.symbol, given_regime(ctx)),
    ]


def _summary_tool(ctx: AgentContext, args: TechnicalArgs) -> list[Evidence]:
    return price_summary(ctx.view, args.symbol)


GET_TECHNICAL_SNAPSHOT = Tool(
    name="get_technical_snapshot",
    description=(
        "One stock's precomputed technical features known today (trend, momentum, volatility, "
        "levels, participation), each with its percentile against the stock's own history and "
        "against the index members, plus the fund's market regime."
    ),
    args=TechnicalArgs,
    fn=_snapshot_tool,
)
GET_PRICE_SUMMARY = Tool(
    name="get_price_summary",
    description=(
        "One stock's recent daily OHLC table, 52-week range, key moving averages, support and "
        "resistance candidates and stop references (ATR multiples, swing lows and highs), "
        "known today, adjusted for splits and dividends."
    ),
    args=TechnicalArgs,
    fn=_summary_tool,
)

TECHNICAL_TOOLS = (GET_TECHNICAL_SNAPSHOT, GET_PRICE_SUMMARY)
"""The technical analyst's tools, in brief order."""


__all__ = [
    "FEATURE_GROUPS",
    "GET_PRICE_SUMMARY",
    "GET_TECHNICAL_SNAPSHOT",
    "MA_FEATURES",
    "SUMMARY_PARTS",
    "TECHNICAL_TOOLS",
    "TechnicalArgs",
    "fmt_feature",
    "given_regime",
    "prefetch",
    "price_summary",
    "regime_item",
    "technical_snapshot",
]
