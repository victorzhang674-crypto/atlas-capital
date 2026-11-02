"""The technical feature registry and the compute plumbing (Stage 15).

A feature is a function of a `PricePanel` (wide date x symbol frames of one window of bars)
that returns a frame aligned with it. It is registered with `@feature(name, lookback, kind,
unit=...)`: `lookback` is the number of sessions of bars it needs for its first value, `kind`
is `ts` (a symbol's value depends only on its own bars and the benchmark's) or `xs` (it may
depend on the requested cross-section), `unit` says what the numbers mean.

`compute_panel` is pure (no data access). `compute_features(view, symbols, names)` reads one
window of bars through `DataView` (point-in-time), exactly `max(lookback) + buffer + history - 1`
sessions long, and returns the last `history` sessions of every feature.

Units: prices are USD, adjusted for splits and dividends with the actions known at `as_of`
(the view's `adj_close`; open, high and low are scaled by the same factor); dollar volume is
the raw close times the raw volume (USD; split-invariant); returns are simple daily returns.
"""

import re
from collections.abc import Callable, Hashable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property
from typing import TYPE_CHECKING, Literal, get_args, overload

import numpy as np
import pandas as pd

from atlas.core.types.instruments import normalize_symbol

if TYPE_CHECKING:
    from atlas.data.view import DataView

FeatureKind = Literal["ts", "xs"]
"""`ts`: a symbol's value uses only its own bars (and the benchmark's); `xs`: it may use the
whole requested cross-section."""

FeatureUnit = Literal[
    "fraction", "annual_fraction", "usd", "ratio", "index_0_100", "zscore", "return_per_usd_mm"
]
"""What a feature's numbers mean: `fraction` a simple return-like fraction (0.05 = 5%),
`annual_fraction` an annualised fraction (volatilities), `usd` a price level in USD (not
comparable across symbols), `ratio` a dimensionless ratio, `index_0_100` an oscillator on
0-100, `zscore` standard deviations, `return_per_usd_mm` absolute return per USD 1M traded."""

FEATURE_KINDS: tuple[str, ...] = get_args(FeatureKind)
FEATURE_UNITS: tuple[str, ...] = get_args(FeatureUnit)

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, eq=False)
class PricePanel:
    """One window of daily bars as wide frames: index `date` (tz-naive `DatetimeIndex`, one
    row per trading session, oldest first), one column per symbol.

    `open`, `high`, `low`, `close` are adjusted prices (USD); `dollar_volume` is raw close x
    raw volume (USD). A missing bar is a NaN row entry. `benchmark_close` is the benchmark's
    adjusted close on the same index (`None` when no feature needs it). `sessions_per_year`
    annualises volatilities.
    """

    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    dollar_volume: pd.DataFrame
    benchmark_close: pd.Series | None = None
    sessions_per_year: int = 252
    _memo: dict[Hashable, pd.DataFrame] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for name in ("open", "high", "low", "dollar_volume"):
            frame = getattr(self, name)
            if not (frame.index.equals(self.close.index) and frame.columns.equals(self.symbols)):
                raise ValueError(f"PricePanel.{name} is not aligned with close")
        if self.benchmark_close is not None and not self.benchmark_close.index.equals(
            self.close.index
        ):
            raise ValueError("PricePanel.benchmark_close is not aligned with close")

    @property
    def index(self) -> pd.DatetimeIndex:
        """The sessions (rows), oldest first."""
        return pd.DatetimeIndex(self.close.index)

    @property
    def symbols(self) -> pd.Index:
        """The symbols (columns)."""
        return self.close.columns

    @cached_property
    def returns(self) -> pd.DataFrame:
        """Simple daily returns of the adjusted close (NaN on the first row and around a
        missing bar)."""
        return self.close.pct_change(fill_method=None)

    @cached_property
    def benchmark_returns(self) -> pd.Series:
        """Simple daily returns of the benchmark's adjusted close (all NaN without one)."""
        if self.benchmark_close is None:
            return pd.Series(float("nan"), index=self.close.index, dtype="float64")
        return self.benchmark_close.pct_change(fill_method=None)

    def memo(self, key: Hashable, compute: Callable[[], pd.DataFrame]) -> pd.DataFrame:
        """`compute()` once per panel and `key` (intermediates shared by several features)."""
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    @classmethod
    def from_bars(
        cls,
        bars: pd.DataFrame,
        sessions: Sequence[date],
        symbols: Sequence[str],
        *,
        benchmark: str | None = None,
        sessions_per_year: int = 252,
    ) -> "PricePanel":
        """The panel of `bars` (rows of `DataView.prices`: raw OHLC, raw volume, our
        `adj_close`) on the session grid `sessions` with columns `symbols`. Open, high and low
        are scaled by each bar's `adj_close / close`. `benchmark` names the symbol whose
        adjusted close becomes `benchmark_close` (all NaN if it has no bars)."""
        index = pd.DatetimeIndex(pd.to_datetime(list(sessions)), name="date")
        columns = pd.Index(list(symbols))
        frame = bars.assign(
            date=pd.to_datetime(bars["date"]),
            factor=bars["adj_close"] / bars["close"],
            dollar_volume=bars["close"] * bars["volume"].astype("float64"),
        )

        def wide(values: pd.Series, wanted: pd.Index) -> pd.DataFrame:
            table = frame.assign(_v=values).pivot(index="date", columns="symbol", values="_v")
            out = table.reindex(index=index, columns=wanted).astype("float64")
            out.columns.name = None
            return out

        bench = None
        if benchmark is not None:
            bench = wide(frame["adj_close"], pd.Index([benchmark]))[benchmark]
            bench.name = benchmark
        return cls(
            open=wide(frame["open"] * frame["factor"], columns),
            high=wide(frame["high"] * frame["factor"], columns),
            low=wide(frame["low"] * frame["factor"], columns),
            close=wide(frame["adj_close"], columns),
            dollar_volume=wide(frame["dollar_volume"], columns),
            benchmark_close=bench,
            sessions_per_year=sessions_per_year,
        )


FeatureFunc = Callable[[PricePanel], pd.DataFrame]


@dataclass(frozen=True)
class FeatureSpec:
    """A registered feature: `name` (snake_case), `lookback` (sessions of bars needed for the
    first value), `kind` (`ts`/`xs`), `unit` (`FeatureUnit`), `description`, the function and
    whether it needs the benchmark's bars (`benchmark`)."""

    name: str
    lookback: int
    kind: FeatureKind
    unit: FeatureUnit
    description: str
    func: FeatureFunc
    benchmark: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.match(self.name):
            raise ValueError(f"feature name {self.name!r} must be snake_case (a-z, 0-9, _)")
        if isinstance(self.lookback, bool) or not isinstance(self.lookback, int):
            raise ValueError(f"{self.name}: lookback must be an int, got {self.lookback!r}")
        if self.lookback < 1:
            raise ValueError(f"{self.name}: lookback must be at least 1, got {self.lookback}")
        if self.kind not in FEATURE_KINDS:
            raise ValueError(f"{self.name}: kind must be one of {FEATURE_KINDS}, got {self.kind!r}")
        if self.unit not in FEATURE_UNITS:
            raise ValueError(f"{self.name}: unit must be one of {FEATURE_UNITS}, got {self.unit!r}")
        if not self.description.strip():
            raise ValueError(f"{self.name}: description must not be empty")

    @property
    def comparable(self) -> bool:
        """Whether values can be compared across symbols (not a USD price level)."""
        return self.unit != "usd"


class FeatureRegistry:
    """Features by name, in registration order."""

    def __init__(self) -> None:
        self._specs: dict[str, FeatureSpec] = {}

    def register(self, spec: FeatureSpec) -> FeatureSpec:
        """Add `spec`; a name registered twice raises `ValueError`."""
        if spec.name in self._specs:
            raise ValueError(f"feature {spec.name!r} is already registered")
        self._specs[spec.name] = spec
        return spec

    def feature(
        self,
        name: str,
        lookback: int,
        kind: FeatureKind = "ts",
        *,
        unit: FeatureUnit,
        description: str | None = None,
        benchmark: bool = False,
    ) -> Callable[[FeatureFunc], FeatureFunc]:
        """Decorator registering the function as feature `name` (the description defaults to
        the first paragraph of its docstring)."""

        def register(func: FeatureFunc) -> FeatureFunc:
            text = description
            if text is None:
                text = (func.__doc__ or "").strip().split("\n\n")[0]
                text = " ".join(text.split())
            self.register(FeatureSpec(name, lookback, kind, unit, text, func, benchmark))
            return func

        return register

    def get(self, name: str) -> FeatureSpec:
        """The spec of `name` (`ValueError` naming it if unknown)."""
        try:
            return self._specs[name]
        except KeyError:
            raise ValueError(f"unknown feature {name!r}; known: {', '.join(self._specs)}") from None

    def specs(self, names: Sequence[str] | None = None) -> tuple[FeatureSpec, ...]:
        """The specs of `names` in request order without repeats (all, in registration order,
        for `None`). Unknown names raise `ValueError` listing them."""
        if names is None:
            return tuple(self._specs.values())
        if isinstance(names, str):
            raise ValueError("names must be a sequence of names, not a string")
        wanted = list(dict.fromkeys(names))
        if not wanted:
            raise ValueError("no features requested")
        unknown = [n for n in wanted if n not in self._specs]
        if unknown:
            raise ValueError(f"unknown features {unknown}; known: {', '.join(self._specs)}")
        return tuple(self._specs[n] for n in wanted)

    def names(self) -> tuple[str, ...]:
        """Registered names in registration order."""
        return tuple(self._specs)

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __iter__(self) -> Iterator[FeatureSpec]:
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)


REGISTRY = FeatureRegistry()
"""The default registry; `atlas.quant.features.technical` registers the library into it."""

feature = REGISTRY.feature
"""`@feature(name, lookback, kind="ts", *, unit, description=None, benchmark=False)` on
`REGISTRY`."""


Layout = Literal["wide", "long"]


def compute_panel(
    panel: PricePanel, names: Sequence[str] | None = None, *, registry: FeatureRegistry = REGISTRY
) -> dict[str, pd.DataFrame]:
    """Every feature in `names` (all registered, for `None`) on `panel`, in request order: one
    float64 frame per feature aligned with the panel (sessions x symbols). Infinite values
    (a division by zero) become NaN. Pure: reads no data. A feature that returns a frame not
    aligned with the panel raises `ValueError`."""
    out: dict[str, pd.DataFrame] = {}
    for spec in registry.specs(names):
        result = spec.func(panel)
        if not (
            isinstance(result, pd.DataFrame)
            and result.index.equals(panel.index)
            and result.columns.equals(panel.symbols)
        ):
            raise ValueError(f"feature {spec.name!r} returned a frame not aligned with the panel")
        values = result.astype("float64")
        out[spec.name] = values.mask(np.isinf(values.to_numpy()))
    return out


def normalize_symbols(symbols: Sequence[str]) -> list[str]:
    """`symbols` normalised (`normalize_symbol`), without repeats, in order. A plain string
    raises `ValueError` (pass a sequence of tickers)."""
    if isinstance(symbols, str) or not isinstance(symbols, Sequence):
        raise ValueError("symbols must be a sequence of tickers, not a string")
    return list(dict.fromkeys(normalize_symbol(s) for s in symbols))


@overload
def compute_features(
    view: "DataView",
    symbols: Sequence[str],
    names: Sequence[str] | None = ...,
    *,
    history: int = ...,
    layout: Literal["wide"] = ...,
    registry: FeatureRegistry = ...,
) -> dict[str, pd.DataFrame]: ...


@overload
def compute_features(
    view: "DataView",
    symbols: Sequence[str],
    names: Sequence[str] | None = ...,
    *,
    history: int = ...,
    layout: Literal["long"],
    registry: FeatureRegistry = ...,
) -> pd.DataFrame: ...


def compute_features(
    view: "DataView",
    symbols: Sequence[str],
    names: Sequence[str] | None = None,
    *,
    history: int = 1,
    layout: Layout = "wide",
    registry: FeatureRegistry = REGISTRY,
) -> dict[str, pd.DataFrame] | pd.DataFrame:
    """Features `names` (all registered, for `None`) of `symbols` over the last `history`
    sessions whose bars are known at `view.as_of`.

    Reads exactly `max(lookback) + quant.features.buffer_sessions + history - 1` sessions of
    bars through the view (plus the benchmark `fund.benchmark` when a feature needs it; it is
    not an output column unless requested). Symbols are normalised; blocked symbols are left
    out (DataView rule); a symbol without bars is all NaN.

    `layout="wide"`: `{name: DataFrame}` with index `date` (the sessions) and one column per
    symbol. `layout="long"`: one DataFrame indexed by `(date, symbol)` with one column per
    feature. Units per feature: see its `FeatureSpec.unit`.
    """
    specs = registry.specs(names)
    if isinstance(history, bool) or not isinstance(history, int) or history < 1:
        raise ValueError(f"history must be a positive int, got {history!r}")
    if layout not in ("wide", "long"):
        raise ValueError(f"layout must be 'wide' or 'long', got {layout!r}")
    wanted = normalize_symbols(symbols)
    settings = view.config.quant.features
    n = max(spec.lookback for spec in specs) + settings.buffer_sessions + history - 1
    benchmark = view.config.fund.benchmark if any(spec.benchmark for spec in specs) else None
    request = [*wanted, benchmark] if benchmark and benchmark not in wanted else wanted
    bars = view.prices(request, n)
    blocked = view.blocked
    panel = PricePanel.from_bars(
        bars,
        view.sessions(n),
        [s for s in wanted if s not in blocked],
        benchmark=benchmark,
        sessions_per_year=settings.sessions_per_year,
    )
    values = compute_panel(panel, [spec.name for spec in specs], registry=registry)
    tail = {name: frame.iloc[-history:] for name, frame in values.items()}
    if layout == "wide":
        return tail
    first = next(iter(tail.values()))
    index = pd.MultiIndex.from_product([first.index, first.columns], names=["date", "symbol"])
    return pd.DataFrame(
        {name: frame.to_numpy().ravel() for name, frame in tail.items()}, index=index
    )
