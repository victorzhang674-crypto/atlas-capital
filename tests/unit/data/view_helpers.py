"""Synthetic lake rows for the Stage 13 quality and DataView tests (no network)."""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from itertools import count
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.pit import bar_available_at
from atlas.data.fundamentals import FACT_COLUMNS, ConceptMap, load_concept_map
from atlas.data.macro import OBS_COLUMNS
from atlas.data.prices import CORPORATE_ACTIONS, PRICES_DAILY

CAL = TradingCalendar()
NY = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def utc(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    """An aware UTC instant."""
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def et(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    """An aware New York instant."""
    return datetime(y, m, d, h, mi, tzinfo=NY)


def sessions_ending(last: date, n: int) -> list[date]:
    """The `n` sessions ending at session `last`, oldest first."""
    return list(CAL.sessions(CAL.offset(last, -(n - 1)), last))


def _stamps(days: Sequence[date]) -> pd.Series:
    values = [bar_available_at(d, CAL) for d in days]
    return pd.to_datetime(pd.Series(values, dtype="object"), utc=True).astype("datetime64[us, UTC]")


def make_bars(
    symbol: str,
    closes: Sequence[float],
    *,
    end: date,
    volume: int | Sequence[int] = 1_000_000,
    vendor: str | Sequence[str] = "yfinance",
    days: Sequence[date] | None = None,
) -> pd.DataFrame:
    """`prices_daily` rows of `symbol`: one bar per session ending at `end` (or on `days`),
    open = high = low = close, `adj_close` = close, `available_at` = the bar's availability."""
    dates = list(days) if days is not None else sessions_ending(end, len(closes))
    if len(dates) != len(closes):
        raise ValueError("days and closes differ in length")
    n = len(dates)
    vols = [volume] * n if isinstance(volume, int) else list(volume)
    vendors = [vendor] * n if isinstance(vendor, str) else list(vendor)
    frame = pd.DataFrame(
        {
            "symbol": [symbol] * n,
            "date": dates,
            "open": np.asarray(closes, dtype="float64"),
            "high": np.asarray(closes, dtype="float64"),
            "low": np.asarray(closes, dtype="float64"),
            "close": np.asarray(closes, dtype="float64"),
            "adj_close": np.asarray(closes, dtype="float64"),
            "volume": np.asarray(vols, dtype="int64"),
            "vendor": vendors,
        }
    )
    frame["available_at"] = _stamps(dates)
    return frame[list(PRICES_DAILY.column_names)]


def walk(n: int, *, start: float = 100.0, vol: float = 0.01, seed: int = 0) -> list[float]:
    """`n` closes of a geometric random walk with daily volatility `vol` (seeded)."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, vol, size=n - 1)
    return list(start * np.exp(np.concatenate([[0.0], np.cumsum(steps)])))


def make_actions(*rows: tuple[str, date, str, float | None, float | None]) -> pd.DataFrame:
    """`corporate_actions` rows from `(symbol, ex_date, type, ratio, amount)`; `available_at` is
    the availability of the first session on or after the ex-date (as Stage 07 stores it)."""
    frame = pd.DataFrame(
        [(s, d, t, r, a, "yfinance") for s, d, t, r, a in rows],
        columns=["symbol", "ex_date", "type", "ratio", "amount", "vendor"],
    )
    sessions = [d if CAL.is_session(d) else CAL.next_session(d) for d in frame["ex_date"]]
    frame["ratio"] = frame["ratio"].astype("float64")
    frame["amount"] = frame["amount"].astype("float64")
    frame["available_at"] = _stamps(sessions)
    return frame[list(CORPORATE_ACTIONS.column_names)]


def no_actions() -> pd.DataFrame:
    """An empty `corporate_actions` frame."""
    return make_actions()


def repo_concept_map() -> ConceptMap:
    """The repository's XBRL concept map (`config/xbrl_map.yaml`)."""
    return load_concept_map(REPO_ROOT / "config" / "xbrl_map.yaml")


_ACCN = count(1)


def make_facts(symbol: str, *specs: Mapping[str, Any]) -> pd.DataFrame:
    """`fundamentals_facts` rows of `symbol`. Each spec needs `tag`, `value`, `end` (date) and
    `filed` (date); optional `start` (date; omitted = instant), `available_at` (aware; default
    21:00 UTC on `filed`), `unit` ("USD"), `accn` (unique), `form` ("10-Q")."""
    rows = []
    for spec in specs:
        start: date | None = spec.get("start")
        end: date = spec["end"]
        filed: date = spec["filed"]
        available = spec.get("available_at", datetime.combine(filed, time(21), tzinfo=UTC))
        rows.append(
            {
                "symbol": symbol,
                "cik": 1,
                "taxonomy": "us-gaap",
                "tag": spec["tag"],
                "unit": spec.get("unit", "USD"),
                "value": float(spec["value"]),
                "period_start": start,
                "period_end": end,
                "duration_days": 0 if start is None else (end - start).days + 1,
                "fy": None,
                "fp": None,
                "form": spec.get("form", "10-Q"),
                "filed": filed,
                "accn": spec.get("accn", f"0000000001-00-{next(_ACCN):06d}"),
                "accepted_at": available,
                "available_at": available,
            }
        )
    df = pd.DataFrame(rows, columns=list(FACT_COLUMNS))
    df["fy"] = df["fy"].astype("Int64")
    for col in ("accepted_at", "available_at"):
        df[col] = pd.to_datetime(df[col], utc=True).astype("datetime64[us, UTC]")
    return df


QUARTERS = (
    (date(2023, 1, 1), date(2023, 3, 31), date(2023, 5, 5)),
    (date(2023, 4, 1), date(2023, 6, 30), date(2023, 8, 4)),
    (date(2023, 7, 1), date(2023, 9, 30), date(2023, 11, 3)),
    (date(2023, 10, 1), date(2023, 12, 31), date(2024, 2, 15)),
)
"""`(start, end, filed)` of four fiscal quarters; the last is filed inside the Stage 13 test
window (sessions to 2024-02-29)."""


def quarterly_facts(
    symbol: str, tag: str, values: Sequence[float], *, instant: bool = False
) -> pd.DataFrame:
    """One fact per quarter in `QUARTERS` (a flow over the quarter, or a balance at its end)."""
    specs = [
        {"tag": tag, "value": v, "end": end, "filed": filed} | ({} if instant else {"start": start})
        for (start, end, filed), v in zip(QUARTERS, values, strict=True)
    ]
    return make_facts(symbol, *specs)


def next_morning(d: date) -> datetime:
    """06:00 New York the day after `d` (when a market series' value for `d` is published)."""
    return datetime.combine(d + timedelta(days=1), time(6), tzinfo=NY)


def macro_rows(
    series_id: str,
    dates: Sequence[date],
    *,
    value: float = 1.0,
    available: Callable[[date], datetime] = next_morning,
) -> pd.DataFrame:
    """`macro_observations` rows of `series_id`, one first-release vintage per date
    (`realtime_start` = the publication date)."""
    stamps = [available(d) for d in dates]
    frame = pd.DataFrame(
        {
            "series_id": [series_id] * len(dates),
            "obs_date": list(dates),
            "value": [float(value) + i * 0.01 for i in range(len(dates))],
            "realtime_start": [market_date_of(t) for t in stamps],
            "available_at": pd.to_datetime(pd.Series(stamps, dtype="object"), utc=True),
        }
    )
    frame["available_at"] = frame["available_at"].astype("datetime64[us, UTC]")
    return frame[list(OBS_COLUMNS)]


def market_date_of(ts: datetime) -> date:
    """The New York date of `ts`."""
    return ts.astimezone(NY).date()


def business_days(start: date, end: date) -> list[date]:
    """Weekdays from `start` to `end`, inclusive."""
    return [ts.date() for ts in pd.bdate_range(start, end)]


def month_starts(start: date, end: date) -> list[date]:
    """First days of the months from `start` to `end`, inclusive."""
    return [ts.date() for ts in pd.date_range(start, end, freq="MS")]


# --- the Stage 13 test world: one lake known at the as-of, one with later rows in every table --

WORLD_AS_OF = date(2024, 3, 1)
"""The as-of of the leak suite (cutoff 08:00 New York: the last known bar is 2024-02-29)."""

WORLD_LATER = date(2024, 3, 18)
"""A later as-of at which every injected row is known."""

HISTORY = sessions_ending(date(2024, 2, 29), 300)
FUTURE = list(CAL.sessions(date(2024, 3, 1), date(2024, 3, 15)))


def _ohlcv(symbol: str, closes: Sequence[float], days: Sequence[date]) -> pd.DataFrame:
    return make_bars(symbol, closes, end=days[-1], days=days, volume=2_000_000)


def _prices(future: bool) -> pd.DataFrame:
    days = HISTORY + FUTURE if future else HISTORY
    n = len(days)
    frames = []
    for seed, symbol in enumerate(["AAA", "BBB", "CCC", "SPY", "BAD"], start=1):
        closes = walk(n, seed=seed)
        if symbol == "AAA":  # a 2:1 split on 2024-01-16, recorded in corporate_actions
            at = days.index(date(2024, 1, 16))
            closes = closes[:at] + [c / 2 for c in closes[at:]]
        if symbol == "CCC" and future:  # a 3:1 split on 2024-03-05 (known only later)
            at = days.index(date(2024, 3, 5))
            closes = closes[:at] + [c / 3 for c in closes[at:]]
        frame = _ohlcv(symbol, closes, days)
        if symbol == "BAD":  # a zero close inside the window: BAD is blocked
            frame.loc[frame["date"] == date(2024, 2, 27), ["open", "high", "low", "close"]] = 0.0
        if symbol == "CCC" and future:  # a zero bar on the as-of day itself: unknown at 08:00
            frame.loc[frame["date"] == date(2024, 3, 1), ["open", "high", "low", "close"]] = 0.0
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _actions(future: bool) -> pd.DataFrame:
    rows = [
        ("AAA", date(2024, 1, 16), "split", 2.0, None),
        ("BBB", date(2024, 2, 9), "dividend", None, 0.5),
    ]
    if future:
        rows += [
            ("BBB", date(2024, 3, 4), "dividend", None, 0.75),
            ("CCC", date(2024, 3, 5), "split", 3.0, None),
            ("AAA", date(2024, 2, 20), "dividend", None, 1.0),
        ]
    actions = make_actions(*rows)
    if future:  # a past ex-date recorded late: known only from 2024-03-04 (a vendor correction)
        late = (actions["symbol"] == "AAA") & (actions["ex_date"] == date(2024, 2, 20))
        actions.loc[late, "available_at"] = pd.Timestamp(bar_available_at(date(2024, 3, 4), CAL))
    return actions


def _instruments() -> pd.DataFrame:
    from atlas.core.types.instruments import AssetClass, Instrument
    from atlas.data.reference import instruments_frame

    stocks = [
        Instrument(symbol=s, asset_class=AssetClass.EQUITY, name=f"{s} Corp", sector="Tech")
        for s in ("AAA", "BBB", "CCC", "BAD", "DDD")
    ]
    spy = Instrument(symbol="SPY", asset_class=AssetClass.ETF, name="SPDR S&P 500")
    return instruments_frame([*stocks, spy])


def _membership(future: bool) -> pd.DataFrame:
    start = date(2010, 1, 4)
    rows: list[tuple[str, str, date, date | None]] = [
        ("SP500", s, start, None) for s in ("AAA", "BBB", "CCC", "BAD")
    ]
    if future:
        rows = [r if r[1] != "BBB" else ("SP500", "BBB", start, date(2024, 3, 6)) for r in rows]
        rows.append(("SP500", "DDD", date(2024, 3, 4), None))
    frame = pd.DataFrame(rows, columns=["index_id", "symbol", "start_date", "end_date"])
    frame["end_date"] = frame["end_date"].astype(object).where(frame["end_date"].notna(), None)
    return frame


def _facts(future: bool) -> pd.DataFrame:
    frames = [
        quarterly_facts(s, "Revenues", [100e6, 105e6, 110e6, 115e6]) for s in ("AAA", "BBB", "BAD")
    ] + [
        quarterly_facts(s, "Assets", [1e9, 1.01e9, 1.02e9, 1.03e9], instant=True)
        for s in ("AAA", "BBB", "BAD")
    ]
    if future:
        start, end, _ = QUARTERS[3]
        frames += [
            make_facts(
                "AAA",
                {
                    "tag": "Revenues",
                    "value": 118e6,
                    "start": start,
                    "end": end,
                    "filed": date(2024, 3, 4),
                    "form": "10-K",
                },
            ),
            make_facts(
                "BBB",
                {
                    "tag": "Assets",
                    "value": 1.04e9,
                    "end": end,
                    "filed": date(2024, 3, 5),
                    "form": "10-K",
                },
            ),
        ]
    return pd.concat(frames, ignore_index=True)


def _cpi_release(d: date) -> datetime:
    month = d.month % 12 + 1
    year = d.year + (d.month == 12)
    return datetime(year, month, 12, 13, 30, tzinfo=UTC)


def _macro(future: bool) -> pd.DataFrame:
    frames = [
        macro_rows("DGS10", business_days(date(2023, 6, 1), date(2024, 2, 29)), value=4.0),
        macro_rows(
            "CPIAUCSL",
            month_starts(date(2022, 1, 1), date(2024, 1, 1)),
            value=300.0,
            available=_cpi_release,
        ),
    ]
    if future:
        frames += [
            macro_rows("DGS10", business_days(date(2024, 3, 1), date(2024, 3, 14)), value=4.5),
            macro_rows(
                "CPIAUCSL",
                [date(2024, 1, 1)],
                value=310.0,
                available=lambda d: utc(2024, 3, 12, 12, 30),
            ),  # a revision of January, published with February's release
            macro_rows(
                "CPIAUCSL",
                [date(2024, 2, 1)],
                value=311.0,
                available=lambda d: utc(2024, 3, 12, 12, 30),
            ),
        ]
    return pd.concat(frames, ignore_index=True)


def _news(future: bool) -> pd.DataFrame:
    from atlas.data.news import RAW_COLUMNS, normalize_news

    def row(
        article_id: str, published: datetime, headline: str, symbols: list[str]
    ) -> dict[str, Any]:
        return {
            "article_id": article_id,
            "published_at": published,
            "updated_at": None,
            "provider": "alpaca",
            "source": "benzinga",
            "headline": headline,
            "summary": "",
            "url": None,
            "symbols": symbols,
        }

    rows = [
        row("alpaca-1", utc(2024, 2, 27, 15), "AAA posts strong gains", ["AAA"]),
        row("alpaca-2", utc(2024, 2, 29, 20), "AAA improved profitability", ["AAA"]),
        row("alpaca-3", utc(2024, 2, 28, 14), "Stocks gain as markets rise", ["SPY"]),
        row("alpaca-4", utc(2024, 2, 28, 16), "BAD default fears", ["BAD"]),
    ]
    if future:
        rows += [
            row(
                "alpaca-10", utc(2024, 3, 1, 13, 30), "AAA bankruptcy fears, weak decline", ["AAA"]
            ),
            row("alpaca-11", utc(2024, 3, 1, 14), "Stocks decline on weak data", ["SPY"]),
            row("alpaca-12", utc(2024, 3, 4, 15), "AAA loss and downgrade", ["AAA"]),
            row("alpaca-13", utc(2024, 3, 11, 15), "Markets weak after failure", ["SPY"]),
        ]
    return normalize_news(pd.DataFrame(rows, columns=list(RAW_COLUMNS)))


def _events(future: bool) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    from atlas.data.events import EVENT_COLUMNS, MACRO_EVENT_COLUMNS, REACTION_COLUMNS

    def event(
        eid: str,
        symbol: str,
        day: date,
        at: datetime | None,
        tod: str,
        source: str,
        known: datetime,
    ) -> dict[str, Any]:
        return {
            "event_id": eid,
            "symbol": symbol,
            "event_type": "earnings",
            "event_date": day,
            "event_time": at,
            "time_of_day": tod,
            "source": source,
            "available_at": known,
        }

    events = [
        event(
            "sec-aaa-1",
            "AAA",
            date(2024, 2, 1),
            utc(2024, 2, 1, 21, 30),
            "AMC",
            "sec_8k",
            utc(2024, 2, 1, 21, 30),
        ),
        event(
            "sec-bad-1",
            "BAD",
            date(2024, 2, 1),
            utc(2024, 2, 1, 21, 30),
            "AMC",
            "sec_8k",
            utc(2024, 2, 1, 21, 30),
        ),
        event(
            "yahoo-aaa-0312",
            "AAA",
            date(2024, 3, 12),
            None,
            "unknown",
            "yahoo",
            utc(2024, 2, 20, 12),
        ),
        event(
            "yahoo-bad-0313",
            "BAD",
            date(2024, 3, 13),
            None,
            "unknown",
            "yahoo",
            utc(2024, 2, 20, 12),
        ),
    ]
    macro = [
        {
            "event_id": "cpi-2024-03-12",
            "event_type": "cpi",
            "event_date": date(2024, 3, 12),
            "event_time": utc(2024, 3, 12, 12, 30),
            "source": "fred_release",
            "available_at": utc(2024, 1, 1, 5),
            "withdrawn_at": None,
        },
    ]
    reactions = [
        {
            "symbol": s,
            "event_id": f"sec-{s.lower()}-1",
            "event_time": utc(2024, 2, 1, 21, 30),
            "reaction_date": date(2024, 2, 2),
            "window_end": date(2024, 2, 6),
            "gap_return": 0.01,
            "oc_return": 0.02,
            "oc_abnormal": 0.015,
            "car": 0.03,
            "benchmark": "SPY",
            "available_at": bar_available_at(date(2024, 2, 6), CAL),
        }
        for s in ("AAA", "BAD")
    ]
    if future:
        events += [
            event(
                "sec-aaa-2",
                "AAA",
                date(2024, 3, 4),
                utc(2024, 3, 4, 21),
                "AMC",
                "sec_8k",
                utc(2024, 3, 4, 21),
            ),
            event(
                "yahoo-bbb-0320",
                "BBB",
                date(2024, 3, 20),
                None,
                "unknown",
                "yahoo",
                utc(2024, 3, 1, 14),
            ),
        ]
        macro.append(
            {
                "event_id": "nfp-2024-03-22",
                "event_type": "nfp",
                "event_date": date(2024, 3, 22),
                "event_time": utc(2024, 3, 22, 12, 30),
                "source": "fred_release",
                "available_at": utc(2024, 3, 1, 15),
                "withdrawn_at": None,
            }
        )
        reactions.append(
            {
                "symbol": "AAA",
                "event_id": "sec-aaa-2",
                "event_time": utc(2024, 3, 4, 21),
                "reaction_date": date(2024, 3, 5),
                "window_end": date(2024, 3, 7),
                "gap_return": -0.05,
                "oc_return": -0.02,
                "oc_abnormal": -0.03,
                "car": -0.08,
                "benchmark": "SPY",
                "available_at": bar_available_at(date(2024, 3, 7), CAL),
            }
        )

    def frame(rows: list[dict[str, Any]], columns: Sequence[str]) -> pd.DataFrame:
        out = pd.DataFrame(rows, columns=list(columns))
        for col in ("event_time", "available_at", "withdrawn_at"):
            if col in out.columns:
                out[col] = pd.to_datetime(
                    pd.Series(list(out[col]), dtype="object"), utc=True
                ).astype("datetime64[us, UTC]")
        return out

    return (
        frame(events, EVENT_COLUMNS),
        frame(macro, MACRO_EVENT_COLUMNS),
        frame(reactions, REACTION_COLUMNS),
    )


def _posts(future: bool) -> pd.DataFrame:
    from atlas.data.sentiment import POST_COLUMNS

    def post(pid: str, symbol: str, at: datetime, text: str) -> dict[str, Any]:
        return {
            "post_id": pid,
            "symbol": symbol,
            "created_at": at,
            "available_at": at,
            "source": "stocktwits",
            "text": text,
            "url": None,
        }

    rows = [
        post("st-1", "AAA", utc(2024, 2, 29, 15), "AAA looks strong"),
        post("st-9", "BAD", utc(2024, 2, 29, 15), "BAD is done"),
    ]
    if future:
        rows += [
            post("st-2", "AAA", utc(2024, 3, 1, 14), "AAA is falling"),
            post("st-3", "AAA", utc(2024, 3, 15, 14), "AAA new lows"),
        ]
    out = pd.DataFrame(rows, columns=list(POST_COLUMNS))
    for col in ("created_at", "available_at"):
        out[col] = pd.to_datetime(out[col], utc=True).astype("datetime64[us, UTC]")
    return out


def build_world(lake: Any, *, future: bool) -> None:
    """Fill `lake` with the Stage 13 test world: prices, actions, instruments, membership,
    fundamentals, macro, news, events, macro events, reactions and social posts known at
    `WORLD_AS_OF` 08:00; with `future=True` also rows in every table known only later."""
    events, macro_events, reactions = _events(future)
    tables = {
        "prices_daily": _prices(future),
        "corporate_actions": _actions(future),
        "instruments": _instruments(),
        "index_membership": _membership(future),
        "fundamentals_facts": _facts(future),
        "macro_observations": _macro(future),
        "news": _news(future),
        "events": events,
        "macro_events": macro_events,
        "earnings_reactions": reactions,
        "social_posts": _posts(future),
    }
    for name, frame in tables.items():
        lake.write_table(name, frame, mode="upsert")


def world_lexicon(folder: Path) -> Any:
    """The lexicon built from the recorded Loughran-McDonald excerpt, written under `folder`."""
    from atlas.data.sentiment import build_lm_lexicon, load_lexicon

    path = folder / "lm_lexicon.csv"
    build_lm_lexicon(pd.read_csv(FIXTURES / "sentiment" / "lm_master_sample.csv")).to_csv(
        path, index=False
    )
    return load_lexicon(path)


def world_catalog() -> Any:
    """The repository's macro series catalog (`config/macro.yaml`)."""
    from atlas.data.macro import load_macro_series

    return load_macro_series(REPO_ROOT / "config" / "macro.yaml")
