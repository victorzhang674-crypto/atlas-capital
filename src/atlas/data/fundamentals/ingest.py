"""Ingest SEC EDGAR fundamentals into the lake's `fundamentals_facts` table.

Per symbol: ticker → CIK (`CikMap`), companyfacts JSON (`EdgarClient`, archived in
`raw/sec/`), the facts of the concept map's tags (`parse_companyfacts`), the filing index for
their accessions (`EdgarClient.filing_index`) and their `available_at`
(`attach_available_at`), then an upsert keyed by the table's primary key (re-running is
idempotent; a restating filing adds rows, it never overwrites the original's).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage import Lake
from atlas.core.types.base import FrozenDict
from atlas.core.types.instruments import normalize_symbol
from atlas.data.fundamentals.edgar_client import CikMap, EdgarClient
from atlas.data.fundamentals.mapping import ConceptMap, load_concept_map
from atlas.data.fundamentals.parser import (
    FUNDAMENTALS_FACTS,
    attach_available_at,
    parse_companyfacts,
)

_log = get_logger(__name__)


@dataclass(frozen=True)
class FundamentalsIngestReport:
    """Outcome of `ingest_fundamentals`.

    `rows` were upserted; `symbols_ok` and `symbols_failed` keep request order (canonical
    symbols); `failure_reasons` says why each failed symbol was skipped; `fallback_rows`
    counts facts whose filing had no acceptance time (`available_at` = filed + 1 session).
    """

    rows: int
    symbols_ok: tuple[str, ...]
    symbols_failed: tuple[str, ...]
    failure_reasons: FrozenDict[str, str] = field(default_factory=lambda: FrozenDict({}))
    fallback_rows: int = 0


def ingest_fundamentals(
    lake: Lake,
    client: EdgarClient,
    symbols: Sequence[str],
    *,
    cik_map: CikMap,
    config: AtlasConfig | None = None,
    calendar: TradingCalendar | None = None,
    concept_map: ConceptMap | None = None,
    refresh: bool = True,
) -> FundamentalsIngestReport:
    """Fetch and store the fundamentals of `symbols` (Atlas symbols: latest tickers).

    `config` defaults to `AtlasConfig()`; `calendar` to `TradingCalendar(config=
    config.calendar)`; `concept_map` to `config.data.fundamentals.xbrl_map_file` (relative to
    the working directory). `refresh=False` reuses archived raw JSON. A symbol without a CIK,
    whose EDGAR data fails (`DataError`), or with no mapped facts, is recorded in
    `symbols_failed` and never aborts the batch.
    """
    cfg = config if config is not None else AtlasConfig()
    cal = calendar if calendar is not None else TradingCalendar(config=cfg.calendar)
    cmap = (
        concept_map
        if concept_map is not None
        else load_concept_map(cfg.data.fundamentals.xbrl_map_file)
    )
    tags = cmap.tags()
    ok: list[str] = []
    reasons: dict[str, str] = {}
    rows = fallback = 0
    for raw_symbol in dict.fromkeys(symbols):
        symbol = normalize_symbol(raw_symbol)
        cik = cik_map.cik(symbol)
        if cik is None:
            reasons[symbol] = "no CIK in company_tickers.json (not a current SEC registrant)"
            continue
        try:
            facts = parse_companyfacts(
                client.companyfacts(cik, refresh=refresh), symbol=symbol, tags=tags
            )
            if facts.empty:
                reasons[symbol] = "no mapped facts in companyfacts"
                continue
            needed = dict(zip(facts["accn"], facts["filed"], strict=True))
            index = client.filing_index(cik, needed, refresh=refresh)
            stored = attach_available_at(facts, index, cal)
            lake.write_table(
                FUNDAMENTALS_FACTS.name,
                stored,
                mode="upsert",
                keys=list(FUNDAMENTALS_FACTS.primary_key),
            )
        except DataError as exc:
            reasons[symbol] = str(exc)
            _log.warning("fundamentals.symbol_failed", symbol=symbol, error=str(exc))
            continue
        ok.append(symbol)
        rows += len(stored)
        fallback += int(stored["accepted_at"].isna().sum())
    report = FundamentalsIngestReport(
        rows=rows,
        symbols_ok=tuple(ok),
        symbols_failed=tuple(reasons),
        failure_reasons=FrozenDict(reasons),
        fallback_rows=fallback,
    )
    _log.info(
        "fundamentals.ingest",
        rows=rows,
        symbols_ok=len(ok),
        symbols_failed=len(reasons),
        fallback_rows=fallback,
    )
    return report
