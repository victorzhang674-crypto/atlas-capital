"""Point-in-time fundamentals from SEC EDGAR (Stage 09).

`edgar_client` fetches EDGAR JSON (ticker → CIK map, companyfacts, submissions) with a rate
limit and a raw cache; `parser` turns it into the `fundamentals_facts` table (registered on
import) with point-in-time `available_at`; `mapping` loads the concept map
(`config/xbrl_map.yaml`) and resolves a concept per period; `pit` builds
quarters, TTM, latest balances, ratios and the wide view (`Fundamentals(lake).pit(symbol,
as_of)`); `ingest` fills the table from EDGAR.
"""

from atlas.data.fundamentals.edgar_client import CikMap, EdgarClient, build_edgar_client
from atlas.data.fundamentals.ingest import FundamentalsIngestReport, ingest_fundamentals
from atlas.data.fundamentals.mapping import (
    PERIOD_COLUMNS,
    PERIOD_KEY,
    RATIO_INPUTS,
    Alternative,
    Concept,
    ConceptMap,
    Part,
    TaxTag,
    latest_filings,
    load_concept_map,
    parse_alternative,
    resolve_concept,
)
from atlas.data.fundamentals.parser import (
    FACT_COLUMNS,
    FUNDAMENTALS_FACTS,
    INDEX_COLUMNS,
    RAW_FACT_COLUMNS,
    attach_available_at,
    parse_companyfacts,
    parse_submissions,
)
from atlas.data.fundamentals.pit import (
    DETAIL_COLUMNS,
    QUARTER_COLUMNS,
    RATIO_NAMES,
    Fundamentals,
    MetricValue,
    compute_ratios,
    fundamentals_pit,
    fundamentals_pit_detail,
    known_facts,
    latest_balance,
    metric_names,
    quarterly_values,
    ttm,
)

__all__ = [
    "DETAIL_COLUMNS",
    "FACT_COLUMNS",
    "FUNDAMENTALS_FACTS",
    "INDEX_COLUMNS",
    "PERIOD_COLUMNS",
    "PERIOD_KEY",
    "QUARTER_COLUMNS",
    "RATIO_INPUTS",
    "RATIO_NAMES",
    "RAW_FACT_COLUMNS",
    "Alternative",
    "CikMap",
    "Concept",
    "ConceptMap",
    "EdgarClient",
    "Fundamentals",
    "FundamentalsIngestReport",
    "MetricValue",
    "Part",
    "TaxTag",
    "attach_available_at",
    "build_edgar_client",
    "compute_ratios",
    "fundamentals_pit",
    "fundamentals_pit_detail",
    "ingest_fundamentals",
    "known_facts",
    "latest_balance",
    "latest_filings",
    "load_concept_map",
    "metric_names",
    "parse_alternative",
    "parse_companyfacts",
    "parse_submissions",
    "quarterly_values",
    "resolve_concept",
    "ttm",
]
