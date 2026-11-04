"""The Barra-style factor risk model (Stage 19): exposures -> factor returns -> factor
covariance + specific risk -> `RiskModel`.

Flow. `prepare_exposures` standardises the raw style values of a cross-section (winsorised,
cap-weighted mean 0, equal-weighted std 1; a missing value is neutral, 0) and builds the design
matrix (market, sector dummies, styles). `fit_factor_history` runs the daily constrained WLS
(`regression.constrained_wls`) over a sequence of `CrossSection`s and collects factor returns
and residuals. `factor_covariance` is the EWMA + Newey-West covariance of the factor returns,
repaired to a well-conditioned positive-definite matrix. `build_risk_model` combines it with
`specific.specific_variance` and the as-of exposures into a `RiskModel`, whose
`cov() = X F X' + D`.

Timing: a `CrossSection` holds the returns of session `day` and the exposure inputs (styles,
sectors, caps) known at the close of the previous session, so a regression never sees
look-ahead data. A model at an as-of uses the factor returns up to that day and the as-of
exposures, i.e. it forecasts the next session.

Units: factor returns, residuals and covariances are daily simple returns / return**2; weights
are fractions of NAV. Zero-mean covariances (as Stage 18).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Literal

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, FactorModelConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.quant.risk_model.psd import condition_number, floor_condition, is_psd, nearest_psd
from atlas.quant.risk_model.regression import constrained_wls, sector_constraint
from atlas.quant.risk_model.specific import specific_variance

_log = get_logger(__name__)

MARKET = "market"
STYLE_NAMES: tuple[str, ...] = (
    "momentum",
    "value",
    "quality",
    "size",
    "beta",
    "growth",
    "leverage",
    "liquidity",
)
"""The style factors, in column order (orientation: raw, never flipped; see the stage plan)."""


@dataclass(frozen=True)
class FactorSet:
    """The factors of the model: `market`, then `sectors`, then `styles`."""

    sectors: tuple[str, ...]
    styles: tuple[str, ...] = STYLE_NAMES

    @property
    def names(self) -> tuple[str, ...]:
        return (MARKET, *self.sectors, *self.styles)

    @classmethod
    def from_config(cls, config: FactorModelConfig) -> "FactorSet":
        return cls(sectors=tuple(config.sectors))


# --- cross-section preparation ----------------------------------------------------------------


def _check_styles(raw: pd.DataFrame, caps: pd.Series, styles: Sequence[str]) -> None:
    missing = [s for s in styles if s not in raw.columns]
    if missing:
        raise ValueError(f"raw style columns missing: {missing}")
    if not raw.index.equals(caps.index):
        raise ValueError("raw styles and caps are not aligned on the same names")
    values = raw[list(styles)].to_numpy(dtype="float64")
    if np.isinf(values).any():
        raise ValueError("style values must be finite or NaN")


def standardize_styles(
    raw: pd.DataFrame, caps: pd.Series, config: FactorModelConfig
) -> tuple[pd.DataFrame, dict[str, str], int]:
    """Barra-style standardisation of the raw style values of the names in `raw`.

    Each style is winsorised at `style_winsor_lower/upper`, shifted to cap-weighted mean 0 and
    scaled to equal-weighted std 1 over the names with a value. A missing value becomes 0
    (neutral). A name missing more than `max_missing_styles` styles is dropped (`exposures`).
    Returns `(styles, dropped, n_filled)`; a style with fewer than 2 values or no spread
    raises `DataError`.
    """
    _check_styles(raw, caps, STYLE_NAMES)
    frame = raw[list(STYLE_NAMES)].astype("float64")
    n_missing = frame.isna().sum(axis=1)
    too_many = n_missing > config.max_missing_styles
    dropped = {str(name): "exposures" for name in frame.index[too_many]}
    frame = frame.loc[~too_many]
    cap = caps.loc[frame.index].to_numpy(dtype="float64")
    out = pd.DataFrame(0.0, index=frame.index, columns=list(STYLE_NAMES))
    for style in STYLE_NAMES:
        x = frame[style].to_numpy()
        ok = ~np.isnan(x)
        if ok.sum() < 2:
            raise DataError(f"style {style!r} has fewer than 2 values in the cross-section")
        lo, hi = np.quantile(x[ok], [config.style_winsor_lower, config.style_winsor_upper])
        clipped = np.clip(x[ok], lo, hi)
        std = float(clipped.std())
        if not std > 0:
            raise DataError(f"style {style!r} has no cross-sectional spread")
        centre = float(cap[ok] @ clipped / cap[ok].sum())
        out.loc[ok, style] = (clipped - centre) / std
    return out, dropped, int(n_missing[~too_many].sum())


@dataclass(frozen=True)
class Exposures:
    """A prepared cross-section: `design` (names x factors), `caps` of the kept names,
    `dropped` (name -> `cap`, `sector` or `exposures`) and `n_filled` neutral style values."""

    design: pd.DataFrame
    caps: pd.Series
    dropped: dict[str, str]
    n_filled: int


def prepare_exposures(
    raw_styles: pd.DataFrame,
    sectors: pd.Series,
    caps: pd.Series,
    factors: FactorSet,
    config: FactorModelConfig,
) -> Exposures:
    """The design matrix of the names in `raw_styles` (index symbol; the `STYLE_NAMES`
    columns): a name with a missing or non-positive `caps` entry (`cap`), a `sectors` entry
    outside `factors.sectors` (`sector`) or too many missing styles (`exposures`) is dropped and
    reported. Standardisation runs over the kept names."""
    names = raw_styles.index
    cap = caps.reindex(names)
    sector = sectors.reindex(names)
    dropped: dict[str, str] = {}
    bad_cap = ~(np.isfinite(cap.to_numpy(dtype="float64")) & (cap.to_numpy(dtype="float64") > 0))
    for name in names[bad_cap]:
        dropped[str(name)] = "cap"
    bad_sector = ~sector.isin(factors.sectors).to_numpy() & ~bad_cap
    for name in names[bad_sector]:
        dropped[str(name)] = "sector"
    keep = names[~(bad_cap | bad_sector)]
    styles, drop_exp, filled = standardize_styles(raw_styles.loc[keep], cap.loc[keep], config)
    dropped.update(drop_exp)
    design = pd.DataFrame(0.0, index=styles.index, columns=list(factors.names))
    design[MARKET] = 1.0
    for name in factors.sectors:
        design[name] = (sector.loc[styles.index] == name).astype("float64")
    design[list(factors.styles)] = styles[list(factors.styles)]
    design.index.name = "symbol"
    return Exposures(design=design, caps=cap.loc[styles.index], dropped=dropped, n_filled=filled)


# --- factor history ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrossSection:
    """One session for the regression: `returns` of session `day` (simple, NaN = none) and,
    known at the previous close, `raw_styles` (the `STYLE_NAMES` columns), `sectors` (GICS
    name) and `caps` (USD), all indexed by symbol."""

    day: date
    returns: pd.Series
    raw_styles: pd.DataFrame
    sectors: pd.Series
    caps: pd.Series


@dataclass(frozen=True)
class FactorHistory:
    """Daily regression output. `factor_returns` (sessions x factors; NaN for a factor absent
    that day), `residuals` (sessions x names, NaN where the name was not in that day's
    regression), `r2` and `n_names` per session, `skipped` days with too few names."""

    factors: FactorSet
    factor_returns: pd.DataFrame
    residuals: pd.DataFrame
    r2: pd.Series
    n_names: pd.Series
    skipped: tuple[date, ...] = ()

    def up_to(self, day: date) -> "FactorHistory":
        """The sessions on or before `day`."""
        keep = self.factor_returns.index <= pd.Timestamp(day)
        return FactorHistory(
            factors=self.factors,
            factor_returns=self.factor_returns.loc[keep],
            residuals=self.residuals.loc[keep],
            r2=self.r2.loc[keep],
            n_names=self.n_names.loc[keep],
            skipped=tuple(d for d in self.skipped if pd.Timestamp(d) <= pd.Timestamp(day)),
        )


def fit_factor_history(
    cross_sections: Iterable[CrossSection],
    factors: FactorSet,
    config: FactorModelConfig,
    *,
    on_short: Literal["raise", "skip"] = "raise",
) -> FactorHistory:
    """Run the daily constrained WLS (weights `sqrt(cap)`, `sum_s cap_share_s f_s = 0`) over
    `cross_sections` (days strictly ascending). A day that cannot be fitted (fewer than
    `min_cross_section` usable names, a degenerate style, a rank-deficient design) raises
    `DataError` (`on_short="raise"`) or is skipped, logged and listed in `skipped` (`"skip"`).
    Standardisation runs over the names with a return that day (the as-of model standardises
    over every name with exposures)."""
    rows: dict[pd.Timestamp, pd.Series] = {}
    resid: dict[pd.Timestamp, pd.Series] = {}
    r2: dict[pd.Timestamp, float] = {}
    count: dict[pd.Timestamp, int] = {}
    skipped: list[date] = []
    last: date | None = None
    for cs in cross_sections:
        if last is not None and not cs.day > last:
            raise ValueError(
                f"cross-sections must be in ascending day order ({cs.day} after {last})"
            )
        last = cs.day
        returns = cs.returns[np.isfinite(cs.returns.to_numpy(dtype="float64"))]
        names = returns.index.intersection(cs.raw_styles.index)
        try:
            prep = prepare_exposures(cs.raw_styles.loc[names], cs.sectors, cs.caps, factors, config)
            if len(prep.design) < config.min_cross_section:
                raise DataError(
                    f"{cs.day}: {len(prep.design)} usable names < min_cross_section "
                    f"({config.min_cross_section}) in the cross-section"
                )
            constraint = sector_constraint(prep.design, prep.caps, factors.sectors)
            res = constrained_wls(
                returns.loc[prep.design.index], prep.design, np.sqrt(prep.caps), constraint
            )
        except DataError as exc:
            if on_short == "skip":
                _log.warning(
                    "risk_model.cross_section_skipped", day=cs.day.isoformat(), error=str(exc)
                )
                skipped.append(cs.day)
                continue
            raise
        stamp = pd.Timestamp(cs.day)
        rows[stamp], resid[stamp] = res.factor_returns, res.residuals
        r2[stamp], count[stamp] = res.r2, res.n_names
    if not rows:
        raise DataError("no cross-section was fitted (none given, or every day was skipped)")
    index = pd.DatetimeIndex(list(rows), name="date")
    frame = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=list(factors.names))
    residuals = pd.DataFrame.from_dict(resid, orient="index")
    residuals = residuals.reindex(columns=sorted(residuals.columns))
    frame.index = residuals.index = index
    return FactorHistory(
        factors=factors,
        factor_returns=frame.astype("float64"),
        residuals=residuals.astype("float64"),
        r2=pd.Series(list(r2.values()), index=index, name="r2"),
        n_names=pd.Series(list(count.values()), index=index, name="n_names"),
        skipped=tuple(skipped),
    )


# --- factor covariance ------------------------------------------------------------------------


def newey_west_covariance(returns: pd.DataFrame, *, half_life: float, lags: int) -> np.ndarray:
    """The EWMA + Newey-West covariance of `returns` (sessions ascending, NaN = missing),
    zero-mean, before any repair.

    Weights `w_t = 0.5 ** ((T - 1 - t) / half_life)`; `G_l[i, j] = sum_t w_t r[t, i] r[t-l, j]
    / sum_t w_t` over the sessions `t >= l` where both are observed; `F = G_0 + sum_{l=1..L}
    (1 - l / (L + 1)) (G_l + G_l')`. A pair with no common observation raises `DataError`.
    """
    if not half_life > 0:
        raise ValueError(f"half_life must be positive, got {half_life}")
    if lags < 0:
        raise ValueError(f"lags must be non-negative, got {lags}")
    x = returns.to_numpy(dtype="float64")
    if np.isinf(x).any():
        raise ValueError("factor returns must be finite or NaN")
    t = len(x)
    if t <= lags:
        raise DataError(f"{t} sessions cannot support {lags} Newey-West lags")
    seen = ~np.isnan(x)
    z = np.where(seen, x, 0.0)
    w = 0.5 ** ((t - 1 - np.arange(t)) / half_life)

    def gamma(lag: int) -> np.ndarray:
        num = (z[lag:] * w[lag:, None]).T @ z[: t - lag]
        den = (seen[lag:] * w[lag:, None]).T @ seen[: t - lag].astype("float64")
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)

    cov = gamma(0)
    for lag in range(1, lags + 1):
        g = gamma(lag)
        cov = cov + (1.0 - lag / (lags + 1)) * (g + g.T)
    bad = ~np.isfinite(cov)
    if bad.any():
        i = int(np.argwhere(bad)[0][0])
        raise DataError(
            f"factor {returns.columns[i]!r} has no observations in common with another factor"
        )
    return (cov + cov.T) / 2.0


@dataclass(frozen=True)
class FactorCovariance:
    """`cov` (K x K ndarray, daily), its `factors`, the repair steps in `adjustments`
    (`nearest_psd`, `condition_floor`) and `n_days` of factor returns used."""

    cov: np.ndarray
    factors: tuple[str, ...]
    adjustments: tuple[str, ...]
    n_days: int


def factor_covariance(
    factor_returns: pd.DataFrame,
    *,
    half_life: float,
    lags: int,
    min_history: int,
    max_condition: float,
) -> FactorCovariance:
    """`newey_west_covariance` repaired to a symmetric positive-definite matrix with
    condition number at most `max_condition` (Stage 18 helpers; each step recorded).
    A factor with fewer than `min_history` observed returns (so also fewer than `min_history`
    sessions) -> `DataError`."""
    if factor_returns.shape[1] == 0:
        raise ValueError("factor_returns has no factor columns")
    n_days = len(factor_returns)
    counts = factor_returns.notna().sum()
    thin = [str(name) for name, n in counts.items() if n < min_history]
    if thin:
        raise DataError(
            f"factor(s) {thin} have fewer than min_history ({min_history}) observed returns"
        )
    cov = newey_west_covariance(factor_returns, half_life=half_life, lags=lags)
    steps: list[str] = []
    if not is_psd(cov):
        positive = bool((np.diag(cov) > 0).all())  # keep the variances when they allow it
        cov = nearest_psd(cov, keep_diagonal=True) if positive else nearest_psd(cov)
        steps.append("nearest_psd")
    if condition_number(cov) > max_condition:
        cov = floor_condition(cov, max_condition)
        steps.append("condition_floor")
    if steps:
        _log.warning("risk_model.factor_cov_adjusted", steps=steps)
    return FactorCovariance(
        cov=cov,
        factors=tuple(map(str, factor_returns.columns)),
        adjustments=tuple(steps),
        n_days=n_days,
    )


# --- the model --------------------------------------------------------------------------------


def _frozen(array: np.ndarray) -> np.ndarray:
    out = np.array(array, dtype="float64", copy=True)
    out.setflags(write=False)
    return out


@dataclass(frozen=True, eq=False)
class RiskModel:
    """A factor risk model at `as_of`: exposures `x` (N x K), factor covariance `f` (K x K)
    and specific variance `d` (N), all daily and read-only; `cov() = x f x' + diag(d)`.

    `exposures`, `factor_cov` and `specific_var` return fresh labelled copies. `dropped` maps
    names left out to the reason; `adjustments` the factor-covariance repairs; `n_days` the
    sessions of factor returns used; `etfs` the names whose exposures are time-series betas.
    Compared by identity.
    """

    as_of: date
    symbols: tuple[str, ...]
    factors: tuple[str, ...]
    x: np.ndarray
    f: np.ndarray
    d: np.ndarray
    dropped: Mapping[str, str] = field(default_factory=dict)
    adjustments: tuple[str, ...] = ()
    n_days: int = 0
    etfs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        x, f, d = _frozen(self.x), _frozen(self.f), _frozen(self.d)
        if x.shape != (len(self.symbols), len(self.factors)) or f.shape != (len(self.factors),) * 2:
            raise ValueError("x, f and the symbol/factor labels have inconsistent shapes")
        if d.shape != (len(self.symbols),):
            raise ValueError("d must have one entry per symbol")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("duplicate symbols")
        if not (np.isfinite(x).all() and np.isfinite(f).all() and np.isfinite(d).all()):
            raise ValueError("x, f and d must be finite")
        if not np.allclose(f, f.T, rtol=1e-12, atol=0.0):
            raise ValueError("the factor covariance f must be symmetric")
        if not (d > 0).all():
            raise ValueError("specific variances must be positive")
        object.__setattr__(self, "x", x)
        object.__setattr__(self, "f", f)
        object.__setattr__(self, "d", d)
        object.__setattr__(self, "dropped", MappingProxyType(dict(self.dropped)))

    @property
    def exposures(self) -> pd.DataFrame:
        return pd.DataFrame(
            self.x.copy(), index=pd.Index(self.symbols, name="symbol"), columns=list(self.factors)
        )

    @property
    def factor_cov(self) -> pd.DataFrame:
        return pd.DataFrame(self.f.copy(), index=list(self.factors), columns=list(self.factors))

    @property
    def specific_var(self) -> pd.Series:
        return pd.Series(
            self.d.copy(), index=pd.Index(self.symbols, name="symbol"), name="specific_var"
        )

    def positions(self, symbols: Sequence[str]) -> np.ndarray:
        """Row positions of `symbols`; `DataError` naming any that the model does not cover."""
        lookup = {s: i for i, s in enumerate(self.symbols)}
        missing = [s for s in symbols if s not in lookup]
        if missing:
            raise DataError(f"not in the risk model (no risk estimate): {missing[:10]}")
        return np.array([lookup[s] for s in symbols], dtype=int)

    def cov(self, symbols: Sequence[str] | None = None) -> pd.DataFrame:
        """`x f x' + diag(d)` for `symbols` (default: all), daily return**2."""
        pos = np.arange(len(self.symbols)) if symbols is None else self.positions(symbols)
        names = [self.symbols[i] for i in pos]
        x = self.x[pos]
        out = x @ self.f @ x.T
        out[np.diag_indices_from(out)] += self.d[pos]
        return pd.DataFrame(out, index=pd.Index(names, name="symbol"), columns=names)

    def vol(self, symbols: Sequence[str] | None = None) -> pd.Series:
        """Daily total vol of each name (`sqrt(x f x' + d)` diagonal)."""
        pos = np.arange(len(self.symbols)) if symbols is None else self.positions(symbols)
        x = self.x[pos]
        var = np.einsum("ik,kl,il->i", x, self.f, x) + self.d[pos]
        return pd.Series(
            np.sqrt(var), index=pd.Index([self.symbols[i] for i in pos], name="symbol"), name="vol"
        )


def build_risk_model(
    history: FactorHistory,
    raw_styles: pd.DataFrame,
    sectors: pd.Series,
    caps: pd.Series,
    as_of: date,
    config: AtlasConfig | None = None,
) -> RiskModel:
    """The risk model at `as_of` from the factor returns and residuals up to that day
    (`history`) and the as-of exposure inputs (`raw_styles`, `sectors`, `caps`, known at
    `as_of`'s close; index symbol). Names without an exposure row or enough residual history
    are left out and listed in `RiskModel.dropped` (`cap`, `sector`, `exposures`, `history`).
    A history ending more than `max_stale_sessions` sessions before `as_of` -> `DataError`.
    """
    cfg = config if config is not None else AtlasConfig()
    rm, fm = cfg.quant.risk_model, cfg.quant.risk_model.factor_model
    hist = history.up_to(as_of)
    if hist.factor_returns.empty:
        raise DataError(f"no factor returns on or before {as_of}")
    last = hist.factor_returns.index[-1].date()
    cal = TradingCalendar(config=cfg.calendar)
    gap = len(cal.sessions(last, as_of)) - 1 if as_of > last else 0
    if gap > fm.max_stale_sessions:
        raise DataError(
            f"factor history is stale: it ends {last}, {gap} sessions before the as-of {as_of} "
            f"(max_stale_sessions {fm.max_stale_sessions})"
        )
    factors = history.factors
    prep = prepare_exposures(raw_styles, sectors, caps, factors, fm)
    fcov = factor_covariance(
        hist.factor_returns,
        half_life=fm.factor_half_life,
        lags=fm.newey_west_lags,
        min_history=rm.min_history,
        max_condition=rm.max_condition,
    )
    resid = hist.residuals.reindex(columns=prep.design.index)
    spec = specific_variance(
        resid,
        sectors,
        half_life=fm.specific_half_life,
        q=fm.shrinkage_q,
        floor_vol=fm.specific_floor_vol,
        min_history=rm.min_history,
    )
    dropped = {**prep.dropped, **spec.dropped}
    kept = sorted(spec.variance.index)
    return RiskModel(
        as_of=as_of,
        symbols=tuple(map(str, kept)),
        factors=factors.names,
        x=prep.design.loc[kept].to_numpy(),
        f=fcov.cov,
        d=spec.variance.loc[kept].to_numpy(),
        dropped=dropped,
        adjustments=fcov.adjustments,
        n_days=fcov.n_days,
    )
