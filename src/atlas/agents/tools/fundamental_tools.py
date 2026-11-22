"""The fundamental analyst's tools (Stage 29): `get_fundamentals_summary`, `get_factor_ranks`,
`get_valuation`, `get_red_flags` (`FUNDAMENTAL_TOOLS`), the pure red-flag rules and
`prefetch`. Of the tools package it imports only `registry.py`; the analyst joins
these tools with the built-in ones (`atlas.agents.research.fundamental_registry`).

Every tool reads only through the context's `DataView` (point in time) and returns `Evidence`,
one item per group of numbers the analyst may cite, with a stable id:

- `fsum:<SYM>:<period end>:<concept | margins | growth>`: the last quarters of a statement
  line with its TTM, and the TTM margins and growth (filed numbers; the item's time is the
  latest `available_at` of the rows it uses);
- `frank:<SYM>:<day>:<value | quality | growth | leverage>`: percentiles against the sector
  peers (the Stage 16 factor panel of the members of `quant.factors.universe` in the
  symbol's GICS sector);
- `val:<SYM>:<day>:<market | pe | ps | pb | ev_ebitda | fcf_yield>`: today's multiples,
  their own history at fiscal quarter ends and the sector median;
- `rflag:<SYM>:<day>:<check>`: one deterministic red-flag check (FLAG, CLEAR or NOT
  EVALUATED) with its numbers;
- `gap:<SYM>:<day>:<tool>`: what `prefetch` could not gather, and why.

Items computed from prices or a cross-section (`frank:`, `val:`, `rflag:`, `gap:`) are timed
one microsecond before the decision cutoff: computed at the decision moment from data known
then (the `FactorStore` convention).

Units in the texts: USD (K, M, B, T = 1e3, 1e6, 1e9, 1e12), USD per share for EPS, share
counts in today's shares (after splits); margins, growth and yields in percent; multiples as
plain numbers. A ratio whose denominator is missing, zero or negative is "n/m" (never a
guess). Windows and thresholds: `agents.fundamental` (`config/fund.yaml`).
"""

import functools
import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Literal, cast

import pandas as pd
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from atlas.agents.context import AgentContext
from atlas.agents.tools.registry import Tool
from atlas.core.calendar import TradingCalendar
from atlas.core.config import FundamentalAnalystConfig, RedFlagsConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.types.instruments import normalize_symbol
from atlas.core.types.research import Evidence
from atlas.data.view import DataView
from atlas.quant.factors import FACTORS, adjusted_shares, factor_panel, style_composites
from atlas.quant.factors.fundamental import FactorPanel

# --- arguments and formatting -----------------------------------------------------------------


class FundamentalArgs(BaseModel):
    """Arguments of the fundamental tools: one ticker (normalised; a malformed one fails
    validation, so the model gets an error text)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Annotated[
        str,
        Field(min_length=1, max_length=20, description="Ticker symbol, for example AAPL"),
        AfterValidator(normalize_symbol),
    ]


_log = get_logger(__name__)

NM = "n/m"
"""What a ratio with a missing, zero or negative denominator reads as."""

_EPSILON = timedelta(microseconds=1)
_YEAR = 365


def _finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)


def fmt_usd(x: float) -> str:
    """`USD 90.75B`: four significant digits with K, M, B or T; `n/a` when not finite."""
    if not _finite(x):
        return "n/a"
    sign, a = ("-" if x < 0 else ""), abs(x)
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= scale:
            return f"USD {sign}{a / scale:.4g}{suffix}"
    return f"USD {sign}{a:.4g}"


def fmt_pct(x: float, *, signed: bool = False) -> str:
    """A fraction as a percentage with one decimal (`+5.2%` when `signed`); `n/m` when not
    finite."""
    if not _finite(x):
        return NM
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def fmt_points(x: float) -> str:
    """A difference of two fractions in percentage points: `+30.0 points`; `n/m` when not
    finite."""
    return f"{x * 100:+.1f} points" if _finite(x) else NM


def fmt_num(x: float, digits: int = 1) -> str:
    """A plain number with `digits` decimals; `n/m` when not finite."""
    return f"{x:.{digits}f}" if _finite(x) else NM


def fmt_shares(x: float) -> str:
    """A share count: `15.46B shares`."""
    return fmt_usd(x).replace("USD ", "") + " shares" if _finite(x) else "n/a"


def _ratio(num: float, den: float) -> float:
    """`num / den`, NaN when either is missing or `den` is not positive."""
    if not (_finite(num) and _finite(den)) or den <= 0:
        return math.nan
    return num / den


def _growth(now: float, before: float) -> float:
    """`now / before - 1`, NaN when either is missing or `before` is not positive."""
    return _ratio(now, before) - 1.0


# --- red-flag rules (pure) --------------------------------------------------------------------

FlagStatus = Literal["FLAG", "CLEAR", "NOT EVALUATED"]

RED_FLAG_CHECKS = ("accruals", "receivables", "fcf_debt", "dilution", "going_concern")
"""The red-flag checks, in report order."""


@dataclass(frozen=True)
class RedFlag:
    """The outcome of one red-flag check: `check` (one of `RED_FLAG_CHECKS`), `status` and a
    one-line `detail` with the numbers it rests on."""

    check: str
    status: FlagStatus
    detail: str


def accruals_rule(ratio: float, ratio_year_ago: float, config: RedFlagsConfig) -> RedFlag:
    """Accruals spike: `ratio` = (TTM net income - TTM operating cash flow) / total assets.
    FLAG when it is above `accruals_max`, or when it rose by more than `accruals_jump` from a
    year earlier to a positive level; NOT EVALUATED without `ratio`."""
    check = "accruals"
    if not _finite(ratio):
        return RedFlag(check, "NOT EVALUATED", "net income, operating cash flow or assets missing")
    before = f"; a year earlier {fmt_pct(ratio_year_ago)}" if _finite(ratio_year_ago) else ""
    detail = (
        f"accruals (TTM net income - TTM operating cash flow) / total assets = "
        f"{fmt_pct(ratio)}{before} (flag above {fmt_pct(config.accruals_max)}, or a rise of "
        f"more than {fmt_points(config.accruals_jump)[1:]} to a positive level)"
    )
    jumped = _finite(ratio_year_ago) and ratio > 0 and ratio - ratio_year_ago > config.accruals_jump
    status: FlagStatus = "FLAG" if ratio > config.accruals_max or jumped else "CLEAR"
    return RedFlag(check, status, detail)


def receivables_rule(
    receivables_growth: float, revenue_growth: float, config: RedFlagsConfig
) -> RedFlag:
    """Receivables growing much faster than revenue: FLAG when receivables growth minus TTM
    revenue growth (both year on year) is above `receivables_excess_growth`; NOT EVALUATED
    when either is missing."""
    check = "receivables"
    if not (_finite(receivables_growth) and _finite(revenue_growth)):
        return RedFlag(check, "NOT EVALUATED", "receivables or revenue a year earlier missing")
    excess = receivables_growth - revenue_growth
    detail = (
        f"receivables {fmt_pct(receivables_growth, signed=True)} vs TTM revenue "
        f"{fmt_pct(revenue_growth, signed=True)} year on year: excess "
        f"{fmt_points(excess)} (flag above {fmt_points(config.receivables_excess_growth)[1:]})"
    )
    status: FlagStatus = "FLAG" if excess > config.receivables_excess_growth else "CLEAR"
    return RedFlag(check, status, detail)


def fcf_debt_rule(fcf_ttm: float, debt_growth: float, config: RedFlagsConfig) -> RedFlag:
    """Negative free cash flow funded by debt: FLAG when TTM free cash flow is negative and
    total debt grew by more than `debt_growth_min` year on year (`inf` = debt from none);
    CLEAR when free cash flow is not negative or debt did not grow that much; NOT EVALUATED
    when free cash flow is missing, or negative with the debt trend unknown."""
    check = "fcf_debt"
    if not _finite(fcf_ttm):
        return RedFlag(check, "NOT EVALUATED", "TTM free cash flow missing")
    if fcf_ttm >= 0:
        return RedFlag(check, "CLEAR", f"TTM free cash flow {fmt_usd(fcf_ttm)} is not negative")
    if math.isnan(debt_growth):
        return RedFlag(
            check,
            "NOT EVALUATED",
            f"TTM free cash flow {fmt_usd(fcf_ttm)} is negative; total debt a year earlier missing",
        )
    growth = (
        "new debt (none a year earlier)"
        if math.isinf(debt_growth)
        else fmt_pct(debt_growth, signed=True)
    )
    detail = (
        f"TTM free cash flow {fmt_usd(fcf_ttm)} with total debt {growth} year on year (flag "
        f"when debt grows more than {fmt_pct(config.debt_growth_min, signed=True)})"
    )
    status: FlagStatus = "FLAG" if debt_growth > config.debt_growth_min else "CLEAR"
    return RedFlag(check, status, detail)


def dilution_rule(share_growth: float, config: RedFlagsConfig) -> RedFlag:
    """Dilution: FLAG when the split-adjusted share count grew by more than `dilution_max`
    year on year; NOT EVALUATED without both counts."""
    check = "dilution"
    if not _finite(share_growth):
        return RedFlag(check, "NOT EVALUATED", "share count a year earlier missing")
    detail = (
        f"shares outstanding (split-adjusted) {fmt_pct(share_growth, signed=True)} year on year "
        f"(flag above {fmt_pct(config.dilution_max, signed=True)})"
    )
    status: FlagStatus = "FLAG" if share_growth > config.dilution_max else "CLEAR"
    return RedFlag(check, status, detail)


def going_concern_hits(text: str, terms: Iterable[str]) -> tuple[str, ...]:
    """The `terms` found in `text` (case-insensitive, whole words), in `terms` order."""
    found = []
    for term in terms:
        words = r"\s+".join(re.escape(w) for w in term.split())
        if words and re.search(rf"(?<!\w){words}(?!\w)", text, flags=re.IGNORECASE):
            found.append(term)
    return tuple(found)


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'"})
_QUOTE_MAX = 200
"""Longest quoted sentence (characters) in a going-concern FLAG."""


@functools.lru_cache(maxsize=256)
def _phrase(phrase: str) -> re.Pattern[str]:
    words = r"\s+".join(re.escape(w) for w in phrase.translate(_APOSTROPHES).split())
    return re.compile(rf"(?<!\w){words}(?!\w)", flags=re.IGNORECASE)


def going_concern_sentences(
    text: str, terms: Iterable[str], exclusions: Iterable[str]
) -> tuple[str, ...]:
    """The sentences of `text` (split after `.`, `!`, `?` and at line breaks, a piece starting
    with a lower-case letter going on with the one before, as after "Inc." or a line break
    inside a sentence; whitespace collapsed) holding every phrase of `terms` and none of
    `exclusions` (case-insensitive, whole words, curly apostrophes read as straight ones), each
    once, in order."""
    needed, banned = tuple(terms), tuple(exclusions)
    whole: list[str] = []
    for piece in _SENTENCE_BREAK.split(text):
        sentence = " ".join(piece.split())
        if whole and sentence[:1].islower():
            whole[-1] = f"{whole[-1]} {sentence}"
        elif sentence:
            whole.append(sentence)
    out: list[str] = []
    for sentence in whole:
        plain = sentence.translate(_APOSTROPHES)
        if (
            sentence not in out
            and all(_phrase(t).search(plain) for t in needed)
            and not any(_phrase(x).search(plain) for x in banned)
        ):
            out.append(sentence)
    return tuple(out)


def going_concern_rule(text: str | None, config: RedFlagsConfig) -> RedFlag:
    """Going-concern language in the MD&A: FLAG when a sentence of `text` holds all of
    `going_concern_terms` and none of `going_concern_exclusions` (the first such sentence
    quoted, at most 200 characters, and how many more); NOT EVALUATED without a text (no MD&A
    of the symbol in the lake)."""
    check = "going_concern"
    if text is None:
        return RedFlag(check, "NOT EVALUATED", "no MD&A text in the data")
    found = going_concern_sentences(
        text, config.going_concern_terms, config.going_concern_exclusions
    )
    if found:
        first = found[0] if len(found[0]) <= _QUOTE_MAX else found[0][: _QUOTE_MAX - 1] + "\u2026"
        more = len(found) - 1
        tail = f" and {more} more sentence{'s' if more > 1 else ''}" if more else ""
        return RedFlag(check, "FLAG", f'MD&A: "{first}"{tail}')
    terms = ", ".join(f'"{t}"' for t in config.going_concern_terms)
    return RedFlag(
        check,
        "CLEAR",
        f"no MD&A sentence holds all of {terms} (sentences with a phrase of "
        "going_concern_exclusions, such as a standard's name, are left out)",
    )


# --- history helpers --------------------------------------------------------------------------

SUMMARY_FLOWS = (
    ("revenue", "revenue", "USD"),
    ("gross_profit", "gross profit", "USD"),
    ("operating_income", "operating income", "USD"),
    ("net_income", "net income", "USD"),
    ("eps_diluted", "diluted EPS", "USD per share"),
    ("operating_cash_flow", "operating cash flow", "USD"),
    ("capex", "capital expenditure", "USD"),
)
"""Statement flows of the summary: (concept, label, unit)."""

SUMMARY_STOCKS = (
    ("total_assets", "total assets"),
    ("accounts_receivable", "accounts receivable"),
    ("cash_and_equivalents", "cash and equivalents"),
    ("total_debt", "total debt"),
    ("stockholders_equity", "stockholders' equity"),
)
"""Balance-sheet lines of the summary (USD): (concept, label)."""

HISTORY_CONCEPTS = (
    *(c for c, _, _ in SUMMARY_FLOWS),
    "depreciation_amortization",
    *(c for c, _ in SUMMARY_STOCKS),
    "shares_outstanding",
)
"""Concepts the tools read with `DataView.fundamentals_history`."""


@dataclass(frozen=True)
class _History:
    """One symbol's `fundamentals_history` rows by concept, known at the view's as-of."""

    rows: pd.DataFrame
    day: date

    def of(self, concept: str, column: str = "value") -> pd.DataFrame:
        """Rows of `concept` with a finite `column`, dated on or before the day, by date."""
        own = self.rows[self.rows["concept"] == concept]
        own = own[own[column].notna() & (own["period_end"] <= self.day)]
        return own.sort_values("period_end", kind="stable")

    def at(self, concept: str, end: date | None, column: str = "value") -> float:
        """The `column` value of `concept` for the period ending exactly on `end` (NaN when
        there is none, or no `end`)."""
        if end is None:
            return math.nan
        own = self.of(concept, column)
        hit = own[own["period_end"] == end]
        return float(hit[column].iloc[-1]) if not hit.empty else math.nan

    def stamp(self, concept: str, end: date, column: str = "value") -> datetime | None:
        own = self.of(concept, column)
        hit = own[own["period_end"] == end]
        return _instant(hit["available_at"].iloc[-1]) if not hit.empty else None


def _instant(value: object) -> datetime:
    return pd.Timestamp(value).tz_convert("UTC").to_pydatetime()  # type: ignore[arg-type]


def _nearest_end(ends: Sequence[date], target: date, tolerance: int) -> date | None:
    """The end in `ends` nearest to `target`, within `tolerance` days (ties: the earlier)."""
    best: date | None = None
    for end in ends:
        gap = abs((end - target).days)
        if gap <= tolerance and (best is None or gap < abs((best - target).days)):
            best = end
    return best


def _require(view: DataView, symbol: str) -> None:
    if symbol in view.blocked:
        raise DataError(f"symbol {symbol} is blocked by a critical data-quality issue")


def _history(view: DataView, symbol: str) -> _History:
    _require(view, symbol)
    rows = view.fundamentals_history([symbol], HISTORY_CONCEPTS)
    if rows.empty:
        raise DataError(f"no fundamentals known for {symbol} on {view.day.isoformat()}")
    return _History(rows=rows, day=view.day)


def _computed_at(view: DataView) -> datetime:
    """The time of a computed evidence item: just before the decision cutoff."""
    return view.as_of.cutoff - _EPSILON


def _tolerance(view: DataView) -> int:
    return view.config.quant.factors.period_tolerance_days


def _settings(view: DataView) -> FundamentalAnalystConfig:
    return view.config.agents.fundamental


def _latest_stamp(stamps: Iterable[datetime | None], fallback: datetime) -> datetime:
    known = [s for s in stamps if s is not None]
    return max(known) if known else fallback


# --- get_fundamentals_summary -----------------------------------------------------------------


def _fmt_value(x: float, unit: str) -> str:
    return f"USD {x:.2f}" if unit == "USD per share" else fmt_usd(x)


def _flow_item(
    symbol: str, history: _History, concept: str, label: str, unit: str, n: int
) -> Evidence | None:
    quarters = history.of(concept).tail(n)
    ttm = history.of(concept, "ttm")
    if quarters.empty and ttm.empty:
        return None
    parts = [
        f"{e} {_fmt_value(float(v), unit)}"
        for e, v in zip(quarters["period_end"], quarters["value"], strict=True)
    ]
    text = f"{symbol} {label} by fiscal quarter ({unit}; quarter end, value): " + (
        "; ".join(parts) if parts else "none"
    )
    stamps = list(quarters["available_at"])
    if not ttm.empty:
        last = ttm.iloc[-1]
        text += f". TTM to {last['period_end']}: {_fmt_value(float(last['ttm']), unit)}"
        stamps.append(last["available_at"])
    ends = list(quarters["period_end"]) + ([ttm["period_end"].iloc[-1]] if not ttm.empty else [])
    return Evidence(
        id=f"fsum:{symbol}:{max(ends).isoformat()}:{concept}",
        source="sec_edgar",
        as_of=max(_instant(s) for s in stamps),
        text=text + ".",
    )


def _stock_item(
    symbol: str, history: _History, concept: str, label: str, n: int
) -> Evidence | None:
    rows = history.of(concept).tail(n)
    if rows.empty:
        return None
    parts = [
        f"{e} {fmt_usd(float(v))}" for e, v in zip(rows["period_end"], rows["value"], strict=True)
    ]
    return Evidence(
        id=f"fsum:{symbol}:{rows['period_end'].iloc[-1].isoformat()}:{concept}",
        source="sec_edgar",
        as_of=max(_instant(s) for s in rows["available_at"]),
        text=f"{symbol} {label} at period end (USD; date, balance): " + "; ".join(parts) + ".",
    )


_MARGIN_LINES = (
    ("gross", "gross_profit"),
    ("operating", "operating_income"),
    ("net", "net_income"),
)
_GROWTH_LINES = (
    ("revenue", "revenue"),
    ("operating income", "operating_income"),
    ("net income", "net_income"),
    ("diluted EPS", "eps_diluted"),
)


def _ttm(history: _History, concept: str, end: date | None) -> float:
    return history.at(concept, end, "ttm") if end is not None else math.nan


def _fcf(history: _History, end: date | None) -> float:
    return _ttm(history, "operating_cash_flow", end) - _ttm(history, "capex", end)


def _margins(history: _History, end: date | None) -> list[str]:
    rev = _ttm(history, "revenue", end)
    out = [f"{name} {fmt_pct(_ratio(_ttm(history, c, end), rev))}" for name, c in _MARGIN_LINES]
    out.append(f"free cash flow {fmt_pct(_ratio(_fcf(history, end), rev))}")
    return out


def _stamps_at(
    history: _History, concepts: Iterable[str], ends: Iterable[date | None]
) -> list[datetime | None]:
    return [history.stamp(c, e, "ttm") for c in concepts for e in ends if e is not None]


def _derived_items(symbol: str, history: _History, view: DataView) -> list[Evidence]:
    revenue = history.of("revenue", "ttm")
    if revenue.empty:
        return []
    ends = list(revenue["period_end"])
    end = ends[-1]
    tol = _tolerance(view)
    year_ago = _nearest_end(ends, end - timedelta(days=_YEAR), tol)
    three_ago = _nearest_end(ends, end - timedelta(days=3 * _YEAR), tol)
    stale = (view.day - end).days > view.config.data.fundamentals.max_staleness_days
    note = (
        f" The latest period ended {end}, {(view.day - end).days} days before {view.day}: stale."
        if stale
        else ""
    )
    concepts = (
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "eps_diluted",
        "operating_cash_flow",
        "capex",
    )
    stamps = _stamps_at(history, concepts, (end, year_ago, three_ago))
    fallback = _instant(revenue["available_at"].iloc[-1])
    margins = f"{symbol} TTM margins to {end}: " + ", ".join(_margins(history, end))
    margins += " (free cash flow = operating cash flow - capital expenditure; all over TTM revenue)"
    margins += (
        f"; TTM to {year_ago}: " + ", ".join(_margins(history, year_ago))
        if year_ago is not None
        else "; a year earlier: not available"
    )
    growth_parts = [
        f"{name} {fmt_pct(_growth(_ttm(history, c, end), _ttm(history, c, year_ago)), signed=True)}"
        for name, c in _GROWTH_LINES
    ]
    growth_parts.append(
        "free cash flow "
        + fmt_pct(_growth(_fcf(history, end), _fcf(history, year_ago)), signed=True)
    )
    growth = (
        f"{symbol} TTM growth to {end} vs TTM to {year_ago}: " + ", ".join(growth_parts)
        if year_ago is not None
        else f"{symbol} TTM growth to {end}: no TTM a year earlier"
    )
    if NM in growth:
        growth += " (n/m: the year-earlier value is missing, zero or negative)"
    if three_ago is not None:
        ratio = _ratio(_ttm(history, "revenue", end), _ttm(history, "revenue", three_ago))
        cagr = ratio ** (1 / 3) - 1 if _finite(ratio) and ratio > 0 else math.nan
        growth += (
            f"; revenue CAGR over 3 years (vs TTM to {three_ago}): "
            f"{fmt_pct(cagr, signed=True)} a year"
        )
    as_of = _latest_stamp(stamps, fallback)
    return [
        Evidence(
            id=f"fsum:{symbol}:{end.isoformat()}:margins",
            source="sec_edgar",
            as_of=as_of,
            text=margins + "." + note,
        ),
        Evidence(
            id=f"fsum:{symbol}:{end.isoformat()}:growth",
            source="sec_edgar",
            as_of=as_of,
            text=growth + "." + note,
        ),
    ]


def fundamentals_summary(view: DataView, symbol: str) -> list[Evidence]:
    """The `get_fundamentals_summary` evidence of `symbol`: per flow the last
    `summary_quarters` discrete quarters and the latest TTM, per balance-sheet line the last
    `summary_quarters` balances, then the TTM margins and growth (vs the TTM a year earlier,
    and the 3-year revenue CAGR). Raises `DataError` for a blocked symbol or one with no
    fundamentals."""
    history = _history(view, symbol)
    n = _settings(view).summary_quarters
    items: list[Evidence] = []
    for concept, label, unit in SUMMARY_FLOWS:
        item = _flow_item(symbol, history, concept, label, unit, n)
        if item is not None:
            items.append(item)
    for concept, label in SUMMARY_STOCKS:
        item = _stock_item(symbol, history, concept, label, n)
        if item is not None:
            items.append(item)
    items += _derived_items(symbol, history, view)
    return items


# --- the sector cross-section -----------------------------------------------------------------


@dataclass(frozen=True)
class SectorPanel:
    """`symbol`'s GICS `sector`, its `peers` (members of `quant.factors.universe` at the
    as-of in that sector, the symbol included, sorted), their factor `panel` (Stage 16) and
    their `fundamentals` (`DataView.fundamentals_panel`)."""

    symbol: str
    sector: str
    peers: tuple[str, ...]
    panel: FactorPanel
    fundamentals: pd.DataFrame


def sector_panel(view: DataView, symbol: str) -> SectorPanel:
    """The sector cross-section of `symbol` at the view's as-of. Raises `DataError` when the
    symbol is blocked, has no sector or gets no factor values."""
    _require(view, symbol)
    sector = view.instrument(symbol).sector
    if not sector:
        raise DataError(f"{symbol} has no GICS sector in the instruments table")
    universe = view.config.quant.factors.universe
    peers = {symbol}
    for other in view.universe(universe):
        try:
            if view.instrument(other).sector == sector:
                peers.add(other)
        except DataError as exc:  # no instrument row: no known sector, so not a peer (logged)
            _log.warning("fundamental.peer_skipped", symbol=other, reason=str(exc))
            continue
    ordered = tuple(sorted(peers))
    panel = factor_panel(view, ordered)
    if symbol not in panel.raw.index:
        raise DataError(f"no factor values for {symbol} on {view.day.isoformat()}")
    fundamentals = view.fundamentals_panel(ordered)
    return SectorPanel(symbol, sector, ordered, panel, fundamentals)


def percentile(values: pd.Series, symbol: str, min_names: int) -> tuple[float | None, int]:
    """The mid-rank percentile (0-100) of `symbol`'s value among the finite `values` (the
    share below it plus half the share equal to it, itself included) and how many values
    there are; `None` when it has no value or fewer than `min_names` values exist."""
    valid = values[values.notna() & values.map(lambda v: math.isfinite(float(v)))].astype("float64")
    n = len(valid)
    if symbol not in valid.index or n < min_names:
        return None, n
    v = float(valid[symbol])
    below = int((valid < v).sum())
    equal = int((valid == v).sum())
    return 100.0 * (below + 0.5 * equal) / n, n


RANK_GROUPS = ("value", "quality", "growth", "leverage")
"""The factor groups `get_factor_ranks` reports."""

_ORIENTATION = {
    "value": "100 = cheapest",
    "quality": "100 = highest quality",
    "growth": "100 = fastest growth",
    "leverage": "100 = most leveraged",
}


def _factor_value(x: float, unit: str) -> str:
    if unit == "fraction":
        return fmt_pct(x)
    if unit == "fraction_per_year":
        return f"{fmt_pct(x)} a year" if _finite(x) else NM
    return fmt_num(x, 2)


def _pct_text(pct: float | None, n: int) -> str:
    return (
        f"percentile {pct:.0f} of {n} peers"
        if pct is not None
        else f"not ranked ({n} peers with a value)"
    )


def factor_ranks_from(view: DataView, sector: SectorPanel) -> list[Evidence]:
    """The `get_factor_ranks` evidence from a computed `sector` cross-section."""
    symbol, day = sector.symbol, view.day.isoformat()
    min_names = _settings(view).min_sector_names
    composites = style_composites(sector.panel.exposures)
    universe = view.config.quant.factors.universe
    items = []
    for group in RANK_GROUPS:
        pct, n = percentile(composites[group], symbol, min_names)
        head = (
            f"{symbol} {group} vs its {sector.sector} peers ({universe} members on {day}): "
            f"style composite at {_pct_text(pct, n)} ({_ORIENTATION[group]}; sector-neutral "
            "z-scores of the factors below, equal weights)"
        )
        parts = []
        for spec in FACTORS:
            if spec.group != group:
                continue
            raw = sector.panel.raw[spec.name]
            value = float(raw[symbol]) if symbol in raw.index else math.nan
            f_pct, f_n = percentile(raw, symbol, min_names)
            parts.append(
                f"{spec.name} {_factor_value(value, spec.unit)} ({_pct_text(f_pct, f_n)}; "
                f"{spec.description.replace(' (Stage 09)', '')})"
            )
        items.append(
            Evidence(
                id=f"frank:{symbol}:{day}:{group}",
                source="factors",
                as_of=_computed_at(view),
                text=head + ". Factors: " + "; ".join(parts) + ".",
            )
        )
    return items


def factor_ranks(view: DataView, symbol: str) -> list[Evidence]:
    """The `get_factor_ranks` evidence of `symbol`: per group in `RANK_GROUPS`, the
    percentile of its style composite and of each raw factor among its sector peers."""
    return factor_ranks_from(view, sector_panel(view, symbol))


# --- get_valuation ----------------------------------------------------------------------------

MULTIPLES = (
    ("pe", "P/E", "market cap / TTM net income"),
    ("ps", "P/S", "market cap / TTM revenue"),
    ("pb", "P/B", "market cap / stockholders' equity"),
    (
        "ev_ebitda",
        "EV/EBITDA",
        "enterprise value / TTM (operating income + depreciation and amortisation)",
    ),
    ("fcf_yield", "FCF yield", "TTM free cash flow / market cap"),
)
"""The multiples of `get_valuation`: (key, label, definition)."""


def multiples(
    cap: float,
    ev: float,
    *,
    net_income: float,
    revenue: float,
    equity: float,
    ebitda: float,
    fcf: float,
) -> dict[str, float]:
    """The `MULTIPLES` from a market cap and enterprise value (USD) and TTM / balance inputs
    (USD); NaN where a denominator is missing or not positive."""
    return {
        "pe": _ratio(cap, net_income),
        "ps": _ratio(cap, revenue),
        "pb": _ratio(cap, equity),
        "ev_ebitda": _ratio(ev, ebitda) if _finite(ev) else math.nan,
        "fcf_yield": _ratio(fcf, cap),
    }


def _metric(frame: pd.DataFrame, symbol: str, name: str) -> float:
    if name not in frame.columns or symbol not in frame.index:
        return math.nan
    return float(cast(float, frame.at[symbol, name]))


def _current_multiples(sector: SectorPanel) -> pd.DataFrame:
    """Today's multiples of every peer (index symbol, `MULTIPLES` keys as columns)."""
    market, fund = sector.panel.market, sector.fundamentals
    rows = {}
    for s in sector.panel.raw.index:

        def m(name: str, s: str = s) -> float:
            return _metric(fund, s, name)

        rows[s] = multiples(
            _metric(market, s, "market_cap"),
            _metric(market, s, "enterprise_value"),
            net_income=m("net_income_ttm"),
            revenue=m("revenue_ttm"),
            equity=m("stockholders_equity"),
            ebitda=m("operating_income_ttm") + m("depreciation_amortization_ttm"),
            fcf=m("fcf_ttm"),
        )
    return pd.DataFrame.from_dict(rows, orient="index", columns=[k for k, _, _ in MULTIPLES])


def _factor_at(splits: pd.DataFrame, symbol: str, day: date) -> float:
    """The split factor of `symbol` at the last session on or before `day` (the first
    session's when `day` precedes the window; 1.0 when unknown)."""
    if symbol not in splits.columns or splits.empty:
        return 1.0
    column = splits[symbol]
    pos = int(column.index.searchsorted(pd.Timestamp(day), side="right")) - 1
    return float(column.iloc[max(pos, 0)])


def _window_sessions(view: DataView, start: date) -> int:
    """How many known sessions reach back to `start`."""
    cal = TradingCalendar(config=view.config.calendar)
    last = view.sessions(1)[-1]
    first = max(cal.start, min(start, last))
    return max(len(cal.sessions(first, last)), 1)


@dataclass(frozen=True)
class _QuarterMultiples:
    end: date
    values: dict[str, float]


def _history_multiples(view: DataView, symbol: str, history: _History) -> list[_QuarterMultiples]:
    """The multiples of `symbol` at each fiscal quarter end of the last `valuation_years`."""
    settings = _settings(view)
    start = view.day - timedelta(days=round(365.25 * settings.valuation_years))
    ends = [e for e in history.of("revenue", "ttm")["period_end"] if e >= start]
    if not ends:
        return []
    n = _window_sessions(view, start - timedelta(days=settings.price_tolerance_days))
    bars = view.prices([symbol], n)
    splits = view.split_factors([symbol], n)
    shares = adjusted_shares(history.rows, splits)
    out = []
    for end in ends:
        before = bars[bars["date"] <= end]
        if before.empty or (end - before["date"].iloc[-1]).days > settings.price_tolerance_days:
            continue
        bar_day = before["date"].iloc[-1]
        price = float(before["close"].iloc[-1]) * _factor_at(splits, symbol, bar_day)
        share_end = _nearest_end(
            list(shares["period_end"]), end, settings.share_count_tolerance_days
        )
        if share_end is None:
            continue
        count = float(shares.loc[shares["period_end"] == share_end, "shares"].iloc[-1])
        cap = price * count
        debt, cash = history.at("total_debt", end), history.at("cash_and_equivalents", end)
        ev = cap + debt - cash if _finite(debt) and _finite(cash) else math.nan
        values = multiples(
            cap,
            ev,
            net_income=_ttm(history, "net_income", end),
            revenue=_ttm(history, "revenue", end),
            equity=history.at("stockholders_equity", end),
            ebitda=_ttm(history, "operating_income", end)
            + _ttm(history, "depreciation_amortization", end),
            fcf=_fcf(history, end),
        )
        out.append(_QuarterMultiples(end, values))
    return out


def _fmt_multiple(key: str, x: float) -> str:
    return fmt_pct(x) if key == "fcf_yield" else fmt_num(x)


def valuation_from(view: DataView, sector: SectorPanel) -> list[Evidence]:
    """The `get_valuation` evidence from a computed `sector` cross-section."""
    symbol, day = sector.symbol, view.day.isoformat()
    settings = _settings(view)
    history = _history(view, symbol)
    current = _current_multiples(sector)
    past = _history_multiples(view, symbol, history)
    at = _computed_at(view)
    market = sector.panel.market
    cap, ev = _metric(market, symbol, "market_cap"), _metric(market, symbol, "enterprise_value")
    price = _metric(market, symbol, "price")
    items = [
        Evidence(
            id=f"val:{symbol}:{day}:market",
            source="valuation",
            as_of=at,
            text=(
                f"{symbol} market cap {fmt_usd(cap)} = close USD {fmt_num(price, 2)} on "
                f"{market.at[symbol, 'price_date']} x "
                f"{fmt_shares(_metric(market, symbol, 'shares'))} "
                f"({market.at[symbol, 'shares_source']} count dated "
                f"{market.at[symbol, 'shares_date']}); "
                f"enterprise value {fmt_usd(ev)} (market cap + total debt - cash and equivalents)."
            ),
        )
    ]
    for key, label, definition in MULTIPLES:
        now = _metric(current, symbol, key)
        own = pd.Series([q.values[key] for q in past], dtype="float64")
        own = own[own.map(math.isfinite)] if not own.empty else own
        text = f"{symbol} {label} ({definition}): now {_fmt_multiple(key, now)}"
        if len(own) >= settings.min_valuation_quarters:
            ends = [q.end for q in past]
            series = pd.concat([own, pd.Series([now], index=["now"])]) if _finite(now) else own
            pct, _ = percentile(series, "now", 1) if _finite(now) else (None, 0)
            text += (
                f"; own history at {len(own)} fiscal quarter ends {min(ends)}..{max(ends)}: median "
                f"{_fmt_multiple(key, float(own.median()))}, "
                f"low {_fmt_multiple(key, float(own.min()))}, "
                f"high {_fmt_multiple(key, float(own.max()))}"
                + (f", now at percentile {pct:.0f} of that history" if pct is not None else "")
            )
        else:
            text += f"; own history: {len(own)} quarter(s) with a value, too few to compare"
        peers = current[key][current[key].map(lambda v: _finite(float(v)))]
        if len(peers) >= settings.min_sector_names:
            median = _fmt_multiple(key, float(peers.median()))
            text += f"; {sector.sector} median {median} ({len(peers)} peers with a value)"
        else:
            text += f"; {sector.sector} median: too few peers with a value ({len(peers)})"
        items.append(
            Evidence(id=f"val:{symbol}:{day}:{key}", source="valuation", as_of=at, text=text + ".")
        )
    return items


def valuation(view: DataView, symbol: str) -> list[Evidence]:
    """The `get_valuation` evidence of `symbol`: its market cap and EV, then per multiple in
    `MULTIPLES` today's value, its own history at the fiscal quarter ends of the last
    `valuation_years` (median, low, high, today's percentile) and the sector median."""
    return valuation_from(view, sector_panel(view, symbol))


# --- get_red_flags ----------------------------------------------------------------------------

_CHECK_TEXT = {
    "accruals": "earnings far ahead of operating cash flow",
    "receivables": "receivables growing much faster than revenue",
    "fcf_debt": "negative free cash flow with rising debt",
    "dilution": "share count growth",
    "going_concern": "going-concern language in the MD&A",
}


def _debt_growth(now: float, before: float) -> float:
    """Debt growth year on year; `inf` for debt from none, NaN when either is missing."""
    if not (_finite(now) and _finite(before)):
        return math.nan
    if before <= 0:
        return math.inf if now > 0 else 0.0
    return now / before - 1.0


def red_flag_results(view: DataView, symbol: str, history: _History) -> list[tuple[RedFlag, str]]:
    """Each check of `RED_FLAG_CHECKS` on `symbol`, with the periods it compared."""
    settings = _settings(view).red_flags
    tol = _tolerance(view)
    staleness = view.config.data.fundamentals.max_staleness_days
    revenue = history.of("revenue", "ttm")
    end = revenue["period_end"].iloc[-1] if not revenue.empty else None
    stale = end is None or (view.day - end).days > staleness
    ends = list(revenue["period_end"])
    year_ago = _nearest_end(ends, end - timedelta(days=_YEAR), tol) if end is not None else None
    periods = f"TTM / balances at {end} vs {year_ago}" if year_ago is not None else f"at {end}"
    out: list[tuple[RedFlag, str]] = []
    if stale:
        why = (
            "no TTM revenue"
            if end is None
            else f"the latest period ended {end}, more than {staleness} days before {view.day}"
        )
        for check in RED_FLAG_CHECKS[:4]:
            out.append((RedFlag(check, "NOT EVALUATED", f"stale or missing filings ({why})"), ""))
    else:

        def accruals(at: date | None) -> float:
            if at is None:
                return math.nan
            return _ratio(
                _ttm(history, "net_income", at) - _ttm(history, "operating_cash_flow", at),
                history.at("total_assets", at),
            )

        out.append((accruals_rule(accruals(end), accruals(year_ago), settings), periods))
        ar_growth = (
            _growth(
                history.at("accounts_receivable", end), history.at("accounts_receivable", year_ago)
            )
            if year_ago
            else math.nan
        )
        rev_growth = (
            _growth(_ttm(history, "revenue", end), _ttm(history, "revenue", year_ago))
            if year_ago
            else math.nan
        )
        out.append((receivables_rule(ar_growth, rev_growth, settings), periods))
        debt_growth = (
            _debt_growth(history.at("total_debt", end), history.at("total_debt", year_ago))
            if year_ago
            else math.nan
        )
        out.append((fcf_debt_rule(_fcf(history, end), debt_growth, settings), periods))
        out.append(_dilution(view, symbol, history, settings, tol, staleness))
    out.append(_going_concern(view, symbol, settings))
    return out


def _going_concern(view: DataView, symbol: str, settings: RedFlagsConfig) -> tuple[RedFlag, str]:
    """The going-concern check on the MD&A of `symbol`'s latest 10-K or 10-Q known at the
    as-of whose MD&A was found (`DataView.mda`), with the filing it read; NOT EVALUATED (reason
    named) without an MD&A, when the filing's period (else its filing date) ended more than
    `mda_max_age_days` before the as-of, or when no filing's MD&A was found."""
    mda = view.mda(symbol)
    if mda is None:
        return going_concern_rule(None, settings), ""
    check = "going_concern"
    periods = f"{mda.form} filed {mda.filed}" + (
        f", period ended {mda.period_end}" if mda.period_end is not None else ""
    )
    dated = mda.period_end if mda.period_end is not None else mda.filed
    if (view.day - dated).days > settings.mda_max_age_days:
        what = (
            f"covers the period ended {dated}"
            if mda.period_end is not None
            else f"was filed {dated}"
        )
        why = (
            f"the latest {mda.form} MD&A {what}, more than {settings.mda_max_age_days} days "
            f"before {view.day}"
        )
        return RedFlag(check, "NOT EVALUATED", why), periods
    if mda.text is None:
        why = (
            f"the MD&A of the {mda.form} filed {mda.filed} was not found in the filing "
            f"({mda.status})"
        )
        return RedFlag(check, "NOT EVALUATED", why), periods
    return going_concern_rule(mda.text, settings), periods


def _dilution(
    view: DataView,
    symbol: str,
    history: _History,
    settings: RedFlagsConfig,
    tol: int,
    staleness: int,
) -> tuple[RedFlag, str]:
    counts = history.of("shares_outstanding")
    if counts.empty:
        return dilution_rule(math.nan, settings), ""
    latest = counts["period_end"].iloc[-1]
    if (view.day - latest).days > staleness:
        return RedFlag(
            "dilution", "NOT EVALUATED", f"the latest share count is dated {latest}: stale"
        ), ""
    n = _window_sessions(view, latest - timedelta(days=_YEAR + tol))
    shares = adjusted_shares(history.rows, view.split_factors([symbol], n))
    ends = list(shares["period_end"])
    before = _nearest_end(ends, latest - timedelta(days=_YEAR), tol)
    if before is None:
        return dilution_rule(math.nan, settings), f"count dated {latest}"

    def count(at: date) -> float:
        return float(shares.loc[shares["period_end"] == at, "shares"].iloc[-1])

    return dilution_rule(
        _growth(count(latest), count(before)), settings
    ), f"counts dated {latest} vs {before}"


def red_flags(view: DataView, symbol: str) -> list[Evidence]:
    """The `get_red_flags` evidence of `symbol`: one item per check of `RED_FLAG_CHECKS`."""
    history = _history(view, symbol)
    day, at = view.day.isoformat(), _computed_at(view)
    items = []
    for flag, periods in red_flag_results(view, symbol, history):
        where = f" ({periods})" if periods else ""
        items.append(
            Evidence(
                id=f"rflag:{symbol}:{day}:{flag.check}",
                source="red_flags",
                as_of=at,
                text=(
                    f"{symbol} red-flag check {flag.check} ({_CHECK_TEXT[flag.check]}): "
                    f"{flag.status}. {flag.detail}{where}."
                ),
            )
        )
    return items


# --- prefetch and the tools -------------------------------------------------------------------


def gap_item(view: DataView, symbol: str, tool: str, error: Exception) -> Evidence:
    """An evidence item saying `tool` could not be computed for `symbol`, and why."""
    return Evidence(
        id=f"gap:{symbol}:{view.day.isoformat()}:{tool}",
        source="atlas",
        as_of=_computed_at(view),
        text=f"{tool} could not be computed for {symbol}: {error}",
    )


def prefetch(view: DataView, symbol: str) -> list[Evidence]:
    """All four tools' evidence for `symbol`, sharing one sector cross-section. A `DataError`
    from the summary (blocked symbol, no fundamentals) propagates; one from another tool
    becomes a `gap:` item."""
    items = fundamentals_summary(view, symbol)
    try:
        sector = sector_panel(view, symbol)
    except DataError as exc:
        items += [
            gap_item(view, symbol, "get_factor_ranks", exc),
            gap_item(view, symbol, "get_valuation", exc),
        ]
    else:
        for tool, build in (
            ("get_factor_ranks", factor_ranks_from),
            ("get_valuation", valuation_from),
        ):
            try:
                items += build(view, sector)
            except DataError as exc:
                items.append(gap_item(view, symbol, tool, exc))
    try:
        items += red_flags(view, symbol)
    except DataError as exc:
        items.append(gap_item(view, symbol, "get_red_flags", exc))
    return items


def _tool(name: str, description: str, body: Callable[[DataView, str], list[Evidence]]) -> Tool:
    def fn(ctx: AgentContext, args: FundamentalArgs) -> list[Evidence]:
        return body(ctx.view, args.symbol)

    return Tool(name=name, description=description, args=FundamentalArgs, fn=fn)


GET_FUNDAMENTALS_SUMMARY = _tool(
    "get_fundamentals_summary",
    "One stock's last fiscal quarters of revenue, profits, EPS, cash flow and capex with "
    "their TTM, recent balance-sheet lines, TTM margins and growth, from filings known today.",
    fundamentals_summary,
)
GET_FACTOR_RANKS = _tool(
    "get_factor_ranks",
    "One stock's value, quality, growth and leverage factor percentiles against the index "
    "members of its sector, known today.",
    factor_ranks,
)
GET_VALUATION = _tool(
    "get_valuation",
    "One stock's market cap and its P/E, P/S, P/B, EV/EBITDA and FCF yield today, against "
    "their own history at past quarter ends and the sector median.",
    valuation,
)
GET_RED_FLAGS = _tool(
    "get_red_flags",
    "Deterministic accounting red-flag checks on one stock (accruals, receivables, negative "
    "free cash flow with rising debt, dilution, going concern): FLAG, CLEAR or NOT EVALUATED.",
    red_flags,
)

FUNDAMENTAL_TOOLS = (GET_FUNDAMENTALS_SUMMARY, GET_FACTOR_RANKS, GET_VALUATION, GET_RED_FLAGS)
"""The fundamental analyst's tools, in brief order."""
