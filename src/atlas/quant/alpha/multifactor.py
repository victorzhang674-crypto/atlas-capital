"""Cross-sectional multi-factor equity alpha (Stage 22).

For the members of `quant.alpha.multifactor.universe` at the view's as-of, each component of
`quant.alpha.multifactor.components` is read through the view:

- `feature` components: the latest value of a Stage 15 technical feature (`compute_features`;
  bars through the previous session), e.g. `momentum_12_1`, `vol_63d`, `ret_21d`;
- `style` components: a Stage 16 style composite (`style_composites` of the `factor_panel`
  exposures, with the values Stage 16 filled from sector medians masked out, so a name with no
  fundamentals of a group has no value there rather than a guessed one);

multiplied by the component's `sign`, winsorised at `quant.factors.winsor_lower/upper`,
neutralised by GICS sector (demeaned within sector; a name without a sector against the whole
cross-section) and z-scored (`clean_component`). `combine` takes the weighted composite of the
available components (a missing component's weight is left out), drops names covering less
than `min_coverage` of the absolute weight, and neutralises and z-scores the result: the
score `z`. `MultiFactorModel.predict` turns it into an annualised expected residual return with
the Grinold rule (`calibration.grinold_alpha`) and a confidence (coverage x reliability).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AlphaMultiFactorConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.pit import guard_not_label
from atlas.data.view import DataView
from atlas.quant.alpha.base import alpha_frame, check_horizon, empty_alpha_frame
from atlas.quant.alpha.calibration import grinold_alpha, ic_weights, trailing_ic
from atlas.quant.factors import (
    FactorPanel,
    composite,
    factor_panel,
    neutralize,
    style_composites,
    winsorize,
    zscore,
)
from atlas.quant.features import REGISTRY, compute_features

_log = get_logger(__name__)

COMPOSITE = "composite"
"""Column of an IC history holding the composite's own IC (used for the Grinold IC)."""


def _known_sector(sectors: pd.Series) -> np.ndarray:
    return sectors.notna().to_numpy(dtype=bool)


def clean_component(
    raw: pd.Series, sectors: pd.Series, *, lower: float, upper: float, min_names: int
) -> pd.Series:
    """`raw` (index symbol) winsorised at its `lower`/`upper` quantiles, demeaned within
    sector (`sectors`: symbol -> GICS sector, `None` unknown; Stage 16 `neutralize`) and
    z-scored over the names with a known sector (population std); all NaN when fewer than
    `min_names` values are valid."""
    x = raw.astype("float64").replace([np.inf, -np.inf], np.nan)
    if int(x.notna().sum()) < min_names:
        return pd.Series(np.nan, index=raw.index, dtype="float64", name=raw.name)
    frame = winsorize(x.to_frame("x"), lower, upper)
    neutral = neutralize(frame, sectors.reindex(raw.index))
    out: pd.Series = zscore(neutral, basis=_known_sector(sectors.reindex(raw.index)))["x"]
    out.name = raw.name
    return out


@dataclass(frozen=True, eq=False)
class ComponentPanel:
    """The cleaned components at one as-of: `values` (index symbol = the members scored, one
    column per component; z-scores, NaN = no value), `sectors` (symbol -> GICS sector or
    `None`) and `resid_vol` (annualised residual vol, fraction; NaN when unknown)."""

    values: pd.DataFrame
    sectors: pd.Series
    resid_vol: pd.Series


@dataclass(frozen=True, eq=False)
class CombineResult:
    """`z` (the sector-neutral, z-scored composite; NaN for names without a score) and
    `coverage` (share of the absolute weight with a component value, in [0, 1])."""

    z: pd.Series
    coverage: pd.Series


def member_sectors(view: DataView, symbols: Sequence[str]) -> pd.Series:
    """Each name's GICS sector from the instruments table (`None` when it has no row, logged);
    `DataError` when none of `symbols` has a row (sector neutrality would silently be lost)."""
    out: dict[str, str | None] = {}
    missing = 0
    for symbol in symbols:
        try:
            out[symbol] = view.instrument(symbol).sector
        except DataError as exc:
            _log.warning("alpha.no_instrument", symbol=symbol, error=str(exc))
            out[symbol] = None
            missing += 1
    if symbols and missing == len(symbols):
        raise DataError(
            f"none of the {len(symbols)} names has a row in the instruments table: sync the "
            "reference data before scoring a cross-section"
        )
    return pd.Series(out, dtype="object", index=pd.Index(list(symbols), name="symbol"))


def style_scores(panel: FactorPanel) -> pd.DataFrame:
    """The Stage 16 style composites (one column per group) of `panel`'s exposures with the
    values Stage 16 filled from sector medians masked out (`panel.filled`): a filled value is
    not a measurement, so a name whose factors of a group were all filled has no value for
    that style (NaN) and a partly filled one is scored on its measured factors."""
    return style_composites(panel.exposures.mask(panel.filled))


def _check_sources(cfg: AlphaMultiFactorConfig) -> None:
    features = [c.source for c in cfg.components.values() if c.kind == "feature"]
    unknown = [name for name in features if name not in REGISTRY]
    if cfg.resid_vol_feature not in REGISTRY:
        unknown.append(cfg.resid_vol_feature)
    if unknown:
        raise ValueError(f"unknown Stage 15 feature(s) {unknown} in quant.alpha.multifactor")


def component_frame(
    view: DataView,
    symbols: Sequence[str] | None = None,
    *,
    config: AlphaMultiFactorConfig | None = None,
) -> ComponentPanel:
    """The cleaned components (module docstring) of `symbols` (default: the members of
    `config.universe` at the view's as-of; blocked names are left out by the view) at
    `view.as_of`; `config` defaults to the view's `quant.alpha.multifactor`. Unknown feature
    names raise `ValueError` before any data is read."""
    cfg = config if config is not None else view.config.quant.alpha.multifactor
    _check_sources(cfg)
    members = view.universe(cfg.universe) if symbols is None else list(symbols)
    index = pd.Index(members, name="symbol", dtype="object")
    if not members:
        empty = pd.Series(dtype="float64", index=index)
        return ComponentPanel(
            values=pd.DataFrame({k: empty for k in cfg.components}, index=index),
            sectors=pd.Series(dtype="object", index=index),
            resid_vol=empty.rename("resid_vol"),
        )
    sectors = member_sectors(view, members)
    names = list(
        dict.fromkeys(
            [c.source for c in cfg.components.values() if c.kind == "feature"]
            + [cfg.resid_vol_feature]
        )
    )
    feats = compute_features(view, members, names, history=1)
    latest = {n: feats[n].iloc[-1].reindex(members) for n in names}
    styles: pd.DataFrame | None = None
    if any(c.kind == "style" for c in cfg.components.values()):
        styles = style_scores(factor_panel(view, members)).reindex(members)
    fc = view.config.quant.factors
    values: dict[str, pd.Series] = {}
    for name, comp in cfg.components.items():
        if comp.kind == "feature":
            raw = latest[comp.source]
        else:
            assert styles is not None
            if comp.source not in styles.columns:
                raise ValueError(
                    f"unknown style {comp.source!r}; known: {', '.join(styles.columns)}"
                )
            raw = styles[comp.source]
        values[name] = clean_component(
            comp.sign * raw.rename(name),
            sectors,
            lower=fc.winsor_lower,
            upper=fc.winsor_upper,
            min_names=cfg.min_names,
        )
    resid = latest[cfg.resid_vol_feature].rename("resid_vol")
    return ComponentPanel(
        values=pd.DataFrame(values, index=index), sectors=sectors, resid_vol=resid
    )


def combine(
    components: pd.DataFrame,
    sectors: pd.Series,
    weights: Mapping[str, float],
    *,
    min_coverage: float,
    min_names: int,
) -> CombineResult:
    """The composite score of `components` (index symbol, cleaned z-scores) with `weights`
    (component -> weight; a weight for a column not present counts as missing): Stage 16
    `composite` without standardising (each name over its available components), names with
    coverage below `min_coverage` dropped, then neutralised by sector and z-scored over the
    names with a known sector. All NaN when fewer than `min_names` names keep a value. A
    forward-return label raises `LookAheadError`."""
    guard_not_label(components, "component frame")
    total = math.fsum(abs(w) for w in weights.values())
    if total <= 0:
        raise ValueError("weights must not all be zero")
    present = {k: float(w) for k, w in weights.items() if k in components.columns and w != 0}
    index = components.index
    if not present:
        nan = pd.Series(np.nan, index=index, dtype="float64")
        return CombineResult(z=nan.rename("z"), coverage=pd.Series(0.0, index=index))
    frame = components[list(present)].astype("float64")
    known = frame.notna().to_numpy()
    coverage = pd.Series(
        known.astype("float64") @ np.abs(np.array(list(present.values()))) / total,
        index=index,
        name="coverage",
    ).clip(0.0, 1.0)
    raw = composite(frame, present, standardize=False).where(coverage >= min_coverage - 1e-12)
    if int(raw.notna().sum()) < min_names:
        return CombineResult(z=pd.Series(np.nan, index=index, name="z"), coverage=coverage)
    sec = sectors.reindex(index)
    neutral = neutralize(raw.to_frame("z"), sec)
    z = zscore(neutral, basis=_known_sector(sec))["z"]
    return CombineResult(z=z.rename("z"), coverage=coverage)


@dataclass(frozen=True, eq=False)
class MultiFactorModel:
    """The multi-factor `AlphaModel` (module docstring).

    `config` overrides the view's `quant.alpha.multifactor`. `ic_history` (optional; index
    decision dates, columns component names and/or `composite`; e.g. `ic_history(...)` at the
    model's horizon) feeds IC weighting (`weighting: ic`) and the Grinold IC, using only the
    ICs known at the view's day within `ic_window_months`. `resid_vol` (optional; symbol ->
    annualised residual vol, e.g. `specific_vol(RiskModel)`) replaces the
    `resid_vol_feature`. Output: `score` = the composite z-score; `exp_return_ann` = annualised
    expected residual return (fraction); `confidence` = coverage x reliability.
    """

    config: AlphaMultiFactorConfig | None = None
    ic_history: pd.DataFrame | None = None
    resid_vol: pd.Series | None = None

    @property
    def name(self) -> str:
        """`multifactor`."""
        return "multifactor"

    @property
    def horizon(self) -> int:
        """The forecast horizon in sessions (`config.horizon`; default 21). `predict`
        raises `ConfigError` when the view's config says otherwise (`check_horizon`)."""
        return (self.config or AlphaMultiFactorConfig()).horizon

    def _trailing(self, view: DataView, cfg: AlphaMultiFactorConfig) -> pd.DataFrame | None:
        if self.ic_history is None:
            return None
        guard_not_label(self.ic_history, "IC history")
        return trailing_ic(
            self.ic_history,
            view.day,
            cfg.horizon,
            window_months=cfg.ic_window_months,
            min_obs=cfg.ic_min_obs,
            calendar=TradingCalendar(config=view.config.calendar),
        )

    def weights(
        self, view: DataView, config: AlphaMultiFactorConfig | None = None
    ) -> tuple[dict[str, float], Literal["ic", "fixed"]]:
        """The composite weights at the view's day and their source."""
        cfg = config or self.config or view.config.quant.alpha.multifactor
        fixed = {k: c.weight for k, c in cfg.components.items()}
        trailing = self._trailing(view, cfg)
        if cfg.weighting == "fixed":
            return fixed, "fixed"
        if trailing is None:
            _log.info("alpha.ic_weights_fallback", reason="no IC history")
            return fixed, "fixed"
        weights, source = ic_weights(trailing, fixed)
        if source == "fixed":
            _log.info("alpha.ic_weights_fallback", reason="no positive known IC")
        return weights, source

    def predict(self, view: DataView) -> pd.DataFrame:
        """The alpha frame of the universe members at `view.as_of` (module docstring)."""
        cfg = self.config or view.config.quant.alpha.multifactor
        check_horizon(self, cfg.horizon)
        return self.predict_components(view, component_frame(view, config=cfg))

    def predict_components(self, view: DataView, panel: ComponentPanel) -> pd.DataFrame:
        """The alpha frame from an already built `panel` (`component_frame(view)` at the same
        view and config): `predict(view)` is `predict_components(view, component_frame(view))`.
        Lets a research run score several weightings on one panel."""
        cfg = self.config or view.config.quant.alpha.multifactor
        check_horizon(self, cfg.horizon)
        if panel.values.empty:
            return empty_alpha_frame()
        weights, _ = self.weights(view, cfg)
        res = combine(
            panel.values,
            panel.sectors,
            weights,
            min_coverage=cfg.min_coverage,
            min_names=cfg.min_names,
        )
        if res.z.isna().all():
            _log.info("alpha.too_few_names", day=view.day.isoformat(), min_names=cfg.min_names)
            return empty_alpha_frame()
        ic, reliability = cfg.ic_prior, cfg.prior_confidence
        trailing = self._trailing(view, cfg)
        if trailing is not None and COMPOSITE in trailing.index:
            row = trailing.to_dict("index")[COMPOSITE]
            mean = float(row["mean"])
            if not math.isnan(mean):
                ic = max(mean, 0.0)
                t = float(row["t_stat"])
                reliability = 0.0 if math.isnan(t) else min(max(t / cfg.t_full, 0.0), 1.0)
                if ic == 0.0:
                    reliability = 0.0
        sigma = panel.resid_vol if self.resid_vol is None else self.resid_vol
        exp = grinold_alpha(
            res.z,
            ic,
            sigma,
            horizon=cfg.horizon,
            periods=view.config.quant.features.sessions_per_year,
        )
        dropped = int((res.z.notna() & exp.isna()).sum())
        if dropped:
            _log.info("alpha.no_resid_vol", day=view.day.isoformat(), names=dropped)
        return alpha_frame(res.z, exp, res.coverage * reliability)
