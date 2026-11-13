"""Reference data for the universe: the S&P 500 source tables, ticker renames and instruments.

`instruments` (registered in `DEFAULT_REGISTRY` on import) holds one row per instrument the
fund may trade or has seen in the index: S&P 500 names past and present and the configured
ETFs. It is static reference data (no `available_at`): names, GICS sectors and listing
exchanges are today's values, not point-in-time ones (see `docs/data-limitations.md`).

Source: Wikipedia's S&P 500 constituents table (`table#constituents`, in "List of S&P 500
companies") and change log (`table#changes`; in "Historical components of the S&P 500" since
2026-08-11, earlier in the same article). The parsers find each table by its id, so either
layout works. Tickers are cleaned of cell junk (`"ALLE |"` → `"ALLE"`) but not normalised
here: `atlas.data.universe` normalises them and records failures as anomalies.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from typing import Self

import httpx
import pandas as pd
from pydantic import Field, ValidationError, model_validator

from atlas.core.config import ReferenceDataConfig, read_config_file
from atlas.core.errors import ConfigError, DataError
from atlas.core.storage import DEFAULT_REGISTRY, Column, ColumnType, Lake, TableSchema
from atlas.core.types.base import AtlasModel, NonEmptyStr
from atlas.core.types.instruments import AssetClass, Instrument, Symbol, normalize_symbol
from atlas.data.prices import PRICES_DAILY

_TICKER_RE = re.compile(r"[A-Za-z0-9.\-/]+")
_FOOTNOTE_RE = re.compile(r"\[\d+\]")
_EXCHANGES = (("nyse.com", "XNYS"), ("nasdaq.com", "XNAS"), ("cboe.com", "BATS"))
"""Quote-link host → ISO 10383 MIC of the listing exchange (BATS = Cboe BZX)."""

CONSTITUENT_COLUMNS = ("symbol", "name", "sector", "industry", "exchange", "date_added")
CHANGE_COLUMNS = ("date", "added", "added_name", "removed", "removed_name", "reason")

_S, _D, _B = ColumnType.STRING, ColumnType.DATE, ColumnType.BOOL

INSTRUMENTS = DEFAULT_REGISTRY.register(
    TableSchema(
        name="instruments",
        columns=(
            Column(name="symbol", dtype=_S),
            Column(name="name", dtype=_S, nullable=True),
            Column(name="asset_class", dtype=_S),
            Column(name="sector", dtype=_S, nullable=True),
            Column(name="industry", dtype=_S, nullable=True),
            Column(name="exchange", dtype=_S, nullable=True),
            Column(name="currency", dtype=_S),
            Column(name="first_trade_date", dtype=_D, nullable=True),
            Column(name="last_trade_date", dtype=_D, nullable=True),
            Column(name="is_etf", dtype=_B),
            Column(name="is_shortable", dtype=_B),
        ),
        primary_key=("symbol",),
        static=True,
        description="Instruments: S&P 500 names past and present, configured ETFs; Stage 08",
    )
)
"""`instruments`: one row per instrument (see `atlas.core.types.instruments.Instrument`)."""


def _read_table(html: str, table_id: str, *, links: bool = False) -> pd.DataFrame:
    """The one `<table id=table_id>` of `html`; with `links`, body cells are `(text, href)`."""
    try:
        tables = pd.read_html(
            StringIO(html),
            attrs={"id": table_id},
            flavor="lxml",
            extract_links="body" if links else None,
        )
    except ValueError as exc:
        raise DataError(f"no table with id {table_id!r} in the page: {exc}") from exc
    if len(tables) != 1:
        raise DataError(f"expected one table with id {table_id!r}, found {len(tables)}")
    frame: pd.DataFrame = tables[0]
    return frame


def _text(value: object) -> str | None:
    """Cell text with footnote markers removed; `None` for an empty or missing cell."""
    if isinstance(value, tuple):
        value = value[0]
    if not isinstance(value, str):
        return None
    text = _FOOTNOTE_RE.sub("", value).strip()
    return text or None


def _ticker(value: object) -> str | None:
    """The leading ticker-like token of a cell (`"ALLE |"` → `"ALLE"`), or `None`."""
    text = _text(value)
    if text is None:
        return None
    match = _TICKER_RE.match(text)
    return match.group(0) if match else text


def _exchange(cell: object) -> str | None:
    href = cell[1] if isinstance(cell, tuple) and len(cell) > 1 else None
    if not isinstance(href, str):
        return None
    for host, mic in _EXCHANGES:
        if host in href:
            return mic
    return None


def parse_constituents(html: str) -> pd.DataFrame:
    """Today's S&P 500 constituents from the page holding `table#constituents`.

    Columns `symbol, name, sector, industry, exchange, date_added`: `sector` / `industry` are
    the GICS sector and sub-industry; `exchange` is the listing exchange's MIC (`XNYS`, `XNAS`,
    `BATS`) read from the symbol's quote link, `None` if unknown; `date_added` is a `date` or
    `None`. Sorted by symbol. Raises `DataError` if the table is missing, lacks a column, or
    lists a symbol twice.
    """
    raw = _read_table(html, "constituents", links=True)
    columns = [c[0] if isinstance(c, tuple) else str(c) for c in raw.columns]
    raw.columns = pd.Index(columns)
    wanted = {
        "Symbol": "symbol",
        "Security": "name",
        "GICS Sector": "sector",
        "GICS Sub-Industry": "industry",
        "Date added": "date_added",
    }
    missing = [c for c in wanted if c not in columns]
    if missing:
        raise DataError(f"constituents table lacks columns {missing}; has {columns}")
    added = pd.to_datetime(raw["Date added"].map(_text), format="%Y-%m-%d", errors="coerce").dt.date
    frame = pd.DataFrame(
        {
            "symbol": raw["Symbol"].map(_ticker),
            "name": raw["Security"].map(_text),
            "sector": raw["GICS Sector"].map(_text),
            "industry": raw["GICS Sub-Industry"].map(_text),
            "exchange": raw["Symbol"].map(_exchange),
            "date_added": [None if pd.isna(d) else d for d in added],
        },
        columns=list(CONSTITUENT_COLUMNS),
    )
    if frame["symbol"].isna().any():
        raise DataError("constituents table has a row without a symbol")
    dupes = sorted(frame.loc[frame["symbol"].duplicated(), "symbol"])
    if dupes:
        raise DataError(f"constituents table lists symbols twice: {dupes}")
    return frame.sort_values("symbol", ignore_index=True)


def parse_changes(html: str) -> pd.DataFrame:
    """The S&P 500 change log from the page holding `table#changes`.

    Columns `date, added, added_name, removed, removed_name, reason`: `date` is the effective
    date (a `date`; the change takes effect before that day's open); tickers are cleaned
    strings or `None` for a one-sided change. Newest first; rows of one date keep source
    order. Raises `DataError` if the table is missing, has an unexpected header, or holds a
    date that is not "Month D, YYYY".
    """
    raw = _read_table(html, "changes")
    top = [c[0] if isinstance(c, tuple) else str(c) for c in raw.columns]
    if top[:6] != ["Effective Date", "Added", "Added", "Removed", "Removed", "Reason"]:
        raise DataError(f"changes table has an unexpected header: {top}")
    texts = raw.iloc[:, 0].map(_text)
    dates = pd.to_datetime(texts, format="%B %d, %Y", errors="coerce")
    bad = texts[dates.isna()]
    if len(bad):
        raise DataError(f"changes table has dates that cannot be read: {bad.tolist()[:5]}")
    frame = pd.DataFrame(
        {
            "date": dates.dt.date,
            "added": raw.iloc[:, 1].map(_ticker),
            "added_name": raw.iloc[:, 2].map(_text),
            "removed": raw.iloc[:, 3].map(_ticker),
            "removed_name": raw.iloc[:, 4].map(_text),
            "reason": raw.iloc[:, 5].map(_text),
        },
        columns=list(CHANGE_COLUMNS),
    )
    return frame.sort_values("date", ascending=False, kind="stable", ignore_index=True)


# --- ticker renames --------------------------------------------------------------------------


class TickerChange(AtlasModel):
    """One ticker rename: the instrument traded as `old` until the day before `effective`,
    and as `new` from `effective` on.

    `since` (optional) is the first day `old` meant this instrument, for a ticker that an
    earlier company also used (Q was Qwest before it was QuintilesIMS). `source` says where
    the date comes from; `note` is free text.
    """

    old: Symbol
    new: Symbol
    effective: date
    since: date | None = None
    source: NonEmptyStr
    note: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.old == self.new:
            raise ValueError(f"old and new tickers must differ, both are {self.old!r}")
        if self.since is not None and self.since >= self.effective:
            raise ValueError(f"since {self.since} must be before effective {self.effective}")
        return self


class _TickerChangesFile(AtlasModel):
    """Body of `config/ticker_changes.yaml`."""

    changes: tuple[TickerChange, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> Self:
        seen: set[tuple[str, date]] = set()
        for change in self.changes:
            key = (change.old, change.effective)
            if key in seen:
                raise ValueError(f"{change.old} renamed twice on {change.effective}")
            seen.add(key)
        return self


class TickerMap:
    """Maps tickers seen on a date to an instrument's identity, its latest ticker.

    `canonical("FB", date(2013, 12, 23))` is `"META"`; `historical("META", date(2015, 1, 2))`
    is `"FB"`. A rename applies to a ticker seen on day `d` when `since <= d < effective`;
    after it, lookup continues from `effective`, so chains resolve (WLP → ANTM → ELV) and no
    rename applies twice (a rename back, A → B → A, terminates).
    """

    def __init__(self, changes: Sequence[TickerChange] = ()) -> None:
        self._changes = tuple(sorted(changes, key=lambda c: (c.effective, c.old)))

    @property
    def changes(self) -> tuple[TickerChange, ...]:
        """The renames, oldest effective date first."""
        return self._changes

    def canonical(self, symbol: str, on: date) -> str:
        """The latest ticker of the instrument that traded as `symbol` on `on`."""
        sym, day = normalize_symbol(symbol), on
        while True:
            step = next(
                (
                    c
                    for c in self._changes
                    if c.old == sym and day < c.effective and (c.since is None or c.since <= day)
                ),
                None,
            )
            if step is None:
                return sym
            sym, day = step.new, step.effective

    def historical(self, symbol: str, day: date) -> str:
        """The ticker under which the instrument now called `symbol` traded on `day`."""
        sym, bound = normalize_symbol(symbol), date.max
        while True:
            candidates = [
                c
                for c in self._changes
                if c.new == sym
                and day < c.effective < bound
                and (c.since is None or c.since <= day)
            ]
            if not candidates:
                return sym
            step = max(candidates, key=lambda c: c.effective)
            sym, bound = step.old, step.effective


def load_ticker_map(path: Path) -> TickerMap:
    """The renames listed in `path` (`changes: [...]`; see `config/ticker_changes.yaml`).

    A missing file gives an empty map. Invalid content raises `ConfigError` naming the file
    and the entry at fault.
    """
    data = read_config_file(path)
    try:
        body = _TickerChangesFile.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{path}: {problems}") from None
    return TickerMap(body.changes)


# --- instruments -------------------------------------------------------------------------------


def _log_names(changes: pd.DataFrame, ticker_map: TickerMap) -> dict[str, str]:
    """Canonical symbol → security name from its most recent change-log row."""
    names: dict[str, str] = {}
    oldest_first = changes.iloc[::-1]
    for day, added, added_name, removed, removed_name in oldest_first[
        ["date", "added", "added_name", "removed", "removed_name"]
    ].itertuples(index=False):
        for raw, name, on in (
            (removed, removed_name, day - timedelta(days=1)),
            (added, added_name, day),
        ):
            if isinstance(raw, str) and isinstance(name, str):
                try:
                    names[ticker_map.canonical(raw, on)] = name
                except ValueError:
                    continue  # recorded as a `bad_symbol` anomaly by the membership build
    return names


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
"""The eleven GICS sector names, as the constituents page writes them."""


class _FormerSectorsFile(AtlasModel):
    """Body of `config/former_sectors.yaml`."""

    sectors: dict[str, str] = Field(default_factory=dict)


def load_former_sectors(path: Path) -> dict[str, str]:
    """The reviewed GICS sectors of former index members in `path` (`sectors: {SYMBOL:
    sector}`; see `config/former_sectors.yaml`), by canonical symbol. A missing file gives
    none; an unknown sector name, an empty symbol or a symbol listed twice raises
    `ConfigError` naming the file."""
    data = read_config_file(path)
    try:
        body = _FormerSectorsFile.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{path}: {problems}") from None
    out: dict[str, str] = {}
    for raw, sector in body.sectors.items():
        try:
            symbol = normalize_symbol(raw)
        except ValueError as exc:
            raise ConfigError(f"{path}: bad symbol {raw!r}: {exc}") from None
        if symbol in out:
            raise ConfigError(f"{path}: {symbol} is listed twice")
        if sector not in GICS_SECTORS:
            raise ConfigError(
                f"{path}: {symbol}: {sector!r} is not a GICS sector ({', '.join(GICS_SECTORS)})"
            )
        out[symbol] = sector
    return out


def build_instruments(
    constituents: pd.DataFrame,
    changes: pd.DataFrame,
    intervals: pd.DataFrame,
    *,
    etf_symbols: Sequence[str],
    ticker_map: TickerMap | None = None,
    trade_dates: pd.DataFrame | None = None,
    former_sectors: Mapping[str, str] | None = None,
) -> list[Instrument]:
    """One `Instrument` per symbol in `intervals` (membership) or `etf_symbols`, by symbol.

    Current constituents carry name, GICS sector and sub-industry and exchange; former
    members carry the name from their latest change-log row and, when `former_sectors`
    (`load_former_sectors`) lists them, that reviewed sector (no sub-industry or exchange);
    the others have no sector. A current constituent always keeps the page's sector. ETFs
    are `AssetClass.ETF` with no sector. All are shortable (S&P 500 stocks and liquid ETFs;
    borrow is checked by later stages). `trade_dates` (`trade_date_ranges` shape) fills first
    and last trade dates.
    """
    reviewed = former_sectors if former_sectors is not None else {}
    tmap = ticker_map if ticker_map is not None else TickerMap()
    current = {normalize_symbol(str(r.symbol)): r for r in constituents.itertuples(index=False)}
    names = _log_names(changes, tmap)
    dates: dict[str, tuple[date | None, date | None]] = {}
    if trade_dates is not None:
        dates = {
            str(sym): (first, last)
            for sym, first, last in zip(
                trade_dates["symbol"],
                trade_dates["first_trade_date"],
                trade_dates["last_trade_date"],
                strict=True,
            )
        }
    out: dict[str, Instrument] = {}
    for symbol in sorted(set(intervals["symbol"]) | set(current)):
        first, last = dates.get(symbol, (None, None))
        row = current.get(symbol)
        out[symbol] = Instrument(
            symbol=symbol,
            asset_class=AssetClass.EQUITY,
            name=row.name if row is not None else names.get(symbol),
            sector=row.sector if row is not None else reviewed.get(symbol),
            industry=row.industry if row is not None else None,
            exchange=row.exchange if row is not None else None,
            first_trade_date=first,
            last_trade_date=last,
        )
    for raw in etf_symbols:
        symbol = normalize_symbol(raw)
        if symbol in out:
            raise DataError(f"ETF {symbol} is also an index member; fix config/universe.yaml")
        first, last = dates.get(symbol, (None, None))
        out[symbol] = Instrument(
            symbol=symbol,
            asset_class=AssetClass.ETF,
            first_trade_date=first,
            last_trade_date=last,
        )
    return [out[s] for s in sorted(out)]


def instruments_frame(instruments: Sequence[Instrument]) -> pd.DataFrame:
    """`instruments` as a frame in `INSTRUMENTS` column order, sorted by symbol."""
    rows = [
        {**i.model_dump(mode="python"), "asset_class": i.asset_class.value, "is_etf": i.is_etf}
        for i in sorted(instruments, key=lambda i: i.symbol)
    ]
    frame = pd.DataFrame(rows, columns=list(INSTRUMENTS.column_names))
    for col in ("first_trade_date", "last_trade_date"):
        frame[col] = frame[col].astype(object).where(frame[col].notna(), None)
    return frame


def read_instruments(lake: Lake) -> tuple[Instrument, ...]:
    """The `instruments` table as contracts, sorted by symbol."""
    frame = lake.read_table(INSTRUMENTS.name)
    out = []
    for rec in frame.to_dict("records"):
        rec.pop("is_etf")
        clean = {k: (None if _missing(v) else v) for k, v in rec.items()}
        out.append(Instrument.model_validate(clean))
    return tuple(out)


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and value != value)


def trade_date_ranges(lake: Lake) -> pd.DataFrame:
    """First and last session with a bar per symbol in `prices_daily` (`symbol,
    first_trade_date, last_trade_date`); empty when the lake has no prices."""
    columns = ["symbol", "first_trade_date", "last_trade_date"]
    if not lake.has_table(PRICES_DAILY.name):
        return pd.DataFrame(columns=columns)
    bars = lake.read_table(PRICES_DAILY.name, columns=["symbol", "date"])
    grouped = bars.groupby("symbol", sort=True)["date"]
    return pd.DataFrame(
        {"first_trade_date": grouped.min(), "last_trade_date": grouped.max()}
    ).reset_index()[columns]


# --- optional live refresh ------------------------------------------------------------------------


def fetch_sp500_pages(config: ReferenceDataConfig) -> tuple[str, str]:
    """Fetch the live constituents and change-log pages: `(constituents_html, changes_html)`.

    One request per page with `config.user_agent` and `config.request_timeout_seconds` (no
    shared connection: on 2026-10-27 Wikimedia answered a second request on one connection
    with 403). A non-200 answer or a transport error raises `DataError` (chained).
    """
    pages = []
    for url in (config.sp500_constituents_url, config.sp500_changes_url):
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": config.user_agent},
                timeout=config.request_timeout_seconds,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise DataError(f"GET {url} failed: {exc}") from exc
        if resp.status_code != 200:
            raise DataError(f"GET {url} returned HTTP {resp.status_code}")
        pages.append(resp.text)
    return pages[0], pages[1]
