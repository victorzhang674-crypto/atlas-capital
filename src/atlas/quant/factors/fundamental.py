"""Raw fundamental factors: market cap and EV from point-in-time shares, and the 17 raw
factor values of each symbol (value, quality, growth, leverage, size, issuance).

Inputs are what `DataView` returns at an as-of (`fundamentals_panel`, `fundamentals_history`,
`prices`, `split_factors`), so every value uses only filings and bars known at that as-of.
`factor_panel(view)` reads the view and runs the whole pipeline (raw values, then
`atlas.quant.factors.cleaning.clean_factors`); the other functions are pure.

Units: USD for prices, caps, EV and statement lines; shares for counts; factors are fractions
(yields, returns on capital, growth rates as simple fractions, CAGR per year), ratios (debt
/ equity, net debt / EBIT), `size` is the natural log of USD market cap. A value whose
denominator is missing or not positive is NaN (never a guess); ±inf never appears.
"""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, FactorsConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.pit import AsOf
from atlas.data.view import DataView
from atlas.quant.factors.cleaning import clean_factors

FactorGroup = Literal["value", "quality", "growth", "leverage", "size", "issuance"]

GROUPS: tuple[FactorGroup, ...] = ("value", "quality", "growth", "leverage", "size", "issuance")
"""Factor groups, in the order of `FACTORS`."""


@dataclass(frozen=True)
class FactorSpec:
    """One raw factor: `name` (snake_case), `group`, `unit` (`fraction`, `fraction_per_year`,
    `ratio`, `log_usd`) and a one-line `description` of its definition and orientation."""

    name: str
    group: FactorGroup
    unit: str
    description: str


FACTORS: tuple[FactorSpec, ...] = (
    FactorSpec("earnings_yield", "value", "fraction", "E/P: TTM net income / market cap"),
    FactorSpec("book_yield", "value", "fraction", "B/P: stockholders' equity / market cap"),
    FactorSpec("fcf_yield", "value", "fraction", "TTM (operating cash flow - capex) / market cap"),
    FactorSpec("ebit_ev", "value", "fraction", "TTM operating income / enterprise value"),
    FactorSpec("sales_ev", "value", "ratio", "TTM revenue / enterprise value"),
    FactorSpec("roe", "quality", "fraction", "TTM net income / latest equity (Stage 09)"),
    FactorSpec("roa", "quality", "fraction", "TTM net income / latest total assets (Stage 09)"),
    FactorSpec("gross_profitability", "quality", "fraction", "TTM gross profit / total assets"),
    FactorSpec(
        "accruals", "quality", "fraction", "negated accruals: -(TTM net income - TTM OCF) / assets"
    ),
    FactorSpec(
        "margin_stability",
        "quality",
        "fraction",
        "-std (ddof 1) of the quarterly gross margin over the last margin_quarters quarters",
    ),
    FactorSpec("revenue_growth", "growth", "fraction", "TTM revenue / TTM revenue a year ago - 1"),
    FactorSpec(
        "eps_growth", "growth", "fraction", "(TTM EPS - TTM EPS a year ago) / |TTM EPS a year ago|"
    ),
    FactorSpec(
        "revenue_cagr_3y",
        "growth",
        "fraction_per_year",
        "(TTM revenue / TTM revenue three years ago)^(1/3) - 1",
    ),
    FactorSpec("debt_to_equity", "leverage", "ratio", "total debt / equity (Stage 09)"),
    FactorSpec(
        "net_debt_ebitda",
        "leverage",
        "ratio",
        "(total debt - cash) / TTM operating income (EBIT: an EBITDA proxy, no D&A in the map)",
    ),
    FactorSpec("size", "size", "log_usd", "ln(market cap in USD)"),
    FactorSpec(
        "net_issuance",
        "issuance",
        "fraction",
        "negated 12-month net share issuance: -(shares now / shares a year ago - 1)",
    ),
)
"""The raw factors, by group (the brief's In scope list)."""

FACTOR_NAMES: tuple[str, ...] = tuple(f.name for f in FACTORS)
"""Names of `FACTORS`, the columns of `raw_factors`."""

HISTORY_CONCEPTS = ("revenue", "gross_profit", "eps_diluted", "shares_outstanding")
"""Concepts `factor_panel` reads with `DataView.fundamentals_history`."""

MARKET_COLUMNS = (
    "price",
    "price_date",
    "shares",
    "shares_date",
    "market_cap",
    "enterprise_value",
)
"""Columns of `compute_market`: raw close (USD) and its session, the latest share count in
today's shares and its date, market cap and enterprise value (USD)."""

SHARES_COLUMNS = ("symbol", "period_end", "shares")
"""Columns of `adjusted_shares`."""

_log = get_logger(__name__)

_YEAR = 365


def _factor_at(splits: pd.DataFrame, symbol: str, day: date) -> float:
    """The split factor of `symbol` at the last session on or before `day` (the first
    session's when `day` precedes the window; 1.0 when the symbol has no column)."""
    if symbol not in splits.columns or splits.empty:
        return 1.0
    column = splits[symbol]
    pos = int(column.index.searchsorted(pd.Timestamp(day), side="right")) - 1
    return float(column.iloc[max(pos, 0)])


def adjusted_shares(history: pd.DataFrame, splits: pd.DataFrame) -> pd.DataFrame:
    """The `shares_outstanding` balances of `history` (`DataView.fundamentals_history` rows)
    in today's shares: each count divided by `splits`' factor (`DataView.split_factors`) at
    its date, so a count dated before a split is restated. `SHARES_COLUMNS`, sorted by
    symbol and date."""
    rows = history[history["concept"] == "shares_outstanding"]
    shares = [
        float(v) / _factor_at(splits, s, d)
        for s, d, v in zip(rows["symbol"], rows["period_end"], rows["value"], strict=True)
    ]
    out = pd.DataFrame(
        {
            "symbol": rows["symbol"].astype("str").to_numpy(),
            "period_end": rows["period_end"].to_numpy(dtype="object"),
            "shares": np.asarray(shares, dtype="float64"),
        }
    )
    return out.sort_values(["symbol", "period_end"], kind="stable", ignore_index=True)


def _floats(column: pd.Series) -> dict[str, float]:
    """`column` as a symbol -> float mapping."""
    return {str(k): float(v) for k, v in column.astype("float64").items()}


def _dates(column: pd.Series) -> dict[str, date]:
    """`column` (of `datetime.date`) as a symbol -> date mapping."""
    return {str(k): v for k, v in column.items()}


def _positive(x: float) -> bool:
    return not math.isnan(x) and x > 0


def _ratio(num: float, den: float) -> float:
    """`num / den` when both are known and `den > 0`, else NaN."""
    return num / den if not math.isnan(num) and _positive(den) else math.nan


def compute_market(
    prices: pd.DataFrame,
    shares: pd.DataFrame,
    splits: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    day: date,
    max_staleness_days: int,
) -> pd.DataFrame:
    """Market cap and EV of each symbol of `panel` (`MARKET_COLUMNS`, index `symbol`).

    `price` = raw close of the symbol's latest bar in `prices` (`DataView.prices` rows of
    the recent window), `shares` = its latest count in `shares` (`adjusted_shares`, today's
    shares) dated within `max_staleness_days` of `day`. Market cap = price x the count in the
    price session's shares (today's count x the split factor at the price's session, so a
    split after the last bar does not double the cap); EV = market cap + `total_debt` -
    `cash_and_equivalents` (NaN when either is missing). NaN when an input is missing or not
    positive.
    """
    floor = day - timedelta(days=max_staleness_days)
    latest_bar = (
        prices.sort_values(["symbol", "date"], kind="stable").groupby("symbol", sort=False).last()
    )
    closes = _floats(latest_bar["close"])
    bar_days = _dates(latest_bar["date"])
    fresh = shares[(shares["period_end"] >= floor) & (shares["period_end"] <= day)]
    latest_count = fresh.groupby("symbol", sort=False).last()
    counts = _floats(latest_count["shares"])
    count_days = _dates(latest_count["period_end"])
    debts, cashes = _floats(panel["total_debt"]), _floats(panel["cash_and_equivalents"])
    rows = []
    for symbol in panel.index:
        price, price_date = closes.get(symbol, math.nan), bar_days.get(symbol)
        count, count_date = counts.get(symbol, math.nan), count_days.get(symbol)
        cap = math.nan
        if _positive(price) and _positive(count) and price_date is not None:
            cap = price * count * _factor_at(splits, symbol, price_date)
        debt, cash = debts[symbol], cashes[symbol]
        ev = cap + debt - cash if not (math.isnan(debt) or math.isnan(cash)) else math.nan
        rows.append((price, price_date, count, count_date, cap, ev))
    index = pd.Index(panel.index, name="symbol")
    out = pd.DataFrame(rows, columns=list(MARKET_COLUMNS), index=index)
    for col in ("price", "shares", "market_cap", "enterprise_value"):
        out[col] = out[col].astype("float64")
    for col in ("price_date", "shares_date"):
        out[col] = out[col].astype("object").where(out[col].notna(), None)
    return out


def _nearest(ends: pd.Series, values: pd.Series, target: date, tolerance: int) -> float:
    """The value at the end nearest `target` within `tolerance` days, else NaN."""
    if ends.empty:
        return math.nan
    gaps = np.array([abs((e - target).days) for e in ends])
    best = int(np.argmin(gaps))
    return float(values.iloc[best]) if gaps[best] <= tolerance else math.nan


def _ttm_points(own: pd.DataFrame, concept: str) -> tuple[pd.Series, pd.Series]:
    rows = own[(own["concept"] == concept) & own["ttm"].notna()].sort_values("period_end")
    return rows["period_end"].reset_index(drop=True), rows["ttm"].reset_index(drop=True)


def _growth(own: pd.DataFrame, floor: date, config: FactorsConfig) -> dict[str, float]:
    tol = config.period_tolerance_days
    out = {"revenue_growth": math.nan, "eps_growth": math.nan, "revenue_cagr_3y": math.nan}
    ends, values = _ttm_points(own, "revenue")
    if not ends.empty and ends.iloc[-1] >= floor:
        e0, r0 = ends.iloc[-1], float(values.iloc[-1])
        r1 = _nearest(ends, values, e0 - timedelta(days=_YEAR), tol)
        r3 = _nearest(ends, values, e0 - timedelta(days=3 * _YEAR), tol)
        if not math.isnan(r0) and _positive(r1):
            out["revenue_growth"] = r0 / r1 - 1
        if _positive(r0) and _positive(r3):
            out["revenue_cagr_3y"] = (r0 / r3) ** (1 / 3) - 1
    ends, values = _ttm_points(own, "eps_diluted")
    if not ends.empty and ends.iloc[-1] >= floor:
        e0, eps0 = ends.iloc[-1], float(values.iloc[-1])
        eps1 = _nearest(ends, values, e0 - timedelta(days=_YEAR), tol)
        if not math.isnan(eps1) and abs(eps1) >= config.min_abs_eps:
            out["eps_growth"] = (eps0 - eps1) / abs(eps1)
    return out


def _margin_stability(
    own: pd.DataFrame, floor: date, config: FactorsConfig, max_quarter_days: int
) -> float:
    def quarters(concept: str) -> pd.Series:
        rows = own[(own["concept"] == concept) & own["value"].notna()]
        return pd.Series(rows["value"].to_numpy(dtype="float64"), index=list(rows["period_end"]))

    revenue, gross = quarters("revenue"), quarters("gross_profit")
    common = sorted(set(revenue.index) & set(gross.index))
    n = config.margin_quarters
    if len(common) < n:
        return math.nan
    last = common[-n:]
    span = (last[-1] - last[0]).days
    if last[-1] < floor or span > (n - 1) * max_quarter_days + config.period_tolerance_days:
        return math.nan
    rev = revenue.loc[last].to_numpy()
    if (rev <= 0).any():
        return math.nan
    margins = gross.loc[last].to_numpy() / rev
    return -float(np.std(margins, ddof=1))


def _issuance(own: pd.DataFrame, floor: date, day: date, config: FactorsConfig) -> float:
    rows = own[(own["period_end"] <= day)].sort_values("period_end")
    if rows.empty or rows["period_end"].iloc[-1] < floor:
        return math.nan
    p0, s0 = rows["period_end"].iloc[-1], float(rows["shares"].iloc[-1])
    earlier = rows.iloc[:-1]
    s1 = _nearest(
        earlier["period_end"], earlier["shares"], p0 - timedelta(days=_YEAR),
        config.period_tolerance_days,
    )  # fmt: skip
    return -(s0 / s1 - 1) if _positive(s0) and _positive(s1) else math.nan


def _value(row: Mapping[str, float], name: str) -> float:
    return float(row.get(name, math.nan))


def raw_factors(
    panel: pd.DataFrame,
    history: pd.DataFrame,
    shares: pd.DataFrame,
    market: pd.DataFrame,
    *,
    day: date,
    config: FactorsConfig,
    max_staleness_days: int,
    max_quarter_days: int,
) -> pd.DataFrame:
    """The raw factors (`FACTORS`, definitions in their specs) of each symbol of `panel`:
    index `symbol` (panel order), one float64 column per `FACTOR_NAMES` entry.

    `panel` = `DataView.fundamentals_panel` (stale metrics already NaN), `history` =
    `DataView.fundamentals_history` of `HISTORY_CONCEPTS`, `shares` = `adjusted_shares`,
    `market` = `compute_market`. Growth, margin stability and issuance need their latest
    period within `max_staleness_days` of `day`; the year-ago (three-years-ago) period is the
    one ending nearest 365 (1095) days before the latest, within
    `config.period_tolerance_days`. Margin stability's quarters may span at most
    (`margin_quarters` - 1) x `max_quarter_days` + the tolerance (`max_quarter_days` = the
    longest fiscal quarter, `data.fundamentals.quarter_days[1]`). A missing input or a
    denominator that is missing or not positive gives NaN.
    """
    floor = day - timedelta(days=max_staleness_days)
    hist_by = dict(tuple(history.groupby("symbol", sort=False)))
    shares_by = dict(tuple(shares.groupby("symbol", sort=False)))
    empty_hist, empty_shares = history.iloc[0:0], shares.iloc[0:0]
    caps, evs = _floats(market["market_cap"]), _floats(market["enterprise_value"])
    rows = []
    for symbol in panel.index:
        p = panel.loc[symbol].to_dict()
        cap, ev = caps[symbol], evs[symbol]
        assets = _value(p, "total_assets")
        ni, ocf = _value(p, "net_income_ttm"), _value(p, "operating_cash_flow_ttm")
        ebit = _value(p, "operating_income_ttm")
        debt, cash = _value(p, "total_debt"), _value(p, "cash_and_equivalents")
        own = hist_by.get(symbol, empty_hist)
        values = {
            "earnings_yield": _ratio(ni, cap),
            "book_yield": _ratio(_value(p, "stockholders_equity"), cap),
            "fcf_yield": _ratio(_value(p, "fcf_ttm"), cap),
            "ebit_ev": _ratio(ebit, ev),
            "sales_ev": _ratio(_value(p, "revenue_ttm"), ev),
            "roe": _value(p, "roe"),
            "roa": _value(p, "roa"),
            "gross_profitability": _ratio(_value(p, "gross_profit_ttm"), assets),
            "accruals": -_ratio(ni - ocf, assets),
            "margin_stability": _margin_stability(own, floor, config, max_quarter_days),
            **_growth(own, floor, config),
            "debt_to_equity": _value(p, "debt_to_equity"),
            "net_debt_ebitda": _ratio(debt - cash, ebit),
            "size": math.log(cap) if _positive(cap) else math.nan,
            "net_issuance": _issuance(shares_by.get(symbol, empty_shares), floor, day, config),
        }
        rows.append([values[name] for name in FACTOR_NAMES])
    out = pd.DataFrame(
        rows, columns=list(FACTOR_NAMES), index=pd.Index(list(panel.index), name="symbol")
    ).astype("float64")
    return out.replace([np.inf, -np.inf], np.nan)


# --- the pipeline through DataView ------------------------------------------------------------


@dataclass(frozen=True)
class FactorPanel:
    """Factor exposures of a cross-section at one as-of.

    `raw` = `raw_factors` (index symbol, `FACTOR_NAMES` columns, units per `FACTORS`);
    `exposures` = the cleaned z-scores (`clean_factors`); `filled` = `True` where a missing
    raw value was filled with its sector median; `market` = `compute_market`; `sectors` =
    each name's GICS sector (`None` = unknown; today's sectors, not point-in-time);
    `config_hash` tags the settings that produced it (`factor_config_hash`).
    """

    as_of: AsOf
    raw: pd.DataFrame
    exposures: pd.DataFrame
    filled: pd.DataFrame
    market: pd.DataFrame
    sectors: pd.Series
    config_hash: str


def factor_config_hash(config: AtlasConfig, factors: FactorsConfig | None = None) -> str:
    """A 12-hex-char tag of every setting the factor values depend on: `quant.factors` (or
    `factors`), the fundamentals' quarter/year lengths and staleness, and `FACTOR_NAMES`."""
    fc = factors if factors is not None else config.quant.factors
    fund = config.data.fundamentals
    payload = {
        "factors": fc.model_dump(mode="json"),
        "fundamentals": {
            "quarter_days": list(fund.quarter_days),
            "annual_days": list(fund.annual_days),
            "max_staleness_days": fund.max_staleness_days,
        },
        "names": list(FACTOR_NAMES),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _split_lookback(view: DataView, config: FactorsConfig, staleness: int) -> int:
    """Sessions of split factors that cover every share count `raw_factors` may use: back
    to `staleness` + one year + the tolerance before the as-of day."""
    cal = TradingCalendar(config=view.config.calendar)
    last = view.sessions(1)[-1]
    back = timedelta(days=staleness + _YEAR + config.period_tolerance_days)
    start = max(cal.start, view.day - back)
    return max(len(cal.sessions(start, last)), 1)


def panel_sectors(view: DataView, symbols: Sequence[str]) -> pd.Series:
    """Each name's GICS sector (`None` when its instrument row has none or it has no row,
    logged). Raises `DataError` when no name has an instrument row: the instruments table is
    empty or not synced, and every name would silently lose sector neutralisation."""
    out: dict[str, str | None] = {}
    missing: list[str] = []
    for symbol in symbols:
        try:
            out[symbol] = view.instrument(symbol).sector
        except DataError as exc:  # not in the instruments table: an unknown sector, logged
            _log.warning("factors.no_instrument", symbol=symbol, error=str(exc))
            out[symbol] = None
            missing.append(symbol)
    if symbols and len(missing) == len(symbols):
        raise DataError(
            f"none of the {len(symbols)} names has a row in the instruments table: sync the "
            "reference data (instruments) before building factor panels"
        )
    return pd.Series(out, dtype="object", index=pd.Index(list(symbols), name="symbol"))


def _empty_panel(view: DataView, config_hash: str) -> FactorPanel:
    index = pd.Index([], name="symbol", dtype="str")
    frame = pd.DataFrame(index=index, columns=list(FACTOR_NAMES), dtype="float64")
    return FactorPanel(
        as_of=view.as_of,
        raw=frame,
        exposures=frame.copy(),
        filled=frame.astype("bool"),
        market=pd.DataFrame(index=index, columns=list(MARKET_COLUMNS)),
        sectors=pd.Series(index=index, dtype="object"),
        config_hash=config_hash,
    )


def factor_panel(
    view: DataView, symbols: Sequence[str] | None = None, *, config: FactorsConfig | None = None
) -> FactorPanel:
    """The fundamental factor panel of `symbols` (default: the members of
    `quant.factors.universe` at the view's as-of) at `view.as_of`.

    Everything is read through `view`, so only filings, bars and splits known at its as-of
    are used: `fundamentals_panel`, `fundamentals_history` (`HISTORY_CONCEPTS`),
    `split_factors` (back far enough for every share count used), and `prices` over the last
    `max_price_age_sessions` sessions. Blocked symbols are left out. `config` overrides the
    view's `quant.factors`.
    """
    fc = config if config is not None else view.config.quant.factors
    tag = factor_config_hash(view.config, fc)
    wanted = view.universe(fc.universe) if symbols is None else list(symbols)
    if not wanted:
        return _empty_panel(view, tag)
    panel = view.fundamentals_panel(wanted)
    names = list(panel.index)
    if not names:
        return _empty_panel(view, tag)
    staleness = view.config.data.fundamentals.max_staleness_days
    history = view.fundamentals_history(names, HISTORY_CONCEPTS)
    splits = view.split_factors(names, _split_lookback(view, fc, staleness))
    prices = view.prices(names, fc.max_price_age_sessions)
    shares = adjusted_shares(history, splits)
    market = compute_market(
        prices, shares, splits, panel, day=view.day, max_staleness_days=staleness
    )
    raw = raw_factors(
        panel,
        history,
        shares,
        market,
        day=view.day,
        config=fc,
        max_staleness_days=staleness,
        max_quarter_days=view.config.data.fundamentals.quarter_days[1],
    )
    sectors = panel_sectors(view, names)
    cleaned = clean_factors(raw, sectors, fc)
    return FactorPanel(
        as_of=view.as_of,
        raw=raw,
        exposures=cleaned.exposures,
        filled=cleaned.filled,
        market=market,
        sectors=sectors,
        config_hash=tag,
    )
