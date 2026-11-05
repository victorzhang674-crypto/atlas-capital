"""Exposure metrics: gross, net, long/short, beta-adjusted net, sector nets (Stage 20).

Units: weights are fractions of NAV by symbol (0.05 = 5%, shorts negative); every exposure is
a fraction of NAV. `gross = sum |w|`, `net = sum w`, `long = sum w+` (>= 0), `short = sum w-`
(<= 0, signed so that `net = long + short` and `gross = long - short`); `beta_adjusted_net =
sum w beta` (betas unitless, to the fund benchmark); `sector_net[s] = sum of w over the names
of sector s`. Names without a sector are grouped under `UNCLASSIFIED` (ETFs have no GICS
sector) and listed.

Fail safe: when betas are given, a nonzero weight without a finite beta raises `DataError` (no
guessed beta in a beta-adjusted number).
"""

from collections.abc import Hashable, Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from atlas.core.errors import DataError
from atlas.quant.metrics._common import as_weights

UNCLASSIFIED = "Unclassified"
"""Group of the names without a sector (or group) label."""


@dataclass(frozen=True)
class ExposureSummary:
    """Exposures of a book as fractions of NAV (module docstring). `beta_adjusted_net` is
    `None` without betas, `sector_net` `None` without sectors; `unclassified` lists the held
    names without a sector."""

    gross: float
    net: float
    long: float
    short: float
    n_long: int
    n_short: int
    beta_adjusted_net: float | None
    sector_net: pd.Series | None
    unclassified: tuple[Hashable, ...]


def _labels(weights: pd.Series, groups: pd.Series | Mapping[Hashable, str]) -> pd.Series:
    series = groups if isinstance(groups, pd.Series) else pd.Series(dict(groups), dtype=object)
    if series.index.has_duplicates:
        raise ValueError("groups has duplicate symbols")
    labels = series.reindex(weights.index)
    labels = labels.where(labels.notna() & (labels.astype(str).str.len() > 0), UNCLASSIFIED)
    return labels.astype(str)


def group_net(weights: pd.Series, groups: pd.Series | Mapping[Hashable, str]) -> pd.Series:
    """Net weight of each group (fraction of NAV), groups sorted by name; names without a
    group go to `UNCLASSIFIED`."""
    w = as_weights(weights)
    labels = _labels(w, groups)
    return w.groupby(labels).sum().sort_index().rename("net")


def group_gross(weights: pd.Series, groups: pd.Series | Mapping[Hashable, str]) -> pd.Series:
    """Gross weight `sum |w|` of each group (fraction of NAV)."""
    w = as_weights(weights)
    labels = _labels(w, groups)
    return w.abs().groupby(labels).sum().sort_index().rename("gross")


def beta_adjusted_net(weights: pd.Series, betas: pd.Series) -> float:
    """`sum w beta` (fraction of NAV); a nonzero weight without a finite beta → `DataError`."""
    w = as_weights(weights)
    if not isinstance(betas, pd.Series):
        raise ValueError("betas must be a Series by symbol")
    if betas.index.has_duplicates:
        raise ValueError("betas has duplicate symbols")
    b = betas.reindex(w.index).astype("float64").to_numpy()
    held = w.to_numpy() != 0.0
    bad = held & ~np.isfinite(b)
    if bad.any():
        raise DataError(f"no beta for held names: {[str(s) for s in w.index[bad]][:10]}")
    return float(np.sum(w.to_numpy()[held] * b[held]))


def exposure_summary(
    weights: pd.Series,
    *,
    betas: pd.Series | None = None,
    sectors: pd.Series | Mapping[Hashable, str] | None = None,
) -> ExposureSummary:
    """Gross, net, long, short, beta-adjusted net and sector nets as fractions of NAV, and the
    counts of long and short names (module docstring)."""
    w = as_weights(weights)
    values = w.to_numpy()
    long_ = float(values[values > 0].sum())
    short = float(values[values < 0].sum())
    sector_net = None
    unclassified: tuple[Hashable, ...] = ()
    if sectors is not None:
        sector_net = group_net(w, sectors)
        labels = _labels(w, sectors)
        unclassified = tuple(w.index[(labels == UNCLASSIFIED).to_numpy() & (values != 0.0)])
    return ExposureSummary(
        gross=float(np.abs(values).sum()),
        net=float(values.sum()),
        long=long_,
        short=short + 0.0,
        n_long=int((values > 0).sum()),
        n_short=int((values < 0).sum()),
        beta_adjusted_net=None if betas is None else beta_adjusted_net(w, betas),
        sector_net=sector_net,
        unclassified=unclassified,
    )
