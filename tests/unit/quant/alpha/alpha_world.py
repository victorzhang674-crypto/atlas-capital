"""Test worlds for the Stage 22 alpha models.

- `planted_components`: pure frames, components with a planted composite and returns whose
  cross-sectional IC with that composite is `rho` (no lake).
- `build_equity_world`: the Stage 16 factor world (12 stocks in 3 sectors, 600 sessions of
  bars to 2024-02-29, three years of filings) plus benchmark bars (`SPY`), read at
  `factor_world.AS_OF` (2024-03-01).
"""

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from factor_world import AS_OF, FactorWorld, build_factor_world, world_bars

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.storage import Lake

CAL = TradingCalendar()
SECTOR_NAMES = ("Energy", "Financials", "Health Care", "Industrials")


@dataclass(frozen=True)
class Planted:
    """`components` (symbols x components, sector-neutral z-scores), `sectors`, the planted
    `weights`, the true composite `z` (sector-neutral, z-scored) and `sigma` (annual)."""

    components: pd.DataFrame
    sectors: pd.Series
    weights: dict[str, float]
    z: pd.Series
    sigma: pd.Series


def _neutral_z(x: np.ndarray, labels: np.ndarray) -> np.ndarray:
    s = pd.Series(x)
    out = (s - s.groupby(labels).transform("mean")).to_numpy()
    return (out - out.mean()) / out.std()


def planted_components(
    n_names: int = 400, *, seed: int = 22, weights: dict[str, float] | None = None
) -> Planted:
    """Five sector-neutral components (correlated a little) and their weighted composite."""
    rng = np.random.default_rng(seed)
    w = weights or {"momentum": 0.3, "value": 0.2, "quality": 0.2, "low_vol": 0.2, "reversal": 0.1}
    symbols = [f"S{i:03d}" for i in range(n_names)]
    labels = np.array([SECTOR_NAMES[i % len(SECTOR_NAMES)] for i in range(n_names)], dtype=object)
    common = rng.standard_normal(n_names)
    comps = {k: _neutral_z(0.3 * common + rng.standard_normal(n_names), labels) for k in w}
    frame = pd.DataFrame(comps, index=pd.Index(symbols, name="symbol"))
    raw = sum(w[k] * frame[k] for k in w) / sum(abs(v) for v in w.values())
    z = pd.Series(_neutral_z(raw.to_numpy(), labels), index=frame.index, name="z")
    sigma = pd.Series(rng.uniform(0.15, 0.45, n_names), index=frame.index, name="resid_vol")
    return Planted(
        components=frame,
        sectors=pd.Series(labels, index=frame.index, dtype="object"),
        weights=dict(w),
        z=z,
        sigma=sigma,
    )


def planted_returns(
    p: Planted, *, rho: float, horizon: int, periods: int, rng: np.random.Generator
) -> pd.Series:
    """`horizon`-session residual returns with IC `rho` against `p.z` (in expectation):
    r = rho * s_h * z + sqrt(1 - rho^2) * s_h * e, s_h = sigma * sqrt(h / P)."""
    s_h = p.sigma * np.sqrt(horizon / periods)
    e = rng.standard_normal(len(p.z))
    return rho * s_h * p.z + np.sqrt(1 - rho**2) * s_h * e


EQUITY_AS_OF: date = AS_OF


def equity_config(**multifactor: object) -> AtlasConfig:
    """`AtlasConfig` for the 12-name world: `min_names` 5 for factors and the alpha."""
    mf = {"min_names": 5, **multifactor}
    return AtlasConfig.model_validate(
        {"quant": {"factors": {"min_names": 5}, "alpha": {"multifactor": mf}}}
    )


def build_equity_world(lake: Lake) -> FactorWorld:
    """The factor world plus 600 sessions of `SPY` bars (the benchmark) to 2024-02-29."""
    world = build_factor_world(lake)
    lake.write_table(
        "prices_daily", world_bars(["SPY"], date(2024, 2, 29), 600, seed=99), mode="upsert"
    )
    return world
