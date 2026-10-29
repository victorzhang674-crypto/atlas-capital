"""Ingest FRED / ALFRED macro series into the lake's `macro_observations` and `macro_series`.

Per series: metadata (`FredClient.series`) → `macro_series`; every vintage date from
`vintage_start` → real-time windows of at most `max_vintage_dates_per_request` vintages
(`vintage_windows`) → observations by real-time period per window → rows, merged across window
boundaries (`collapse_vintages`) → `available_at` (`attach_available_at`) → upsert keyed by
the table's primary key. The whole vintage history is read each time, so re-running is
idempotent and a new vintage adds rows without touching older ones.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.logging import get_logger
from atlas.core.storage import Lake
from atlas.core.types.base import FrozenDict
from atlas.data.macro.fred_client import FredClient
from atlas.data.macro.series import (
    MACRO_OBSERVATIONS,
    MACRO_SERIES,
    MacroCatalog,
    attach_available_at,
    collapse_vintages,
    load_macro_series,
    parse_observations,
    parse_series_metadata,
    vintage_windows,
)

_log = get_logger(__name__)

LATEST = date(9999, 12, 31)
"""FRED's "latest" date for `observation_end` / `realtime_end`."""


@dataclass(frozen=True)
class MacroIngestReport:
    """Outcome of `ingest_macro`.

    `rows` were upserted into `macro_observations`; `series_ok` and `series_failed` keep
    request order; `failure_reasons` says why each failed series was skipped; `revision_rows`
    counts rows that revise an earlier vintage of the same observation.
    """

    rows: int
    series_ok: tuple[str, ...]
    series_failed: tuple[str, ...]
    failure_reasons: FrozenDict[str, str] = field(default_factory=lambda: FrozenDict({}))
    revision_rows: int = 0


def _requested(series_ids: Sequence[str] | None, catalog: MacroCatalog) -> tuple[str, ...]:
    if series_ids is None:
        return catalog.ids
    if isinstance(series_ids, str):
        raise ValueError("series_ids must be a sequence of FRED ids, not one string")
    requested = tuple(dict.fromkeys(s.strip().upper() for s in series_ids))
    unknown = [s for s in requested if s not in catalog]
    if unknown:
        raise ValueError(f"not in the macro series catalog: {unknown}")
    return requested


def ingest_macro(
    lake: Lake,
    client: FredClient,
    series_ids: Sequence[str] | None = None,
    *,
    config: AtlasConfig | None = None,
    catalog: MacroCatalog | None = None,
    observation_end: date | None = None,
    realtime_end: date | None = None,
    refresh: bool = True,
) -> MacroIngestReport:
    """Fetch and store the vintage history of `series_ids` (default: the whole catalog).

    Observations are read from `config.data.macro.observation_start` to `observation_end`, and
    vintages from `vintage_start` to `realtime_end` (both default to FRED's latest; pass a
    past date to rebuild the data as it stood then). `config` defaults to `AtlasConfig()`,
    `catalog` to `config.data.macro.series_file` (relative to the working directory).
    `refresh=False` reuses archived raw JSON. The request is checked first: a bare string or an
    id not in the catalog raises `ValueError`; ids are upper-cased and de-duplicated. A series
    whose FRED data fails (`DataError`) is recorded in `series_failed` and never aborts the
    batch.
    """
    cfg = config if config is not None else AtlasConfig()
    settings = cfg.data.macro
    cat = catalog if catalog is not None else load_macro_series(settings.series_file)
    requested = _requested(series_ids, cat)
    obs_end = observation_end if observation_end is not None else LATEST
    rt_end = realtime_end if realtime_end is not None else LATEST
    ok: list[str] = []
    reasons: dict[str, str] = {}
    rows = revisions = 0
    for series_id in requested:
        series = cat.get(series_id)
        try:
            meta = parse_series_metadata(client.series(series_id, refresh=refresh), series)
            vintages = client.vintage_dates(
                series_id,
                realtime_start=settings.vintage_start,
                realtime_end=rt_end,
                refresh=refresh,
            )
            windows = vintage_windows(
                vintages, settings.vintage_start, rt_end, settings.max_vintage_dates_per_request
            )
            parts = [
                parse_observations(
                    client.observations(
                        series_id,
                        observation_start=settings.observation_start,
                        observation_end=obs_end,
                        realtime_start=low,
                        realtime_end=high,
                        refresh=refresh,
                    ),
                    series_id,
                )
                for low, high in windows
            ]
            merged = collapse_vintages(pd.concat(parts, ignore_index=True))
            stored = attach_available_at(merged, series, settings)
            lake.write_table(MACRO_SERIES.name, meta, mode="upsert")
            lake.write_table(MACRO_OBSERVATIONS.name, stored, mode="upsert")
        except DataError as exc:
            reasons[series_id] = str(exc)
            _log.warning("macro.series_failed", series_id=series_id, error=str(exc))
            continue
        ok.append(series_id)
        rows += len(stored)
        revisions += len(stored) - stored["obs_date"].nunique()
    report = MacroIngestReport(
        rows=rows,
        series_ok=tuple(ok),
        series_failed=tuple(reasons),
        failure_reasons=FrozenDict(reasons),
        revision_rows=revisions,
    )
    _log.info(
        "macro.ingest",
        rows=rows,
        series_ok=len(ok),
        series_failed=len(reasons),
        revision_rows=revisions,
    )
    return report
