# Event fixtures (Stage 12)

Used by `tests/unit/data/events/`; no test touches the network (respx or an injected function
serves these files). Recorder: `Planning/stages/stage-12-events-sentiment/artifacts/record_event_fixtures.py`
(output: `record-fixtures-output.txt`), run 2026-10-29 with the owner's `SEC_USER_AGENT` and
`FRED_API_KEY` (neither appears in any file).

| Path | What | Source |
|---|---|---|
| `submissions/CIK0000320193.json` | AAPL EDGAR submissions, `filings.recent` **trimmed** to the 108 8-K / 10-Q / 10-K filings filed on or after 2018-01-01 (every column kept, incl. `items` and `reportDate`); `filings.files` unchanged (page 001 is served from Stage 09's `fixtures/fundamentals/submissions/`) | **Recorded** from `https://data.sec.gov/submissions/CIK0000320193.json` |
| `fred/release_dates_10.json`, `fred/release_dates_50.json` | `fred/release/dates` for CPI (151 dates) and the Employment Situation (146), `realtime_start=2015-01-01`, scheduled dates included (to 2026-12) | **Recorded** from the FRED API |
| `fred/initial_CPIAUCSL.json`, `fred/initial_PAYEMS.json` | `fred/series/observations` with `output_type=4` (initial release only) from 2014-01-01 (153 rows each) | **Recorded** from the FRED API |
| `yahoo_calendar.json` | yfinance `Ticker(s).calendar` for AAPL, MSFT, JPM (dates as ISO text; estimates kept as received, unused) | **Recorded** through yfinance 1.7 |

Re-recording later gives newer filings and dates, so tests pin past values only (the CPI/NFP
counts are pinned to these files).

SEC EDGAR data is public information of the U.S. Securities and Exchange Commission. FRED® data
is provided by the Federal Reserve Bank of St. Louis; CPI and payrolls by the U.S. Bureau of
Labor Statistics.
