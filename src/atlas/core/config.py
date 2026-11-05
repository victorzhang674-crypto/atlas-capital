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
from collections.abc import Mapping
from datetime import date, time
from decimal import Decimal
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Self

import yaml
from dotenv import dotenv_values
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import to_jsonable_python
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.exceptions import SettingsError

from atlas.core.errors import ConfigError
from atlas.core.types.base import FrozenDict, FrozenMap
from atlas.core.types.instruments import normalize_symbol

Profile = Literal["dev", "backtest", "paper"]
PROFILES: tuple[Profile, ...] = ("dev", "backtest", "paper")

# Bounded float types. Upper bounds catch a percent typed where a fraction belongs (5 vs 0.05).
_Frac = Annotated[float, Field(gt=0, le=1)]  # (0, 1]
_OpenFrac = Annotated[float, Field(gt=0, lt=1)]  # (0, 1)
_Confidence = Annotated[float, Field(gt=0.5, lt=1)]  # (0.5, 1)
_NonNeg = Annotated[float, Field(ge=0)]


class _Frozen(BaseModel):
    """Immutable model that rejects unknown keys and NaN/infinite numbers. List fields are
    tuples for the same reason."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class FundConfig(_Frozen):
    """The fund mandate. `initial_capital` is in the base currency and positive (spec A6)."""

    name: str = "Atlas Capital"
    base_currency: Literal["USD"] = "USD"
    initial_capital: Annotated[Decimal, Field(gt=0)] = Decimal("10000000")
    benchmark: str = "SPY"


class DrawdownTier(_Frozen):
    """One fund drawdown circuit breaker (spec §9).

    `drawdown` is a positive fraction from the high-water mark (0.05 = -5%); `degross_frac`
    is the fraction of gross exposure to remove; `halt` stops trading until the owner resets.
    Tiers only escalate: a deeper tier never removes less, and never switches
    `block_new_names` or `halt` back off.
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
        for flag in ("block_new_names", "halt"):
            if any(getattr(a, flag) and not getattr(b, flag) for a, b in pairwise(tiers)):
                raise ValueError(f"drawdown_tiers: {flag} cannot switch off at a deeper tier")
        return self


class PodConfig(_Frozen):
    """One strategy pod (spec §8). `risk_budget` is a fraction of the fund's risk budget;
    `universe` is a key of `DataConfig.universes`."""

    name: str
    enabled: bool = True
    risk_budget: Annotated[float, Field(ge=0, le=1)]
    rebalance: Literal["daily", "weekly", "event"]
    universe: str


# Read-only `Mapping` fields: validated into a `FrozenDict`, dumped as a plain dict.
_PodMap = FrozenMap[str, PodConfig]


def _default_pods() -> FrozenDict[str, PodConfig]:
    def pod(name: str, budget: float, rebalance: str, universe: str) -> PodConfig:
        return PodConfig.model_validate(
            {"name": name, "risk_budget": budget, "rebalance": rebalance, "universe": universe}
        )

    return FrozenDict(
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


_UniverseMap = FrozenMap[str, UniverseConfig]


def _default_universes() -> FrozenDict[str, UniverseConfig]:
    return FrozenDict(
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
    as a mismatch (0.005 = 0.5%). `memo_size` bounds the yfinance adapter's memo of downloaded
    (symbol, window) bar sets; the least recently used is dropped first. `yahoo_no_data_file`
    lists symbols Yahoo has no bars for, which the yfinance adapter does not request (relative
    to the repo root).
    """

    batch_size: Annotated[int, Field(gt=0)] = 50
    max_retries: Annotated[int, Field(ge=0)] = 3
    backoff_seconds: _NonNeg = 1.0
    min_request_interval_seconds: _NonNeg = 0.5
    request_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    alpaca_feed: Literal["iex", "sip"] = "iex"
    alpaca_data_url: str = "https://data.alpaca.markets"
    adj_mismatch_tolerance: _OpenFrac = 0.005
    memo_size: Annotated[int, Field(gt=0)] = 1000
    yahoo_no_data_file: Path = Path("config/yahoo_no_data.yaml")


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
    is the manual ticker-rename list and `known_anomalies_file` the reviewed change-log
    anomalies (logged at info, not as warnings), both relative to the repo root and not
    resolved here.
    """

    sp500_constituents_url: str = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    sp500_changes_url: str = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
    user_agent: str = "AtlasCapital/0.1 (research and paper-trading simulator)"
    request_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    ticker_changes_file: Path = Path("config/ticker_changes.yaml")
    known_anomalies_file: Path = Path("config/membership_anomalies.yaml")


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


class NewsConfig(_Frozen):
    """Company and market news (Stage 11).

    Alpaca: `alpaca_news_url` is the news endpoint; `page_limit` articles per page (Alpaca's
    maximum is 50); `symbols_per_request` tickers per request; a failed request is retried
    `max_retries` times, waiting `backoff_seconds` (doubling); `min_request_interval_seconds`
    is the pause between requests (the free tier allows 200 per minute);
    `request_timeout_seconds` bounds one HTTP request. Yahoo: `yahoo_max_items` articles asked
    per symbol (recent news only). Summaries are cut to `max_summary_chars` characters.

    Query: two articles are the same story when published at most `dedup_window_hours` apart
    and their content hashes match or their headlines score at least `fuzzy_threshold` (0-100,
    rapidfuzz ratio). An article with more than `roundup_max_symbols` tags is a roundup and is
    dropped unless its headline names the ticker. Relevance is `headline_relevance` when the
    headline names the ticker and `symbols_relevance` when it is only tagged. Market-wide news
    is untagged news or news tagged with one of `market_symbols`. `lookback_days` and `limit`
    are the query defaults (calendar days before the cutoff; articles returned).
    """

    alpaca_news_url: str = "https://data.alpaca.markets/v1beta1/news"
    page_limit: Annotated[int, Field(ge=1, le=50)] = 50
    symbols_per_request: Annotated[int, Field(ge=1)] = 50
    max_retries: Annotated[int, Field(ge=0)] = 3
    backoff_seconds: _NonNeg = 1.0
    min_request_interval_seconds: _NonNeg = 0.3
    request_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    yahoo_max_items: Annotated[int, Field(ge=1)] = 20
    max_summary_chars: Annotated[int, Field(ge=1)] = 1000
    dedup_window_hours: Annotated[float, Field(gt=0)] = 48.0
    fuzzy_threshold: Annotated[float, Field(ge=0, le=100)] = 92.0
    roundup_max_symbols: Annotated[int, Field(ge=1)] = 10
    headline_relevance: Annotated[float, Field(gt=0)] = 1.0
    symbols_relevance: Annotated[float, Field(gt=0)] = 0.5
    market_symbols: tuple[str, ...] = ("SPY", "QQQ")
    lookback_days: Annotated[int, Field(ge=1)] = 14
    limit: Annotated[int, Field(ge=1)] = 50

    @field_validator("market_symbols", mode="after")
    @classmethod
    def _normalise_market_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        symbols = tuple(normalize_symbol(s) for s in value)
        if not symbols:
            raise ValueError("market_symbols must name at least one symbol")
        if len(set(symbols)) != len(symbols):
            raise ValueError(f"duplicate market_symbols: {list(symbols)}")
        return symbols

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.symbols_relevance >= self.headline_relevance:
            raise ValueError("symbols_relevance must be below headline_relevance")
        return self


class EventsConfig(_Frozen):
    """Corporate and macro events (Stage 12).

    `macro_calendar_file` holds the FOMC meetings and the FRED release definitions (relative to
    the repo root, not resolved here). Earnings dates come from EDGAR filings filed from
    `history_start` on; a 10-Q/10-K is an earnings event only if no 8-K item 2.02 was accepted
    in the `periodic_match_days` calendar days before it. `upcoming_earnings` names the
    optional provider of scheduled dates (`yahoo`, or `none`); its failed requests are retried
    `max_retries` times, waiting `backoff_seconds` (doubling), with
    `min_request_interval_seconds` between requests.

    Queries: a scheduled earnings date is dropped once an actual earnings event of the symbol
    on or after `scheduled_date - scheduled_match_days` (calendar days) is known. Reactions span
    `reaction_days` sessions from the reaction session and are measured against
    `reaction_benchmark`. `horizon_days` (calendar days after the as-of day) and `past_n`
    (events returned) are the query defaults. With `estimate_earnings`, a symbol without a
    current scheduled date gets an estimated next earnings date: a known actual earnings date
    plus 52 weeks that falls more than `estimate_min_gap_days` calendar days after its latest
    known actual event.
    """

    macro_calendar_file: Path = Path("config/macro_calendar.yaml")
    history_start: date = date(2015, 1, 1)
    periodic_match_days: Annotated[int, Field(ge=1)] = 60
    upcoming_earnings: Literal["yahoo", "none"] = "yahoo"
    max_retries: Annotated[int, Field(ge=0)] = 3
    backoff_seconds: _NonNeg = 1.0
    min_request_interval_seconds: _NonNeg = 0.5
    scheduled_match_days: Annotated[int, Field(ge=0)] = 10
    reaction_days: Annotated[int, Field(ge=1)] = 3
    reaction_benchmark: str = "SPY"
    horizon_days: Annotated[int, Field(ge=1)] = 14
    past_n: Annotated[int, Field(ge=1)] = 8
    estimate_earnings: bool = True
    estimate_min_gap_days: Annotated[int, Field(ge=1)] = 20

    @field_validator("reaction_benchmark", mode="after")
    @classmethod
    def _normalise_benchmark(cls, value: str) -> str:
        return normalize_symbol(value)


class SocialConfig(_Frozen):
    """Optional social-media posts (Stage 12), off unless `enabled`.

    `providers` are fetched in order (`stocktwits`, `reddit`); StockTwits streams come from
    `stocktwits_url`, Reddit searches of `reddit_subreddits` from `reddit_url`. Each request asks
    for at most `max_items` posts (Reddit's maximum is 100) and sends `user_agent`; a failed
    request is retried `max_retries` times, waiting `backoff_seconds` (doubling), with
    `min_request_interval_seconds` between requests and `request_timeout_seconds` per request.
    `lookback_days` (calendar days before the cutoff) and `limit` (posts returned) are the query
    defaults.
    """

    enabled: bool = False
    providers: tuple[Literal["stocktwits", "reddit"], ...] = ("stocktwits", "reddit")
    stocktwits_url: str = "https://api.stocktwits.com/api/2/streams/symbol"
    reddit_url: str = "https://www.reddit.com"
    reddit_subreddits: Annotated[tuple[str, ...], Field(min_length=1)] = (
        "wallstreetbets",
        "stocks",
        "investing",
    )
    max_items: Annotated[int, Field(ge=1, le=100)] = 100
    lookback_days: Annotated[int, Field(ge=1)] = 7
    limit: Annotated[int, Field(ge=1)] = 50
    max_retries: Annotated[int, Field(ge=0)] = 3
    backoff_seconds: _NonNeg = 1.0
    min_request_interval_seconds: _NonNeg = 1.0
    request_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    user_agent: str = "AtlasCapital/0.1 (research and paper-trading simulator)"

    @model_validator(mode="after")
    def _check(self) -> Self:
        if len(set(self.providers)) != len(self.providers):
            raise ValueError(f"duplicate providers: {list(self.providers)}")
        if not self.user_agent.strip():
            raise ValueError("user_agent must not be blank")
        return self


class SentimentConfig(_Frozen):
    """Numeric news sentiment (Stage 12).

    `lexicon_file` is the Loughran-McDonald word list (`word,category`; relative to the repo
    root, not resolved here), built from the master dictionary at `lexicon_url` by
    `download_lm_lexicon`. A positive word with a negation among the `negation_window` words
    before it is not counted. The per-symbol score weights each article by its relevance and
    by `0.5 ** (age / half_life_days)` (age in days before the cutoff), over the articles of the
    last `lookback_days` calendar days (at most `max_articles`); `daily_days` is the default
    length of the daily series, in sessions. `social` configures the optional social posts.
    """

    lexicon_file: Path = Path("data/reference/lm_lexicon.csv")
    lexicon_url: str = (
        "https://drive.usercontent.google.com/download?id=1iq2RUf8qGFEAk1g8wQntP3habOnR3fXF"
        "&export=download&confirm=t"
    )
    negation_window: Annotated[int, Field(ge=0)] = 3
    half_life_days: Annotated[float, Field(gt=0)] = 3.0
    lookback_days: Annotated[int, Field(ge=1)] = 14
    max_articles: Annotated[int, Field(ge=1)] = 500
    daily_days: Annotated[int, Field(ge=1)] = 90
    social: SocialConfig = Field(default_factory=SocialConfig)


_SeverityName = Literal["info", "warning", "critical"]


class QualitySeverities(_Frozen):
    """Severity of each data quality check (Stage 13). A `critical` issue blocks its symbol
    (or macro series) in `DataView` on the report's day; `warning` and `info` are reported
    only."""

    missing_sessions: _SeverityName = "warning"
    stale_price: _SeverityName = "critical"
    return_outlier: _SeverityName = "warning"
    ohlc_inconsistent: _SeverityName = "critical"
    nonpositive_price: _SeverityName = "critical"
    split_mismatch: _SeverityName = "critical"
    fundamentals_jump: _SeverityName = "warning"
    macro_gap: _SeverityName = "warning"
    vendor_mix: _SeverityName = "warning"
    fundamentals_period_after_filing: _SeverityName = "warning"


class FrequencyDays(_Frozen):
    """A number of calendar days per observation frequency of a macro series."""

    daily: Annotated[int, Field(gt=0)]
    weekly: Annotated[int, Field(gt=0)]
    monthly: Annotated[int, Field(gt=0)]
    quarterly: Annotated[int, Field(gt=0)]
    annual: Annotated[int, Field(gt=0)]


class QualityConfig(_Frozen):
    """Data quality checks run by `atlas.data.quality.quality_report` (Stage 13).

    A report covers the last `window_sessions` sessions whose bars are known at the as-of
    cutoff. `stale_min_sessions`: consecutive unchanged closes (each bar with volume > 0)
    that make a stale price. A return outlier is a simple daily return whose magnitude
    exceeds `outlier_sigma` times the standard deviation of the previous
    `outlier_vol_window` returns (at least `outlier_min_returns` of them), floored at
    `outlier_min_daily_vol` (a daily fraction). An outlier on a session without a corporate
    action whose gross move is within `split_ratio_tolerance` (relative) of `1/k` or `k` for
    a ratio `k` in `split_ratios` is an unrecorded split. `fundamentals_jump_ratio`: quarter
    over quarter change factor of a fundamental value that is flagged (either way). Macro
    series: `macro_max_gap_days` between consecutive observations and `macro_max_age_days`
    of the last observation on the as-of day, by inferred frequency (calendar days).
    """

    window_sessions: Annotated[int, Field(ge=1)] = 21
    stale_min_sessions: Annotated[int, Field(ge=2)] = 5
    outlier_sigma: Annotated[float, Field(gt=0)] = 8.0
    outlier_vol_window: Annotated[int, Field(ge=2)] = 63
    outlier_min_returns: Annotated[int, Field(ge=2)] = 20
    outlier_min_daily_vol: _NonNeg = 0.005
    split_ratios: Annotated[tuple[Annotated[float, Field(gt=1)], ...], Field(min_length=1)] = (
        2.0,
        3.0,
        4.0,
        5.0,
        8.0,
        10.0,
        15.0,
        20.0,
        1.5,
    )
    split_ratio_tolerance: Annotated[float, Field(gt=0, lt=0.2)] = 0.03
    fundamentals_jump_ratio: Annotated[float, Field(gt=1)] = 10.0
    macro_max_gap_days: FrequencyDays = Field(
        default_factory=lambda: FrequencyDays(
            daily=7, weekly=14, monthly=45, quarterly=135, annual=400
        )
    )
    macro_max_age_days: FrequencyDays = Field(
        default_factory=lambda: FrequencyDays(
            daily=10, weekly=21, monthly=100, quarterly=230, annual=550
        )
    )
    severity: QualitySeverities = Field(default_factory=QualitySeverities)

    @model_validator(mode="after")
    def _vol_window_covers_minimum(self) -> Self:
        if self.outlier_vol_window < self.outlier_min_returns:
            raise ValueError(
                f"outlier_vol_window ({self.outlier_vol_window}) must be at least "
                f"outlier_min_returns ({self.outlier_min_returns})"
            )
        return self


class ViewConfig(_Frozen):
    """`DataView` settings (Stage 13): `cache_size` is the number of results kept in the LRU
    cache shared by a view and the views `advance` creates from it. `fundamentals_cache_size`
    is the number of (symbol, known facts) concept-value sets kept across as-of days."""

    cache_size: Annotated[int, Field(ge=1)] = 256
    fundamentals_cache_size: Annotated[int, Field(ge=1)] = 5000


class SyncWorkers(_Frozen):
    """Threads in each provider's pool during a sync (Stage 14). Request rates are still
    bounded by each client's own throttle, shared by its threads. `yahoo` should stay 1:
    yfinance keeps module-level state and is not thread-safe. `local` runs lake-only steps."""

    yahoo: Annotated[int, Field(ge=1)] = 1
    sec: Annotated[int, Field(ge=1)] = 4
    fred: Annotated[int, Field(ge=1)] = 2
    alpaca: Annotated[int, Field(ge=1)] = 2
    wikipedia: Annotated[int, Field(ge=1)] = 1
    local: Annotated[int, Field(ge=1)] = 1


class SyncChunks(_Frozen):
    """Keys (symbols, or macro series ids) per sync chunk, per step (Stage 14). The checkpoint
    is written after each chunk, so smaller chunks lose less work when a run stops."""

    prices: Annotated[int, Field(ge=1)] = 50
    fundamentals: Annotated[int, Field(ge=1)] = 20
    macro: Annotated[int, Field(ge=1)] = 2
    news: Annotated[int, Field(ge=1)] = 50
    earnings: Annotated[int, Field(ge=1)] = 20
    upcoming: Annotated[int, Field(ge=1)] = 50
    social: Annotated[int, Field(ge=1)] = 20


class SyncConfig(_Frozen):
    """The data sync and backfill (`atlas data sync|update`, Stage 14).

    `default_start` is the first date of a sync without `--from`. `state_file` is the resume
    checkpoint, relative to the lake root. Windowed datasets re-fetch `update_overlap_days`
    calendar days before a key's covered end (late vendor corrections); a key the vendor had
    no data for over its whole covered history (`empty`, e.g. a delisted name) is asked again
    for new days only once its last check is `empty_retry_days` days old. News is fetched and
    checkpointed in windows of `news_window_days` calendar days, news scoring in windows of
    `sentiment_window_days`. `status` counts a symbol as having enough price history when its
    first and last bars are `status_min_price_years` years apart. `universe` is the index
    universe whose members (at any time in the window) are synced. `archive_raw` keeps every
    price and news answer as it arrived under the lake's `raw/<vendor>/` (`atlas.data.archive`).
    """

    default_start: date = date(2015, 1, 1)
    state_file: Path = Path("cache/sync_state.json")
    update_overlap_days: Annotated[int, Field(ge=0)] = 5
    empty_retry_days: Annotated[int, Field(ge=0)] = 30
    news_window_days: Annotated[int, Field(ge=1)] = 90
    sentiment_window_days: Annotated[int, Field(ge=1)] = 365
    status_min_price_years: Annotated[float, Field(gt=0)] = 8.0
    universe: Annotated[str, Field(min_length=1)] = "sp500"
    archive_raw: bool = True
    workers: SyncWorkers = Field(default_factory=SyncWorkers)
    chunk_size: SyncChunks = Field(default_factory=SyncChunks)

    @field_validator("state_file")
    @classmethod
    def _relative_state_file(cls, value: Path) -> Path:
        if value.is_absolute():
            raise ValueError("state_file must be relative to the lake root")
        return value


class DataConfig(_Frozen):
    """Data settings. `lake_path` is relative to the repo root and is not resolved here."""

    lake_path: Path = Path("data")
    providers: DataProviders = Field(default_factory=DataProviders)
    prices: PriceDataConfig = Field(default_factory=PriceDataConfig)
    reference: ReferenceDataConfig = Field(default_factory=ReferenceDataConfig)
    fundamentals: FundamentalsConfig = Field(default_factory=FundamentalsConfig)
    macro: MacroConfig = Field(default_factory=MacroConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    events: EventsConfig = Field(default_factory=EventsConfig)
    sentiment: SentimentConfig = Field(default_factory=SentimentConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    view: ViewConfig = Field(default_factory=ViewConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
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


class SnapshotConfig(_Frozen):
    """The per-symbol `TechnicalSnapshot` for the agents (Stage 15).

    A latest value's percentile against the symbol's own history uses its last
    `history_sessions` values of that feature (today's included) and needs at least
    `min_history` of them; the percentile against the universe compares it with the latest
    values of the members of `universe` at `as_of` (plus the requested symbols) and needs at
    least `min_universe` of them.
    """

    history_sessions: Annotated[int, Field(ge=2)] = 252
    min_history: Annotated[int, Field(ge=1)] = 63
    universe: Annotated[str, Field(min_length=1)] = "sp500"
    min_universe: Annotated[int, Field(ge=1)] = 20

    @model_validator(mode="after")
    def _min_history_fits(self) -> Self:
        if self.min_history > self.history_sessions:
            raise ValueError(
                f"min_history ({self.min_history}) must not exceed history_sessions "
                f"({self.history_sessions})"
            )
        return self


class FeaturesConfig(_Frozen):
    """The technical feature library (`atlas.quant.features`, Stage 15).

    `compute_features` loads each feature's lookback plus `buffer_sessions` sessions (room for
    missing bars and the warm-up of recursive smoothers such as EMAs). Volatilities are
    annualised with `sessions_per_year` trading sessions.
    """

    buffer_sessions: Annotated[int, Field(ge=0)] = 63
    sessions_per_year: Annotated[int, Field(ge=1)] = 252
    snapshot: SnapshotConfig = Field(default_factory=SnapshotConfig)


class FactorsConfig(_Frozen):
    """The fundamental factor library (`atlas.quant.factors`, Stage 16).

    `factor_panel` covers the members of `universe` at `as_of` by default. Cleaning clips each
    factor at its cross-sectional `winsor_lower`/`winsor_upper` quantiles (fractions,
    0 <= lower < upper <= 1) and leaves a factor with fewer than `min_names` valid raw values
    all NaN; `size_neutral` also regresses each factor (except size) on the size exposure.
    A year-ago (three-years-ago) fiscal period is the one ending nearest 365 (1095) days
    before the latest, within `period_tolerance_days`; margin stability uses the last
    `margin_quarters` discrete quarters; EPS growth needs a year-ago TTM EPS of at least
    `min_abs_eps` USD in absolute value; the price for market cap is the latest known close no
    older than `max_price_age_sessions` sessions.
    """

    universe: Annotated[str, Field(min_length=1)] = "sp500"
    winsor_lower: Annotated[float, Field(ge=0, le=1)] = 0.01
    winsor_upper: Annotated[float, Field(ge=0, le=1)] = 0.99
    min_names: Annotated[int, Field(ge=2)] = 20
    size_neutral: bool = False
    period_tolerance_days: Annotated[int, Field(ge=1)] = 20
    margin_quarters: Annotated[int, Field(ge=2)] = 8
    min_abs_eps: Annotated[float, Field(gt=0)] = 0.01
    max_price_age_sessions: Annotated[int, Field(ge=1)] = 5

    @model_validator(mode="after")
    def _winsor_ordered(self) -> Self:
        if not self.winsor_lower < self.winsor_upper:
            raise ValueError(
                f"winsor_lower ({self.winsor_lower}) must be below winsor_upper "
                f"({self.winsor_upper})"
            )
        return self


def _ascending_positive(name: str, values: tuple[int, ...]) -> tuple[int, ...]:
    """`values` if non-empty, positive and strictly ascending; `ValueError` naming `name`."""
    if not values:
        raise ValueError(f"{name} must not be empty")
    if any(v < 1 for v in values):
        raise ValueError(f"{name} must be positive, got {values}")
    if any(b <= a for a, b in pairwise(values)):
        raise ValueError(f"{name} must be strictly ascending without repeats, got {values}")
    return values


class ResearchBacktestConfig(_Frozen):
    """The vectorised long-short backtest of the signal research toolkit (Stage 17).

    On the first evaluation date of each `rebalance` period (`signal` = every evaluation
    date) it goes long the top quantile and short the bottom one, each leg `gross / 2` of NAV
    (fractions: 2.0 = 100% long and 100% short), with `equal` weights or `signal` weights
    (proportional to the distance of the name's rank from the median). Each rebalance costs
    `cost_bps` basis points per unit of one-side turnover (sum of |weight change|).
    """

    rebalance: Literal["signal", "weekly", "monthly", "quarterly"] = "monthly"
    weighting: Literal["equal", "signal"] = "equal"
    cost_bps: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 5.0
    gross: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 2.0


class ResearchConfig(_Frozen):
    """The signal research toolkit (`atlas.quant.research`, Stage 17).

    Signals are evaluated on the first session of each `frequency` period for the members of
    `universe` at that date, against forward total returns over `horizons` sessions. A date
    needs `min_names` names with both a signal and a label (`min_group_names` inside one
    sector) for its IC and quantiles; quantile analysis uses `n_quantiles` buckets; the rank
    autocorrelation of the signal is measured at `autocorr_lags` evaluation dates; the IC
    chart shows a `rolling_ic_window`-date rolling mean. Tear sheets are written under
    `<data.lake_path>/<out_dir>/<signal name>/` (`out_dir` relative to the lake).
    """

    universe: Annotated[str, Field(min_length=1)] = "sp500"
    horizons: tuple[int, ...] = (1, 5, 21, 63)
    frequency: Literal["daily", "weekly", "monthly"] = "monthly"
    n_quantiles: Annotated[int, Field(ge=2)] = 5
    min_names: Annotated[int, Field(ge=3)] = 20
    min_group_names: Annotated[int, Field(ge=3)] = 10
    autocorr_lags: tuple[int, ...] = (1, 2, 3, 6, 12)
    rolling_ic_window: Annotated[int, Field(ge=1)] = 12
    out_dir: Annotated[str, Field(min_length=1)] = "runs/research"
    backtest: ResearchBacktestConfig = Field(default_factory=ResearchBacktestConfig)

    @field_validator("horizons")
    @classmethod
    def _horizons(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        return _ascending_positive("horizons", value)

    @field_validator("autocorr_lags")
    @classmethod
    def _lags(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        return _ascending_positive("autocorr_lags", value)

    @field_validator("out_dir")
    @classmethod
    def _relative_out_dir(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"out_dir must be a path inside the lake, got {value!r}")
        return value

    @model_validator(mode="after")
    def _min_names_covers_quantiles(self) -> Self:
        if self.min_names < self.n_quantiles:
            raise ValueError(
                f"min_names ({self.min_names}) must be at least n_quantiles ({self.n_quantiles})"
            )
        return self


class BlendWeights(_Frozen):
    """Weights of the realised-vol blend (Stage 18): the blended next-session variance is
    `ewma * var_ewma + garch * var_garch + realised * var_realised`. Each weight is a
    non-negative fraction and the three sum to 1."""

    ewma: Annotated[float, Field(ge=0, le=1)] = 0.4
    garch: Annotated[float, Field(ge=0, le=1)] = 0.4
    realised: Annotated[float, Field(ge=0, le=1)] = 0.2

    @model_validator(mode="after")
    def _sum_to_one(self) -> Self:
        total = self.ewma + self.garch + self.realised
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"blend weights must sum to 1, got {total}")
        return self


class VolatilityConfig(_Frozen):
    """Volatility forecasts (`atlas.quant.risk_model.volatility`, Stage 18).

    `method` is the default forecast (`ewma`, `garch`, `realised` or `blend`). Realised vol is
    the root mean square of the last `realised_window` daily returns; GARCH(1,1) is fitted only
    on at least `garch_min_obs` returns (fewer, or a failed fit, falls back to EWMA); the blend
    weights the three variances with `blend_weights`.
    """

    method: Literal["ewma", "garch", "realised", "blend"] = "blend"
    realised_window: Annotated[int, Field(ge=2)] = 21
    garch_min_obs: Annotated[int, Field(ge=30)] = 252
    blend_weights: BlendWeights = Field(default_factory=BlendWeights)


GICS_SECTORS: tuple[str, ...] = (
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
"""The 11 GICS sector names as the instruments table holds them."""


class EtfConfig(_Frozen):
    """ETF exposures by time-series regression on the factor returns (Stage 19): the last
    `window` sessions, at least `min_obs` of them observed, with a ridge penalty `ridge`
    relative to the mean diagonal of `FᵀF` (the sector constraint makes the factor returns
    nearly collinear). `universe` names the ETF list read by default from real data."""

    universe: Annotated[str, Field(min_length=1)] = "macro_etfs"
    window: Annotated[int, Field(ge=30)] = 252
    min_obs: Annotated[int, Field(ge=30)] = 126
    ridge: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 1.0e-3

    @model_validator(mode="after")
    def _min_obs_within_window(self) -> Self:
        if self.min_obs > self.window:
            raise ValueError(f"min_obs ({self.min_obs}) must not exceed window ({self.window})")
        return self


class FactorModelConfig(_Frozen):
    """The Barra-style factor risk model (`atlas.quant.risk_model.factor_model`, Stage 19).

    `sectors` are the sector factors (GICS names). The factor covariance is an EWMA with
    half-life `factor_half_life` sessions plus a Newey-West adjustment of `newey_west_lags`
    lags; specific variance is an EWMA (`specific_half_life`) of squared residuals whose vol is
    shrunk to the sector median with strength `shrinkage_q` (0 = no shrinkage) and floored at
    `specific_floor_vol` (daily). A daily cross-section needs `min_cross_section` names. Styles
    are winsorised at the `style_winsor_*` quantiles; a name missing more than
    `max_missing_styles` styles is dropped. The fundamental styles are refreshed from the factor
    panel every `anchor_months` month-ends (1 = monthly, 3 = quarter-ends; the panel is the
    slow part of a real-data build and fundamentals change quarterly). A model is built only
    when its factor history reaches within `max_stale_sessions` sessions of the as-of.
    """

    sectors: tuple[str, ...] = GICS_SECTORS
    factor_half_life: Annotated[int, Field(ge=1)] = 90
    newey_west_lags: Annotated[int, Field(ge=0)] = 2
    specific_half_life: Annotated[int, Field(ge=1)] = 90
    min_cross_section: Annotated[int, Field(ge=10)] = 100
    style_winsor_lower: Annotated[float, Field(ge=0, le=1)] = 0.01
    style_winsor_upper: Annotated[float, Field(ge=0, le=1)] = 0.99
    max_missing_styles: Annotated[int, Field(ge=0)] = 2
    shrinkage_q: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 1.0
    specific_floor_vol: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 0.002
    anchor_months: Annotated[int, Field(ge=1, le=12)] = 1
    max_stale_sessions: Annotated[int, Field(ge=0)] = 5
    etf: EtfConfig = Field(default_factory=EtfConfig)

    @model_validator(mode="after")
    def _checked(self) -> Self:
        if not self.sectors or len(set(self.sectors)) != len(self.sectors):
            raise ValueError(f"sectors must be non-empty and unique, got {self.sectors}")
        if not self.style_winsor_lower < self.style_winsor_upper:
            raise ValueError(
                f"style_winsor_lower ({self.style_winsor_lower}) must be below "
                f"style_winsor_upper ({self.style_winsor_upper})"
            )
        return self


class RiskModelConfig(_Frozen):
    """Covariance estimators and volatility forecasts (`atlas.quant.risk_model`, Stage 18).

    Units: daily simple returns in, daily covariances and vols out. A name needs `min_history`
    returns (and, under `missing="pairwise"`, `min_history` common sessions with every other
    name) to be estimated. `missing` is `pairwise` (each pair on its common sessions, then the
    nearest PSD matrix) or `complete` (only sessions where every name has a return). A
    covariance whose condition number exceeds `max_condition` has its small eigenvalues
    floored. `default_method` is the estimator `covariance_estimator()` builds by default;
    `ewma_lambda` is the EWMA decay per session (RiskMetrics daily 0.94).
    """

    min_history: Annotated[int, Field(ge=2)] = 63
    missing: Literal["pairwise", "complete"] = "pairwise"
    max_condition: Annotated[float, Field(gt=1, allow_inf_nan=False)] = 1.0e5
    default_method: Literal["sample", "ewma", "ledoit_wolf", "oas"] = "ledoit_wolf"
    ewma_lambda: _OpenFrac = 0.94
    volatility: VolatilityConfig = Field(default_factory=VolatilityConfig)
    factor_model: FactorModelConfig = Field(default_factory=FactorModelConfig)


class MetricsConfig(_Frozen):
    """The risk and performance metrics library (`atlas.quant.metrics`, Stage 20).

    Units: rates are annual fractions; windows and horizons are sessions. `risk_free_annual`
    is the default risk-free rate of Sharpe, alpha and beta (converted geometrically to a
    per-session rate); `rolling_window` the default window of rolling metrics;
    `tail_quantile` the lower quantile of the tail ratio; `omega_threshold_annual` the default
    Omega threshold. VaR / CVaR: `var_method` is the default method on a series
    (`historical`, `normal`, `cornish_fisher`), `var_min_obs` the fewest observations for any
    VaR (fewer raise `DataError`), `horizon_method` how a historical VaR reaches a multi-session
    horizon (`sqrt`: square-root-of-time; `overlapping`: overlapping h-session returns),
    `zero_mean` whether the parametric methods assume a zero mean, `mc_simulations` the Monte
    Carlo scenarios. `liquidity_horizons` are the sessions at which the liquid shares of a book
    are reported; an ADV read from the data must cover at least `adv_min_coverage` of its
    window's sessions to be trusted (a new listing or missing bars give no usable ADV).
    A Cornish-Fisher VaR is refused (`DataError`) unless its quantile map is increasing over
    `|z| <= cornish_fisher_z_max` standard deviations.
    Confidence levels, the ADV participation and the ADV window come from
    `limits` (one source).
    """

    risk_free_annual: Annotated[float, Field(ge=-0.05, le=0.5)] = 0.0
    rolling_window: Annotated[int, Field(ge=2)] = 63
    tail_quantile: Annotated[float, Field(gt=0, lt=0.5)] = 0.05
    omega_threshold_annual: Annotated[float, Field(gt=-1, allow_inf_nan=False)] = 0.0
    var_method: Literal["historical", "normal", "cornish_fisher"] = "historical"
    var_min_obs: Annotated[int, Field(ge=10)] = 63
    horizon_method: Literal["sqrt", "overlapping"] = "sqrt"
    zero_mean: bool = True
    mc_simulations: Annotated[int, Field(ge=1000)] = 20_000
    liquidity_horizons: tuple[int, ...] = (1, 3, 5)
    adv_min_coverage: Annotated[float, Field(gt=0, le=1)] = 0.9
    cornish_fisher_z_max: Annotated[float, Field(ge=3, le=8)] = 4.0

    @field_validator("liquidity_horizons")
    @classmethod
    def _horizons(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        return _ascending_positive("liquidity_horizons", value)


class RegimeVolConfig(_Frozen):
    """Volatility regime (`atlas.quant.regime.vol_trend`, Stage 21).

    Units: windows are sessions; thresholds are percentiles (fractions in (0, 1]); the floors
    are VIX index points and an annualised volatility fraction. The benchmark's 21-session
    realised vol and VIX are each ranked against their trailing `percentile_window` sessions
    (`percentile_min_obs` needed); the score is the larger rank. Score < `low_below` → LOW,
    < `high_from` → NORMAL, < `crisis_from` → HIGH, else CRISIS when VIX ≥ `crisis_vix_floor`
    or realised vol ≥ `crisis_rv_floor` (otherwise HIGH).
    """

    percentile_window: Annotated[int, Field(ge=20)] = 1260
    percentile_min_obs: Annotated[int, Field(ge=2)] = 252
    low_below: _OpenFrac = 0.25
    high_from: _OpenFrac = 0.75
    crisis_from: _Frac = 0.95
    crisis_vix_floor: _NonNeg = 30.0
    crisis_rv_floor: _NonNeg = 0.30

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.percentile_min_obs > self.percentile_window:
            raise ValueError(
                f"percentile_min_obs ({self.percentile_min_obs}) must not exceed "
                f"percentile_window ({self.percentile_window})"
            )
        if not self.low_below < self.high_from < self.crisis_from:
            raise ValueError("low_below < high_from < crisis_from must be ascending")
        return self


class RegimeTrendConfig(_Frozen):
    """Trend regime (Stage 21). Three votes (benchmark above its 200-session SMA, 12-1
    momentum > 0, breadth > `breadth_bull_above`); breadth needs `breadth_min_names` members
    with a value. Bullish share of the available votes ≥ `up_share` → UP, ≤ `down_share` →
    DOWN, else NEUTRAL. Shares are fractions."""

    breadth_min_names: Annotated[int, Field(ge=1)] = 20
    breadth_bull_above: _OpenFrac = 0.5
    up_share: Annotated[float, Field(gt=0.5, le=1)] = 1.0
    down_share: Annotated[float, Field(ge=0, lt=0.5)] = 0.34


class RegimeHMMConfig(_Frozen):
    """Statistical regime: Gaussian HMM (hmmlearn) on an expanding window refitted monthly
    (Stage 21). `n_states` 2 or 3, ordered by volatility; a fit needs `min_obs` feature rows
    (sessions); `n_init` seeded starts (`seed + i`), each up to `n_iter` EM iterations with
    tolerance `tol` (log-likelihood); a start with a state holding less than
    `min_state_share` of the rows (or a singular covariance) is degenerate and skipped. The
    risk scalar treats P(turbulent) ≥ `turbulent_prob` as the turbulent state."""

    n_states: Annotated[int, Field(ge=2, le=3)] = 3
    min_obs: Annotated[int, Field(ge=50)] = 504
    n_init: Annotated[int, Field(ge=1)] = 5
    n_iter: Annotated[int, Field(ge=10)] = 200
    tol: Annotated[float, Field(gt=0)] = 1.0e-4
    seed: Annotated[int, Field(ge=0)] = 21
    turbulent_prob: _OpenFrac = 0.5
    min_state_share: Annotated[float, Field(ge=0, lt=0.5)] = 0.01


class RegimeCreditConfig(_Frozen):
    """Credit stress inputs (Stage 21). FRED serves the HY OAS (`oas_series`, percent) only for
    the last 3 years, so a price proxy is used where it is short: the duration-hedged excess
    log return of `proxy_long` over `proxy_hedge` (`ln r_long - proxy_hedge_ratio · ln
    r_hedge`); its negative running sum is the spread-widening level. The z-score uses the
    trailing `zscore_window` observations; `source` `auto` takes the OAS when that many OAS
    observations are known, else the proxy (`oas` / `proxy` force one). Stress when
    z > `zscore_threshold`."""

    source: Literal["auto", "oas", "proxy"] = "auto"
    oas_series: str = "BAMLH0A0HYM2"
    proxy_long: str = "HYG"
    proxy_hedge: str = "IEF"
    proxy_hedge_ratio: _NonNeg = 0.5
    zscore_window: Annotated[int, Field(ge=20)] = 252
    zscore_threshold: Annotated[float, Field(gt=0)] = 2.0


class RegimeStressConfig(_Frozen):
    """Stress flag (Stage 21): credit z-score above its threshold or the Chicago Fed NFCI
    (index, 0 = average conditions) above `nfci_threshold`."""

    nfci_threshold: Annotated[float, Field(allow_inf_nan=False)] = 0.0


class RegimeMacroConfig(_Frozen):
    """Macro quadrant (Stage 21). Claims trend = mean of the last `claims_short_weeks` weekly
    initial claims minus the mean of the last `claims_long_weeks`; FRED observations within
    `lookback_days` calendar days before the decision day are read."""

    claims_short_weeks: Annotated[int, Field(ge=1)] = 4
    claims_long_weeks: Annotated[int, Field(ge=2)] = 26
    lookback_days: Annotated[int, Field(ge=400)] = 800

    @model_validator(mode="after")
    def _weeks(self) -> Self:
        if self.claims_short_weeks >= self.claims_long_weeks:
            raise ValueError("claims_short_weeks must be below claims_long_weeks")
        return self


class RegimeVolScalars(_Frozen):
    """Risk-scalar multiplier per volatility regime (fractions in (0, 1])."""

    low: _Frac = 1.0
    normal: _Frac = 1.0
    high: _Frac = 0.7
    crisis: _Frac = 0.4


class RegimeTrendScalars(_Frozen):
    """Risk-scalar multiplier per trend regime (fractions in (0, 1])."""

    up: _Frac = 1.0
    neutral: _Frac = 0.9
    down: _Frac = 0.75


class RegimeQuadrantScalars(_Frozen):
    """Risk-scalar multiplier per macro quadrant (fractions in (0, 1])."""

    goldilocks: _Frac = 1.0
    reflation: _Frac = 1.0
    stagflation: _Frac = 0.85
    deflation: _Frac = 0.9


class RegimeScalarConfig(_Frozen):
    """The deterministic `risk_scalar` mapping (Stage 21): the product of the multipliers of
    the vol regime, the trend regime, the stress flag (`stress` when flagged), the HMM
    (`hmm_turbulent` when P(turbulent) ≥ `hmm.turbulent_prob`) and the macro quadrant; a
    missing component multiplies by 1; the product is clipped to [`floor`, `cap`] ⊂
    [0.25, 1]. Vol and trend tables must be monotone (a worse label never raises it)."""

    floor: Annotated[float, Field(ge=0.25, le=1)] = 0.25
    cap: Annotated[float, Field(ge=0.25, le=1)] = 1.0
    vol: RegimeVolScalars = Field(default_factory=RegimeVolScalars)
    trend: RegimeTrendScalars = Field(default_factory=RegimeTrendScalars)
    stress: _Frac = 0.6
    hmm_turbulent: _Frac = 0.85
    quadrant: RegimeQuadrantScalars = Field(default_factory=RegimeQuadrantScalars)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.floor > self.cap:
            raise ValueError(f"floor ({self.floor}) must not exceed cap ({self.cap})")
        v, t = self.vol, self.trend
        if not v.low >= v.normal >= v.high >= v.crisis:
            raise ValueError("vol multipliers must be monotone: low >= normal >= high >= crisis")
        if not t.up >= t.neutral >= t.down:
            raise ValueError("trend multipliers must be monotone: up >= neutral >= down")
        return self


class RegimeConfig(_Frozen):
    """Market-regime detection (`atlas.quant.regime`, Stage 21). The benchmark is
    `fund.benchmark`; breadth uses the point-in-time members of universe `universe`; the HMM
    window starts at `train_start`; FRED daily series are carried forward over at most
    `max_fill_sessions` sessions without an observation."""

    universe: Annotated[str, Field(min_length=1)] = "sp500"
    train_start: date = date(2015, 1, 1)
    max_fill_sessions: Annotated[int, Field(ge=0)] = 5
    vol: RegimeVolConfig = Field(default_factory=RegimeVolConfig)
    trend: RegimeTrendConfig = Field(default_factory=RegimeTrendConfig)
    hmm: RegimeHMMConfig = Field(default_factory=RegimeHMMConfig)
    credit: RegimeCreditConfig = Field(default_factory=RegimeCreditConfig)
    stress: RegimeStressConfig = Field(default_factory=RegimeStressConfig)
    macro: RegimeMacroConfig = Field(default_factory=RegimeMacroConfig)
    risk_scalar: RegimeScalarConfig = Field(default_factory=RegimeScalarConfig)


class QuantConfig(_Frozen):
    """Quant engine settings (Phase 2)."""

    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    factors: FactorsConfig = Field(default_factory=FactorsConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    risk_model: RiskModelConfig = Field(default_factory=RiskModelConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)


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
    quant: QuantConfig = Field(default_factory=QuantConfig)
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
    """`yaml.SafeLoader` that rejects a key repeated within one mapping.

    YAML merge keys work as in YAML 1.1: `<<: *anchor` (or a list of anchors, earlier ones
    winning) supplies keys that the mapping's own keys override. Only keys written in the
    mapping itself, `<<` included, count as repeats.
    """


_MERGE_TAG = "tag:yaml.org,2002:merge"


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.Node) -> dict[Any, Any]:
    if not isinstance(node, yaml.MappingNode):
        raise yaml.constructor.ConstructorError(
            None, None, f"expected a mapping, got {type(node).__name__}", node.start_mark
        )
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = "<<" if key_node.tag == _MERGE_TAG else loader.construct_object(key_node)
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
    loader.flatten_mapping(node)
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
    """Parse one YAML file under `config/` with the loader's rules: UTF-8, no repeated keys
    (YAML merge keys allowed), a mapping at the top. A missing or empty file is `{}`; errors
    raise `ConfigError` naming the file. For reference files that `load_config` does not read
    (Stage 08 ticker renames).
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
_SECTIONS = frozenset({"fund", "limits", "pods", "data", "quant", "llm", "backtest", "calendar"})
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


def _canonical_decimals(value: Any) -> Any:
    """`value` with every `Decimal` as a plain fixed-point string without trailing zeros, so
    equal amounts written differently (`1.0e7`, `10000000.00`) dump alike; zero is `"0"`."""
    if isinstance(value, Decimal):
        return "0" if value.is_zero() else format(value.normalize(), "f")
    if isinstance(value, Mapping):
        return {key: _canonical_decimals(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical_decimals(item) for item in value]
    return value


def config_fingerprint(cfg: AtlasConfig) -> str:
    """Stable 12-hex-char tag of every non-secret setting, for labelling runs.

    SHA-256 of the canonical JSON dump (sorted keys, Decimals by value) with `secrets`
    excluded, so the same settings give the same tag on any machine and in any process.
    """
    dump = _canonical_decimals(cfg.model_dump(exclude={"secrets"}))
    payload = json.dumps(to_jsonable_python(dump), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]
