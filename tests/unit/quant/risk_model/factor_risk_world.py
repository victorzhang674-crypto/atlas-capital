"""Synthetic world for the Stage 19 factor-risk-model tests: known exposures, factor returns
and specific returns, all seeded.

`make_world` draws `n` names in the 11 GICS sectors with static caps and style exposures
standardised the Barra way (cap-weighted mean 0, equal-weighted std 1); factor returns for `t`
sessions from a known covariance and projected onto the cap-weighted sector constraint
(`sum_s share_s f_s = 0`, the extra return moved to `market`, which leaves every name's return
unchanged); heterogeneous specific returns; and `returns = factor_returns @ exposures.T +
specific_returns`.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar

SECTORS: tuple[str, ...] = (
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
)
STYLES: tuple[str, ...] = (
    "momentum",
    "value",
    "quality",
    "size",
    "beta",
    "growth",
    "leverage",
    "liquidity",
)


@dataclass(frozen=True)
class FactorWorld:
    """`exposures` N x K (market, sectors, styles), `factor_returns` T x K and
    `specific_returns`, `returns` T x N, `caps` (USD) and `sectors` per name,
    `specific_vol` the true daily vol per name, `true_factor_cov` the covariance the factor
    returns were drawn from (after the constraint projection)."""

    symbols: list[str]
    dates: pd.DatetimeIndex
    sectors: pd.Series
    caps: pd.Series
    exposures: pd.DataFrame
    factor_returns: pd.DataFrame
    specific_returns: pd.DataFrame
    specific_vol: pd.Series
    returns: pd.DataFrame
    true_factor_cov: pd.DataFrame

    @property
    def factor_names(self) -> list[str]:
        return list(self.exposures.columns)

    @property
    def styles(self) -> pd.DataFrame:
        return self.exposures[list(STYLES)]


def _standardise(x: np.ndarray, weights: np.ndarray) -> np.ndarray:
    centred = x - float(weights @ x)
    return centred / centred.std()


def make_world(
    n: int = 600,
    t: int = 800,
    seed: int = 0,
    *,
    market_vol: float = 0.010,
    sector_vol: float = 0.0125,
    style_vol: float = 0.006,
    spec_range: tuple[float, float] = (0.012, 0.025),
    start: str = "2015-01-02",
    factor_only: bool = False,
    business_days: bool = False,
) -> FactorWorld:
    rng = np.random.default_rng(seed)
    symbols = [f"S{i:04d}" for i in range(n)]
    if business_days:  # plain weekdays: for very long samples beyond the trading calendar
        dates = pd.DatetimeIndex(pd.bdate_range(start, periods=t), name="date")
    else:
        first = date.fromisoformat(start)
        sessions = TradingCalendar().sessions(first, first + timedelta(days=int(t * 1.6) + 30))[:t]
        dates = pd.DatetimeIndex(pd.to_datetime(list(sessions)), name="date")
    k_sec, k_sty = len(SECTORS), len(STYLES)
    # sectors: every sector gets at least n // (2 * k_sec) names
    base = np.repeat(np.arange(k_sec), max(n // (2 * k_sec), 1))
    rest = rng.choice(k_sec, size=n - len(base), p=rng.dirichlet(np.full(k_sec, 8.0)))
    labels = rng.permutation(np.concatenate([base, rest]))[:n]
    sectors = pd.Series([SECTORS[i] for i in labels], index=symbols, name="sector")
    caps = pd.Series(np.exp(rng.normal(22.5, 1.0, n)), index=symbols, name="cap")
    weights = caps.to_numpy() / caps.sum()
    # styles
    size = _standardise(np.log(caps.to_numpy()), weights)
    raw = rng.normal(size=(n, k_sty))
    raw[:, STYLES.index("size")] = size
    for name, rho in (("liquidity", -0.4), ("beta", 0.15), ("quality", 0.2)):
        j = STYLES.index(name)
        raw[:, j] = rho * size + np.sqrt(1 - rho**2) * raw[:, j]
    styles = np.column_stack([_standardise(raw[:, j], weights) for j in range(k_sty)])
    design = np.zeros((n, 1 + k_sec + k_sty))
    design[:, 0] = 1.0
    design[np.arange(n), 1 + labels] = 1.0
    design[:, 1 + k_sec :] = styles
    names = ["market", *SECTORS, *STYLES]
    exposures = pd.DataFrame(design, index=pd.Index(symbols, name="symbol"), columns=names)
    # factor covariance and the constraint projection P (f -> P f)
    vols = np.array([market_vol] + [sector_vol] * k_sec + [style_vol] * k_sty)
    loadings = rng.normal(scale=0.3, size=(len(vols), 3))
    corr_raw = loadings @ loadings.T + np.eye(len(vols))
    d = np.sqrt(np.diag(corr_raw))
    cov = (corr_raw / np.outer(d, d)) * np.outer(vols, vols)
    shares = np.array([weights[labels == s].sum() for s in range(k_sec)])
    proj = np.eye(len(vols))
    proj[1 : 1 + k_sec, 1 : 1 + k_sec] -= np.outer(np.ones(k_sec), shares)  # f_s - sum share f
    proj[0, 1 : 1 + k_sec] += shares  # market gets sum share f
    true_cov = proj @ cov @ proj.T
    draws = rng.multivariate_normal(np.zeros(len(vols)), cov, size=t, method="cholesky")
    factors = draws @ proj.T
    factor_returns = pd.DataFrame(factors, index=dates, columns=names)
    # specific
    level = rng.uniform(*spec_range, size=k_sec)
    spec_vol = np.clip(level[labels] * np.exp(rng.normal(0.0, 0.2, n)), 0.005, 0.06)
    spec = np.zeros((t, n)) if factor_only else rng.normal(size=(t, n)) * spec_vol
    returns = factors @ design.T + spec
    return FactorWorld(
        symbols=symbols,
        dates=dates,
        sectors=sectors,
        caps=caps,
        exposures=exposures,
        factor_returns=factor_returns,
        specific_returns=pd.DataFrame(spec, index=dates, columns=symbols),
        specific_vol=pd.Series(spec_vol, index=symbols, name="specific_vol"),
        returns=pd.DataFrame(returns, index=dates, columns=symbols),
        true_factor_cov=pd.DataFrame(true_cov, index=names, columns=names),
    )


def cross_sections(world: FactorWorld, start: int = 0, stop: int | None = None) -> list:
    """The world's sessions `start:stop` as `CrossSection`s (static exposures and caps)."""
    from atlas.quant.risk_model.factor_model import CrossSection

    stop = len(world.dates) if stop is None else stop
    return [
        CrossSection(
            day=world.dates[i].date(),
            returns=world.returns.iloc[i],
            raw_styles=world.styles,
            sectors=world.sectors,
            caps=world.caps,
        )
        for i in range(start, stop)
    ]
