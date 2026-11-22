"""The `filing_text` table: the MD&A of SEC filings, point in time (Stage 29 follow-up 2).

Per symbol: ticker → CIK (`CikMap`), the filings of `data.filing_text.forms` filed on or after
`data.filing_text.since` (the submissions JSON, archived in `raw/sec/` like the fundamentals'
filing index), and for each filing not yet stored: the primary document, its text
(`html_to_text`) and its MD&A item (`extract_mda`: 10-K Item 7, 10-Q Part I Item 2). When
the item only points to the annual report, the filing's Exhibit 13 and 99 documents are read
(`extract_exhibit_mda`), then a later MD&A section of the same document; when the filing has
no MD&A item heading at all (an annual-report layout with a cross-reference index), its own
MD&A heading is used (`extract_exhibit_mda` on the primary document). An MD&A shorter than
`data.filing_text.short_mda_chars` is a page of pointers when a later MD&A heading of the same
document holds a longer section (that one is kept) or, failing that, when it points to the
annual report and an exhibit's MD&A is longer (that one is kept).

One row per filing and section (`section` is `mda`), stored even when no MD&A was found
(`status` `by_reference` / `not_found`, no `text`), so a filing is fetched once. The documents
are not archived: a row keeps the document's URL and SHA-256. `available_at` is the filing's
acceptance time, else the open of the first session after the filing date (the rule of
`fundamentals_facts`).
"""

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig, FilingTextConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage import AVAILABLE_AT, DEFAULT_REGISTRY, Column, ColumnType, Lake, TableSchema
from atlas.core.types.base import FrozenDict
from atlas.core.types.instruments import normalize_symbol
from atlas.data.filings.mda import (
    MdaExtract,
    extract_exhibit_mda,
    extract_mda,
    html_to_text,
    points_elsewhere,
)
from atlas.data.fundamentals import CikMap, EdgarClient, parse_submissions

_log = get_logger(__name__)

_S, _I, _D, _T = ColumnType.STRING, ColumnType.INT64, ColumnType.DATE, ColumnType.TIMESTAMP

FILING_TEXT = DEFAULT_REGISTRY.register(
    TableSchema(
        name="filing_text",
        columns=(
            Column(name="symbol", dtype=_S),
            Column(name="cik", dtype=_I),
            Column(name="accn", dtype=_S),
            Column(name="form", dtype=_S),
            Column(name="period_end", dtype=_D, nullable=True),
            Column(name="filed", dtype=_D),
            Column(name="accepted_at", dtype=_T, nullable=True),
            Column(name=AVAILABLE_AT, dtype=_T),
            Column(name="section", dtype=_S),
            Column(name="source", dtype=_S),
            Column(name="document", dtype=_S),
            Column(name="url", dtype=_S),
            Column(name="sha256", dtype=_S),
            Column(name="status", dtype=_S),
            Column(name="chars", dtype=_I),
            Column(name="text", dtype=_S, nullable=True),
        ),
        primary_key=("symbol", "accn", "section"),
        partition_by=("symbol",),
        description="MD&A text of SEC filings (10-K Item 7 or Exhibit 13), keyed by filing; "
        "Stage 29 follow-up 2",
    )
)

MDA = "mda"
"""The `section` value of an MD&A row."""

EXHIBIT_13 = "EX-13"
"""The EDGAR document type of an annual report to shareholders filed with a 10-K."""

EXHIBITS = (EXHIBIT_13, "EX-99")
"""Exhibit types read, in this order, when a filing's MD&A item points elsewhere: the annual
report (EX-13) or another exhibit holding it (EX-99, as Clorox files it)."""


@dataclass(frozen=True)
class MdaText:
    """One filing's MD&A as the view returns it: the filing (`form`, `accn`, `filed`, fiscal
    `period_end`, `available_at` UTC), the extraction `status`, where the text came from
    (`source` `primary` or `EX-13`, `document`) and the `text` (`None` unless `ok`)."""

    symbol: str
    form: str
    accn: str
    filed: date
    period_end: date | None
    available_at: datetime
    status: str
    source: str
    document: str
    text: str | None


def mda_text(row: pd.Series) -> MdaText:
    """A `filing_text` row as an `MdaText` (missing values become `None`)."""

    def opt(value: Any) -> Any:
        return None if value is None or (not isinstance(value, str) and pd.isna(value)) else value

    return MdaText(
        symbol=str(row["symbol"]),
        form=str(row["form"]),
        accn=str(row["accn"]),
        filed=row["filed"],
        period_end=opt(row["period_end"]),
        available_at=pd.Timestamp(row[AVAILABLE_AT]).to_pydatetime(),
        status=str(row["status"]),
        source=str(row["source"]),
        document=str(row["document"]),
        text=opt(row["text"]),
    )


LIST_COLUMNS = ("accn", "form", "filed", "accepted_at", "period_end", "primary_document")
"""Columns of `parse_filing_list`."""


def parse_filing_list(table: Mapping[str, Any]) -> pd.DataFrame:
    """The filings of a submissions column table (`filings.recent` or a history page):
    `parse_submissions`' columns plus `period_end` (`reportDate`, `None` when blank) and
    `primary_document`. Missing or ragged columns and bad dates raise `DataError`."""
    base = parse_submissions(table)
    try:
        reports = list(table["reportDate"])
        documents = list(table["primaryDocument"])
    except (KeyError, TypeError) as exc:
        raise DataError("submissions table lacks reportDate or primaryDocument") from exc
    if not len(reports) == len(documents) == len(base):
        raise DataError(
            f"submissions columns differ in length: {[len(base), len(reports), len(documents)]}"
        )
    try:
        ends = [date.fromisoformat(r) if r else None for r in reports]
    except (TypeError, ValueError) as exc:
        raise DataError(f"submissions: bad reportDate ({exc})") from exc
    base["period_end"] = pd.Series(ends, dtype="object")
    base["primary_document"] = pd.Series(documents, dtype="str")
    return base[list(LIST_COLUMNS)]


def filing_list(
    client: EdgarClient, cik: int, *, forms: Sequence[str], since: date, refresh: bool
) -> pd.DataFrame:
    """The filings of `cik` of `forms` filed on or after `since` (`LIST_COLUMNS`), oldest
    first: `filings.recent` plus the history pages whose date range reaches `since`."""
    subs = client.submissions(cik, refresh=refresh)
    filings = subs.get("filings")
    if not isinstance(filings, Mapping) or not isinstance(filings.get("recent"), Mapping):
        raise DataError(f"submissions of CIK {cik}: no filings.recent table")
    frames = [parse_filing_list(filings["recent"])]
    for page in filings.get("files", []):
        try:
            high = date.fromisoformat(page["filingTo"])
            name = page["name"]
        except (KeyError, TypeError, ValueError) as exc:
            raise DataError(f"submissions of CIK {cik}: bad history entry {page!r}") from exc
        if high >= since:
            frames.append(parse_filing_list(client.submissions_page(name)))
    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset="accn", keep="first")
    out = out[out["form"].isin(list(forms)) & (out["filed"] >= since)]
    return out.sort_values(["filed", "accn"], kind="stable").reset_index(drop=True)


@dataclass(frozen=True)
class FilingTextIngestReport:
    """Outcome of `ingest_filing_text`: `rows` stored; `symbols_ok` and `symbols_failed` in
    request order; `failure_reasons` per failed symbol; `statuses` counts the stored rows by
    `status` (`ok`, `by_reference`, `not_found`)."""

    rows: int
    symbols_ok: tuple[str, ...]
    symbols_failed: tuple[str, ...]
    failure_reasons: FrozenDict[str, str] = field(default_factory=lambda: FrozenDict({}))
    statuses: FrozenDict[str, int] = field(default_factory=lambda: FrozenDict({}))


def _stored_accns(lake: Lake, symbol: str) -> set[str]:
    if not lake.has_table(FILING_TEXT.name):
        return set()
    rows = lake.read_table(
        FILING_TEXT.name, filters=[("symbol", "=", symbol)], columns=["accn", "section"]
    )
    return set(rows.loc[rows["section"] == MDA, "accn"])


def _unless_later(found: MdaExtract, text: str, settings: FilingTextConfig) -> MdaExtract:
    """`found`, or the longest section under a later MD&A heading of the same document (not an
    "Item" line) when `found` is shorter than `short_mda_chars` and that section is longer:
    a short Item 7 is then a page of pointers to it (Southern Company's combined MD&A,
    Entergy's "refer to ...")."""
    if found.text is None or len(found.text) >= settings.short_mda_chars:
        return found
    later = extract_exhibit_mda(text, min_chars=settings.min_mda_chars, skip_items=True)
    return later if later.text is not None and len(later.text) > len(found.text) else found


def _mda_of(
    client: EdgarClient, cik: int, filing: pd.Series, settings: FilingTextConfig
) -> tuple[MdaExtract, str, str, bytes]:
    """The MD&A of one filing: `(extract, source, document name, document bytes)`."""
    min_chars = settings.min_mda_chars
    name = str(filing["primary_document"])
    body = client.filing_document(cik, filing["accn"], name)
    text = html_to_text(body)
    found = extract_mda(text, min_chars=min_chars, form=str(filing["form"]))
    if found.status == "not_found":
        # an annual-report layout with a cross-reference index has no "Item 7" heading
        # (JPMorgan, Bank of America, GE): its MD&A heading is used instead
        found = extract_exhibit_mda(text, min_chars=min_chars)
        if found.text is None:
            return found, "primary", name, body
    if found.text is not None:
        found = _unless_later(found, text, settings)
        if len(found.text or "") >= settings.short_mda_chars or not points_elsewhere(
            found.text or ""
        ):
            return found, "primary", name, body
        # a short MD&A that points to the annual report (Total System Services, Sempra)
    for kind, exhibit in client.filing_documents(cik, filing["accn"]):
        prefix = next((p for p in EXHIBITS if kind.upper().startswith(p)), None)
        if prefix is None:
            continue
        ex_body = client.filing_document(cik, filing["accn"], exhibit)
        ex_found = extract_exhibit_mda(html_to_text(ex_body), min_chars=min_chars)
        if ex_found.text is not None and len(ex_found.text) > len(found.text or ""):
            return ex_found, prefix, exhibit, ex_body
    if found.text is not None:
        return found, "primary", name, body
    # the annual report is a later section of the 10-K itself (AEP, Mosaic, Time Warner)
    later = extract_exhibit_mda(text, min_chars=min_chars, skip_items=True)
    if later.status == "ok":
        return later, "primary", name, body
    return found, "primary", name, body


def _rows(
    symbol: str,
    cik: int,
    filings: pd.DataFrame,
    client: EdgarClient,
    config: AtlasConfig,
    calendar: TradingCalendar,
) -> pd.DataFrame:
    settings = config.data.filing_text
    template = config.data.fundamentals.filing_document_url
    records = []
    for _, filing in filings.iterrows():
        found, source, document, body = _mda_of(client, cik, filing, settings)
        accepted = filing["accepted_at"]
        available = (
            accepted
            if not pd.isna(accepted)
            else pd.Timestamp(calendar.session_open(calendar.next_session(filing["filed"])))
        )
        records.append(
            {
                "symbol": symbol,
                "cik": cik,
                "accn": filing["accn"],
                "form": filing["form"],
                "period_end": filing["period_end"],
                "filed": filing["filed"],
                "accepted_at": accepted,
                AVAILABLE_AT: available,
                "section": MDA,
                "source": source,
                "document": document,
                "url": template.format(
                    cik=cik, folder=str(filing["accn"]).replace("-", ""), name=document
                ),
                "sha256": hashlib.sha256(body).hexdigest(),
                "status": found.status,
                "chars": len(found.text) if found.text is not None else 0,
                "text": found.text,
            }
        )
    frame = pd.DataFrame.from_records(records, columns=[c.name for c in FILING_TEXT.columns])
    for col in ("accepted_at", AVAILABLE_AT):
        frame[col] = pd.to_datetime(frame[col], utc=True).astype("datetime64[us, UTC]")
    return frame


def ingest_filing_text(
    lake: Lake,
    client: EdgarClient,
    symbols: Sequence[str],
    *,
    cik_map: CikMap,
    config: AtlasConfig | None = None,
    calendar: TradingCalendar | None = None,
    refresh: bool = True,
) -> FilingTextIngestReport:
    """Fetch and store the MD&A of the new filings of `symbols` (Atlas symbols).

    `config` defaults to `AtlasConfig()`, `calendar` to `TradingCalendar(config=config.
    calendar)`. `refresh=False` reuses the archived submissions JSON (the filing list); a
    filing already stored is never fetched again. A symbol without a CIK, without a filing of
    `forms` since `since`, or whose EDGAR data fails (`DataError`) is recorded in
    `symbols_failed` and never stops the batch; a failed symbol stores nothing. The request
    is checked first: a bare string or an invalid symbol raises `ValueError`; symbols are
    normalised, then de-duplicated in first-seen order.
    """
    if isinstance(symbols, str):
        raise ValueError("symbols must be a sequence of tickers, not one string")
    requested = tuple(dict.fromkeys(normalize_symbol(s) for s in symbols))
    cfg = config if config is not None else AtlasConfig()
    cal = calendar if calendar is not None else TradingCalendar(config=cfg.calendar)
    settings = cfg.data.filing_text
    ok: list[str] = []
    reasons: dict[str, str] = {}
    statuses: Counter[str] = Counter()
    rows = 0
    for symbol in requested:
        cik = cik_map.cik(symbol)
        if cik is None:
            reasons[symbol] = "no CIK (not in company_tickers.json or config/cik_overrides.yaml)"
            continue
        try:
            filings = filing_list(
                client, cik, forms=settings.forms, since=settings.since, refresh=refresh
            )
            if filings.empty:
                reasons[symbol] = f"no {'/'.join(settings.forms)} filed since {settings.since}"
                continue
            new = filings[~filings["accn"].isin(_stored_accns(lake, symbol))]
            stored = _rows(symbol, cik, new, client, cfg, cal)
            if not stored.empty:
                lake.write_table(
                    FILING_TEXT.name, stored, mode="upsert", keys=list(FILING_TEXT.primary_key)
                )
        except DataError as exc:
            reasons[symbol] = str(exc)
            _log.warning("filing_text.symbol_failed", symbol=symbol, error=str(exc))
            continue
        ok.append(symbol)
        rows += len(stored)
        statuses.update(stored["status"])
    report = FilingTextIngestReport(
        rows=rows,
        symbols_ok=tuple(ok),
        symbols_failed=tuple(reasons),
        failure_reasons=FrozenDict(reasons),
        statuses=FrozenDict(dict(statuses)),
    )
    _log.info(
        "filing_text.ingest",
        rows=rows,
        symbols_ok=len(ok),
        symbols_failed=len(reasons),
        statuses=dict(statuses),
    )
    return report
