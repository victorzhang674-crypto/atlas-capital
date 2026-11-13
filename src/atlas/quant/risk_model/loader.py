"""Real-data inputs of the factor risk model (Stage 19): `DataView` + Stage 15/16 -> daily
cross-sections for the regression, and the as-of exposure inputs for a model.

Styles (see the stage plan): `value`, `quality`, `growth`, `leverage` = the Stage 16
`style_composites` of the cleaned factor exposures at a monthly anchor; `size` = ln(market cap)
(USD, refreshed daily from the adjusted close); `momentum` = `momentum_12_1`, `beta` =
`beta_252d`, `liquidity` = `amihud_21d` (Stage 15, daily). Orientation is never flipped.

Timing. `AsOf.for_session(D)` is the decision on the morning of D: bars through D-1 are known.
`style_inputs(view)` therefore describes the close of the view's latest known session (its
`data_date`). For the regression of session `t`, `load_cross_sections` uses the fundamental
inputs of the latest monthly anchor `a <= t` (a view at `a`: data through `a-1`), prices-based
styles and caps through the close of `t-1`, and the returns of `t`. Everything is read through
`DataView`, so each piece is point-in-time.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import cast

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.factors.composite import style_composites
from atlas.quant.factors.fundamental import factor_panel, panel_sectors
from atlas.quant.factors.store import FactorStore, StoredExposures, monthly_rebalance_dates
from atlas.quant.features.registry import (
    REGISTRY,
    PricePanel,
    compute_features,
    compute_panel,
)
from atlas.quant.risk_model.etf_mapping import EtfExposures, etf_exposures, extend_model
from atlas.quant.risk_model.factor_model import (
    STYLE_NAMES,
    CrossSection,
    FactorHistory,
    FactorSet,
    RiskModel,
    build_risk_model,
    fit_factor_history,
)
from atlas.quant.risk_model.store import RiskModelStore

_log = get_logger(__name__)

FUNDAMENTAL_STYLES: tuple[str, ...] = ("value", "quality", "growth", "leverage")
"""Styles taken from the Stage 16 group composites."""

TECHNICAL_FEATURES: dict[str, str] = {
    "momentum": "momentum_12_1",
    "beta": "beta_252d",
    "liquidity": "amihud_21d",
}
"""Style -> Stage 15 feature."""


@dataclass(frozen=True)
class StyleInputs:
    """The exposure inputs of a model at the close of `data_date`: `raw_styles` (names x
    `STYLE_NAMES`), `sectors` (GICS name or None) and `caps` (USD)."""

    data_date: date
    raw_styles: pd.DataFrame
    sectors: pd.Series
    caps: pd.Series


def _log_cap(caps: pd.Series) -> pd.Series:
    values = caps.to_numpy(dtype="float64")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(values > 0, np.log(np.where(values > 0, values, 1.0)), np.nan)
    return pd.Series(out, index=caps.index)


def _fundamental(
    view: DataView, symbols: Sequence[str] | None
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """`(value/quality/growth/leverage composites, sectors, market caps)` at `view`."""
    panel = factor_panel(view, symbols)
    comps = style_composites(panel.exposures)[list(FUNDAMENTAL_STYLES)]
    return comps, panel.sectors, panel.market["market_cap"].astype("float64")


def _fundamental_stored(
    view: DataView, stored: StoredExposures
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """`_fundamental(view, None)` from the cached panel of the view's day: the same style
    composites and sectors; the market caps are exp(the stored raw `size`), which is the cap
    `factor_panel` computed up to floating-point rounding (about 1e-16 relative)."""
    names = list(stored.exposures.index)
    comps = style_composites(stored.exposures)[list(FUNDAMENTAL_STYLES)]
    caps = pd.Series(np.exp(stored.raw["size"].to_numpy(dtype="float64")), index=comps.index)
    return comps, panel_sectors(view, names), caps


def style_inputs(view: DataView, symbols: Sequence[str] | None = None) -> StyleInputs:
    """The model's exposure inputs for `symbols` (default: the factor universe) as known at
    `view`: fundamental styles and caps from `factor_panel`, technical styles from
    `compute_features` (latest session)."""
    comps, sectors, caps = _fundamental(view, symbols)
    names = list(comps.index)
    if not names:
        raise DataError(f"no names with factor exposures at {view.as_of.day}")
    tech = compute_features(view, names, list(TECHNICAL_FEATURES.values()), history=1)
    raw = comps.copy()
    raw["size"] = _log_cap(caps.reindex(names))
    for style, feature in TECHNICAL_FEATURES.items():
        raw[style] = tech[feature].iloc[-1].reindex(names)
    return StyleInputs(
        data_date=view.sessions(1)[-1],
        raw_styles=raw[list(STYLE_NAMES)],
        sectors=sectors.reindex(names),
        caps=caps.reindex(names),
    )


def build_model_from_view(
    view: DataView, history: FactorHistory, symbols: Sequence[str] | None = None
) -> RiskModel:
    """The risk model for the decision at `view`: exposure inputs from `style_inputs(view)`,
    factor returns up to its `data_date` from `history`."""
    inputs = style_inputs(view, symbols)
    return build_risk_model(
        history, inputs.raw_styles, inputs.sectors, inputs.caps, inputs.data_date, view.config
    )


def _row(frame: pd.DataFrame, stamp: pd.Timestamp, columns: Sequence[str]) -> pd.Series:
    """Row `stamp` of `frame` for `columns`, as a Series that owns its values (NaN for a column
    the frame lacks). `frame.loc[stamp, columns]` slices `columns` over every row first and
    returns a view of one row: kept in a cross-section, it holds that whole slice alive, one
    per session (memory ~ sessions^2 x names)."""
    return cast(pd.Series, frame.loc[stamp]).reindex(list(columns)).copy()


@dataclass(frozen=True)
class _Anchor:
    """The fundamental inputs at one monthly anchor (a view at `day`, data through the
    previous session)."""

    day: date
    data_date: date
    styles: pd.DataFrame
    sectors: pd.Series
    caps: pd.Series


def load_cross_sections(
    lake: Lake,
    start: date,
    end: date,
    config: AtlasConfig | None = None,
    *,
    symbols: Sequence[str] | None = None,
    factor_store: FactorStore | None = None,
) -> list[CrossSection]:
    """The daily cross-sections for the sessions in [`start`, `end`]: one per session, names =
    the factor universe (`symbols`, default the point-in-time members) at the latest monthly
    anchor on or before the session. Raises `DataError` when the window has no session.

    `factor_store`: a monthly anchor whose panel is in that cache (`FactorStore.read_on`: the
    panel computed on the anchor day itself, so the same timing) is read from it instead of
    computed; an anchor it lacks is computed. Not used with `symbols` (a stored panel is
    cleaned over the whole universe). The cache must have been built from this lake as it is
    now: it is keyed by the factor settings, not by the data."""
    cfg = config if config is not None else AtlasConfig()
    cal = TradingCalendar(config=cfg.calendar)
    days = cal.sessions(start, end)
    if not days:
        raise DataError(f"no trading session in [{start}, {end}]")
    step = cfg.quant.risk_model.factor_model.anchor_months
    months = monthly_rebalance_dates(
        cal.offset(days[0], -1) - timedelta(days=31 * step + 14), end, cal
    )
    anchors_all = [a for a in months if a.month % step == 0] if step > 1 else list(months)
    anchor_for = {}
    for day in days:
        eligible = [a for a in anchors_all if a <= day]
        if not eligible:
            raise DataError(f"no monthly anchor on or before {day}")
        anchor_for[day] = eligible[-1]
    view: DataView | None = None
    anchors: dict[date, _Anchor] = {}
    for a in sorted(set(anchor_for.values())):
        view = DataView(lake, a, cfg) if view is None else view.advance(a)
        stored = (
            factor_store.read_on(view.as_of)
            if factor_store is not None and symbols is None
            else None
        )
        if stored is not None:
            styles, sectors, caps = _fundamental_stored(view, stored)
        else:
            styles, sectors, caps = _fundamental(view, symbols)
        anchors[a] = _Anchor(
            day=a, data_date=view.sessions(1)[-1], styles=styles, sectors=sectors, caps=caps
        )
        _log.info(
            "risk_model.anchor_loaded",
            anchor=a.isoformat(),
            names=len(styles),
            source="computed" if stored is None else "cache",
        )
    names = sorted({n for anchor in anchors.values() for n in anchor.styles.index})
    if not names:
        raise DataError("no names with factor exposures in the window")
    features = list(TECHNICAL_FEATURES.values())
    specs = REGISTRY.specs(features)
    settings = cfg.quant.features
    n_hist = max(s.lookback for s in specs) + settings.buffer_sessions
    first_needed = cal.offset(cal.offset(days[0], -1), -(n_hist - 1))
    n = len(cal.sessions(first_needed, days[-1]))
    benchmark = cfg.fund.benchmark
    data_view = DataView(lake, cal.next_session(days[-1]), cfg)
    request = names if benchmark in names else [*names, benchmark]
    bars = data_view.prices(request, n)
    blocked = data_view.blocked
    usable = [s for s in names if s not in blocked]
    if not usable:
        raise DataError(
            f"every name in the window is blocked by the data-quality checks: {names[:5]}"
        )
    panel = PricePanel.from_bars(
        bars,
        data_view.sessions(n),
        usable,
        benchmark=benchmark,
        sessions_per_year=settings.sessions_per_year,
    )
    tech = compute_panel(panel, features)
    close, returns = panel.close, panel.returns
    out: list[CrossSection] = []
    for day in days:
        anchor = anchors[anchor_for[day]]
        prev = pd.Timestamp(cal.offset(day, -1))
        base = pd.Timestamp(anchor.data_date)
        members = [m for m in anchor.styles.index if m in close.columns]
        if prev not in close.index or base not in close.index:
            raise DataError(f"no price panel rows for {day} (previous close {prev.date()})")
        with np.errstate(invalid="ignore", divide="ignore"):
            caps = anchor.caps.reindex(members) * (
                _row(close, prev, members) / _row(close, base, members)
            )
        raw = anchor.styles.reindex(members).copy()
        raw["size"] = _log_cap(caps)
        for style, feature in TECHNICAL_FEATURES.items():
            raw[style] = _row(tech[feature], prev, members)
        out.append(
            CrossSection(
                day=day,
                returns=_row(returns, pd.Timestamp(day), members),
                raw_styles=raw[list(STYLE_NAMES)],
                sectors=anchor.sectors.reindex(members),
                caps=caps,
            )
        )
    return out


def build_factor_history(
    lake: Lake,
    start: date,
    end: date,
    config: AtlasConfig | None = None,
    *,
    symbols: Sequence[str] | None = None,
    factor_store: FactorStore | None = None,
) -> FactorHistory:
    """`fit_factor_history` over `load_cross_sections` (`factor_store` as there); days with
    too few usable names are skipped and listed in `FactorHistory.skipped`."""
    cfg = config if config is not None else AtlasConfig()
    fm = cfg.quant.risk_model.factor_model
    return fit_factor_history(
        load_cross_sections(lake, start, end, cfg, symbols=symbols, factor_store=factor_store),
        FactorSet.from_config(fm),
        fm,
        on_short="skip",
    )


def etf_exposures_from_view(
    view: DataView, history: FactorHistory, etfs: Sequence[str] | None = None
) -> EtfExposures:
    """ETF exposures for the decision at `view`: the last `etf.window` daily returns of
    `etfs` (default: the members of `etf.universe`) read through `view`, regressed on the
    factor returns of `history` up to the view's latest known session."""
    cfg = view.config
    ec = cfg.quant.risk_model.factor_model.etf
    names = list(etfs) if etfs is not None else view.universe(ec.universe)
    if not names:
        raise DataError(f"no ETFs to estimate (universe {ec.universe!r} is empty at {view.day})")
    data_date = view.sessions(1)[-1]
    hist = history.up_to(data_date)
    if hist.factor_returns.empty:
        raise DataError(f"no factor returns on or before {data_date}")
    returns = view.returns(names, ec.window)
    return etf_exposures(returns, hist.factor_returns, cfg)


def build_and_store(
    lake: Lake,
    store: RiskModelStore,
    start: date,
    end: date,
    config: AtlasConfig | None = None,
    *,
    symbols: Sequence[str] | None = None,
    etfs: Sequence[str] | None = None,
    factor_store: FactorStore | None = None,
) -> tuple[FactorHistory, tuple[date, ...]]:
    """Fit the daily factor returns over [`start`, `end`] from `lake`, store them in `store`,
    and store a model snapshot on each month-end (data date) that has at least
    `quant.risk_model.min_history` sessions of factor returns. With `etfs`, each snapshot also
    carries their time-series exposures; a month-end where none of them has enough history
    yet (`etf.min_obs`) is stored without ETFs and logged (`risk_model.etfs_skipped`). Returns
    the history and the snapshot dates. `factor_store`: the regression's monthly anchors are
    read from that factor cache where it has them (`load_cross_sections`); the snapshots are
    always computed, because a snapshot is taken the morning after the month-end and no panel
    is stored for that day."""
    cfg = config if config is not None else AtlasConfig()
    cal = TradingCalendar(config=cfg.calendar)
    history = build_factor_history(
        lake, start, end, cfg, symbols=symbols, factor_store=factor_store
    )
    store.write_factor_returns(history)
    index = history.factor_returns.index
    written: list[date] = []
    view: DataView | None = None
    for day in monthly_rebalance_dates(start, end, cal):
        stamp = pd.Timestamp(day)
        if stamp not in index or int((index <= stamp).sum()) < cfg.quant.risk_model.min_history:
            continue
        decision = cal.next_session(day)
        # one view advanced month by month: the same data as a fresh view per month, while the
        # per-symbol fundamentals cache is shared (a fresh view re-resolves every symbol)
        view = DataView(lake, decision, cfg) if view is None else view.advance(decision)
        upto = history.up_to(day)
        model = build_model_from_view(view, upto, symbols)
        if etfs:
            try:
                model = extend_model(model, etf_exposures_from_view(view, upto, etfs))
            except DataError as exc:
                # early month-ends: enough history for a model, not yet for ETF betas
                # (`etf.min_obs`). The snapshot is stored without ETFs (outside the model).
                _log.warning("risk_model.etfs_skipped", date=day.isoformat(), reason=str(exc))
        store.write_snapshot(model)
        written.append(day)
        _log.info("risk_model.snapshot_stored", date=day.isoformat(), names=len(model.symbols))
    _log.info("risk_model.built_and_stored", sessions=len(index), snapshots=len(written))
    return history, tuple(written)
