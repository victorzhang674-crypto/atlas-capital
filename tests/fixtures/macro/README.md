# Macro fixtures (Stage 10)

Used by `tests/unit/data/macro/`; no test touches the network (respx serves these files at
their FRED API URLs, see `macro_helpers.serve_fixture`).

The FRED API needs an API key, which was not available when these were recorded
(`FRED_API_KEY` empty), so the files are **built in the documented FRED API JSON shapes from
real ALFRED data** downloaded without a key on 2026-10-29 by
`Planning/stages/stage-10-macro/artifacts/record_fred_fixtures.py` (output:
`record-fixtures-output.txt`). Values, dates and vintage dates are real; the JSON wrapping is
ours.

| Path | What | Source |
|---|---|---|
| `series/CPIAUCSL.json`, `series/DGS10.json` | `fred/series` shape with only `id`, `title`, `frequency`, `frequency_short`, `units`, `seasonal_adjustment`, `seasonal_adjustment_short` (as shown on the ALFRED page; DGS10's units read `%` there) | title and description of `https://alfred.stlouisfed.org/series/downloaddata?seid=<ID>` |
| `vintagedates/<ID>.json` | `fred/series/vintagedates` shape: every vintage date (CPIAUCSL 670 from 1972-07-21; DGS10 5139 from 2005-06-28, to the recording day). The fixture server filters them to the requested real-time window and pages them | the vintage list of the same ALFRED page |
| `observations/CPIAUCSL.json` | `fred/series/observations`, `output_type=1`, answering `observation_start=2018-01-01`, `observation_end=2021-06-01`, `realtime_start=2019-01-01`, `realtime_end=2021-12-31`: 114 rows for 42 months (each seasonal-factor revision is a new real-time period) | one ALFRED CSV per vintage (`alfredgraph.csv?id=CPIAUCSL&vintage_date=<D>`) at 2019-01-01 and at each of the 39 vintage dates in the window; consecutive vintages with the same value form one period, periods clipped to the window as the API does |
| `observations/DGS10.json` | the same for `observation_start=2020-03-02`, `observation_end=2020-04-30`, `realtime_start=2020-03-01`, `realtime_end=2020-05-31`: 44 rows, one per business day (no revisions in that window; Good Friday 2020-04-10 is `"."`) | 64 ALFRED CSVs (2020-03-01 and the 63 vintages in the window) |

Real values the tests pin: CPI March 2020 is absent in the 2020-04-09 vintage, **257.953** from
2020-04-10, **257.989** from 2021-02-08; Feb 2020 259.050 (2020-03-11); March 2019 254.148
(2019-04-10), 254.095 (2020-02-11), 254.147 (2021-02-08). DGS10 2020-03-02 = 1.10, first in the
2020-03-03 vintage.

FRED® and ALFRED® data are provided by the Federal Reserve Bank of St. Louis; CPI by the U.S.
Bureau of Labor Statistics; Treasury yields by the Board of Governors of the Federal Reserve
System.
