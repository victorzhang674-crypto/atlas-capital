# Universe fixtures (Stage 08)

Used by `tests/unit/data/test_universe.py`; no test touches the network.

| File | What | Source |
|---|---|---|
| `wikipedia_sp500_constituents.html` | The S&P 500 constituents table (`table#constituents`, 503 rows: symbol linked to its exchange quote page, security, GICS sector and sub-industry, HQ, date added, CIK, founded) | **Recorded** 2026-10-27 from [List of S&P 500 companies, revision 1382178819](https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&oldid=1382178819) (2026-10-27 02:50 UTC) |
| `wikipedia_sp500_changes.html` | The change log (`table#changes`, 412 rows, 1976-07-01 → 2026-10-06: effective date, added ticker/security, removed ticker/security, reason, refs) | **Recorded** 2026-10-27 from [Historical components of the S&P 500, revision 1379179365](https://en.wikipedia.org/w/index.php?title=Historical_components_of_the_S%26P_500&oldid=1379179365) (2026-10-08 11:11 UTC) |

Each file holds the table cut verbatim from the article revision, inside a minimal HTML document with a provenance comment. The change log moved out of "List of S&P 500 companies" on 2026-08-11 (revision 1368903137); the parsers find each table by its id, so they read either layout.

Recorder: `Planning/stages/stage-08-universe/artifacts/record_wikipedia_fixtures.py` (fixed revisions, so re-recording gives the same tables).

Content © Wikipedia contributors, licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); used unchanged as test data.
