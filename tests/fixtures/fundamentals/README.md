# Fundamentals fixtures (Stage 09)

Used by `tests/unit/data/fundamentals/`; no test touches the network (respx serves these files
at their EDGAR URLs, see `fundamentals_helpers.serve_fixture`).

| Path | What | Source |
|---|---|---|
| `companyfacts/CIK0000320193.json` (AAPL), `CIK0000789019.json` (MSFT), `CIK0000019617.json` (JPM) | XBRL companyfacts, **trimmed** to the 35 tags named in `config/xbrl_map.yaml` (every unit and every filing of those tags kept, values unchanged) | **Recorded** 2026-10-28 from `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` |
| `submissions/CIK##########.json` | Filing index (`filings.recent`), **trimmed** to the filings whose accession number appears in the trimmed companyfacts (every column kept); `filings.files` lists all history pages unchanged | **Recorded** 2026-10-28 from `https://data.sec.gov/submissions/CIK##########.json` |
| `submissions/CIK##########-submissions-NNN.json` | History pages whose date range covers the filing date of a fact whose accession is not in `recent` (AAPL 1, MSFT 1, JPM 44), trimmed the same way | **Recorded** 2026-10-28 from `https://data.sec.gov/submissions/<name>` |
| `company_tickers.json` | 18 tickers of six registrants (AAPL, MSFT, JPM and its preferreds, BRK-B/BRK-A, GOOGL/GOOG/…, META), entries kept with their original keys and fields | **Recorded** 2026-10-28 from `https://www.sec.gov/files/company_tickers.json` (10,429 entries) with the owner's `SEC_USER_AGENT`, trimmed to those six CIKs (`Planning/stages/stage-09-fundamentals/artifacts/record_company_tickers.py`). It replaced a file built earlier the same day from the submissions `cik`/`tickers`/`name`, because www.sec.gov answers 403 without a contact User-Agent; both hold the same 18 ticker-CIK pairs |

Recorders: `Planning/stages/stage-09-fundamentals/artifacts/record_sec_fixtures.py` (output: `record-fixtures-output.txt`) and `record_company_tickers.py` (output: `record-company-tickers-output.txt`). Re-recording later gives newer filings, so tests pin values of past fiscal periods only.

SEC EDGAR data is public information of the U.S. Securities and Exchange Commission.
