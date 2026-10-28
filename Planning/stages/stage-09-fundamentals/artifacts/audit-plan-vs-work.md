# Stage 09 audit — plan vs work (2026-10-28)

Owner's request: "After Stage 9 completion, please also help me to audit the stage by comparing the plans with the work that you have done." Audit run after every acceptance criterion was verified and before the stage commit, so its fixes and records go into the stage commit. Sources compared: [brief.md](../brief.md), [plan.md](../plan.md), the code and tests, [worklog.md](../worklog.md), [verification.md](../verification.md).

## 1. Brief → work

| Brief item | Done? | Where |
|---|---|---|
| EDGAR client: `company_tickers.json` (ticker→CIK), `companyfacts`; User-Agent from config; 10 req/s; raw JSON cached in `raw/sec/` | Yes | `edgar_client.py` (`CikMap`, `EdgarClient`, `build_edgar_client` reads the `SEC_USER_AGENT` secret); throttle 0.1 s before every request but the first (`test_client_throttles_requests`); raw archive (`test_client_sends_user_agent_and_caches_raw`) |
| Long table `fundamentals_facts(cik, symbol, tag, unit, value, period_start, period_end, fp, fy, form, filed, accepted_at→available_at, accn)` | Yes, plus `taxonomy`, `duration_days`, `accepted_at` kept | `parser.py` (D1). Only the concept map's tags are stored (raw JSON keeps all): an interpretation, logged |
| Concept mapping with fallbacks, 15 concepts, TotalDebt as a sum of parts | Yes | `config/xbrl_map.yaml`, `mapping.py` (D6) |
| Q4 = FY − Q1..Q3; TTM for flows; latest balance for stocks | Yes (also YTD differences) | `pit.py` `quarterly_values`, `ttm`, `latest_balance` |
| Restatements: keep each filing; latest value filed before `as_of` per period | Yes | PK includes `accn`; `known_facts` = `pit_filter` → `latest_filings` |
| Wide view + ratios (gross/op margin, ROE, ROA, D/E, FCF) | Yes | `fundamentals_pit`, `Fundamentals.pit` (D9) |
| Acceptance timestamps from submissions; else filed + 1 session | Yes | `filing_index`, `attach_available_at` (D2, D5); UTC verified (artifacts/acceptance-utc-check.txt) |
| Fixtures AAPL, MSFT, JPM companyfacts trimmed | Yes | real recordings (README); `company_tickers.json` built, not recorded (403) |
| Tasks 1–6 and their named tests (AAPL FY2023 revenue 383,285 M; JPM gross profit NaN; restatement; look-ahead) | Yes | verification.md, "Brief tasks" line |
| Acceptance: `fundamentals_pit("AAPL", as_of)` matches fixture figures; restatement and look-ahead tests pass | Yes | verification.md (ALL CRITERIA VERIFIED) |

Out of scope respected: no analyst estimates, no non-US filers, no filing text.

## 2. Plan → work, findings

| # | Finding | Severity | Resolution |
|---|---|---|---|
| A1 | Three test names in the plan differ from the code: `test_fundamentals_pit_aapl_fy2023` → `test_acceptance_fundamentals_pit_aapl_fy2023`, `test_ratio_with_nonpositive_denominator_is_nan` → `test_ratio_with_missing_or_nonpositive_denominator_is_nan`, `test_resolve_ignores_other_units` → `test_resolve_ignores_other_units_and_kinds`. Every other one of the 64 test names in the plan exists (checked by script). The cases are covered | Records | Plan annotated in place |
| A2 | Interfaces changed during execution, before their code existed: `filing_index(cik, needed)` (was `filed_dates`); `Fundamentals` has no `calendar` parameter | Records | Annotated in plan D3/D5/D9 and Decisions log |
| A3 | Public names built beyond the plan's file table (`latest_filings`, `parse_alternative`, `RATIO_INPUTS`, `PERIOD_KEY`/`PERIOD_COLUMNS`, `MetricValue`, `compute_ratios`, `metric_names`, `RATIO_NAMES`, `DETAIL_COLUMNS`, `QUARTER_COLUMNS`, `Fundamentals.facts`, `EdgarClient.close`/context manager) | Records | Plan file table annotated; all documented and tested |
| A4 | Tests that were never red: `test_fundamentals_defaults`/`test_fundamentals_validation` (config model written first, worklog Task 1) and `test_fundamentals_guards.py` (written for coverage after the code) | Process | Mutation checks: 12 mutants of the guarded lines, **12/12 killed** (artifacts/mutation-checks.txt) |
| A5 | An untested, undocumented behaviour of the fallback rule: within a period the first tag wins even when a lower-priority tag was filed later, so a restatement filed under another tag is not picked up | Limitation | Pinned by `test_resolve_priority_beats_a_later_filing_of_a_lower_tag` and a line in `docs/data-limitations.md` (Fundamentals, item 3); full verification re-run afterwards |
| A6 | `-m live` test skipped: `SEC_USER_AGENT` is not set (needs the owner's contact User-Agent in `.env`) | Not a criterion | Real-data check run against live data.sec.gov with the descriptive User-Agent (artifacts/real-ingest-check.txt); Backlog item asks the owner to add the secret |
| A7 | Plan constraint "one commit for the stage" vs Project.md Rule 6A, which wants the commit and merge hashes and the merged-`main` checks recorded in the worklog: those records can only be written after the commit and the fast-forward, so they need a second, records-only commit. The sandbox also cannot write the main checkout, so the fast-forward is a Rule 6 exception (owner runs it), as in Stages 07–08 | Process | Pending at audit time: stage commit, fast-forward attempt, owner fast-forward, merged-main checks, records-only commit |
| A8 | `-k acceptance` also selects five acceptance-*time* tests (7 selected, both acceptance tests among them) | Cosmetic | Recorded in verification.md |
| A9 | Plan Task 8 lists four words for the doc test; the test checks seven (also "nan", "q4", "dimension") | None | Stricter than planned |

## 3. Design decisions D1–D11 → code

All eleven match the code (D3, D5, D9 with the annotations of A2). Spot checks: D1 PK and `partition_by=("symbol",)` in `FUNDAMENTALS_FACTS`; D2 fallback = `calendar.session_open(calendar.next_session(filed))` (`test_available_at_fallback_is_next_session_open`: Friday 2023-11-03 → Monday 14:30 UTC); D7 ranges from `cfg.data.fundamentals.quarter_days`/`annual_days`; D8 staleness boundary inclusive at 400 days (`test_stale_metric_is_nan`, mutant killed); D10 every key in `config/fund.yaml` with a comment and in `FundamentalsConfig` with a default (`test_every_config_key_is_written_in_yaml`, `test_every_yaml_key_has_a_comment`); D11 failures never abort (`test_failed_symbol_never_aborts_batch`).

## 4. Definition of Done (stage protocol §3)

| Item | Status |
|---|---|
| Acceptance criteria met with named evidence | Yes (verification.md) |
| Brief's Verify run by the AI | Yes, 127 passed after the audit fix |
| `pytest -q` green, nothing skipped beyond live/llm | Yes |
| ruff check / format clean; mypy clean | Yes |
| Docstrings with units on new public names | Yes (45 checked, none missing) |
| New config keys in YAML with comments and in `AtlasConfig` with defaults | Yes (`data.fundamentals`) |
| Stage folder holds plan, worklog, raw verification output | Yes |
| Evidence block in verification.md and PROGRESS.md before DONE | Yes |
| One commit `stage-09: …`, verified | Pending at audit time (A7) |
| Merged into `main`, checks re-run on the merged result, hashes in worklog | Pending at audit time (A7: sandbox → owner fast-forward) |

## 5. Conclusion

The work matches the brief and the plan: every brief item, task and acceptance criterion is built and verified, with two interface changes and three test renames recorded. One real gap was found (A5, an undocumented fallback behaviour) and fixed in records and tests before the commit. What remains is git: the stage commit, the fast-forward of `main` (owner, sandbox), the merged-`main` checks and their records (A7).
