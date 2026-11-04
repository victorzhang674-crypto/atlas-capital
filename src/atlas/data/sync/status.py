"""Lake status (Stage 14): row counts, date ranges, last sync, coverage of the current
members and the latest quality report, read-only."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import duckdb
import pandas as pd

from atlas.core.clock import get_clock
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage import Lake
from atlas.core.storage.lake import DATA_FILE
from atlas.core.types.base import FrozenDict
from atlas.data.macro import load_macro_series
from atlas.data.quality import (
    DATA_QUALITY_ISSUES,
    blocked_symbols,
    read_quality_issues,
)
from atlas.data.sync.datasets import DATASET_STEPS, state_path
from atlas.data.sync.state import SyncState
from atlas.data.universe import Universe

_log = get_logger(__name__)

# --- status ----------------------------------------------------------------------------------

STATUS_TABLES: Mapping[str, tuple[str, str | None, str | None]] = FrozenDict(
    {
        "index_membership": ("universe", "start_date", "symbol"),
        "instruments": ("universe", "first_trade_date", "symbol"),
        "prices_daily": ("prices", "date", "symbol"),
        "corporate_actions": ("prices", "ex_date", "symbol"),
        "fundamentals_facts": ("fundamentals", "period_end", "symbol"),
        "macro_series": ("macro", None, "series_id"),
        "macro_observations": ("macro", "obs_date", "series_id"),
        "news": ("news", "published_at", "symbols"),
        "events": ("events", "event_date", "symbol"),
        "macro_events": ("events", "event_date", None),
        "earnings_reactions": ("events", "reaction_date", "symbol"),
        "news_sentiment": ("sentiment", "published_at", None),
        "social_posts": ("sentiment", "created_at", "symbol"),
        "data_quality_issues": ("quality", "report_day", "symbol"),
    }
)
"""Tables `lake_status` summarises: `table -> (dataset, date column, key column)`; the key
column of `news` is the tag list (distinct tagged symbols are counted)."""

STATUS_COLUMNS: tuple[str, ...] = ("table", "dataset", "rows", "keys", "first", "last", "last_sync")


@dataclass(frozen=True)
class CoverageLine:
    """How much of a dataset the lake holds: `covered` of `total` (current index members, or
    catalog series), under `rule`; `missing` lists what is not covered (sorted)."""

    dataset: str
    rule: str
    covered: int
    total: int
    missing: tuple[str, ...] = ()

    @property
    def pct(self) -> float:
        """`covered / total` in percent (NaN when `total` is 0)."""
        return 100.0 * self.covered / self.total if self.total else float("nan")


@dataclass(frozen=True)
class LakeStatus:
    """What `lake_status` found on `as_of` (a New York date).

    `tables` has `STATUS_COLUMNS`: rows, distinct keys, first and last date (`date`; the UTC
    date for timestamp columns) and the latest sync of the table's dataset (aware UTC, from
    the checkpoint; `None` if never synced). `members` is the number of current members of
    `data.sync.universe`; `coverage` has one `CoverageLine` per dataset; `quality` counts the
    issues of the latest persisted quality report (`quality_day`) by severity, and `blocked`
    lists its symbols with a critical issue. `last_run` is the checkpoint's latest run entry;
    `note` explains why a part could not be computed (e.g. no membership yet)."""

    as_of: date
    tables: pd.DataFrame
    members: int
    coverage: tuple[CoverageLine, ...]
    quality_day: date | None
    quality: pd.DataFrame
    blocked: tuple[str, ...]
    last_run: dict[str, Any] | None = None
    note: str | None = None


def _glob(lake: Lake, table: str) -> str | None:
    if not lake.table_files(table):
        return None
    return str(lake.table_path(table) / "**" / DATA_FILE)


_SOURCE = "read_parquet($files, hive_partitioning = false)"


def _one(con: duckdb.DuckDBPyConnection, sql: str, files: str) -> tuple[Any, ...]:
    row = con.execute(sql.format(src=_SOURCE), {"files": files}).fetchone()
    assert row is not None  # an aggregate query always returns one row
    return tuple(row)


def _summaries(con: duckdb.DuckDBPyConnection, lake: Lake, state: SyncState) -> pd.DataFrame:
    rows = []
    for table, (dataset, day_col, key_col) in STATUS_TABLES.items():
        files = _glob(lake, table)
        count = keys = 0
        first = last = None
        if files is not None:
            span = f"CAST(min({day_col}) AS DATE), CAST(max({day_col}) AS DATE)"
            if day_col is None:
                span = "NULL, NULL"
            count, first, last = _one(con, f"SELECT count(*), {span} FROM {{src}}", files)
            if key_col == "symbols":
                sql = "SELECT count(DISTINCT s) FROM (SELECT unnest(symbols) AS s FROM {src})"
                (keys,) = _one(con, sql, files)
            elif key_col is not None:
                (keys,) = _one(con, f"SELECT count(DISTINCT {key_col}) FROM {{src}}", files)
        rows.append(
            {
                "table": table,
                "dataset": dataset,
                "rows": int(count),
                "keys": int(keys),
                "first": first,
                "last": last,
                "last_sync": state.last_synced(DATASET_STEPS[dataset]),
            }
        )
    return pd.DataFrame(rows, columns=list(STATUS_COLUMNS))


def _per_key(con: duckdb.DuckDBPyConnection, lake: Lake, table: str, sql: str) -> pd.DataFrame:
    files = _glob(lake, table)
    if files is None:
        return pd.DataFrame()
    return con.execute(sql.format(src=_SOURCE), {"files": files}).df()


def lake_status(lake: Lake, config: AtlasConfig, *, as_of: date | None = None) -> LakeStatus:
    """Summarise the lake on `as_of` (default: the clock's New York date) without writing
    anything (aggregates run in an in-memory DuckDB over the Parquet files, so it is safe while
    a sync is running in another process).

    Coverage (the M1 acceptance numbers), over the current members of `data.sync.universe`:
    `prices` — first and last stored bar at least `data.sync.status_min_price_years` years
    (of 365.25 days) apart; `prices (listed >= Ny)` — the same, over the members whose vendor
    history is that long (the owner-approved M1 reading, Decisions log 2026-11-01): a member
    is left out only when the checkpoint shows its prices were asked for from before the
    N-year mark and its first stored bar is after it (a younger listing); `fundamentals` — at
    least one stored fact; `news` — at least one tagged article; `events` — at least one
    earnings event. `macro` — catalog series
    (`data.macro.series_file`) with at least one observation.
    """
    day = as_of if as_of is not None else get_clock().today()
    settings = config.data.sync
    state = SyncState.load(state_path(lake, config))
    note: str | None = None
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET TimeZone = 'UTC'")
        tables = _summaries(con, lake, state)
        try:
            members = tuple(sorted(Universe(lake, config).members(day, settings.universe)))
        except DataError as exc:
            members = ()
            note = f"no current members ({exc}); run the universe dataset"
        spans = _per_key(
            con,
            lake,
            "prices_daily",
            "SELECT symbol, min(date) AS first, max(date) AS last FROM {src} GROUP BY symbol",
        )
        years = settings.status_min_price_years
        mark = day - timedelta(days=years * 365.25)
        enough: set[str] = set()
        short: set[str] = set()
        if not spans.empty:
            days = (pd.to_datetime(spans["last"]) - pd.to_datetime(spans["first"])).dt.days
            enough = set(spans.loc[days.to_numpy() >= years * 365.25, "symbol"])
            firsts = pd.to_datetime(spans["first"]).dt.date
            for symbol, first in zip(spans["symbol"], firsts, strict=True):
                asked = state.coverage("prices", str(symbol))
                if symbol not in enough and asked is not None and asked.start <= mark < first:
                    short.add(str(symbol))
        facts = _per_key(con, lake, "fundamentals_facts", "SELECT DISTINCT symbol FROM {src}")
        tagged = _per_key(con, lake, "news", "SELECT DISTINCT unnest(symbols) AS symbol FROM {src}")
        earnings = _per_key(
            con,
            lake,
            "events",
            "SELECT DISTINCT symbol FROM {src} WHERE event_type = 'earnings'",
        )
        observed = _per_key(con, lake, "macro_observations", "SELECT DISTINCT series_id FROM {src}")
    finally:
        con.close()

    def of_members(dataset: str, rule: str, have: set[str]) -> CoverageLine:
        missing = tuple(m for m in members if m not in have)
        return CoverageLine(dataset, rule, len(members) - len(missing), len(members), missing)

    def column(frame: pd.DataFrame, name: str) -> set[str]:
        return set() if frame.empty else {str(v) for v in frame[name]}

    catalog = load_macro_series(config.data.macro.series_file).ids
    have_series = column(observed, "series_id")
    listed = tuple(m for m in members if m not in short)
    coverage = (
        of_members("prices", f">= {years:g} years of daily prices", enough),
        CoverageLine(
            f"prices (listed >= {years:g}y)",
            f">= {years:g} years, members with that much vendor history",
            sum(1 for m in listed if m in enough),
            len(listed),
            tuple(m for m in listed if m not in enough),
        ),
        of_members("fundamentals", "at least one stored XBRL fact", column(facts, "symbol")),
        CoverageLine(
            "macro",
            "catalog series with observations",
            sum(1 for s in catalog if s in have_series),
            len(catalog),
            tuple(s for s in catalog if s not in have_series),
        ),
        of_members("news", "at least one tagged article", column(tagged, "symbol")),
        of_members("events", "at least one earnings event", column(earnings, "symbol")),
    )
    quality_day, quality, blocked = _quality_summary(lake, config)
    runs = state.runs
    return LakeStatus(
        as_of=day,
        tables=tables,
        members=len(members),
        coverage=coverage,
        quality_day=quality_day,
        quality=quality,
        blocked=blocked,
        last_run=runs[-1] if runs else None,
        note=note,
    )


def _quality_summary(
    lake: Lake, config: AtlasConfig
) -> tuple[date | None, pd.DataFrame, tuple[str, ...]]:
    empty = pd.DataFrame({"severity": pd.Series(dtype="str"), "issues": pd.Series(dtype="int64")})
    if not lake.table_files(DATA_QUALITY_ISSUES.name):
        return None, empty, ()
    days = lake.read_table(DATA_QUALITY_ISSUES.name, columns=["report_day"])["report_day"]
    if days.empty:
        return None, empty, ()
    latest: date = max(days)
    issues = read_quality_issues(lake, latest, config=config)
    order = ["critical", "warning", "info"]
    counts = issues["severity"].value_counts()
    frame = pd.DataFrame(
        {
            "severity": [s for s in order if s in counts],
            "issues": [int(counts[s]) for s in order if s in counts],
        }
    )
    return latest, frame, tuple(sorted(blocked_symbols(issues)))
