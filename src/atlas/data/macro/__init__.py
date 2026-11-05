"""Point-in-time US macro data from FRED / ALFRED (Stage 10).

`fred_client` fetches FRED JSON (metadata, vintage dates, observations by real-time period) with
a rate limit and a raw cache; `series` holds the series catalog (`config/macro.yaml`), the
`macro_observations` and `macro_series` tables (registered on import), the parsing of FRED JSON
and the `available_at` rules; `ingest` fills the tables from FRED; `pit` answers what was known
at `as_of` (`Macro(lake).pit(series_ids, as_of, lookback)`); `transforms` derives YoY,
3-month annualised, z-score, curve slope and real rate from a point-in-time frame.
"""

from atlas.data.macro.fred_client import FredClient, build_fred_client
from atlas.data.macro.ingest import LATEST, MacroIngestReport, ingest_macro
from atlas.data.macro.pit import (
    DETAIL_COLUMNS,
    Macro,
    known_observations,
    macro_pit,
    macro_pit_detail,
    macro_vintages,
)
from atlas.data.macro.series import (
    MACRO_OBSERVATIONS,
    MACRO_SERIES,
    OBS_COLUMNS,
    ROW_COLUMNS,
    MacroCatalog,
    MacroSeries,
    SeriesKind,
    attach_available_at,
    collapse_vintages,
    load_macro_series,
    parse_observations,
    parse_series_metadata,
    vintage_windows,
)
from atlas.data.macro.transforms import (
    annualized_3m_pct,
    curve_slope,
    real_rate,
    yoy_pct,
    zscore,
)

__all__ = [
    "DETAIL_COLUMNS",
    "LATEST",
    "MACRO_OBSERVATIONS",
    "MACRO_SERIES",
    "OBS_COLUMNS",
    "ROW_COLUMNS",
    "FredClient",
    "Macro",
    "MacroCatalog",
    "MacroIngestReport",
    "MacroSeries",
    "SeriesKind",
    "annualized_3m_pct",
    "attach_available_at",
    "build_fred_client",
    "collapse_vintages",
    "curve_slope",
    "ingest_macro",
    "known_observations",
    "load_macro_series",
    "macro_pit",
    "macro_pit_detail",
    "macro_vintages",
    "parse_observations",
    "parse_series_metadata",
    "real_rate",
    "vintage_windows",
    "yoy_pct",
    "zscore",
]
