"""EDGAR JSON → tables: companyfacts → the long `fundamentals_facts` table, submissions → a
filing index with acceptance times, and the point-in-time `available_at` of every fact.

`fundamentals_facts` (registered in `DEFAULT_REGISTRY` on import) holds one row per reported
XBRL fact **as filed**: the same period reported again in a later filing (a comparative, an
amendment, a restatement) is another row with its own accession number. Values are in the
fact's `unit`: `USD`, `USD/shares` (per-share amounts) or `shares`. `duration_days` is 0 for an
instant (balance-sheet date; no `period_start`), else the inclusive length of the period.

Point in time: `available_at` is the EDGAR acceptance time of the fact's filing (UTC; checked
against EDGAR's filing-date rule, see the Stage 09 artifacts) when the filing index has it,
else the open of the first session after the filing date (`filed` + 1 session, conservative).
"""

import math
from collections.abc import Collection, Mapping
from datetime import date
from typing import Any

import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage import (
    AVAILABLE_AT,
    DEFAULT_REGISTRY,
    Column,
    ColumnType,
    TableSchema,
)
from atlas.core.types.instruments import normalize_symbol
from atlas.data.fundamentals.mapping import TaxTag

_log = get_logger(__name__)

_S, _I, _F, _D, _T = (
    ColumnType.STRING,
    ColumnType.INT64,
    ColumnType.FLOAT64,
    ColumnType.DATE,
    ColumnType.TIMESTAMP,
)

FUNDAMENTALS_FACTS = DEFAULT_REGISTRY.register(
    TableSchema(
        name="fundamentals_facts",
        columns=(
            Column(name="symbol", dtype=_S),
            Column(name="cik", dtype=_I),
            Column(name="taxonomy", dtype=_S),
            Column(name="tag", dtype=_S),
            Column(name="unit", dtype=_S),
            Column(name="value", dtype=_F),
            Column(name="period_start", dtype=_D, nullable=True),
            Column(name="period_end", dtype=_D),
            Column(name="duration_days", dtype=_I),
            Column(name="fy", dtype=_I, nullable=True),
            Column(name="fp", dtype=_S, nullable=True),
            Column(name="form", dtype=_S),
            Column(name="filed", dtype=_D),
            Column(name="accn", dtype=_S),
            Column(name="accepted_at", dtype=_T, nullable=True),
            Column(name=AVAILABLE_AT, dtype=_T),
        ),
        primary_key=("symbol", "taxonomy", "tag", "unit", "period_end", "duration_days", "accn"),
        partition_by=("symbol",),
        description="XBRL facts as filed (SEC companyfacts), keyed by filing; Stage 09",
    )
)
"""`fundamentals_facts`: one row per reported fact and filing, partitioned by symbol."""

FACT_COLUMNS = FUNDAMENTALS_FACTS.column_names
"""Columns of `fundamentals_facts`, in order."""

RAW_FACT_COLUMNS = tuple(c for c in FACT_COLUMNS if c not in ("accepted_at", AVAILABLE_AT))
"""Columns `parse_companyfacts` returns (before `attach_available_at`)."""

INDEX_COLUMNS = ("accn", "form", "filed", "accepted_at")
"""Columns of a filing index (`parse_submissions`)."""

_KEY = list(FUNDAMENTALS_FACTS.primary_key)


def _iso_date(value: object, what: str) -> date:
    if not isinstance(value, str):
        raise DataError(f"{what}: expected an ISO date, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataError(f"{what}: expected an ISO date, got {value!r}") from exc


def _fact_row(entry: object, taxonomy: str, tag: str, unit: str) -> tuple[Any, ...]:
    where = f"{taxonomy}:{tag} [{unit}]"
    if not isinstance(entry, Mapping):
        raise DataError(f"{where}: fact is not an object: {entry!r}")
    missing = [k for k in ("end", "val", "accn", "form", "filed") if k not in entry]
    if missing:
        raise DataError(f"{where}: fact lacks {missing}: {dict(entry)!r}")
    accn = entry["accn"]
    where = f"{where} accn {accn}"
    value = entry["val"]
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise DataError(f"{where}: value is not a finite number: {value!r}")
    end = _iso_date(entry["end"], where)
    start = _iso_date(entry["start"], where) if entry.get("start") is not None else None
    if start is not None and start > end:
        raise DataError(f"{where}: period start {start} is after its end {end}")
    fy = entry.get("fy")
    if fy is not None and (isinstance(fy, bool) or not isinstance(fy, int)):
        raise DataError(f"{where}: fy is not an integer: {fy!r}")
    duration = 0 if start is None else (end - start).days + 1
    return (
        taxonomy,
        tag,
        unit,
        float(value),
        start,
        end,
        duration,
        fy,
        entry.get("fp"),
        str(entry["form"]),
        _iso_date(entry["filed"], where),
        str(accn),
    )


def parse_companyfacts(
    payload: Mapping[str, Any], *, symbol: str, tags: Collection[TaxTag]
) -> pd.DataFrame:
    """The facts of the `tags` in a companyfacts payload as rows of `RAW_FACT_COLUMNS`.

    Every unit of each tag is kept; rows are sorted by the table's primary key. A fact
    reported twice in one filing is kept once (a conflicting duplicate keeps the first value,
    with a warning). A payload without `cik`/`facts`, or a malformed fact, raises `DataError`.
    """
    facts = payload.get("facts")
    cik = payload.get("cik")
    if not isinstance(facts, Mapping):
        raise DataError("companyfacts payload has no `facts` object")
    try:
        cik_int = int(cik)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DataError(f"companyfacts payload has no valid `cik`: {cik!r}") from exc
    rows: list[tuple[Any, ...]] = []
    for taxonomy, tag in sorted(tags):
        body = facts.get(taxonomy, {}).get(tag)
        if body is None:
            continue
        units = body.get("units") if isinstance(body, Mapping) else None
        if not isinstance(units, Mapping):
            raise DataError(f"{taxonomy}:{tag}: no `units` object")
        for unit, entries in units.items():
            if not isinstance(entries, list):
                raise DataError(f"{taxonomy}:{tag} [{unit}]: facts are not a list")
            rows.extend(_fact_row(e, taxonomy, tag, unit) for e in entries)
    names = [c for c in RAW_FACT_COLUMNS if c not in ("symbol", "cik")]
    df = pd.DataFrame.from_records(rows, columns=names)
    df.insert(0, "cik", pd.Series([cik_int] * len(df), dtype="int64"))
    df.insert(0, "symbol", pd.Series([normalize_symbol(symbol)] * len(df), dtype="str"))
    df["value"] = df["value"].astype("float64")
    df["duration_days"] = df["duration_days"].astype("int64")
    df["fy"] = df["fy"].astype("Int64")
    for col in ("taxonomy", "tag", "unit", "form", "accn"):
        df[col] = df[col].astype("str")
    df["fp"] = df["fp"].astype("str").where(df["fp"].notna(), None)
    return _dedupe(df).sort_values(_KEY).reset_index(drop=True)[list(RAW_FACT_COLUMNS)]


def _dedupe(df: pd.DataFrame) -> pd.DataFrame:
    dup = df.duplicated(subset=_KEY, keep="first")
    if not dup.any():
        return df
    first = df.drop_duplicates(subset=_KEY, keep="first").set_index(_KEY)["value"]
    later = df.loc[dup].set_index(_KEY)["value"]
    conflicts = int((later != first.reindex(later.index)).sum())
    if conflicts:
        _log.warning("fundamentals.conflicting_duplicates", count=conflicts)
    return df.loc[~dup]


def parse_submissions(table: Mapping[str, Any]) -> pd.DataFrame:
    """A filing index from a submissions column table (`filings.recent` or a history page):
    columns `accn, form, filed, accepted_at` (`accepted_at` UTC, `NaT` when blank).

    Ragged columns or unparseable dates raise `DataError`.
    """
    sources = ("accessionNumber", "form", "filingDate", "acceptanceDateTime")
    try:
        columns = [list(table[name]) for name in sources]
    except (KeyError, TypeError) as exc:
        raise DataError(f"submissions table lacks one of {sources}") from exc
    if len({len(c) for c in columns}) > 1:
        raise DataError(f"submissions columns differ in length: {[len(c) for c in columns]}")
    accn, form, filed, accepted = columns
    try:
        stamps = pd.to_datetime(
            pd.Series([a if a else None for a in accepted], dtype="object"),
            utc=True,
            format="ISO8601",
        )
    except (ValueError, TypeError) as exc:
        raise DataError(f"submissions: bad acceptanceDateTime ({exc})") from exc
    return pd.DataFrame(
        {
            "accn": pd.Series(accn, dtype="str"),
            "form": pd.Series(form, dtype="str"),
            "filed": pd.Series(
                [_iso_date(f, "submissions filingDate") for f in filed], dtype="object"
            ),
            "accepted_at": stamps.astype("datetime64[us, UTC]"),
        }
    )


def attach_available_at(
    facts: pd.DataFrame, index: pd.DataFrame, calendar: TradingCalendar
) -> pd.DataFrame:
    """`facts` (`RAW_FACT_COLUMNS`) with `accepted_at` and `available_at` (`FACT_COLUMNS`).

    `accepted_at` is the filing's acceptance time from `index` (`NaT` when the accession is
    not there); `available_at` is `accepted_at`, else `calendar.session_open` of the first
    session after `filed`.
    """
    accepted = index.drop_duplicates(subset="accn")[["accn", "accepted_at"]].copy()
    accepted["accepted_at"] = accepted["accepted_at"].astype("datetime64[us, UTC]")
    out = facts.drop(columns=["accepted_at"], errors="ignore").merge(
        accepted, on="accn", how="left", validate="many_to_one"
    )
    out["accepted_at"] = out["accepted_at"].astype("datetime64[us, UTC]")
    missing = out["accepted_at"].isna()
    out[AVAILABLE_AT] = out["accepted_at"]
    if missing.any():
        days = sorted(set(out.loc[missing, "filed"]))
        opens = pd.DataFrame(
            {
                "filed": pd.Series(days, dtype="object"),
                "open": pd.Series(
                    [pd.Timestamp(calendar.session_open(calendar.next_session(d))) for d in days],
                    dtype="datetime64[us, UTC]",
                ),
            }
        )
        fallback = out[["filed"]].merge(opens, on="filed", how="left")["open"]
        out[AVAILABLE_AT] = out["accepted_at"].where(~missing, fallback.to_numpy())
        _log.info("fundamentals.available_at_fallback", rows=int(missing.sum()))
    out[AVAILABLE_AT] = out[AVAILABLE_AT].astype("datetime64[us, UTC]")
    return out[list(FACT_COLUMNS)]
