"""`DataView` -> `RegimeState` (Stage 21): `regime_at(view)` for one decision day and
`regime_history(lake, start, end)` for every decision day of a range, through one code path.

Timing. A state for decision day `t` uses what the decision of `t` knows (its `AsOf`): bars
through the previous session `s` (`data_date`), FRED rows published before the cutoff.

Everything is read from **one** `DataView` (the last decision day's) and sliced per decision:

- **FRED series** (`VIXCLS`, `T10Y2Y`, the HY OAS, `INDPRO`, `PAYEMS`, `ICSA`, `CPILFESL`,
  `NFCI`): `DataView.macro_vintages` returns every vintage known at the view's cutoff; for
  decision `t`, Stage 10's `known_observations` at `t`'s cutoff gives exactly the vintages `t`
  knew (late first releases and later revisions included). Daily series are put on the
  session grid by observation date (a non-session date moves to the next session) and carried
  forward over at most `max_fill_sessions` sessions. Releases are read over
  `macro.lookback_days` calendar days before `t`.
- **Prices**: one panel read at the view, starting at a fixed anchor (`train_start` minus the
  vol-percentile window and 300 sessions), so every run computes bit-identical rolling values.
  Every price input is a ratio of adjusted closes inside a trailing window (`vol_21d`,
  `price_to_sma_200`, `momentum_12_1`, daily returns, the credit proxy), which a later split or
  dividend back-adjustment multiplies through unchanged; rows after `s` are never used for `t`.
- **Breadth** uses `DataView.members_on(t)` (membership is dated by its start/end dates).
- **HMM**: the fit for `t`'s month M is trained on the feature rows (`ret`, `vol`, `credit`,
  `slope`) of the sessions before M, with the slope known at the cutoff of M's first session;
  the probability for `t` is the forward-filtered probability of `s`, with the slope known at
  `t`. The window starts at `quant.regime.train_start` (expanding).

So `regime_history` equals `regime_at` day by day (tested), with two differences from a view
per day: (1) a split or dividend that becomes known between a day and the end of the range
back-adjusts that day's prices too, which changes the readings in the last bits only (ratios
are invariant; ~1e-15 relative, labels unchanged; tested); (2) the data-quality block list is
the one of the reading view (a symbol or series blocked only on an earlier day is not dropped
for that day: on the real lake, EOG and HP were blocked on 2020-03-16 and breadth read 0.1189
in the history vs 0.1195 in a one-day view). One view per day would cost a full quality
report per day (about 50 s on the real lake).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf
from atlas.core.storage import Lake
from atlas.data.macro.pit import known_observations
from atlas.data.quality import latest_known_session
from atlas.data.view import DataView
from atlas.quant.features.registry import PricePanel, compute_panel
from atlas.quant.regime.hmm import (
    HMMFit,
    filter_probabilities,
    month_fit,
    month_start,
    state_labels,
)
from atlas.quant.regime.macro_quadrant import (
    INFLATION_SERIES,
    NFCI_SERIES,
    classify_quadrant,
    credit_proxy,
    credit_zscore,
    growth_score,
    growth_votes,
    inflation_gap,
    stress_flag,
)
from atlas.quant.regime.state import Component, RegimeState, risk_scalar
from atlas.quant.regime.vol_trend import (
    breadth,
    classify_trend,
    classify_vol,
    trailing_percentile,
    trend_votes,
    vol_score,
)

_log = get_logger(__name__)

VIX_SERIES = "VIXCLS"
"""FRED id of the VIX close (index points)."""

SLOPE_SERIES = "T10Y2Y"
"""FRED id of the 10y - 2y Treasury slope (percentage points)."""

RELEASE_SERIES: tuple[str, ...] = ("INDPRO", "PAYEMS", "ICSA", INFLATION_SERIES, NFCI_SERIES)
"""FRED ids of the macro quadrant and stress releases."""

PRICE_FEATURES: tuple[str, ...] = ("vol_21d", "price_to_sma_200", "momentum_12_1")
"""Stage 15 features the regimes use (one source)."""

HMM_FEATURES: tuple[str, ...] = ("ret", "vol", "credit", "slope")
"""Columns of the HMM feature frame."""

_EXTRA_SESSIONS = 300
"""Sessions read before the vol-percentile window: 252 for 12-1 momentum plus a margin."""

_VINTAGE_COLUMNS = ["series_id", "obs_date", "value", "realtime_start", "available_at"]


@dataclass(frozen=True)
class _Vintages:
    """FRED rows (every vintage known at the reading view) and what a decision knew."""

    rows: pd.DataFrame

    @classmethod
    def read(cls, view: DataView, series: Sequence[str], span: timedelta) -> "_Vintages":
        try:
            rows = view.macro_vintages(list(series), span)
        except DataError as exc:  # no macro table at all: every series is missing (reported)
            _log.warning("regime.macro_unavailable", day=view.day.isoformat(), error=str(exc))
            rows = pd.DataFrame(columns=_VINTAGE_COLUMNS)
        return cls(rows)

    def known(self, point: AsOf, first: date) -> pd.DataFrame:
        """The values known at `point` for observations dated in [`first`, `point.day`]: index
        `obs_date` (DatetimeIndex), one float column per series (Stage 10 semantics)."""
        if self.rows.empty:
            return pd.DataFrame(dtype="float64")
        latest = known_observations(self.rows, point)
        inside = latest["obs_date"].map(lambda d: first <= d <= point.day).astype(bool)
        chosen = latest.loc[inside & latest["value"].notna()]
        wide = chosen.pivot(index="obs_date", columns="series_id", values="value")
        wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index), name="obs_date")
        return wide.sort_index().astype("float64")


def _on_sessions(column: pd.Series | None, sessions: pd.DatetimeIndex, max_fill: int) -> pd.Series:
    """A FRED daily series on the session grid: each observation at the first session on or
    after its date (the latest observation wins a session), carried forward over at most
    `max_fill` sessions."""
    values = np.full(len(sessions), np.nan)
    if column is not None and len(sessions):
        clean = column.dropna()
        pos = sessions.searchsorted(pd.DatetimeIndex(clean.index), side="left")
        keep = pos < len(sessions)
        frame = pd.DataFrame({"pos": pos[keep], "value": clean.to_numpy(dtype="float64")[keep]})
        frame = frame.drop_duplicates("pos", keep="last")
        values[frame["pos"].to_numpy(dtype="int64")] = frame["value"].to_numpy(dtype="float64")
    out = pd.Series(values, index=sessions, dtype="float64")
    return out.ffill(limit=max_fill) if max_fill > 0 else out


def _last(column: pd.Series | None) -> float:
    if column is None:
        return math.nan
    clean = column.dropna()
    return float(clean.iloc[-1]) if len(clean) else math.nan


def _opt(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def _members(view: DataView, day: date, universe: str) -> tuple[str, ...]:
    try:
        return tuple(view.members_on(day, universe))
    except DataError as exc:  # no membership data: breadth is missing (reported)
        _log.warning("regime.universe_unavailable", day=day.isoformat(), error=str(exc))
        return ()


def _first_session(day: date, cal: TradingCalendar) -> date:
    return day if cal.is_session(day) else cal.next_session(day)


def _compute(view: DataView, points: Sequence[AsOf]) -> list[RegimeState]:
    """The states of the decisions `points` (ascending, none after `view`'s cutoff)."""
    cfg = view.config
    rc = cfg.quant.regime
    bench = cfg.fund.benchmark
    cal = TradingCalendar(config=cfg.calendar)
    if any(p.cutoff > view.as_of.cutoff for p in points):
        raise ValueError("a decision is after the reading view's cutoff")
    data_dates = [latest_known_session(p, cal) for p in points]
    members = [_members(view, p.day, rc.universe) for p in points]

    # --- one price panel from a fixed anchor ---
    legs = [rc.credit.proxy_long, rc.credit.proxy_hedge]
    start = cal.offset(
        _first_session(rc.train_start, cal), -(rc.vol.percentile_window + _EXTRA_SESSIONS)
    )
    n = len(cal.sessions(start, data_dates[-1]))
    names = sorted({m for ms in members for m in ms} - {bench, *legs})
    symbols = list(dict.fromkeys([bench, *legs, *names]))
    bars = view.prices(symbols, n)
    if bars.empty or not (bars["symbol"] == bench).any():
        raise DataError(f"no bars for the benchmark {bench} before {points[-1].day}")
    panel = PricePanel.from_bars(
        bars, view.sessions(n), symbols, sessions_per_year=cfg.quant.features.sessions_per_year
    )
    sessions = pd.DatetimeIndex(panel.close.index)
    feats = compute_panel(panel, list(PRICE_FEATURES))
    close = panel.close
    rv = feats["vol_21d"][bench]
    ratio = feats["price_to_sma_200"]
    momentum = feats["momentum_12_1"][bench]
    rv_pct = trailing_percentile(rv, rc.vol.percentile_window, rc.vol.percentile_min_obs)
    proxy = credit_proxy(close[legs[0]], close[legs[1]], rc.credit.proxy_hedge_ratio)
    base = pd.DataFrame(
        {"ret": close[bench] / close[bench].shift(1) - 1.0, "vol": rv, "credit": proxy["change"]},
        index=sessions,
    )
    base = base.loc[base.index >= pd.Timestamp(rc.train_start)]

    # --- FRED vintages known at the view ---
    market = _Vintages.read(
        view,
        (VIX_SERIES, SLOPE_SERIES, rc.credit.oas_series),
        timedelta(days=(view.day - sessions[0].date()).days + 1),
    )
    releases = _Vintages.read(
        view,
        RELEASE_SERIES,
        timedelta(days=(view.day - points[0].day).days + rc.macro.lookback_days),
    )

    # --- trend votes for every decision at once ---
    stamps = pd.DatetimeIndex([pd.Timestamp(d) for d in data_dates])
    absent = stamps.difference(sessions)
    if len(absent):
        raise DataError(f"no price panel rows for {list(absent.date[:3])}")
    mask = pd.DataFrame(False, index=pd.RangeIndex(len(points)), columns=pd.Index(names))
    for i, ms in enumerate(members):
        mask.loc[i, [m for m in ms if m in mask.columns]] = True
    breadth_share = breadth(
        ratio.reindex(columns=names).loc[stamps].reset_index(drop=True),
        mask,
        rc.trend.breadth_min_names,
    )
    breadth_share.index = stamps
    votes = trend_votes(ratio[bench].loc[stamps], momentum.loc[stamps], breadth_share, rc.trend)
    trends = classify_trend(votes, rc.trend)

    fits: dict[pd.Timestamp, HMMFit | None] = {}
    out: list[RegimeState] = []
    window = rc.vol.percentile_window
    for point, data_date, stamp in zip(points, data_dates, stamps, strict=True):
        upto = sessions[sessions <= stamp]
        known_market = market.known(point, sessions[0].date())
        vix = _on_sessions(known_market.get(VIX_SERIES), upto, rc.max_fill_sessions)
        vix_now = float(vix.iloc[-1])
        vix_pct = trailing_percentile(vix.iloc[-window:], window, rc.vol.percentile_min_obs)
        score = vol_score(rv_pct.loc[[stamp]], pd.Series([float(vix_pct.iloc[-1])], index=[stamp]))
        vol_label = classify_vol(
            score, pd.Series([vix_now], index=[stamp]), rv.loc[[stamp]], rc.vol
        ).iloc[0]
        trend_label = trends.loc[stamp]
        if vol_label is None:
            raise DataError(
                f"no volatility regime for {point.day}: {bench} realised vol / VIX history "
                "too short"
            )
        if trend_label is None:
            raise DataError(f"no trend regime for {point.day}: no {bench} 200-session average")

        # HMM: the month's fit (slope known at the month's first session), today's filter
        month = month_start(point.day)
        if month not in fits:
            first = AsOf.for_session(_first_session(month.date(), cal), config=cfg.calendar)
            slope_m = _on_sessions(
                market.known(first, sessions[0].date()).get(SLOPE_SERIES),
                sessions,
                rc.max_fill_sessions,
            )
            fits[month] = month_fit(base.assign(slope=slope_m).dropna(), month, rc.hmm)
        fit = fits[month]
        hmm_probs: tuple[float, ...] | None = None
        if fit is not None:
            slope_t = _on_sessions(known_market.get(SLOPE_SERIES), upto, rc.max_fill_sessions)
            rows = base.loc[base.index <= stamp].assign(slope=slope_t).dropna()
            if len(rows):
                last = filter_probabilities(fit, rows).iloc[-1]
                hmm_probs = tuple(float(p) for p in last.to_numpy())

        oas = known_market.get(rc.credit.oas_series)
        credit_z, source = credit_zscore(
            None if oas is None else oas.loc[:stamp], proxy["level"].loc[:stamp], rc.credit
        )
        rel = releases.known(point, point.day - timedelta(days=rc.macro.lookback_days))
        g_votes = growth_votes(
            rel.get("INDPRO"), rel.get("PAYEMS"), rel.get("ICSA"), rc.macro, cfg.data.macro
        )
        growth = growth_score(g_votes)
        gap = inflation_gap(rel.get(INFLATION_SERIES), cfg.data.macro)
        quadrant = classify_quadrant(growth, gap)
        nfci = _last(rel.get(NFCI_SERIES))
        stress, stress_missing = stress_flag(credit_z, nfci, rc)

        missing: list[Component] = []
        if not math.isfinite(vix_now):
            missing.append("vix")
        if not math.isfinite(float(breadth_share.loc[stamp])):
            missing.append("breadth")
        if not math.isfinite(float(votes["momentum"].loc[stamp])):
            missing.append("momentum")
        if hmm_probs is None:
            missing.append("hmm")
        if quadrant is None:
            missing.append("macro_quadrant")
        missing.extend(stress_missing)
        out.append(
            RegimeState(
                as_of=point.day,
                data_date=data_date,
                vol_regime=vol_label,
                trend_regime=trend_label,
                hmm_probs=hmm_probs,
                hmm_labels=() if hmm_probs is None else state_labels(len(hmm_probs)),
                macro_quadrant=quadrant,
                stress_flag=stress,
                risk_scalar=risk_scalar(
                    vol_label,
                    trend_label,
                    stress=stress,
                    quadrant=quadrant,
                    p_turbulent=None if hmm_probs is None else hmm_probs[-1],
                    config=rc,
                ),
                credit_source=source if math.isfinite(credit_z) else None,
                realised_vol=_opt(float(rv.loc[stamp])),
                vix=_opt(vix_now),
                vol_score=_opt(float(score.iloc[0])),
                price_to_sma_200=_opt(float(ratio[bench].loc[stamp])),
                momentum_12_1=_opt(float(momentum.loc[stamp])),
                breadth=_opt(float(breadth_share.loc[stamp])),
                credit_z=_opt(credit_z),
                nfci=_opt(nfci),
                growth_score=_opt(growth),
                inflation_gap=_opt(gap),
                missing=tuple(missing),
            )
        )
        if len(out) % 250 == 0:
            _log.info("regime.history_progress", days=len(out), last=point.day.isoformat())
    return out


def regime_at(view: DataView) -> RegimeState:
    """The regime of `view`'s decision (`view.as_of`), from what `view` knows (module
    docstring).

    Raises `DataError` when the benchmark has no bars or too short a history for a volatility
    or trend regime; optional components (VIX, breadth, momentum, HMM, macro quadrant, credit,
    NFCI) that cannot be computed are `None` and listed in `RegimeState.missing`."""
    return _compute(view, [view.as_of])[0]


def regime_history(
    lake: Lake, start: date, end: date, config: AtlasConfig | None = None
) -> list[RegimeState]:
    """The regime of every decision day (trading session) in [`start`, `end`], ascending, each
    from what that day's decision knew, read through one `DataView` at `end` (module
    docstring). Raises `ValueError` when `start` > `end` and `DataError` when the range holds
    no session."""
    if start > end:
        raise ValueError(f"start ({start}) must not be after end ({end})")
    cfg = config if config is not None else AtlasConfig()
    cal = TradingCalendar(config=cfg.calendar)
    days = cal.sessions(start, end)
    if not days:
        raise DataError(f"no trading session in [{start}, {end}]")
    view = DataView(lake, days[-1], cfg)
    states = _compute(view, [AsOf.for_session(d, config=cfg.calendar) for d in days])
    _log.info(
        "regime.history_built", days=len(states), first=start.isoformat(), last=end.isoformat()
    )
    return states
