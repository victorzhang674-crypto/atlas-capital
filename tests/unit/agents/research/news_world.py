"""The Stage 31 test world, known at 2024-03-01 08:00 New York (13:00 UTC).

Sectors and sessions: `AAA` (the stock under study) and the Technology peers `P01`..`P04` and
`NOS`, `H01` and `H02` (Health Care), `NOSEC` (no sector) and SPY (the reaction benchmark, not
a member). 30 sessions of bars to 2024-02-29 with a small repeating wiggle and constant
volumes, so the average dollar volume ranks the peers: `P01` > `P02` > `P04` > `P03` > `NOS`.

News: `AAA` has two articles of its own, a copy of the first (dropped by the deduplication),
a 12-ticker roundup (dropped), an article older than the 21-day window and one it shares with
`P01`. `P01` has four articles (the per-peer cap is 3), `P02` one, `H01` one (another sector),
`P03`, `P04` and `NOS` none.

Events: `AAA` has a scheduled earnings date (2024-03-07 after the close; an older snapshot said
2024-03-08), a scheduled ex-dividend date (2024-03-14), a past report (2024-02-01) and a past
dividend; `P01` a scheduled earnings date (2024-03-05 before the open); `P02` only a report a
year ago (so an estimated date 2024-03-07); `P03` a scheduled date (2024-03-06 after the close;
it has no news); `NOS` nothing; a CPI release (macro) on 2024-03-12.
`AAA` has 11 earnings reactions (the last 8 are shown).

A dirty copy adds rows known only after the as-of: later articles and a correction of the
second article, a later earnings snapshot (2024-03-08) and a report on 2024-03-04 with its
reaction. They must never show.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.pit import bar_available_at
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.core.types.instruments import AssetClass, Instrument
from atlas.data.events import EVENT_COLUMNS, MACRO_EVENT_COLUMNS, REACTION_COLUMNS
from atlas.data.news import RAW_COLUMNS, normalize_news
from atlas.data.prices import CORPORATE_ACTIONS, PRICES_DAILY
from atlas.data.reference import instruments_frame
from atlas.data.view import DataView

CAL = TradingCalendar()
AS_OF = date(2024, 3, 1)
"""The world's as-of (08:00 New York = 13:00 UTC: the last known bar is 2024-02-29)."""
LAST = date(2024, 2, 29)
SESSIONS = 30
STOCK = "AAA"
PEERS = ("P01", "P02", "P03", "P04", "NOS")
"""AAA's Technology peers."""
HEALTH = ("H01", "H02")
MEMBERS = (STOCK, *PEERS, *HEALTH, "NOSEC")
BENCHMARK = "SPY"
PEERS_BY_ADV = ("P01", "P02", "P04", "P03", "NOS")
"""The peers, highest average dollar volume first."""

CONFIG = AtlasConfig()

# (price, shares per session): ADV = price x shares, constant over the window.
_TRADING = {
    "AAA": (100.0, 9_000_000),
    "P01": (50.0, 5_000_000),
    "P02": (40.0, 3_000_000),
    "P03": (30.0, 1_000_000),
    "P04": (35.0, 2_000_000),
    "NOS": (10.0, 100_000),
    "H01": (60.0, 4_000_000),
    "H02": (20.0, 1_000_000),
    "NOSEC": (25.0, 500_000),
    BENCHMARK: (500.0, 50_000_000),
}

# AAA's earnings reactions, oldest first: (event day, gap, open-to-close, abnormal, 3-day CAR).
REACTIONS = (
    (date(2021, 8, 3), 0.01, 0.005, 0.004, 0.02),
    (date(2021, 11, 2), -0.03, -0.01, -0.012, -0.04),
    (date(2022, 2, 1), 0.05, 0.01, 0.008, 0.06),
    (date(2022, 5, 3), 0.02, 0.0, -0.001, 0.01),
    (date(2022, 8, 2), -0.01, 0.0, 0.001, -0.005),
    (date(2022, 11, 1), 0.04, 0.02, 0.015, 0.05),
    (date(2023, 1, 31), -0.02, -0.01, -0.008, -0.03),
    (date(2023, 5, 2), 0.03, 0.01, 0.006, 0.02),
    (date(2023, 8, 1), -0.04, -0.02, -0.018, -0.05),
    (date(2023, 10, 31), 0.02, 0.015, 0.01, 0.03),
    (date(2024, 2, 1), 0.01, 0.02, 0.015, 0.03),
)
LAST_EIGHT = REACTIONS[-8:]
"""The reactions the tool shows: the 8 newest."""


def utc(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    """An aware UTC instant."""
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def _stamps(values: list[Any]) -> pd.Series:
    return pd.to_datetime(pd.Series(values, dtype="object"), utc=True).astype("datetime64[us, UTC]")


# --- prices and reference -----------------------------------------------------------------------


def world_bars() -> pd.DataFrame:
    """Bars of every symbol over the last `SESSIONS` sessions (a small repeating wiggle, so no
    price is stale)."""
    days = list(CAL.sessions(CAL.offset(LAST, -(SESSIONS - 1)), LAST))
    frames = []
    wiggle = 1.0 + 0.004 * np.array([i % 5 for i in range(len(days))])  # no stale prices
    for symbol, (price, volume) in _TRADING.items():
        close = price * wiggle
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "date": days,
                    "open": close,
                    "high": close * 1.001,
                    "low": close * 0.999,
                    "close": close,
                    "adj_close": close,
                    "volume": np.full(len(days), volume, dtype="int64"),
                    "vendor": "yfinance",
                    "available_at": _stamps([bar_available_at(d, CAL) for d in days]),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)[list(PRICES_DAILY.column_names)]


def _dividend() -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "symbol": [STOCK],
            "ex_date": [date(2023, 11, 10)],
            "type": "dividend",
            "ratio": [None],
            "amount": [0.24],
            "vendor": "yfinance",
            "available_at": _stamps([bar_available_at(date(2023, 11, 10), CAL)]),
        }
    )
    frame["ratio"] = pd.to_numeric(frame["ratio"]).astype("float64")
    return frame[list(CORPORATE_ACTIONS.column_names)]


def _instruments() -> pd.DataFrame:
    sectors = {
        **{s: "Information Technology" for s in (STOCK, *PEERS)},
        **{s: "Health Care" for s in HEALTH},
        "NOSEC": None,
    }
    rows = [
        Instrument(symbol=s, asset_class=AssetClass.EQUITY, name=f"{s} Inc", sector=sectors[s])
        for s in MEMBERS
    ]
    rows.append(Instrument(symbol=BENCHMARK, asset_class=AssetClass.ETF, name="SPDR S&P 500"))
    return instruments_frame(rows)


def _membership() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "index_id": "SP500",
            "symbol": list(MEMBERS),
            "start_date": date(2010, 1, 4),
            "end_date": None,
        }
    )


# --- news ---------------------------------------------------------------------------------------

_ROUNDUP = ["AAA", "H01", "H02", "P01", "P02", "P03", "P04", "NOS", "NOSEC", "SPY", "QQQ", "ZZZ"]


def _article(
    article_id: str,
    published: datetime,
    headline: str,
    symbols: list[str],
    summary: str = "",
    provider: str = "alpaca",
) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "published_at": published,
        "updated_at": None,
        "provider": provider,
        "source": "benzinga" if provider == "alpaca" else "Reuters",
        "headline": headline,
        "summary": summary,
        "url": f"https://news.example/{article_id}",
        "symbols": symbols,
    }


def world_news(*, future: bool) -> pd.DataFrame:
    """The articles (module docstring); `future=True` adds those known only after the as-of."""
    rows = [
        _article(
            "alpaca-a1",
            utc(2024, 2, 28, 15),
            "AAA raises full-year guidance",
            ["AAA"],
            "The company lifted its revenue outlook by 3%.",
        ),
        _article(
            "alpaca-a2",
            utc(2024, 2, 26, 14),
            "AAA names new chief financial officer",
            ["AAA"],
            "The board picked a former treasurer for the role.",
        ),
        _article(
            "yahoo-a1b",
            utc(2024, 2, 28, 16),
            "AAA raises full-year guidance",
            ["AAA"],
            "The company lifted its revenue outlook by 3%.",
            provider="yahoo",
        ),
        _article("alpaca-a4", utc(2024, 2, 27, 18), "Stocks to watch this week", _ROUNDUP),
        _article("alpaca-a5", utc(2024, 1, 15, 14), "AAA announces a buyback", ["AAA"]),
        _article(
            "alpaca-a6",
            utc(2024, 2, 29, 10),
            "AAA and P01 sign a supply agreement",
            ["AAA", "P01"],
            "Deliveries start in the second quarter.",
        ),
        _article("alpaca-p1", utc(2024, 2, 27, 15), "P01 faces antitrust probe", ["P01"]),
        _article("alpaca-p2", utc(2024, 2, 28, 14), "P01 launches a new chip", ["P01"]),
        _article("alpaca-p3", utc(2024, 2, 29, 14), "P01 chief executive steps down", ["P01"]),
        _article("alpaca-p4", utc(2024, 2, 26, 15), "P01 beats estimates", ["P01"]),
        _article("alpaca-q1", utc(2024, 2, 28, 12), "P02 wins a defence contract", ["P02"]),
        _article("alpaca-h1", utc(2024, 2, 28, 13), "H01 drug approved", ["H01"]),
    ]
    if future:
        rows += [
            _article("alpaca-a7", utc(2024, 3, 1, 15), "AAA sued over patents", ["AAA"]),
            _article("alpaca-a8", utc(2024, 3, 4, 15), "AAA loses a big customer", ["AAA"]),
            _article("alpaca-p5", utc(2024, 3, 1, 14), "P01 recalls its chip", ["P01"]),
            _article("alpaca-q2", utc(2024, 3, 1, 13, 30), "P02 raises prices", ["P02"]),
        ]
    frame = normalize_news(pd.DataFrame(rows, columns=list(RAW_COLUMNS)))
    if future:  # a correction of AAA's second article, published after the cutoff
        fixed = frame.loc[frame["article_id"] == "alpaca-a2"].copy()
        fixed["available_at"] = _stamps([utc(2024, 3, 1, 14)]).to_numpy()
        fixed["updated_at"] = fixed["available_at"]
        fixed["summary"] = "The board picked a former auditor for the role."
        frame = pd.concat([frame, fixed], ignore_index=True)
    return frame


# --- events and reactions -----------------------------------------------------------------------


def _event(
    event_id: str,
    symbol: str,
    event_type: str,
    day: date,
    at: datetime | None,
    tod: str,
    source: str,
    known: datetime,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "symbol": symbol,
        "event_type": event_type,
        "event_date": day,
        "event_time": at,
        "time_of_day": tod,
        "source": source,
        "available_at": known,
    }


def world_events(*, future: bool) -> pd.DataFrame:
    """Company events (module docstring)."""
    e = _event
    rows = [
        e("sec-aaa-1", "AAA", "earnings", date(2024, 2, 1), utc(2024, 2, 1, 21, 30), "AMC",
          "sec_8k", utc(2024, 2, 1, 21, 30)),
        e("yahoo-AAA-2024-03-08", "AAA", "earnings", date(2024, 3, 8), None, "unknown",
          "yahoo", utc(2024, 2, 10, 12)),
        e("yahoo-AAA-2024-03-07", "AAA", "earnings", date(2024, 3, 7), utc(2024, 3, 7, 21), "AMC",
          "yahoo", utc(2024, 2, 20, 12)),
        e("yahoo-AAA-div-2024-03-14", "AAA", "ex_dividend", date(2024, 3, 14), None, "unknown",
          "yahoo", utc(2024, 2, 20, 12)),
        e("yahoo-P01-2024-03-05", "P01", "earnings", date(2024, 3, 5), utc(2024, 3, 5, 12, 30),
          "BMO", "yahoo", utc(2024, 2, 20, 12)),
        e("sec-p02-1", "P02", "earnings", date(2023, 3, 9), utc(2023, 3, 9, 21), "AMC",
          "sec_8k", utc(2023, 3, 9, 21)),
        e("yahoo-P03-2024-03-06", "P03", "earnings", date(2024, 3, 6), utc(2024, 3, 6, 21), "AMC",
          "yahoo", utc(2024, 2, 20, 12)),
    ]  # fmt: skip
    if future:
        rows += [
            e("yahoo-AAA-2024-03-08b", "AAA", "earnings", date(2024, 3, 8), None, "unknown",
              "yahoo", utc(2024, 3, 1, 14)),
            e("sec-aaa-2", "AAA", "earnings", date(2024, 3, 4), utc(2024, 3, 4, 21), "AMC",
              "sec_8k", utc(2024, 3, 4, 21)),
        ]  # fmt: skip
    out = pd.DataFrame(rows, columns=list(EVENT_COLUMNS))
    for col in ("event_time", "available_at"):
        out[col] = _stamps(list(out[col]))
    return out


def world_macro_events() -> pd.DataFrame:
    """A CPI release on 2024-03-12 (market-wide: the tools leave it out)."""
    out = pd.DataFrame(
        [
            {
                "event_id": "cpi-2024-03-12",
                "event_type": "cpi",
                "event_date": date(2024, 3, 12),
                "event_time": utc(2024, 3, 12, 12, 30),
                "source": "fred_release",
                "available_at": utc(2024, 1, 1, 5),
                "withdrawn_at": None,
            }
        ],
        columns=list(MACRO_EVENT_COLUMNS),
    )
    for col in ("event_time", "available_at", "withdrawn_at"):
        out[col] = _stamps(list(out[col]))
    return out


def world_reactions(*, future: bool) -> pd.DataFrame:
    """AAA's earnings reactions (`REACTIONS`); `future=True` adds the 2024-03-04 report's."""
    rows = []
    for i, (day, gap, oc, abnormal, car) in enumerate(REACTIONS):
        first = CAL.next_session(day)  # an after-the-close report reacts the next session
        rows.append(
            {
                "symbol": STOCK,
                "event_id": "sec-aaa-1" if i == len(REACTIONS) - 1 else f"sec-aaa-r{i}",
                "event_time": utc(day.year, day.month, day.day, 21, 30),
                "reaction_date": first,
                "window_end": CAL.offset(first, 2),
                "gap_return": gap,
                "oc_return": oc,
                "oc_abnormal": abnormal,
                "car": car,
                "benchmark": BENCHMARK,
                "available_at": bar_available_at(CAL.offset(first, 2), CAL),
            }
        )
    if future:
        rows.append(
            {
                "symbol": STOCK,
                "event_id": "sec-aaa-2",
                "event_time": utc(2024, 3, 4, 21),
                "reaction_date": date(2024, 3, 5),
                "window_end": date(2024, 3, 7),
                "gap_return": -0.2,
                "oc_return": -0.1,
                "oc_abnormal": -0.1,
                "car": -0.3,
                "benchmark": BENCHMARK,
                "available_at": bar_available_at(date(2024, 3, 7), CAL),
            }
        )
    out = pd.DataFrame(rows, columns=list(REACTION_COLUMNS))
    for col in ("event_time", "available_at"):
        out[col] = _stamps(list(out[col]))
    return out


# --- the world ----------------------------------------------------------------------------------


def write_world(lake: Lake, *, future: bool) -> None:
    """Write the world's tables; `future=True` adds the rows known only after the as-of."""
    tables = {
        "prices_daily": world_bars(),
        "corporate_actions": _dividend(),
        "instruments": _instruments(),
        "index_membership": _membership(),
        "news": world_news(future=future),
        "events": world_events(future=future),
        "macro_events": world_macro_events(),
        "earnings_reactions": world_reactions(future=future),
    }
    for name, frame in tables.items():
        lake.write_table(name, frame, mode="upsert")


@dataclass(frozen=True)
class NewsWorld:
    clean: Lake
    dirty: Lake

    def view(
        self, which: str = "clean", day: date = AS_OF, config: AtlasConfig = CONFIG
    ) -> DataView:
        return DataView(self.clean if which == "clean" else self.dirty, day, config)


def build_news_world(root: Path) -> NewsWorld:
    """The clean and the dirty lake under `root`."""
    clean = Lake(root / "clean", registry=DEFAULT_REGISTRY.copy())
    write_world(clean, future=False)
    dirty = Lake(root / "dirty", registry=DEFAULT_REGISTRY.copy())
    write_world(dirty, future=True)
    return NewsWorld(clean=clean, dirty=dirty)
