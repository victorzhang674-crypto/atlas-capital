"""Inputs for the Stage 16 factor tests: hand-built frames in the shapes `DataView` returns
(`fundamentals_panel`, `fundamentals_history`, `prices`, `split_factors`), and a synthetic
lake world (prices, splits, instruments, membership and three years of quarterly filings for
a dozen companies in three sectors) read through `DataView`."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import count
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.pit import bar_available_at
from atlas.core.storage import Lake
from atlas.core.types.instruments import AssetClass, Instrument
from atlas.data.fundamentals import FACT_COLUMNS, HISTORY_COLUMNS, metric_names
from atlas.data.fundamentals.mapping import load_concept_map
from atlas.data.prices import CORPORATE_ACTIONS, PRICES_DAILY
from atlas.data.reference import instruments_frame

CAL = TradingCalendar()
REPO_ROOT = Path(__file__).resolve().parents[4]
CONCEPT_MAP = load_concept_map(REPO_ROOT / "config" / "xbrl_map.yaml")
METRICS = metric_names(CONCEPT_MAP)


# --- hand-built frames -----------------------------------------------------------------------


def panel_frame(rows: Mapping[str, Mapping[str, float]]) -> pd.DataFrame:
    """A `fundamentals_panel`-shaped frame: index `symbol`, every metric column (NaN unless
    given)."""
    frame = pd.DataFrame(
        {m: [float(r.get(m, np.nan)) for r in rows.values()] for m in METRICS},
        index=pd.Index(list(rows), name="symbol"),
    )
    return frame.astype("float64")


def history_frame(
    rows: Sequence[tuple[str, str, date, float, float]],
    *,
    available_at: datetime = datetime(2020, 1, 1, tzinfo=UTC),
) -> pd.DataFrame:
    """A `fundamentals_history`-shaped frame from `(symbol, concept, period_end, value, ttm)`
    (NaN for a missing side)."""
    frame = pd.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "concept": [r[1] for r in rows],
            "period_start": [None] * len(rows),
            "period_end": [r[2] for r in rows],
            "value": [float(r[3]) for r in rows],
            "ttm": [float(r[4]) for r in rows],
            "available_at": pd.Series([available_at] * len(rows), dtype="datetime64[us, UTC]"),
            "source": ["reported"] * len(rows),
            "ttm_source": [None] * len(rows),
        }
    )
    return frame[["symbol", *HISTORY_COLUMNS]]


def prices_frame(rows: Sequence[tuple[str, date, float]]) -> pd.DataFrame:
    """`DataView.prices`-shaped rows `(symbol, date, close)` (other OHLC = close)."""
    frame = pd.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "open": [r[2] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[2] for r in rows],
            "volume": [1_000_000] * len(rows),
            "adj_close": [r[2] for r in rows],
            "vendor": ["yfinance"] * len(rows),
        }
    )
    frame["available_at"] = pd.Series(
        [bar_available_at(r[1], CAL) for r in rows], dtype="object"
    ).astype("datetime64[us, UTC]")
    return frame


def splits_frame(
    sessions: Sequence[date],
    factors: Mapping[str, Sequence[float]] | None = None,
    *,
    symbols: Sequence[str] = (),
) -> pd.DataFrame:
    """A `DataView.split_factors`-shaped frame over `sessions` (1.0 unless given)."""
    data = {s: [1.0] * len(sessions) for s in symbols}
    data.update({s: list(map(float, f)) for s, f in (factors or {}).items()})
    return pd.DataFrame(data, index=pd.DatetimeIndex(pd.to_datetime(list(sessions)), name="date"))


# --- the synthetic lake world ----------------------------------------------------------------

AS_OF = date(2024, 3, 1)
"""The world's as-of (08:00 New York: last known bar 2024-02-29)."""

SECTORS = {
    "AAA": "Information Technology",
    "BBB": "Information Technology",
    "CCC": "Information Technology",
    "DDD": "Information Technology",
    "EEE": "Financials",
    "FFF": "Financials",
    "GGG": "Financials",
    "HHH": "Financials",
    "III": "Health Care",
    "JJJ": "Health Care",
    "KKK": "Health Care",
    "LLL": None,
}
STOCKS = tuple(SECTORS)
_ACCN = count(1)


def fiscal_quarters(last_end: date, n: int) -> list[tuple[date, date]]:
    """`n` calendar quarters `(start, end)` ending with the quarter that ends on `last_end`,
    oldest first."""
    periods = pd.period_range(end=pd.Period(last_end, freq="Q"), periods=n, freq="Q")
    return [(p.start_time.date(), p.end_time.date()) for p in periods]


@dataclass(frozen=True)
class Company:
    """One synthetic filer: quarterly flows grow at `growth` per quarter from `revenue0`
    (USD), with fixed margins; balances and shares move slowly."""

    symbol: str
    revenue0: float
    growth: float
    gross_margin: float
    op_margin: float
    net_margin: float
    ocf_margin: float
    capex_ratio: float
    assets: float
    equity: float
    debt: float
    cash: float
    shares: float
    share_drift: float
    eps_scale: float


def companies(seed: int = 16) -> list[Company]:
    """A dozen seeded companies with varied, known parameters."""
    rng = np.random.default_rng(seed)
    out = []
    for i, sym in enumerate(STOCKS):
        out.append(
            Company(
                symbol=sym,
                revenue0=float(rng.uniform(200e6, 2e9)),
                growth=float(rng.uniform(-0.02, 0.05)),
                gross_margin=float(rng.uniform(0.2, 0.7)),
                op_margin=float(rng.uniform(0.05, 0.3)),
                net_margin=float(rng.uniform(0.02, 0.2)),
                ocf_margin=float(rng.uniform(0.05, 0.3)),
                capex_ratio=float(rng.uniform(0.02, 0.1)),
                assets=float(rng.uniform(1e9, 1e10)),
                equity=float(rng.uniform(3e8, 4e9)),
                debt=float(rng.uniform(0, 3e9)),
                cash=float(rng.uniform(5e7, 1e9)),
                shares=float(rng.uniform(5e7, 5e8)),
                share_drift=float(rng.uniform(-0.01, 0.01)) if i != 3 else 0.0,
                eps_scale=1.0,
            )
        )
    return out


def filed_after(end: date, days: int = 35) -> date:
    """The filing date `days` after a period end (a weekday)."""
    d = end + timedelta(days=days)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _fact(
    symbol: str,
    tag: str,
    value: float,
    end: date,
    filed: date,
    *,
    start: date | None = None,
    unit: str = "USD",
    taxonomy: str = "us-gaap",
    form: str = "10-Q",
    available_at: datetime | None = None,
) -> dict[str, Any]:
    available = available_at or datetime.combine(filed, time(21), tzinfo=UTC)
    return {
        "symbol": symbol,
        "cik": 1000 + STOCKS.index(symbol) if symbol in STOCKS else 9999,
        "taxonomy": taxonomy,
        "tag": tag,
        "unit": unit,
        "value": float(value),
        "period_start": start,
        "period_end": end,
        "duration_days": 0 if start is None else (end - start).days + 1,
        "fy": None,
        "fp": None,
        "form": form,
        "filed": filed,
        "accn": f"0000000016-24-{next(_ACCN):06d}",
        "accepted_at": available,
        "available_at": available,
    }


def facts_frame(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """`fundamentals_facts` rows from `_fact` dicts."""
    df = pd.DataFrame(list(rows), columns=list(FACT_COLUMNS))
    df["fy"] = df["fy"].astype("Int64")
    for col in ("accepted_at", "available_at"):
        df[col] = pd.to_datetime(df[col], utc=True).astype("datetime64[us, UTC]")
    return df


QUARTERS = fiscal_quarters(date(2023, 12, 31), 16)
"""16 calendar quarters 2020-Q1 .. 2023-Q4 (a TTM three years before the latest one needs
them all); Q4 2023 is filed 2024-02-05 (known at `AS_OF`)."""


def company_facts(c: Company, quarters: Sequence[tuple[date, date]] = QUARTERS) -> list[dict]:
    """Discrete quarterly flows, quarter-end balances and a cover-page share count per
    filing for company `c`."""
    rows: list[dict[str, Any]] = []
    for k, (start, end) in enumerate(quarters):
        filed = filed_after(end)
        rev = c.revenue0 * (1 + c.growth) ** k
        wobble = 1 + 0.02 * np.sin(k + (STOCKS.index(c.symbol) if c.symbol in STOCKS else 0))
        shares = c.shares * (1 + c.share_drift) ** k
        flows = {
            "Revenues": rev,
            "GrossProfit": rev * c.gross_margin * wobble,
            "OperatingIncomeLoss": rev * c.op_margin,
            "NetIncomeLoss": rev * c.net_margin,
            "NetCashProvidedByUsedInOperatingActivities": rev * c.ocf_margin,
            "PaymentsToAcquirePropertyPlantAndEquipment": rev * c.capex_ratio,
        }
        for tag, value in flows.items():
            rows.append(_fact(c.symbol, tag, value, end, filed, start=start))
        rows.append(
            _fact(
                c.symbol,
                "EarningsPerShareDiluted",
                rev * c.net_margin / shares,
                end,
                filed,
                start=start,
                unit="USD/shares",
            )
        )
        grow = (1 + c.growth / 4) ** k
        balances = {
            "Assets": c.assets * grow,
            "StockholdersEquity": c.equity * grow,
            "LongTermDebt": c.debt,
            "CashAndCashEquivalentsAtCarryingValue": c.cash,
        }
        for tag, value in balances.items():
            rows.append(_fact(c.symbol, tag, value, end, filed))
        rows.append(
            _fact(
                c.symbol,
                "EntityCommonStockSharesOutstanding",
                shares,
                filed - timedelta(days=7),
                filed,
                unit="shares",
                taxonomy="dei",
            )
        )
    return rows


def world_bars(symbols: Sequence[str], last: date, n: int, seed: int = 16) -> pd.DataFrame:
    """`n` sessions of random-walk bars ending at session `last` for `symbols`."""
    rng = np.random.default_rng(seed)
    days = list(CAL.sessions(CAL.offset(last, -(n - 1)), last))
    frames = []
    for i, sym in enumerate(symbols):
        closes = (20 + 10 * i) * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
        frame = pd.DataFrame(
            {
                "symbol": sym,
                "date": days,
                "open": closes,
                "high": closes * 1.01,
                "low": closes * 0.99,
                "close": closes,
                "adj_close": closes,
                "volume": 2_000_000,
                "vendor": "yfinance",
            }
        )
        frame["available_at"] = pd.Series(
            [bar_available_at(d, CAL) for d in days], dtype="object"
        ).astype("datetime64[us, UTC]")
        frames.append(frame[list(PRICES_DAILY.column_names)])
    return pd.concat(frames, ignore_index=True)


def actions_frame(
    rows: Sequence[tuple[str, date, str, float | None, float | None]],
) -> pd.DataFrame:
    """`corporate_actions` rows `(symbol, ex_date, type, ratio, amount)`, known from the
    ex-date's bar."""
    frame = pd.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "ex_date": [r[1] for r in rows],
            "type": [r[2] for r in rows],
            "ratio": [r[3] for r in rows],
            "amount": [r[4] for r in rows],
            "vendor": "yfinance",
            "available_at": [bar_available_at(CAL.prev_session(r[1]), CAL) for r in rows],
        }
    )
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True).astype(
        "datetime64[us, UTC]"
    )
    for col in ("ratio", "amount"):
        frame[col] = pd.to_numeric(frame[col]).astype("float64")
    return frame[list(CORPORATE_ACTIONS.column_names)]


SPLIT = ("CCC", date(2024, 2, 12), 2.0)
"""CCC splits 2:1 on 2024-02-12, after its last cover-page share count (dated 2024-01-29, a
week before its Q4 filing of 2024-02-05): that count is in pre-split shares."""


@dataclass(frozen=True)
class FactorWorld:
    """The synthetic world in `lake`: `companies` filed `QUARTERS`; bars to 2024-02-29."""

    lake: Lake
    companies: tuple[Company, ...]
    sessions: tuple[date, ...]


def build_factor_world(lake: Lake, seed: int = 16) -> FactorWorld:
    """Write the world into `lake`: 600 sessions of bars to 2024-02-29 (CCC halves on the
    split day), the CCC split, instruments with `SECTORS`, `SP500` membership and facts."""
    cos = companies(seed)
    last = date(2024, 2, 29)
    bars = world_bars(STOCKS, last, 600, seed)
    sym, ex, ratio = SPLIT
    hit = (bars["symbol"] == sym) & (bars["date"] >= ex)
    for col in ("open", "high", "low", "close", "adj_close"):
        bars.loc[hit, col] = bars.loc[hit, col] / ratio
    lake.write_table("prices_daily", bars, mode="upsert")
    lake.write_table(
        "corporate_actions", actions_frame([(sym, ex, "split", ratio, None)]), mode="upsert"
    )
    instruments = [
        Instrument(symbol=s, asset_class=AssetClass.EQUITY, name=f"{s} Inc", sector=sector)
        for s, sector in SECTORS.items()
    ]
    lake.write_table("instruments", instruments_frame(instruments), mode="upsert")
    membership = pd.DataFrame(
        {
            "index_id": "SP500",
            "symbol": list(STOCKS),
            "start_date": date(2010, 1, 4),
            "end_date": None,
        }
    )
    lake.write_table("index_membership", membership, mode="upsert")
    rows = [r for c in cos for r in company_facts(c)]
    lake.write_table("fundamentals_facts", facts_frame(rows), mode="upsert")
    days = tuple(sorted(set(bars["date"])))
    return FactorWorld(lake=lake, companies=tuple(cos), sessions=days)
