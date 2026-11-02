"""Per-symbol `TechnicalSnapshot` for the agents (Stage 15; the Technical Analyst, Stage 30).

A snapshot holds, for one symbol at one `as_of`, the latest value of every requested feature
with two percentiles: against the symbol's own last `snapshot.history_sessions` values of that
feature (today's included), and against the latest values of a cross-section (the members of
`snapshot.universe` at `as_of` plus the requested symbols). Percentiles are mid-ranks,
`(#below + 0.5 * #equal) / #valid`, as fractions in (0, 1). Features priced in USD (moving
averages, ATR, MACD) are not comparable across symbols, so they get no universe percentile.
A missing value or too few comparison values gives `None`, never a guess (fail safe).
"""

import math
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Annotated, Self

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from atlas.core.pit import AsOf
from atlas.core.types.base import AtlasModel, NonEmptyStr, Real, UnitInterval
from atlas.core.types.instruments import Symbol
from atlas.data.view import DataView
from atlas.quant.features.registry import (
    REGISTRY,
    FeatureRegistry,
    FeatureUnit,
    compute_features,
    normalize_symbols,
)


class FeatureReading(AtlasModel):
    """One feature of one symbol: `value` in the feature's `unit` (`None` if undefined),
    `pct_own` its percentile in the symbol's own recent history and `pct_universe` in the
    cross-section (fractions in [0, 1]; `None` if not computable)."""

    name: NonEmptyStr
    unit: FeatureUnit
    value: Real | None
    pct_own: UnitInterval | None
    pct_universe: UnitInterval | None


class TechnicalSnapshot(AtlasModel):
    """The technical picture of `symbol` at `as_of`: the readings on `session` (the latest
    session whose bar is known), with `history_sessions` sessions of own history and a
    cross-section of `universe_size` symbols behind the percentiles."""

    symbol: Symbol
    as_of: AsOf
    session: date
    history_sessions: Annotated[int, Field(ge=1)]
    universe_size: Annotated[int, Field(ge=1)]
    readings: tuple[FeatureReading, ...]

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        names = [r.name for r in self.readings]
        if len(names) != len(set(names)):
            raise ValueError("readings repeat a feature name")
        return self

    def reading(self, name: str) -> FeatureReading:
        """The reading of feature `name` (`ValueError` if absent)."""
        for reading in self.readings:
            if reading.name == name:
                return reading
        raise ValueError(f"{self.symbol}: no reading {name!r}")

    def value(self, name: str) -> float | None:
        """The latest value of feature `name`, in its unit (`None` if undefined)."""
        return self.reading(name).value


def percentile_rank(sample: Iterable[float], value: float, *, min_count: int = 1) -> float | None:
    """Mid-rank percentile of `value` in `sample` (NaNs ignored): `(#below + 0.5 * #equal) /
    #valid`, a fraction in (0, 1) when `value` is in the sample. `None` if `value` is NaN or
    fewer than `min_count` valid values remain."""
    values = np.asarray(list(sample), dtype="float64")
    values = values[~np.isnan(values)]
    if math.isnan(value) or len(values) < max(min_count, 1):
        return None
    below = float((values < value).sum())
    equal = float((values == value).sum())
    return (below + 0.5 * equal) / len(values)


def _mid_ranks(sample: np.ndarray, value: np.ndarray, min_count: int) -> np.ndarray:
    """Column-wise `percentile_rank` of `value[j]` in `sample[:, j]` (NaN where undefined)."""
    valid = ~np.isnan(sample)
    count = valid.sum(axis=0)
    below = (sample < value).sum(axis=0)
    equal = (sample == value).sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (below + 0.5 * equal) / count
    return np.where((count >= max(min_count, 1)) & ~np.isnan(value), out, np.nan)


def _opt(x: float) -> float | None:
    return None if math.isnan(x) else float(x)


def build_snapshots(
    view: DataView,
    symbols: Sequence[str],
    *,
    names: Sequence[str] | None = None,
    universe: Sequence[str] | None = None,
    registry: FeatureRegistry = REGISTRY,
) -> dict[str, TechnicalSnapshot]:
    """`TechnicalSnapshot`s of `symbols` at `view.as_of`, keyed by normalised symbol in
    request order; blocked symbols are left out (DataView rule).

    Features `names` (all registered, for `None`) are computed once over the cross-section =
    `universe` (default: the members of `quant.features.snapshot.universe` at `as_of`) plus
    `symbols`, for the last `snapshot.history_sessions` sessions. Own-history percentiles need
    `snapshot.min_history` valid values; universe percentiles need `snapshot.min_universe`
    valid values and a feature not in USD.
    """
    settings = view.config.quant.features.snapshot
    wanted = normalize_symbols(symbols)
    members = universe if universe is not None else view.universe(settings.universe)
    cross = list(dict.fromkeys([*wanted, *normalize_symbols(members)]))
    specs = registry.specs(names)
    values = compute_features(
        view,
        cross,
        [spec.name for spec in specs],
        history=settings.history_sessions,
        registry=registry,
    )
    first = next(iter(values.values()))
    present = [s for s in wanted if s in first.columns]
    session = pd.Timestamp(first.index[-1]).date()
    readings: dict[str, list[FeatureReading]] = {s: [] for s in present}
    for spec in specs:
        frame = values[spec.name]
        history = frame[present].to_numpy(dtype="float64")
        latest = history[-1]
        own = _mid_ranks(history, latest, settings.min_history)
        if spec.comparable:
            row = frame.iloc[-1].to_numpy(dtype="float64")
            sample = np.broadcast_to(row[:, None], (len(row), len(present)))
            ranks = _mid_ranks(sample, latest, settings.min_universe)
        else:
            ranks = np.full(len(present), np.nan)
        for j, symbol in enumerate(present):
            readings[symbol].append(
                FeatureReading(
                    name=spec.name,
                    unit=spec.unit,
                    value=_opt(latest[j]),
                    pct_own=_opt(own[j]),
                    pct_universe=_opt(ranks[j]),
                )
            )
    return {
        symbol: TechnicalSnapshot(
            symbol=symbol,
            as_of=view.as_of,
            session=session,
            history_sessions=settings.history_sessions,
            universe_size=len(first.columns),
            readings=tuple(readings[symbol]),
        )
        for symbol in present
    }
