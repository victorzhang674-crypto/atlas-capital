# Stage 09 final audit (2026-10-29)

Owner's request: "is everything for this stage completed? please fully audit according to the planning markdown files." Audited on `main` = stage branch = `9423387` (both clean), against Project.md, `01-master-roadmap.md`, `00-design-spec.md` (§3, §11, the sections the brief cites), `02-stage-protocol.md` (§2, §3 Definition of Done, §4–§5), [brief.md](../brief.md), [plan.md](../plan.md) including its addendum, and the records. The earlier audit ([audit-plan-vs-work.md](audit-plan-vs-work.md), 2026-10-28) covered plan vs work at DONE; this one covers everything since as well.

**Verdict: complete.** Every brief item, task and acceptance criterion is built and verified on `main`; every Definition-of-Done item holds; the records had seven small gaps (F1–F7), all fixed in the records commit `stage-09: final audit records`. No code or test changed in this audit.

## 1. Fresh verification (this audit)

| Check | Result | Raw |
|---|---|---|
| `make check` on `main` `9423387` (read-only) | exit 0: ruff All checks passed!, 113 files already formatted, mypy Success (55 files), **948 passed**, 4 deselected | [audit-main-check.txt](audit-main-check.txt) |
| Brief's Verify `uv run pytest tests/unit/data/fundamentals -q` on `main` | **141 passed**, 1 deselected | same |
| `-m live` on `main` (real EDGAR, `SEC_USER_AGENT` from the root `.env`) | **1 passed**: AAPL FY2023 revenue TTM 383,285 M as of 2023-11-03; former member CELG via `config/cik_overrides.yaml`, FY2018 revenue 15,281 M as of 2019-03-01 | same |
| Unit tests with HTTP(S)_PROXY / ALL_PROXY at a dead port (no network possible) | 253 passed (fundamentals + config) | [audit-no-network.txt](audit-no-network.txt) |
| Main checkout before/after | clean / clean | — |
| Owner's email in tracked files; `.env` tracked | 0 files; not tracked (gitignored) | — |

## 2. Brief → work

| Brief item | Status | Evidence |
|---|---|---|
| EDGAR client: `company_tickers.json` (ticker→CIK), `companyfacts`; User-Agent from config; 10 req/s; raw JSON in `raw/sec/` | Done | `edgar_client.py`; `test_client_throttles_requests` (0.1 s between requests), `test_client_sends_user_agent_and_caches_raw`, `test_build_edgar_client_requires_user_agent`, `test_user_agent_is_not_logged`; live test |
| Long table `fundamentals_facts(cik, symbol, tag, unit, value, period_start, period_end, fp, fy, form, filed, accepted_at→available_at, accn)` | Done (+ `taxonomy`, `duration_days`) | `parser.py`; `test_parse_companyfacts_aapl_fy2023_revenue`, `test_facts_frame_validates_against_schema` |
| Concept mapping with fallbacks, 15 concepts, TotalDebt as sum of parts | Done | `config/xbrl_map.yaml`, `mapping.py`; `test_load_concept_map_repo_file`, `test_aapl_total_debt_fy2023`, `test_bank_revenue_is_net_interest_plus_noninterest_income` |
| Q4 = FY − Q1..Q3; TTM for flows; latest balance for stocks | Done | `pit.py`; `test_q4_from_fy_minus_three_quarters`, `test_aapl_q4_fy2023_revenue_derived`, `test_ttm_*`, `test_latest_balance` |
| Restatements: keep each filing; latest value filed before `as_of` per period | Done | PK includes `accn`; `known_facts`; `test_jpm_q1_2012_restatement`, `test_msft_asc606_recast` |
| Wide PIT view + ratios (gross margin, op margin, ROE, ROA, debt/equity, FCF) | Done | `Fundamentals(lake).pit`, `fundamentals_pit`; `test_acceptance_fundamentals_pit_aapl_fy2023` |
| Acceptance time from submissions; else filed date + 1 session | Done | `filing_index`, `attach_available_at`; UTC verified ([acceptance-utc-check.txt](acceptance-utc-check.txt)); `test_available_at_uses_acceptance_time`, `…_fallback_is_next_session_open` |
| Out of scope (estimates, non-US filers, filing text) | Respected | none built |
| Files (`edgar_client.py, parser.py, mapping.py, pit.py`, `config/xbrl_map.yaml`, AAPL/MSFT/JPM fixtures, tests) | Done (+ `ingest.py`, `config/cik_overrides.yaml`: logged) | `src/atlas/data/fundamentals/`, `tests/fixtures/fundamentals/` (README) |
| Tasks 1–6 | Done | verification.md, "Brief tasks" line |
| **Acceptance:** `fundamentals_pit("AAPL", as_of)` matches fixture figures | **Verified** | `test_acceptance_fundamentals_pit_aapl_fy2023` (15 concepts + 6 ratios = the 10-K), `test_fundamentals_reads_lake`, live test |
| **Acceptance:** restatement tests pass | **Verified** | `test_restated_value_not_seen_before_its_filing`, `test_jpm_q1_2012_restatement`, `test_msft_asc606_recast` |
| **Acceptance:** look-ahead tests pass | **Verified** | `test_future_facts_do_not_change_result`, `test_guard_raises_if_filter_bypassed` (`LookAheadError`), `test_known_facts_cutoff_is_strict` |
| Verify command | **Run** | 141 passed on `main` |

## 3. Plan → work

| Plan part | Status |
|---|---|
| Tasks 1–8, every box | Ticked, each with its tests run (worklog); 73 test names named in the plan exist, except 3 renamed and annotated (A1 of the first audit) |
| D1–D11 | Match the code; D3/D5/D9/D11 changes annotated in the plan (filing_index signature, no `calendar` on `Fundamentals`, up-front request validation) |
| Global constraints | Hold: canonical symbols; acceptance-time PIT rule; units in docstrings; config in YAML; no network in unit tests (no-network run above); unique test basenames; secret never logged. "One commit" → see F5 |
| Review focus 1–5 | Each has its named test, all passing |
| Addendum E1–E4, A1–A3 (owner-approved scope extension) | Done: `config/cik_overrides.yaml` (164 former members since 2018; 162 CIKs; FRC, SBNY null as FDIC filers; reused tickers APC, STI caught), `CikOverride`, `load_cik_overrides`, `CikMap.with_overrides`, `build_cik_map`; `test_cik_overrides_cover_former_members_since_2018` (exact set equality); real-data check of CELG, ATVI, APC, STI, SIVB ([former-members-check.txt](former-members-check.txt)); the bank-revenue bug found there fixed test-first |

## 4. Definition of Done (stage protocol §3) and Project.md rules

| Item | Status |
|---|---|
| Acceptance criteria with named evidence | Yes (§2) |
| Brief's Verify run by the AI | Yes (this audit and every follow-up) |
| `pytest -q` green, nothing skipped beyond live/llm | Yes: 948 passed, 4 deselected (all `live`) |
| ruff / format / mypy clean | Yes |
| Docstrings with units on new public names | Yes: 49 checked, none missing ([ext-docstrings.txt](ext-docstrings.txt)); coverage 100% ([ext-coverage.txt](ext-coverage.txt)) |
| New config keys in YAML with comments and in `AtlasConfig` with defaults | Yes: `data.fundamentals` incl. `cik_overrides_file` (`test_every_config_key_is_written_in_yaml`, `test_every_yaml_key_has_a_comment`, `test_fundamentals_defaults`) |
| Stage folder: plan, complete worklog, raw output in artifacts | Yes after F3/F4 |
| Evidence block in verification.md and PROGRESS.md before DONE | Yes (ALL CRITERIA VERIFIED; later runs appended) |
| One commit `stage-09: …` | 7 commits + this records commit; each logged in the Decisions log after F5 (Rule 6A forces records-only commits after each merge; Project.md outranks the DoD) |
| Merged into `main`, checks re-run on the merged result, hashes in worklog | Yes after F3/F4 (`main` = `9423387`, `make check` green) |
| Rule 0 (read first), Rule 1 (verify before claiming), Rule 2/2A (scope, stage folder) | Held; the one scope extension was asked and approved in chat and logged |
| Rule 3 (collisions logged), Rule 4 (no later-stage work) | Held: Stage 12 (8-K items) and Stage 13 (caching) work left in the Backlog |
| Rule 5 (paper only, no network/LLM in unit tests, no secrets committed) | Held (no-network run; email/`.env` scan) |
| Rule 6/6A (AI runs commands and git) | Held; the sandbox cannot write the main checkout, so fast-forwards ran in the owner's Terminal panel at the owner's request, each verified from the sandbox |

## 5. Findings of this audit (all records; fixed in `stage-09: final audit records`)

| # | Finding | Fix |
|---|---|---|
| F1 | PROGRESS handoff "Built" still said 135 new tests / suite 934 | 149 new + 1 live / suite 948, with the history 934 → 937 → 948 |
| F2 | Handoff "Known gaps" and a Backlog item still said the root `.env` was to be copied (done 2026-10-28, verified: gitignored, mode 600, read by `load_config`) | Gap removed; Backlog item rewritten as done + how new worktrees get the secret |
| F3 | Worklog had no entry for the `23ed7be` commit, its fast-forward and `.env` copy (Terminal tab c1) or the `make check` run on `main` `23ed7be` (exit 0, 937 passed, run in chat) | Entry added, labelled as added by this audit |
| F4 | Worklog had no entry for the fast-forward of `9423387` (tab c3) | Entry added; its merged-main `make check` is this audit's run |
| F5 | No Decisions row for the records-only commits `e98dc1f` and `9423387` | One row covering every Stage 09 commit and why |
| F6 | (checked, no change) PROGRESS handoff "Key interfaces" predates the addendum; the addendum line right below it names `build_cik_map` and says to use it for ingestion | — |
| F7 | Backlog suggested deriving total liabilities from LiabilitiesAndStockholdersEquity − equity, while the worklog called a derivation a guess | Reworded: only as an exact identity when every term (incl. noncontrolling interest and temporary equity) is tagged |

## 6. What is not done, and why (none blocks the stage)

- Former members that left the index **before 2018** have no CIK override (outside the backtest window, spec A7); Backlog says how to extend `config/cik_overrides.yaml` and when to re-run it.
- Stage 12's 8-K `items` column and Stage 13's per-symbol cache belong to those stages (Rule 4).
- TTM EPS method and average-balance ratios are documented design choices, not gaps (Backlog ideas).
