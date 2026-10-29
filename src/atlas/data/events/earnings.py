"""Earnings dates: actual events from EDGAR filing timestamps, scheduled dates from a provider.

Actual earnings events come from a company's filing index (EDGAR submissions, Stage 09's
`EdgarClient`): an `8-K` whose items include `2.02` (Results of Operations and Financial
Condition) is an earnings event; a `10-Q` or `10-K` is one only when no such 8-K was accepted
in the `periodic_match_days` calendar days before it (companies that announce in the periodic
report itself). The event's time and `available_at` are the filing's EDGAR acceptance time: the
press release may come out minutes earlier, so the market never knows the event early here.
Filings without an acceptance time are skipped (counted, never guessed).

Scheduled (upcoming) dates come from an optional `EarningsCalendarProvider` (Yahoo by
default). A snapshot's rows are known from the moment it was taken (`known_at`), carry no time
(`event_time` NaT, `time_of_day` `unknown`) and use `source="yahoo"`; every snapshot is kept, so
the first knowledge of a date is never moved by a later fetch.
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final, Protocol, runtime_checkable

import numpy as np
import pandas as pd
import yfinance as yf

from atlas.core.calendar import TradingCalendar, market_date
from atlas.core.clock import get_clock
from atlas.core.config import AtlasConfig, EventsConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage import Lake
from atlas.core.types.base import FrozenDict
from atlas.core.types.instruments import normalize_symbol, vendor_symbol
from atlas.data.events.base import EVENTS, classify_time_of_day, empty_events, normalize_events
from atlas.data.fundamentals.edgar_client import CikMap, EdgarClient
from atlas.data.providers.base import RetryPolicy, Throttle, call_with_retry

_log = get_logger(__name__)

FILING_COLUMNS: Final = ("accn", "form", "filed", "report_date", "accepted_at", "items")
"""Columns of `parse_filings`."""

EARNINGS_ITEM: Final = "2.02"
"""8-K item "Results of Operations and Financial Condition"."""

PERIODIC_SOURCES: Final = FrozenDict({"10-Q": "sec_10q", "10-K": "sec_10k"})
"""Periodic report forms and the event `source` they give."""

EVENT_ID_PREFIX: Final = "sec-"
"""`event_id` of an actual earnings event is this prefix plus the accession number."""


@dataclass(frozen=True)
class EventsIngestReport:
    """Outcome of `ingest_earnings`.

    `rows` were upserted; `symbols_ok` and `symbols_failed` keep request order (canonical
    symbols); `failure_reasons` says why each failed symbol was skipped; `skipped_filings`
    counts earnings filings without an acceptance time (no event written).
    """

    rows: int
    symbols_ok: tuple[str, ...]
    symbols_failed: tuple[str, ...]
    failure_reasons: FrozenDict[str, str] = field(default_factory=lambda: FrozenDict({}))
    skipped_filings: int = 0


def parse_filings(table: Mapping[str, Any]) -> pd.DataFrame:
    """A submissions column table (`filings.recent` or a history page) as `FILING_COLUMNS`:
    `filed` and `report_date` are dates (`None` when blank), `accepted_at` UTC (`NaT` when
    blank), `items` the 8-K item list as text (`""` when absent).

    `items` and `reportDate` may be missing (older pages); the other columns may not
    (`DataError`), nor may columns differ in length or dates fail to parse.
    """
    required = ("accessionNumber", "form", "filingDate", "acceptanceDateTime")
    try:
        accn, form, filed, accepted = (list(table[name]) for name in required)
    except (KeyError, TypeError) as exc:
        raise DataError(f"submissions table lacks one of {required}") from exc
    n = len(accn)
    report = list(table["reportDate"]) if "reportDate" in table else [""] * n
    items = list(table["items"]) if "items" in table else [""] * n
    lengths = {len(c) for c in (accn, form, filed, accepted, report, items)}
    if len(lengths) > 1:
        raise DataError(f"submissions columns differ in length: {sorted(lengths)}")
    try:
        stamps = pd.to_datetime(
            pd.Series([a if a else None for a in accepted], dtype="object"),
            utc=True,
            format="ISO8601",
        )
        filed_dates = [date.fromisoformat(str(f)) for f in filed]
        report_dates = [date.fromisoformat(str(r)) if r else None for r in report]
    except (ValueError, TypeError) as exc:
        raise DataError(f"submissions: bad date ({exc})") from exc
    return pd.DataFrame(
        {
            "accn": pd.Series([str(a) for a in accn], dtype="str"),
            "form": pd.Series([str(f) for f in form], dtype="str"),
            "filed": pd.Series(filed_dates, dtype="object"),
            "report_date": pd.Series(report_dates, dtype="object"),
            "accepted_at": stamps.astype("datetime64[us, UTC]"),
            "items": pd.Series([str(i) if i else "" for i in items], dtype="str"),
        }
    )


def fetch_filings(
    client: EdgarClient, cik: int, *, since: date, refresh: bool = False
) -> pd.DataFrame:
    """The filings of `cik` filed on or after `since` (`FILING_COLUMNS`), from
    `filings.recent` and the history pages whose `filingTo` is on or after `since`; a filing in
    two tables is kept once."""
    subs = client.submissions(cik, refresh=refresh)
    filings = subs.get("filings")
    if not isinstance(filings, Mapping) or not isinstance(filings.get("recent"), Mapping):
        raise DataError(f"submissions of CIK {cik}: no filings.recent table")
    frames = [parse_filings(filings["recent"])]
    for page in filings.get("files", []):
        try:
            high = date.fromisoformat(page["filingTo"])
            name = page["name"]
        except (KeyError, TypeError, ValueError) as exc:
            raise DataError(f"submissions of CIK {cik}: bad history entry {page!r}") from exc
        if high >= since:
            frames.append(parse_filings(client.submissions_page(name)))
    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset="accn", keep="first")
    out = out.loc[[d >= since for d in out["filed"]]]
    return out.reset_index(drop=True)


def _is_earnings_8k(form: str, items: str) -> bool:
    return form == "8-K" and EARNINGS_ITEM in {i.strip() for i in items.split(",")}


def earnings_events(
    filings: pd.DataFrame,
    symbol: str,
    calendar: TradingCalendar,
    *,
    periodic_match_days: int,
) -> tuple[pd.DataFrame, int]:
    """Earnings events of `symbol` from its filings (see the module docstring), as an event
    frame, and the number of earnings filings skipped for lack of an acceptance time.

    `periodic_match_days` is in calendar days: a 10-Q/10-K accepted more than that after the
    last earnings 8-K is an event of its own.
    """
    canonical = normalize_symbol(symbol)
    is_8k = [_is_earnings_8k(f, i) for f, i in zip(filings["form"], filings["items"], strict=True)]
    is_periodic = filings["form"].isin(list(PERIODIC_SOURCES)).to_numpy()
    candidates = np.asarray(is_8k) | is_periodic
    timed = filings["accepted_at"].notna().to_numpy()
    skipped = int((candidates & ~timed).sum())
    if skipped:
        _log.warning("events.filing_without_acceptance", symbol=canonical, count=skipped)
    eights = filings.loc[np.asarray(is_8k) & timed]
    periodic = filings.loc[is_periodic & timed]
    if len(periodic):
        times = periodic["accepted_at"].dt.tz_localize(None).to_numpy()[:, None]
        announced = eights["accepted_at"].dt.tz_localize(None).to_numpy()[None, :]
        window = np.timedelta64(periodic_match_days, "D")
        covered = ((announced <= times) & (announced >= times - window)).any(axis=1)
        periodic = periodic.loc[~covered]
    rows = [_event(row, canonical, "sec_8k", calendar) for row in eights.to_dict("records")] + [
        _event(row, canonical, PERIODIC_SOURCES[row["form"]], calendar)
        for row in periodic.to_dict("records")
    ]
    if not rows:
        return empty_events(), skipped
    return normalize_events(pd.DataFrame(rows)), skipped


def _event(
    row: Mapping[Any, Any], symbol: str, source: str, cal: TradingCalendar
) -> dict[str, Any]:
    accepted = pd.Timestamp(row["accepted_at"]).to_pydatetime()
    return {
        "event_id": f"{EVENT_ID_PREFIX}{row['accn']}",
        "symbol": symbol,
        "event_type": "earnings",
        "event_date": market_date(accepted),
        "event_time": accepted,
        "time_of_day": classify_time_of_day(accepted, cal),
        "source": source,
        "available_at": accepted,
    }


def _request(symbols: Sequence[str]) -> tuple[str, ...]:
    if isinstance(symbols, str):
        raise ValueError("symbols must be a sequence of tickers, not one string")
    return tuple(dict.fromkeys(normalize_symbol(s) for s in symbols))


def ingest_earnings(
    lake: Lake,
    client: EdgarClient,
    symbols: Sequence[str],
    *,
    cik_map: CikMap,
    config: AtlasConfig | None = None,
    calendar: TradingCalendar | None = None,
    refresh: bool = True,
) -> EventsIngestReport:
    """Fetch the filings of `symbols` and upsert their earnings events into `events`.

    Filings filed from `config.data.events.history_start` are read (`config` defaults to
    `AtlasConfig()`, `calendar` to `TradingCalendar(config=config.calendar)`). A bare string or
    an invalid symbol raises `ValueError` before any request. A symbol without a CIK, or whose
    EDGAR data fails (`DataError`), is recorded in `symbols_failed` and never aborts the
    batch. Re-running is idempotent (rows are keyed by symbol, accession and acceptance time).
    """
    requested = _request(symbols)
    cfg = config if config is not None else AtlasConfig()
    cal = calendar if calendar is not None else TradingCalendar(config=cfg.calendar)
    settings = cfg.data.events
    ok: list[str] = []
    reasons: dict[str, str] = {}
    rows = skipped = 0
    for symbol in requested:
        cik = cik_map.cik(symbol)
        if cik is None:
            reasons[symbol] = "no CIK (not in company_tickers.json or config/cik_overrides.yaml)"
            continue
        try:
            filings = fetch_filings(client, cik, since=settings.history_start, refresh=refresh)
            events, missing = earnings_events(
                filings, symbol, cal, periodic_match_days=settings.periodic_match_days
            )
            if not events.empty:
                lake.write_table(EVENTS.name, events, mode="upsert", keys=list(EVENTS.primary_key))
        except DataError as exc:
            reasons[symbol] = str(exc)
            _log.warning("events.symbol_failed", symbol=symbol, error=str(exc))
            continue
        ok.append(symbol)
        rows += len(events)
        skipped += missing
    report = EventsIngestReport(
        rows=rows,
        symbols_ok=tuple(ok),
        symbols_failed=tuple(reasons),
        failure_reasons=FrozenDict(reasons),
        skipped_filings=skipped,
    )
    _log.info(
        "events.ingest_earnings",
        rows=rows,
        symbols_ok=len(ok),
        symbols_failed=len(reasons),
        skipped_filings=skipped,
    )
    return report


# --- scheduled (upcoming) dates --------------------------------------------------------------

CalendarFn = Callable[[str], Mapping[str, Any]]
"""`vendor symbol -> calendar mapping` holding `"Earnings Date": [date, ...]` (yfinance's
`Ticker.calendar`)."""


@runtime_checkable
class EarningsCalendarProvider(Protocol):
    """A source of scheduled earnings dates."""

    name: str

    def fetch_upcoming(self, symbols: Sequence[str]) -> pd.DataFrame:
        """The next scheduled earnings date of each of `symbols` that has one, as a frame with
        columns `symbol` (canonical) and `event_date` (`date`)."""
        ...


class YahooEarningsCalendar:
    """`EarningsCalendarProvider` backed by Yahoo Finance (`yf.Ticker(s).calendar`).

    When Yahoo gives a date range (two dates, not yet confirmed), the earliest is kept. A
    symbol without a usable date is left out. Any error inside `calendar_fn` is retried
    (`max_retries`, `backoff_seconds` from `cfg.data.events`), then raises `DataError`.
    `calendar_fn` and `sleep` are injectable for tests.
    """

    name = "yahoo"

    def __init__(
        self,
        config: EventsConfig | None = None,
        *,
        calendar_fn: CalendarFn | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """A provider using `config` (`cfg.data.events`)."""
        settings = config if config is not None else EventsConfig()
        self._fn = calendar_fn if calendar_fn is not None else _yahoo_calendar
        self._policy = RetryPolicy(
            max_retries=settings.max_retries,
            backoff_seconds=settings.backoff_seconds,
            min_interval_seconds=settings.min_request_interval_seconds,
        )
        self._sleep = sleep
        self._throttle = Throttle(self._policy.min_interval_seconds, sleep)

    def fetch_upcoming(self, symbols: Sequence[str]) -> pd.DataFrame:
        """See `EarningsCalendarProvider.fetch_upcoming`."""
        rows: list[dict[str, Any]] = []
        for symbol in _request(symbols):
            self._throttle.wait()
            ticker = vendor_symbol(symbol)
            answer = call_with_retry(
                lambda t=ticker: self._fn(t),  # type: ignore[misc]
                self._policy,
                self._sleep,
                f"yahoo calendar {ticker}",
                (Exception,),
                event="events.request_failed",
            )
            day = _earliest_date(
                answer.get("Earnings Date") if isinstance(answer, Mapping) else None
            )
            if day is None:
                _log.debug("events.no_scheduled_date", symbol=symbol)
                continue
            rows.append({"symbol": symbol, "event_date": day})
        return pd.DataFrame(
            {
                "symbol": pd.Series([r["symbol"] for r in rows], dtype="str"),
                "event_date": pd.Series([r["event_date"] for r in rows], dtype="object"),
            }
        )


def _yahoo_calendar(ticker: str) -> Mapping[str, Any]:
    answer: Mapping[str, Any] = yf.Ticker(ticker).calendar
    return answer


def _earliest_date(value: Any) -> date | None:
    if not isinstance(value, list | tuple):
        return None
    days = [v.date() if isinstance(v, datetime) else v for v in value]
    valid = [d for d in days if isinstance(d, date)]
    return min(valid) if valid else None


def ingest_upcoming_earnings(
    lake: Lake,
    provider: EarningsCalendarProvider,
    symbols: Sequence[str],
    *,
    known_at: datetime | None = None,
) -> int:
    """Fetch scheduled earnings dates and upsert them into `events`; returns the rows written.

    `known_at` (aware; default `get_clock().now()`) is the snapshot's `available_at`. Dates
    before its New York date are dropped (stale). A provider `DataError` propagates (nothing
    written: fail safe).
    """
    when = known_at if known_at is not None else get_clock().now()
    if when.utcoffset() is None:
        raise ValueError(f"naive datetime {when!r}; pass a timezone-aware datetime")
    found = provider.fetch_upcoming(symbols)
    today = market_date(when)
    found = found.loc[[d >= today for d in found["event_date"]]]
    if found.empty:
        return 0
    rows = [
        {
            "event_id": f"{provider.name}-{symbol}-{day.isoformat()}",
            "symbol": symbol,
            "event_type": "earnings",
            "event_date": day,
            "event_time": None,
            "time_of_day": "unknown",
            "source": provider.name,
            "available_at": when,
        }
        for symbol, day in zip(found["symbol"], found["event_date"], strict=True)
    ]
    events = normalize_events(pd.DataFrame(rows))
    lake.write_table(EVENTS.name, events, mode="upsert", keys=list(EVENTS.primary_key))
    _log.info("events.ingest_upcoming", provider=provider.name, rows=len(events))
    return len(events)


def build_upcoming_provider(config: AtlasConfig) -> EarningsCalendarProvider | None:
    """The configured provider of scheduled dates (`data.events.upcoming_earnings`), or `None`
    when it is `none`."""
    settings = config.data.events
    if settings.upcoming_earnings == "none":
        return None
    return YahooEarningsCalendar(settings)
