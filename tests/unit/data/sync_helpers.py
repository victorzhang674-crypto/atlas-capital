"""A small fake data world for the Stage 14 sync tests (and the CLI tests).

Index: AAPL and MSFT all along, OLD removed and NEW added on 2024-02-01; ETFs SPY and QQQ.
Prices come from a `FakePriceProvider`, news from a fake Alpaca provider, scheduled dates
from a fake calendar; EDGAR answers from the recorded AAPL/MSFT fixtures (respx); FRED
from an in-process fake client over two series. The clock sits at 2024-04-01 08:00 ET.
"""

import json
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import respx

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, FundamentalsConfig
from atlas.core.errors import DataError
from atlas.data.fundamentals import CikMap, EdgarClient
from atlas.data.news import RAW_COLUMNS, normalize_news
from atlas.data.providers.base import (
    check_request,
    empty_actions,
    normalize_actions,
    normalize_daily,
)
from atlas.data.sentiment import build_lm_lexicon, load_lexicon
from atlas.data.sync import SyncSources

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
TEST_USER_AGENT = "Atlas test suite tests@example.invalid"

START = date(2024, 1, 2)
TODAY = date(2024, 4, 1)  # a Monday session; 2024-03-29 is Good Friday
LAST_BAR = date(2024, 3, 28)
CHANGE_DAY = date(2024, 2, 1)
MEMBERS = ("AAPL", "MSFT", "NEW", "OLD")
CURRENT = ("AAPL", "MSFT", "NEW")
ETFS = ("SPY", "QQQ")
SERIES = ("CPIAUCSL", "DGS10")


# --- Wikipedia pages -------------------------------------------------------------------------


def constituents_page(rows: Sequence[tuple[str, str, date]]) -> str:
    """A minimal `table#constituents` page: `(symbol, name, date added)` rows on XNAS."""
    head = (
        "<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th>"
        "<th>Headquarters Location</th><th>Date added</th><th>CIK</th><th>Founded</th></tr>"
    )
    body = "".join(
        f'<tr><td><a href="https://www.nasdaq.com/market-activity/stocks/{s.lower()}" '
        f'class="external text">{s}</a></td><td>{n}</td><td>Information Technology</td>'
        f"<td>Software</td><td>Somewhere</td><td>{d.isoformat()}</td><td>0000000001</td>"
        "<td>1990</td></tr>"
        for s, n, d in rows
    )
    return f'<html><body><table id="constituents"><tbody>{head}{body}</tbody></table></body></html>'


def changes_page(rows: Sequence[tuple[date, str, str, str, str, str]]) -> str:
    """A minimal `table#changes` page: `(effective, added, name, removed, name, reason)`."""
    head = (
        '<tr><th rowspan="2">Effective Date</th><th colspan="2">Added</th>'
        '<th colspan="2">Removed</th><th rowspan="2">Reason</th><th rowspan="2">Refs</th></tr>'
        "<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>"
    )
    body = "".join(
        f"<tr><td>{d:%B} {d.day}, {d.year}</td><td>{a}</td><td>{an}</td><td>{r}</td>"
        f"<td>{rn}</td><td>{why}</td><td></td></tr>"
        for d, a, an, r, rn, why in rows
    )
    return f'<html><body><table id="changes"><tbody>{head}{body}</tbody></table></body></html>'


def world_pages() -> tuple[str, str]:
    """The world's two Wikipedia pages."""
    constituents = constituents_page(
        [
            ("AAPL", "Apple Inc.", date(1982, 11, 30)),
            ("MSFT", "Microsoft", date(1994, 6, 1)),
            ("NEW", "New Co", CHANGE_DAY),
        ]
    )
    changes = changes_page([(CHANGE_DAY, "NEW", "New Co", "OLD", "Old Co", "Acquired")])
    return constituents, changes


# --- prices ----------------------------------------------------------------------------------


def sessions(start: date, end: date) -> tuple[date, ...]:
    return TradingCalendar().sessions(start, end)


def world_bars(last: date = LAST_BAR) -> pd.DataFrame:
    """Bars from 2023-12-01 to `last`: OLD until 2024-01-31, NEW from 2024-02-01."""
    frames = []
    spans = {
        "AAPL": (date(2023, 12, 1), last),
        "MSFT": (date(2023, 12, 1), last),
        "OLD": (date(2023, 12, 1), date(2024, 1, 31)),
        "NEW": (CHANGE_DAY, last),
        "SPY": (date(2023, 12, 1), last),
        "QQQ": (date(2023, 12, 1), last),
    }
    for i, (symbol, (first, end)) in enumerate(spans.items()):
        days = sessions(first, end)
        closes = [100.0 + i * 10 + 0.1 * k + (0.5 if k % 2 else 0.0) for k in range(len(days))]
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "date": list(days),
                    "open": closes,
                    "high": [c * 1.01 for c in closes],
                    "low": [c * 0.99 for c in closes],
                    "close": closes,
                    "adj_close": closes,
                    "volume": [1_000_000 + k for k in range(len(days))],
                    "vendor": "fake",
                }
            )
        )
    return normalize_daily(pd.concat(frames, ignore_index=True))


class WorldPrices:
    """A thread-safe fake `PriceProvider` over `world_bars()`; records every daily request.
    `crash_on` (1-based daily call) raises `crash` (default `KeyboardInterrupt`)."""

    name = "fake"

    def __init__(
        self,
        bars: pd.DataFrame | None = None,
        *,
        crash_on: int | None = None,
        crash: BaseException | None = None,
        fail: Sequence[str] = (),
    ) -> None:
        self.bars = world_bars() if bars is None else bars
        self.crash_on = crash_on
        self.crash = crash if crash is not None else KeyboardInterrupt()
        self.fail = set(fail)
        self.calls: list[tuple[tuple[str, ...], date, date]] = []
        self._lock = threading.Lock()

    def fetch_daily(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        syms = check_request(symbols, start, end)
        with self._lock:
            self.calls.append((syms, start, end))
            number = len(self.calls)
        if number == self.crash_on:
            raise self.crash
        if self.fail & set(syms):
            raise DataError(f"fake: vendor error for {sorted(self.fail & set(syms))}")
        b = self.bars
        mask = b["symbol"].isin(syms) & (b["date"] >= start) & (b["date"] <= end)
        return b[mask].reset_index(drop=True)

    def fetch_actions(self, symbols: Sequence[str], start: date, end: date) -> pd.DataFrame:
        check_request(symbols, start, end)
        return normalize_actions(empty_actions())


# --- news ------------------------------------------------------------------------------------


def article(
    article_id: str, published: datetime, symbols: Sequence[str], headline: str
) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "published_at": published,
        "updated_at": None,
        "provider": "alpaca",
        "source": "benzinga",
        "headline": headline,
        "summary": "Shares gained after strong results and record growth.",
        "url": None,
        "symbols": list(symbols),
    }


def world_articles() -> pd.DataFrame:
    rows = [
        article(
            "alpaca-1", datetime(2024, 1, 10, 15, tzinfo=UTC), ["AAPL"], "Apple beats estimates"
        ),
        article("alpaca-2", datetime(2024, 2, 15, 15, tzinfo=UTC), ["MSFT"], "Microsoft gains"),
        article("alpaca-3", datetime(2024, 3, 20, 15, tzinfo=UTC), ["NEW", "AAPL"], "New Co joins"),
        article("alpaca-4", datetime(2024, 1, 20, 15, tzinfo=UTC), ["OLD"], "Old Co acquired"),
        article("alpaca-5", datetime(2024, 3, 5, 15, tzinfo=UTC), ["SPY"], "Stocks rally"),
    ]
    return normalize_news(pd.DataFrame(rows, columns=list(RAW_COLUMNS)))


class WorldNews:
    """A thread-safe fake news provider named `alpaca`: articles tagged with a requested
    symbol, published in `[start, end)`; records requests; `error` makes every call raise."""

    name = "alpaca"

    def __init__(self, frame: pd.DataFrame | None = None, error: Exception | None = None) -> None:
        self.frame = world_articles() if frame is None else frame
        self.error = error
        self.calls: list[tuple[tuple[str, ...] | None, datetime, datetime]] = []
        self._lock = threading.Lock()

    def fetch_news(
        self, symbols: Sequence[str] | None, start: datetime, end: datetime
    ) -> pd.DataFrame:
        wanted = None if symbols is None else tuple(symbols)
        with self._lock:
            self.calls.append((wanted, start, end))
        if self.error is not None:
            raise self.error
        f = self.frame
        inside = (f["published_at"] >= start) & (f["published_at"] < end)
        if wanted is not None:
            inside &= f["symbols"].map(lambda tags: bool(set(tags) & set(wanted)))
        return f.loc[inside].reset_index(drop=True)


class WorldCalendar:
    """A fake scheduled-dates provider: AAPL and MSFT report on 2024-04-25."""

    name = "yahoo"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail: set[str] = set()

    def fetch_upcoming(self, symbols: Sequence[str]) -> pd.DataFrame:
        self.calls.append(tuple(symbols))
        if self.fail & set(symbols):
            raise DataError(f"yahoo calendar {sorted(self.fail & set(symbols))[0]} failed")
        rows = [s for s in symbols if s in ("AAPL", "MSFT")]
        return pd.DataFrame(
            {
                "symbol": pd.Series(rows, dtype="str"),
                "event_type": pd.Series(["earnings"] * len(rows), dtype="str"),
                "event_date": pd.Series([date(2024, 4, 25)] * len(rows), dtype="object"),
            }
        )


# --- FRED ------------------------------------------------------------------------------------


class WorldFred:
    """An in-process fake `FredClient` over two series with one vintage each (thread-safe)."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []
        self.closed = False
        self._lock = threading.Lock()

    def _note(self, what: str, series_id: str) -> None:
        with self._lock:
            self.calls.append((what, series_id))
        if self.error is not None:
            raise self.error

    def series(self, series_id: str, *, refresh: bool = False) -> dict[str, Any]:
        self._note("series", series_id)
        freq = "M" if series_id == "CPIAUCSL" else "D"
        return {
            "seriess": [
                {
                    "id": series_id,
                    "title": series_id,
                    "frequency_short": freq,
                    "units": "Index" if freq == "M" else "Percent",
                    "seasonal_adjustment_short": "SA" if freq == "M" else "NSA",
                }
            ]
        }

    def vintage_dates(
        self, series_id: str, *, realtime_start: date, realtime_end: date, refresh: bool = False
    ) -> tuple[date, ...]:
        self._note("vintages", series_id)
        return (date(2024, 2, 13),) if series_id == "CPIAUCSL" else (date(2024, 1, 3),)

    def observations(
        self,
        series_id: str,
        *,
        observation_start: date,
        observation_end: date,
        realtime_start: date,
        realtime_end: date,
        refresh: bool = False,
    ) -> dict[str, Any]:
        self._note("observations", series_id)
        if series_id == "CPIAUCSL":
            rows = [("2023-12-01", "308.7"), ("2024-01-01", "309.7")]
            vintage = "2024-02-13"
        else:
            rows = [("2024-01-02", "3.95"), ("2024-01-03", "3.91")]
            vintage = "2024-01-03"
        return {
            "count": len(rows),
            "offset": 0,
            "observations": [
                {"realtime_start": vintage, "realtime_end": "9999-12-31", "date": d, "value": v}
                for d, v in rows
            ],
        }

    def close(self) -> None:
        self.closed = True


def world_macro_yaml(folder: Path) -> Path:
    path = folder / "macro.yaml"
    path.write_text(
        "series:\n"
        '  CPIAUCSL: {kind: release, release_time: "08:30", description: "CPI"}\n'
        '  DGS10: {kind: market, description: "10-year yield"}\n'
    )
    return path


# --- EDGAR -----------------------------------------------------------------------------------


def serve_sec(request: httpx.Request) -> httpx.Response:
    """respx side effect: EDGAR companyfacts and submissions from the recorded fixtures."""
    name = request.url.path.rsplit("/", 1)[-1]
    if "/companyfacts/" in request.url.path:
        candidates = [FIXTURES / "fundamentals" / "companyfacts" / name]
    else:
        candidates = [
            FIXTURES / "events" / "submissions" / name,
            FIXTURES / "fundamentals" / "submissions" / name,
        ]
    for file in candidates:
        if file.exists():
            return httpx.Response(200, content=file.read_bytes())
    return httpx.Response(404)


def sec_router() -> Iterator[respx.MockRouter]:
    """A respx router answering data.sec.gov from the fixtures (use as a fixture body)."""
    with respx.mock(assert_all_called=False) as router:
        router.route(host="data.sec.gov").mock(side_effect=serve_sec)
        yield router


def world_cik_map(client: EdgarClient) -> CikMap:
    payload = json.loads((FIXTURES / "fundamentals" / "company_tickers.json").read_text())
    return CikMap.from_company_tickers(payload)


class NoSleep:
    def __call__(self, seconds: float) -> None:
        return None


def world_edgar() -> EdgarClient:
    return EdgarClient(FundamentalsConfig(), user_agent=TEST_USER_AGENT, lake=None, sleep=NoSleep())


# --- config and sources ----------------------------------------------------------------------


def world_lexicon_file(folder: Path) -> Path:
    path = folder / "lm_lexicon.csv"
    master = pd.read_csv(FIXTURES / "sentiment" / "lm_master_sample.csv")
    build_lm_lexicon(master).to_csv(path, index=False)
    return path


def world_config(folder: Path, **sync: Any) -> AtlasConfig:
    """`AtlasConfig()` for the world: ETFs SPY/QQQ, the two-series catalog, the test lexicon,
    `data.sync` fields replaced by `sync`."""
    base = AtlasConfig()
    data = base.data
    universes = dict(data.universes)
    universes["macro_etfs"] = universes["macro_etfs"].model_copy(update={"tickers": ETFS})
    macro = data.macro.model_copy(update={"series_file": world_macro_yaml(folder)})
    sentiment = data.sentiment.model_copy(update={"lexicon_file": world_lexicon_file(folder)})
    settings = data.sync.model_copy(update=sync)
    new_data = data.model_copy(
        update={"universes": universes, "macro": macro, "sentiment": sentiment, "sync": settings}
    )
    return base.model_copy(update={"data": new_data})


@dataclass
class World:
    """The fakes behind one `SyncSources`, kept for assertions."""

    prices: WorldPrices = field(default_factory=WorldPrices)
    news: WorldNews = field(default_factory=WorldNews)
    fred: WorldFred = field(default_factory=WorldFred)
    calendar: WorldCalendar = field(default_factory=WorldCalendar)
    pages: tuple[str, str] = field(default_factory=world_pages)
    lexicon_file: Path | None = None
    page_calls: int = 0
    built: dict[str, int] = field(default_factory=dict)

    def sources(self, **replace: Any) -> SyncSources:
        def count(name: str, value: Any) -> Any:
            self.built[name] = self.built.get(name, 0) + 1
            return value

        def pages() -> tuple[str, str]:
            self.page_calls += 1
            return self.pages

        def lexicon() -> Any:
            assert self.lexicon_file is not None
            return load_lexicon(self.lexicon_file)

        made: Mapping[str, Any] = {
            "price_provider": lambda: count("price_provider", self.prices),
            "edgar": lambda: count("edgar", world_edgar()),
            "cik_map": world_cik_map,
            "fred": lambda: count("fred", self.fred),
            "calendar_fred": lambda: None,
            "news_provider": lambda: count("news_provider", self.news),
            "upcoming_provider": lambda: count("upcoming_provider", self.calendar),
            "social_providers": lambda: (),
            "universe_pages": pages,
            "lexicon": lexicon,
        }
        return SyncSources(**{**made, **replace})


def at(day: date, hour: int = 8, minute: int = 0) -> datetime:
    """`day` at `hour:minute` New York, as an aware UTC instant."""
    from zoneinfo import ZoneInfo

    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo("America/New_York")
    ).astimezone(UTC)


ONE_DAY = timedelta(days=1)
