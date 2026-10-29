"""Typed fund configuration: mandate, risk limits, pods, data, LLM and backtest settings.

Units: weights, exposures and limits are fractions of NAV (0.05 = 5%); drawdown thresholds
and short caps are positive magnitudes (0.05 means a -5% drawdown); money is `Decimal` in
the base currency; dates are trading-calendar `date`s.
"""

import hashlib
import json
import math
import os
import re
from collections.abc import Iterator, Mapping
from datetime import date, time
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from dotenv import dotenv_values
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    SerializerFunctionWrapHandler,
    ValidationError,
    WrapSerializer,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.exceptions import SettingsError

from atlas.core.errors import ConfigError

Profile = Literal["dev", "backtest", "paper"]
PROFILES: tuple[Profile, ...] = ("dev", "backtest", "paper")

# Bounded float types. Upper bounds catch a percent typed where a fraction belongs (5 vs 0.05).
_Frac = Annotated[float, Field(gt=0, le=1)]  # (0, 1]
_OpenFrac = Annotated[float, Field(gt=0, lt=1)]  # (0, 1)
_Confidence = Annotated[float, Field(gt=0.5, lt=1)]  # (0.5, 1)
_NonNeg = Annotated[float, Field(ge=0)]


class _Frozen(BaseModel):
    """Immutable model that rejects unknown keys. List fields are tuples for the same reason."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class _FrozenDict[K, V](Mapping[K, V]):
    """Read-only mapping (unlike `MappingProxyType` it pickles and deep-copies)."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[K, V]) -> None:
        self._data = dict(data)

    def __getitem__(self, key: K) -> V:
        return self._data[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._data!r})"

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self._data,))


def _freeze[V](value: Mapping[str, V]) -> _FrozenDict[str, V]:
    return _FrozenDict(value)


def _thaw(value: Mapping[str, Any], handler: SerializerFunctionWrapHandler) -> Any:
    return handler(dict(value))


class FundConfig(_Frozen):
    """The fund mandate. `initial_capital` is in the base currency (spec A6)."""

    name: str = "Atlas Capital"
    base_currency: Literal["USD"] = "USD"
    initial_capital: Decimal = Decimal("10000000")
    benchmark: str = "SPY"


class DrawdownTier(_Frozen):
    """One fund drawdown circuit breaker (spec §9).

    `drawdown` is a positive fraction from the high-water mark (0.05 = -5%); `degross_frac`
    is the fraction of gross exposure to remove; `halt` stops trading until the owner resets.
    """

    drawdown: _OpenFrac
    degross_frac: Annotated[float, Field(ge=0, le=1)]
    block_new_names: bool
    halt: bool


class PodStops(_Frozen):
    """Pod drawdown stops (spec §9), as positive fractions from the pod's high-water mark."""

    halve_budget_at: _OpenFrac = 0.08
    budget_multiplier: _OpenFrac = 0.5
    freeze_at: _OpenFrac = 0.12

    @model_validator(mode="after")
    def _halve_before_freeze(self) -> Self:
        if self.halve_budget_at >= self.freeze_at:
            raise ValueError(
                f"halve_budget_at ({self.halve_budget_at}) must be less than "
                f"freeze_at ({self.freeze_at})"
            )
        return self


def _default_drawdown_tiers() -> tuple[DrawdownTier, ...]:
    return (
        DrawdownTier(drawdown=0.05, degross_frac=0.25, block_new_names=False, halt=False),
        DrawdownTier(drawdown=0.10, degross_frac=0.50, block_new_names=True, halt=False),
        DrawdownTier(drawdown=0.15, degross_frac=1.00, block_new_names=True, halt=True),
    )


class LimitsConfig(_Frozen):
    """Every risk limit in spec §9. Exposures are fractions of NAV; VaR/CVaR/stress are
    1-day losses as fractions of NAV; style exposure is in cross-sectional sigmas."""

    target_vol_annual: _OpenFrac = 0.10
    max_gross: Annotated[float, Field(gt=0, le=5)] = 2.00
    net_min: Annotated[float, Field(ge=-1, le=1)] = -0.10
    net_max: Annotated[float, Field(ge=-1, le=1)] = 0.40
    beta_min: Annotated[float, Field(ge=-2, le=2)] = -0.10
    beta_max: Annotated[float, Field(ge=-2, le=2)] = 0.30
    max_single_long: _Frac = 0.05
    max_single_short: _Frac = 0.03
    max_sector_net: _Frac = 0.15
    max_style_exposure_sigma: Annotated[float, Field(gt=0, le=5)] = 0.5
    max_order_adv_frac: _Frac = 0.05
    adv_window_days: Annotated[int, Field(ge=1)] = 20
    max_liquidation_days: Annotated[float, Field(gt=0)] = 3.0
    liquidation_adv_frac: _Frac = 0.10
    var_confidence: _Confidence = 0.95
    max_var: _OpenFrac = 0.015
    cvar_confidence: _Confidence = 0.975
    max_cvar: _OpenFrac = 0.025
    max_stress_loss: _OpenFrac = 0.15
    drawdown_tiers: tuple[DrawdownTier, ...] = Field(default_factory=_default_drawdown_tiers)
    pod_stops: PodStops = Field(default_factory=PodStops)

    @model_validator(mode="after")
    def _check_ranges_and_tiers(self) -> Self:
        if self.net_min >= self.net_max:
            raise ValueError(f"net_min ({self.net_min}) must be less than net_max ({self.net_max})")
        if self.beta_min >= self.beta_max:
            raise ValueError(
                f"beta_min ({self.beta_min}) must be less than beta_max ({self.beta_max})"
            )
        tiers = self.drawdown_tiers
        if not tiers:
            raise ValueError("drawdown_tiers must not be empty")
        if any(a.drawdown >= b.drawdown for a, b in pairwise(tiers)):
            raise ValueError("drawdown_tiers: drawdown thresholds must be strictly increasing")
        if any(a.degross_frac > b.degross_frac for a, b in pairwise(tiers)):
            raise ValueError("drawdown_tiers: degross_frac must be non-decreasing")
        return self


class PodConfig(_Frozen):
    """One strategy pod (spec §8). `risk_budget` is a fraction of the fund's risk budget;
    `universe` is a key of `DataConfig.universes`."""

    name: str
    enabled: bool = True
    risk_budget: Annotated[float, Field(ge=0, le=1)]
    rebalance: Literal["daily", "weekly", "event"]
    universe: str


# Read-only `Mapping` fields: validated into a `_FrozenDict`, dumped as a plain dict.
_PodMap = Annotated[Mapping[str, PodConfig], AfterValidator(_freeze), WrapSerializer(_thaw)]


def _default_pods() -> _FrozenDict[str, PodConfig]:
    def pod(name: str, budget: float, rebalance: str, universe: str) -> PodConfig:
        return PodConfig.model_validate(
            {"name": name, "risk_budget": budget, "rebalance": rebalance, "universe": universe}
        )

    return _FrozenDict(
        {
            "fundamental_ls": pod("Fundamental L/S", 0.30, "weekly", "sp500"),
            "systematic": pod("Systematic Factor", 0.25, "weekly", "sp500"),
            "statarb": pod("Stat-Arb", 0.15, "daily", "sp500"),
            "global_macro": pod("Global Macro", 0.20, "weekly", "macro_etfs"),
            "event_driven": pod("Event-Driven", 0.10, "event", "sp500"),
        }
    )


class UniverseConfig(_Frozen):
    """A named universe: either an index (membership resolved point-in-time by Stage 08)
    or a static ticker list."""

    description: str
    index: str | None = None
    tickers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _index_xor_tickers(self) -> Self:
        if (self.index is None) == (not self.tickers):
            raise ValueError("exactly one of index or tickers must be set")
        duplicates = sorted({t for t in self.tickers if self.tickers.count(t) > 1})
        if duplicates:
            raise ValueError(f"duplicate tickers: {', '.join(duplicates)}")
        return self


_MACRO_ETFS = (
    "SPY",
    "QQQ",
    "IWM",
    "EFA",
    "EEM",
    "XLB",
    "XLC",
    "XLE",
    "XLF",
    "XLI",
    "XLK",
    "XLP",
    "XLRE",
    "XLU",
    "XLV",
    "XLY",
    "TLT",
    "IEF",
    "SHY",
    "LQD",
    "HYG",
    "GLD",
    "SLV",
    "USO",
    "DBC",
    "UUP",
    "FXE",
    "FXY",
    "VNQ",
    "BIL",
)


_UniverseMap = Annotated[
    Mapping[str, UniverseConfig], AfterValidator(_freeze), WrapSerializer(_thaw)
]


def _default_universes() -> _FrozenDict[str, UniverseConfig]:
    return _FrozenDict(
        {
            "sp500": UniverseConfig(
                description="S&P 500 constituents (point-in-time membership from Stage 08)",
                index="SP500",
            ),
            "macro_etfs": UniverseConfig(
                description="Liquid macro ETFs: regions, sectors, rates, credit, commodities, FX",
                tickers=_MACRO_ETFS,
            ),
        }
    )


class DataProviders(_Frozen):
    """Which provider serves each data family."""

    prices: Literal["yfinance", "alpaca"] = "yfinance"
    fundamentals: Literal["sec_edgar"] = "sec_edgar"
    macro: Literal["fred"] = "fred"
    news: Literal["alpaca", "yahoo"] = "alpaca"


class PriceDataConfig(_Frozen):
    """Daily price fetching and checks (Stage 07).

    `batch_size` symbols per vendor request; a failed request is retried `max_retries` times,
    waiting `backoff_seconds` (doubling each retry); `min_request_interval_seconds` is the pause
    between vendor requests (rate limit); `request_timeout_seconds` bounds one HTTP request.
    `alpaca_feed` is the Alpaca data feed (`iex` on the free tier). `adj_mismatch_tolerance`
    is the fraction above which our recomputed adjusted close and the vendor's are recorded
    as a mismatch (0.005 = 0.5%).
    """

    batch_size: Annotated[int, Field(gt=0)] = 50
    max_retries: Annotated[int, Field(ge=0)] = 3
    backoff_seconds: _NonNeg = 1.0
    min_request_interval_seconds: _NonNeg = 0.5
    request_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    alpaca_feed: Literal["iex", "sip"] = "iex"
    alpaca_data_url: str = "https://data.alpaca.markets"
    adj_mismatch_tolerance: _OpenFrac = 0.005


class TradableConfig(_Frozen):
    """Liquidity filters of `Universe.tradable` (Stage 08), computed from bars known before
    the decision cutoff.

    `min_price`: last raw close, USD. `min_adv_usd`: average daily dollar volume (mean of raw
    close x volume) over the last `adv_window_days` bars, USD. `min_history_days`: bars of
    history. `max_stale_sessions`: how many sessions the last bar may lag the latest session
    whose bar is known (a name that stopped trading drops out).
    """

    min_price: Annotated[float, Field(gt=0)] = 5.0
    min_adv_usd: _NonNeg = 20_000_000.0
    adv_window_days: Annotated[int, Field(gt=0)] = 20
    min_history_days: Annotated[int, Field(gt=0)] = 252
    max_stale_sessions: Annotated[int, Field(ge=0)] = 5

    @model_validator(mode="after")
    def _history_covers_adv_window(self) -> Self:
        if self.min_history_days < self.adv_window_days:
            raise ValueError(
                f"min_history_days ({self.min_history_days}) must be at least "
                f"adv_window_days ({self.adv_window_days})"
            )
        return self


class ReferenceDataConfig(_Frozen):
    """Where the universe reference data comes from (Stage 08).

    The two URLs are the Wikipedia pages holding the S&P 500 constituents table and change
    log; `user_agent` is sent with every request (Wikimedia's robot policy wants a
    descriptive one); `request_timeout_seconds` bounds one HTTP request. `ticker_changes_file`
    is the manual ticker-rename list, relative to the repo root and not resolved here.
    """

    sp500_constituents_url: str = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    sp500_changes_url: str = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
    user_agent: str = "AtlasCapital/0.1 (research and paper-trading simulator)"
    request_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    ticker_changes_file: Path = Path("config/ticker_changes.yaml")


class FundamentalsConfig(_Frozen):
    """SEC EDGAR fundamentals (Stage 09).

    URLs are templates: `{cik:010d}` is the zero-padded CIK, `{name}` a submissions history
    file name. `max_requests_per_second` is the client's rate limit (the SEC allows at most 10);
    `request_timeout_seconds` bounds one HTTP request; a failed request is retried
    `max_retries` times, waiting `backoff_seconds` (doubling). `xbrl_map_file` is the concept
    map, relative to the repo root and not resolved here. Durations, in days, of a fiscal
    quarter (`quarter_days`) and a fiscal year (`annual_days`) are inclusive ranges that admit
    52/53-week years; a metric whose period ended more than `max_staleness_days` before the
    as-of day is not used. `cik_overrides_file` lists verified CIKs of former S&P 500 members
    (relative to the repo root, not resolved here).
    """

    company_tickers_url: str = "https://www.sec.gov/files/company_tickers.json"
    companyfacts_url: str = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    submissions_url: str = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
    submissions_page_url: str = "https://data.sec.gov/submissions/{name}"
    max_requests_per_second: Annotated[float, Field(gt=0, le=10)] = 10.0
    request_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    max_retries: Annotated[int, Field(ge=0)] = 3
    backoff_seconds: _NonNeg = 1.0
    xbrl_map_file: Path = Path("config/xbrl_map.yaml")
    quarter_days: tuple[int, int] = (80, 100)
    annual_days: tuple[int, int] = (350, 380)
    max_staleness_days: Annotated[int, Field(gt=0)] = 400
    cik_overrides_file: Path = Path("config/cik_overrides.yaml")

    @model_validator(mode="after")
    def _check(self) -> Self:
        for name in ("companyfacts_url", "submissions_url"):
            if "{cik" not in getattr(self, name):
                raise ValueError(f"{name} must contain a {{cik:010d}} placeholder")
        if "{name}" not in self.submissions_page_url:
            raise ValueError("submissions_page_url must contain a {name} placeholder")
        for name in ("quarter_days", "annual_days"):
            low, high = getattr(self, name)
            if not 0 < low <= high:
                raise ValueError(f"{name} must be an increasing range of positive days")
        if self.quarter_days[1] >= self.annual_days[0]:
            raise ValueError("quarter_days must end before annual_days starts")
        return self


class MacroConfig(_Frozen):
    """FRED / ALFRED macro data (Stage 10).

    `api_url` is the FRED API base; `max_requests_per_second` the client's rate limit (FRED
    allows 120 requests per minute); `request_timeout_seconds` bounds one HTTP request; a failed
    request is retried `max_retries` times, waiting `backoff_seconds` (doubling). `series_file`
    is the series catalog, relative to the repo root and not resolved here. Ingestion reads
    observations dated from `observation_start` and every vintage from `vintage_start` (FRED's
    earliest date, 1776-07-04, means all vintages), at most `max_vintage_dates_per_request`
    vintages per request (FRED's JSON limit is 2000) and `page_limit` rows per page (FRED's
    maximum is 100000).

    Point in time: a market series' value is available `market_lag_days` calendar days after
    its observation date at `market_available_time` (naive, America/New_York; keep it before
    `calendar.decision_time`, since a value is known only strictly before the cutoff).
    Transforms find a lagged value at most `transform_lag_tolerance_days` days before the
    target date; the z-score uses a trailing window of `zscore_window_years` years and needs
    `zscore_min_years` years of history.
    """

    api_url: str = "https://api.stlouisfed.org/fred"
    max_requests_per_second: Annotated[float, Field(gt=0, le=2)] = 2.0
    request_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    max_retries: Annotated[int, Field(ge=0)] = 3
    backoff_seconds: _NonNeg = 1.0
    series_file: Path = Path("config/macro.yaml")
    observation_start: date = date(2000, 1, 1)
    vintage_start: date = date(1776, 7, 4)
    max_vintage_dates_per_request: Annotated[int, Field(ge=2, le=2000)] = 2000
    page_limit: Annotated[int, Field(ge=1, le=100_000)] = 100_000
    market_lag_days: Annotated[int, Field(ge=1)] = 1
    market_available_time: time = time(6, 0)
    transform_lag_tolerance_days: Annotated[int, Field(ge=0)] = 7
    zscore_window_years: Annotated[int, Field(gt=0)] = 10
    zscore_min_years: Annotated[int, Field(gt=0)] = 5

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.market_available_time.tzinfo is not None:
            raise ValueError("market_available_time must be naive (it is read in New York)")
        if self.zscore_min_years > self.zscore_window_years:
            raise ValueError("zscore_min_years must not exceed zscore_window_years")
        return self


class DataConfig(_Frozen):
    """Data settings. `lake_path` is relative to the repo root and is not resolved here."""

    lake_path: Path = Path("data")
    providers: DataProviders = Field(default_factory=DataProviders)
    prices: PriceDataConfig = Field(default_factory=PriceDataConfig)
    reference: ReferenceDataConfig = Field(default_factory=ReferenceDataConfig)
    fundamentals: FundamentalsConfig = Field(default_factory=FundamentalsConfig)
    macro: MacroConfig = Field(default_factory=MacroConfig)
    tradable: TradableConfig = Field(default_factory=TradableConfig)
    universes: _UniverseMap = Field(default_factory=_default_universes)


class LLMTiers(_Frozen):
    """Model id per tier (spec §11)."""

    deep: str = "claude-opus-5-5"
    quick: str = "claude-haiku-5-5"


class LLMBudgets(_Frozen):
    """LLM spend caps in USD (spec A8) and a per-call output token cap."""

    daily_usd: Annotated[float, Field(gt=0)] = 5.0
    per_run_usd: Annotated[float, Field(gt=0)] = 25.0
    per_agent_call_max_tokens: Annotated[int, Field(gt=0)] = 8000


class LLMConfig(_Frozen):
    """LLM gateway values; consumed by Stage 24."""

    provider: Literal["anthropic", "openai_compatible"] = "anthropic"
    tiers: LLMTiers = Field(default_factory=LLMTiers)
    budgets: LLMBudgets = Field(default_factory=LLMBudgets)


class CostConfig(_Frozen):
    """Simulated trading costs. Spreads in basis points, `impact_k` is the square-root impact
    coefficient, commission in base currency per share, borrow as an annual fraction."""

    half_spread_bps: _NonNeg = 5.0
    impact_k: _NonNeg = 0.1
    commission_per_share: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    borrow_gc_annual: _NonNeg = 0.003


class BacktestConfig(_Frozen):
    """Backtest window (spec A7). `end=None` means the latest available session."""

    start: date = date(2018, 1, 1)
    in_sample_end: date = date(2021, 12, 31)
    end: date | None = None
    costs: CostConfig = Field(default_factory=CostConfig)

    @model_validator(mode="after")
    def _ordered_window(self) -> Self:
        if self.start >= self.in_sample_end:
            raise ValueError(
                f"start ({self.start}) must be before in_sample_end ({self.in_sample_end})"
            )
        if self.end is not None and self.in_sample_end >= self.end:
            raise ValueError(
                f"in_sample_end ({self.in_sample_end}) must be before end ({self.end})"
            )
        return self


class CalendarConfig(_Frozen):
    """Trading calendar bounds and point-in-time timing (Stage 05).

    `start`/`end` bound the XNYS calendar (inclusive dates). `decision_time` is a naive
    wall-clock time read in America/New_York: the daily decision cutoff. A daily bar is known
    `bar_publish_lag_minutes` minutes after its session's close.
    """

    start: date = date(2000, 1, 1)
    end: date = date(2030, 12, 31)
    decision_time: time = time(8, 0)
    bar_publish_lag_minutes: Annotated[int, Field(ge=0)] = 15

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.start >= self.end:
            raise ValueError(f"start ({self.start}) must be before end ({self.end})")
        if self.decision_time.tzinfo is not None:
            raise ValueError("decision_time must be naive (it is read in America/New_York)")
        return self


class Secrets(_Frozen):
    """API credentials, read only from the environment or `.env`. Never logged or hashed."""

    anthropic_api_key: SecretStr | None = None
    alpaca_api_key_id: SecretStr | None = None
    alpaca_api_secret_key: SecretStr | None = None
    fred_api_key: SecretStr | None = None
    sec_user_agent: SecretStr | None = None


class AtlasConfig(BaseSettings):
    """The whole configuration. Build it with `load_config()`; `AtlasConfig()` alone gives
    the code defaults and reads nothing from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="ATLAS__", env_nested_delimiter="__", frozen=True, extra="forbid"
    )

    profile: Profile = "dev"
    fund: FundConfig = Field(default_factory=FundConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    pods: _PodMap = Field(default_factory=_default_pods)
    data: DataConfig = Field(default_factory=DataConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    secrets: Secrets = Field(default_factory=Secrets)

    @model_validator(mode="after")
    def _check_pods(self) -> Self:
        total = math.fsum(pod.risk_budget for pod in self.pods.values())
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            per_pod = {pod_id: pod.risk_budget for pod_id, pod in self.pods.items()}
            raise ValueError(f"pod risk budgets sum to {total:.6g}, expected 1.0: {per_pod}")
        for pod_id, pod in self.pods.items():
            if pod.universe not in self.data.universes:
                known = ", ".join(sorted(self.data.universes))
                raise ValueError(
                    f"pods.{pod_id}.universe: unknown universe {pod.universe!r}; known: {known}"
                )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Only explicit init values; `load_config` assembles every other layer itself."""
        return (init_settings,)


class _UniqueKeyLoader(yaml.SafeLoader):
    """`yaml.SafeLoader` that rejects a key repeated within one mapping."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.Node) -> dict[Any, Any]:
    if not isinstance(node, yaml.MappingNode):
        raise yaml.constructor.ConstructorError(
            None, None, f"expected a mapping, got {type(node).__name__}", node.start_mark
        )
    loader.flatten_mapping(node)
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        try:
            hash(key)
        except TypeError:
            raise yaml.constructor.ConstructorError(
                None, None, f"unhashable key {key!r}", key_node.start_mark
            ) from None
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _read_yaml(path: Path) -> dict[str, Any]:
    """Parse one YAML file; a missing or empty file is an empty layer."""
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: not valid UTF-8 text ({exc.reason})") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc.strerror}") from exc
    try:
        # _UniqueKeyLoader is a SafeLoader subclass, so this is a safe load.
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.MarkedYAMLError as exc:
        line = exc.problem_mark.line + 1 if exc.problem_mark is not None else "?"
        raise ConfigError(f"{path}: invalid YAML at line {line}: {exc.problem}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping, got {type(data).__name__}")
    return data


def read_config_file(path: Path) -> dict[str, Any]:
    """Parse one YAML file under `config/` with the loader's rules: UTF-8, no repeated keys,
    a mapping at the top. A missing or empty file is `{}`; errors raise `ConfigError` naming
    the file. For reference files that `load_config` does not read (Stage 08 ticker renames).
    """
    return _read_yaml(path)


def _deep_merge(*layers: Mapping[str, Any]) -> dict[str, Any]:
    """Merge layers left to right: mappings merge recursively, lists and scalars replace."""
    merged: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            if isinstance(value, Mapping):
                current = merged.get(key)
                merged[key] = _deep_merge(current if isinstance(current, Mapping) else {}, value)
            else:
                merged[key] = value
    return merged


_ENV_PREFIX = "ATLAS__"
# Top-level keys that YAML, env vars and overrides may set.
_SECTIONS = frozenset({"fund", "limits", "pods", "data", "llm", "backtest", "calendar"})
_RESERVED = {
    "secrets": "'secrets' may only come from environment variables or .env",
    "profile": "'profile' may only come from load_config(profile=...)",
}


def _check_reserved(layer: Mapping[str, Any], source: str) -> None:
    for key, why in _RESERVED.items():
        if key in layer:
            raise ConfigError(f"{source}: {why}")


def _check_env_names(names: list[str], source: str) -> None:
    """Reject `ATLAS__<x>__...` names whose section `<x>` is not a config section."""
    for name in names:
        if not name.upper().startswith(_ENV_PREFIX):
            continue
        section = name[len(_ENV_PREFIX) :].split("__", 1)[0].lower()
        if section in _RESERVED:
            raise ConfigError(f"{source}: {name} is not allowed; {_RESERVED[section]}")
        if section not in _SECTIONS:
            valid = ", ".join(sorted(_SECTIONS))
            raise ConfigError(f"{source}: {name} names no config section; valid: {valid}")


def _env_layers(dotenv_path: Path) -> list[dict[str, Any]]:
    """The `.env` layer then the process-env layer, from `ATLAS__` variables only."""
    file_vars = dotenv_values(dotenv_path) if dotenv_path.is_file() else {}
    _check_env_names(list(file_vars), str(dotenv_path))
    _check_env_names(list(os.environ), "environment")
    options: dict[str, Any] = {
        "env_prefix": _ENV_PREFIX,
        "env_nested_delimiter": "__",
        "case_sensitive": False,
    }
    dotenv = _run_source(
        DotEnvSettingsSource(AtlasConfig, env_file=dotenv_path, **options), dotenv_path
    )
    env = _run_source(EnvSettingsSource(AtlasConfig, **options), "environment")
    # The .env source also returns unprefixed keys (e.g. fred_api_key); keep config sections.
    return [{k: v for k, v in dotenv.items() if k in _SECTIONS}, env]


def _run_source(source: PydanticBaseSettingsSource, origin: object) -> dict[str, Any]:
    """Call a pydantic-settings source, turning its JSON parse errors into `ConfigError`."""
    try:
        return source()
    except SettingsError as exc:
        # The message names the field, never the value: 'error parsing value for field "x" ...'
        match = re.search(r'field "(\w+)"', str(exc))
        section = match.group(1).upper() if match else "<SECTION>"
        raise ConfigError(f"{origin}: an ATLAS__{section}__* value is not valid JSON") from exc


def _read_secrets(dotenv_path: Path) -> Secrets:
    """Each `Secrets` field from the env var of the same name (upper case), else from `.env`.

    Empty values (as copied from `.env.example`) count as unset.
    """
    file_vars = dotenv_values(dotenv_path) if dotenv_path.is_file() else {}
    env = {k.upper(): v for k, v in os.environ.items()}
    dotenv = {k.upper(): v for k, v in file_vars.items()}
    values: dict[str, str] = {}
    for field in Secrets.model_fields:
        value = env.get(field.upper()) or dotenv.get(field.upper())
        if value:
            values[field] = value
    return Secrets.model_validate(values)


def _yaml_layers(config_dir: Path, profile: str) -> list[dict[str, Any]]:
    """The YAML layers in merge order, each shaped like the `AtlasConfig` tree."""
    fund_layer = _read_yaml(config_dir / "fund.yaml")
    _check_reserved(fund_layer, str(config_dir / "fund.yaml"))
    layers = [
        fund_layer,
        {"limits": _read_yaml(config_dir / "limits.yaml")},
        {"data": {"universes": _read_yaml(config_dir / "universe.yaml")}},
    ]
    pods_dir = config_dir / "pods"
    if pods_dir.is_dir():
        for path in sorted(pods_dir.glob("*.yaml")):
            layers.append({"pods": {path.stem: _read_yaml(path)}})
    profile_path = config_dir / "profiles" / f"{profile}.yaml"
    profile_layer = _read_yaml(profile_path)
    _check_reserved(profile_layer, str(profile_path))
    layers.append(profile_layer)
    return layers


def _default_layer(config_dir: Path) -> dict[str, Any]:
    """The code defaults as a merge layer.

    Pod files and universe.yaml define whole collections: when any `pods/*.yaml` exists
    the default pods are dropped (deleting a file removes that pod), and a non-empty
    universe.yaml replaces the default universes. Later layers still merge into entries.
    """
    defaults = AtlasConfig().model_dump(exclude={"secrets"})
    pods_dir = config_dir / "pods"
    if pods_dir.is_dir() and any(pods_dir.glob("*.yaml")):
        del defaults["pods"]
    if _read_yaml(config_dir / "universe.yaml"):
        del defaults["data"]["universes"]
    return defaults


def _format_errors(exc: ValidationError, profile: str) -> str:
    lines = [f"Invalid configuration (profile {profile!r}):"]
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


def load_config(
    profile: str = "dev",
    overrides: dict[str, Any] | None = None,
    config_dir: Path | None = None,
) -> AtlasConfig:
    """Load and validate the configuration for `profile`.

    Layers, lowest first: code defaults < fund.yaml < limits.yaml < universe.yaml <
    pods/*.yaml < profiles/<profile>.yaml < `.env` < `ATLAS__*` env vars < `overrides`.
    Mappings merge key by key and lists are replaced, except that the pod files and
    universe.yaml define whole collections (see `_default_layer`); `.yml` files are rejected.
    `config_dir` defaults to `./config`; `.env` is read from its parent directory. Secrets
    come only from env vars or `.env`. Raises `ConfigError` naming the file, variable or
    dotted key at fault (never a value).
    """
    if profile not in PROFILES:
        raise ConfigError(f"Unknown profile {profile!r}; expected one of: {', '.join(PROFILES)}")
    config_dir = Path.cwd() / "config" if config_dir is None else Path(config_dir)
    if not config_dir.is_dir():
        raise ConfigError(f"config directory not found: {config_dir}")
    stray = sorted(config_dir.rglob("*.yml"))
    if stray:
        raise ConfigError(f"{stray[0]}: use the .yaml extension; .yml files are not read")

    overrides = overrides or {}
    _check_reserved(overrides, "overrides")
    dotenv_path = config_dir.parent / ".env"

    defaults = _default_layer(config_dir)
    data = _deep_merge(
        defaults,
        *_yaml_layers(config_dir, profile),
        *_env_layers(dotenv_path),
        overrides,
        {"profile": profile},
    )
    try:
        return AtlasConfig(**data, secrets=_read_secrets(dotenv_path))
    except ValidationError as exc:
        # `from None`: the formatted message keeps every location and reason, while the
        # original error would echo input values, e.g. a secret pasted into a YAML file.
        raise ConfigError(_format_errors(exc, profile)) from None


def config_fingerprint(cfg: AtlasConfig) -> str:
    """Stable 12-hex-char tag of every non-secret setting, for labelling runs.

    SHA-256 of the canonical JSON dump (sorted keys) with `secrets` excluded, so the same
    settings give the same tag on any machine and in any process.
    """
    payload = json.dumps(
        cfg.model_dump(mode="json", exclude={"secrets"}), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]
