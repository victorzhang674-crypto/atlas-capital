# Stage 29 — Fundamental analyst agent · Verification evidence

Output of commands run in this stage's chat (Project.md Rule 1); raw files in [artifacts/](artifacts/).

#### Verification evidence — Stage 29 — 2026-11-18
- Commands run (this chat):
  - Gate (before any work): `uv run pytest -q` → 5034 passed, 81 deselected, exit 0 · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
  - `uv run pytest tests/unit/agents/research/test_fundamental.py -q` (brief's Verify) → 28 passed, exit 0 · raw: [artifacts/verify-brief.txt](artifacts/verify-brief.txt)
  - `uv run pytest -q` → 5180 passed, 82 deselected (live/llm/integration), exit 0 · raw: [artifacts/pytest-full.txt](artifacts/pytest-full.txt)
  - `make check` → exit 0: pytest (parallel) 5177 passed + timing 3 passed · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
    - `uv run ruff check .` → All checks passed!
    - `uv run ruff format --check .` → 502 files already formatted
    - `uv run mypy src/atlas` → Success: no issues found in 185 source files
  - `make test-integration` (what CI runs besides `make check`) → 48 passed, 15 skipped, exit 0 · raw: [artifacts/test-integration.txt](artifacts/test-integration.txt)
  - `uv run pytest tests/unit/agents/research/test_fundamental_live.py -m llm -s -q` (live DeepSeek, `DEEPSEEK_API_KEY` from `.env`) → 1 passed, status ok, 1 call, 21 ids cited, USD 0.001022 · raw: [artifacts/live-test.txt](artifacts/live-test.txt)
  - Recording (`artifacts/record_fixture.py`): main lake vs fixture slice, AAPL's 28 main-lake items → 27 equal, the receivables line and check differ (main lake has no receivables rows) · raw: [artifacts/fixture-compare.txt](artifacts/fixture-compare.txt); live run → status ok, 1 call, 6,433 + 1,730 tokens, USD 0.00200 · raw: [artifacts/live-run.txt](artifacts/live-run.txt)
  - Coverage of the new modules → `fundamental_tools.py` 97%, `research/fundamental.py` 99%, `data/slice.py` 99% (146 tests) · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
- Acceptance criteria (brief: "The report validates, cites evidence for every quantitative claim, and runs offline from the cache"):
  - [x] The report validates — evidence: `test_the_recorded_aapl_run_replays_offline` parses the recorded live answer into `AnalystReport` and compares it with `expected_report.json`; `check_output` passes (status `ok`, `problems == ()`): ticker, as_of, analyst, the four signals `quality`, `valuation`, `growth`, `balance_sheet`, the six summary sections; FakeLLM: `test_a_cited_report_validates_and_returns_its_evidence`, `test_a_report_problem_gets_a_repair_turn` (7 cases), `test_problems_left_after_the_repair_make_the_report_low_quality`
  - [x] Cites evidence for every quantitative claim — evidence: the replay test asserts `citations.ok` (no unknown id, no uncited signal), every cited id was shown, and `uncited_numbers` is empty for the summary and every risk (each sentence that states a number carries an `[E:<id>]` tag); the tools give every number an id (`test_summary_items_and_ids`, ids taggable); the rule is enforced on every run (`test_uncited_numbers`, the repair case "risks[0]: Debt doubled to 4.2B.")
  - [x] Runs offline from the cache — evidence: the replay test removes `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY`, copies the fixture's lake slice, loads `llm_cache.jsonl` (`atlas llm cache export` of the live run), builds the gateway in `replay_only` (`OfflineProvider`), and asserts `llm_calls == cache_hits == 1`, cost 0 and the recorded report; it needs no lake and no key and runs in CI
  - [x] In scope items (tools, red-flag rules, prompt, signals and confidence, golden FakeLLM tests, the recorded fixture with `atlas data slice`) — evidence: [audit-report.md](artifacts/audit-report.md) table "Brief, item by item"
- Waivers: none

**Result:** ALL CRITERIA VERIFIED

#### Follow-up — Stage 29 — 2026-11-18
Owner: "please help me to fix the problems and complete what you have not done. after the fix, please have a final audit and report your progress." No code under `src/` changed; tests, records and the main lake's data did ([audit-report.md › Follow-up](artifacts/audit-report.md)).

- Commands run (this chat, one after another, nothing else running):
  - `uv run pytest tests/unit/agents/research/test_fundamental.py -q` (brief's Verify) → 29 passed, exit 0 · raw: [artifacts/followup-verify-brief.txt](artifacts/followup-verify-brief.txt)
  - `uv run pytest -q` → 5260 passed, 82 deselected, exit 0 · raw: [artifacts/followup-pytest-full.txt](artifacts/followup-pytest-full.txt). After the summary, DuckDB's nanobind bindings print a leak notice at interpreter exit; it is informational and does not change the exit code.
  - `make check` → exit 0 · raw: [artifacts/followup-make-check.txt](artifacts/followup-make-check.txt)
    - `ruff check .` → All checks passed!
    - `ruff format --check .` → 503 files already formatted
    - `mypy src/atlas` → Success: no issues found in 185 source files
    - pytest: 5257 passed + 3 timing passed
  - `make test-integration` → 48 passed, 15 skipped, exit 0 · raw: [artifacts/followup-test-integration.txt](artifacts/followup-test-integration.txt)
  - Mutation testing (mutmut 3.8.1 plus plain-process re-runs) → 2,323 of 2,396 killed (97.0%; 85.7% before). The 73 survivors are classified: 48 equivalent, 9 unreachable, 16 untested edges · [artifacts/mutation/mutation-summary.md](artifacts/mutation/mutation-summary.md)
  - Main-lake receivables: offline re-parse → 68,846 rows added, every old row unchanged. Stores rebuilt with equal values, swapped in with a backup. Read-back: AAPL 29 of 29 items equal to the fixture · raw: [main-lake-*.txt](artifacts/)
- Acceptance criteria: unchanged and still met. The replay test and the FakeLLM tests pass in the runs above (`test_the_recorded_aapl_run_replays_offline` is in the brief's Verify, 29 passed).
- Waivers: none

**Result:** ALL CRITERIA VERIFIED (re-run after the follow-up)

#### Follow-up 2 — Stage 29 — 2026-11-18
Owner: "please complete the ones that are not done and have a final audit on your own work". MD&A text was added on the owner's approval of the spec change. Graph wiring was kept with Stage 35 at the owner's choice. Records: [audit-report.md › Follow-up 2](artifacts/audit-report.md), [worklog](worklog.md).

- Commands run (this chat, one after another, on the final code):
  - `uv run pytest tests/unit/agents/research/test_fundamental.py -q` (brief's Verify) → 29 passed, exit 0 · raw: [artifacts/followup2-verify-brief.txt](artifacts/followup2-verify-brief.txt)
  - `uv run pytest -q` → 5374 passed, 82 deselected, exit 0 · raw: [artifacts/followup2-pytest-full.txt](artifacts/followup2-pytest-full.txt)
  - `make check` → exit 0 · raw: [artifacts/followup2-make-check.txt](artifacts/followup2-make-check.txt)
    - `ruff check .` → All checks passed!
    - `ruff format --check .` → 510 files already formatted
    - `mypy src/atlas` → Success: no issues found in 188 source files
    - pytest: 5371 passed + 3 timing passed
  - `make test-integration` → 48 passed, 15 skipped, exit 0 · raw: [artifacts/followup2-test-integration.txt](artifacts/followup2-test-integration.txt)
  - Main lake:
    - `atlas data sync --datasets filings` (scratch lake) → 748 keys, 735 ok, 13 empty, 0 failed, 7,643 rows · [artifacts/filing-text-backfill.txt](artifacts/filing-text-backfill.txt)
    - refetch after E7 → 7,558 of 7,643 with an MD&A · [artifacts/filing-text-refetch.txt](artifacts/filing-text-refetch.txt)
    - swap (owner's Terminal panel) → files equal, swap exit 0 · [artifacts/main-lake-filing-text-swap.txt](artifacts/main-lake-filing-text-swap.txt)
    - read-back → only `filing_text` added; point in time checked · [artifacts/main-lake-filing-text-readback.txt](artifacts/main-lake-filing-text-readback.txt)
    - rule review → 16 FLAGs of 7,558 MD&As, each reviewed · [artifacts/filing-text-review.txt](artifacts/filing-text-review.txt)
  - Mutation testing:
    - Stage 29 modules: 2,335 of 2,396 killed (97.5%);
    - new code: 778 of 811 killed (95.9%);
    - every survivor classified · [artifacts/mutation/mutation-summary.md](artifacts/mutation/mutation-summary.md)
- Acceptance criteria: unchanged and still met.
  - The recorded-run replay is in the brief's Verify (29 passed). Its lake slice has no `filing_text`, so the evidence and the cached request are byte-identical.
  - The brief's In-scope item "going-concern keywords if MD&A is available" now has its MD&A: `test_fundamental_going_concern.py`, `test_fundamental_red_flags.py`, and the main-lake read-back.
- Waivers: none

**Result:** ALL CRITERIA VERIFIED (re-run after follow-up 2)

#### Follow-up 3 — Stage 29 — 2026-11-22
Owner: "can you help me to complete the tasks that are still being opened?" The follow-up covered 10-Q MD&As, the seven companies without an MD&A, the last four edge mutants and the CI record of `1e32337`. Graph wiring stays with Stage 35. Records: [audit-report.md › Follow-up 3](artifacts/audit-report.md), [worklog](worklog.md), [plan.md › Follow-up 3](plan.md).

- Commands run (this chat, one after another, on the final code):
  - `uv run pytest tests/unit/agents/research/test_fundamental.py -q` (brief's Verify) → 29 passed, exit 0 · raw: [artifacts/followup3-verify-brief.txt](artifacts/followup3-verify-brief.txt)
  - `uv run pytest -q` → 5418 passed, 82 deselected, exit 0 · raw: [artifacts/followup3-pytest-full.txt](artifacts/followup3-pytest-full.txt)
  - `make check` → exit 0 · raw: [artifacts/followup3-make-check.txt](artifacts/followup3-make-check.txt)
    - `ruff check .` → All checks passed!
    - `ruff format --check .` → 510 files already formatted
    - `mypy src/atlas` → Success: no issues found in 188 source files
    - pytest: 5415 passed + 3 timing passed
  - `make test-integration` → 48 passed, 15 skipped, exit 0 · raw: [artifacts/followup3-test-integration.txt](artifacts/followup3-test-integration.txt)
  - Main lake:
    - Round 1 sync (scratch lake) → 748 keys, 742 ok, 6 empty, 0 failed, 30,597 rows · [artifacts/filing-text-backfill-followup3.txt](artifacts/filing-text-backfill-followup3.txt)
    - Re-fetch after H7 → 748 keys, 0 failed, 7,152 rows · [artifacts/filing-text-refetch-followup3.txt](artifacts/filing-text-refetch-followup3.txt)
    - Control sample → 200 of 200 identical · [artifacts/filing-text-control-sample-followup3.txt](artifacts/filing-text-control-sample-followup3.txt)
    - H9 recheck → 399 of 400 identical; the one difference (Roper) was fetched again · [artifacts/filing-text-recheck-followup3.txt](artifacts/filing-text-recheck-followup3.txt)
    - Compared with round 1 · [artifacts/filing-text-compare-round1-followup3.txt](artifacts/filing-text-compare-round1-followup3.txt)
    - Swap (owner's Terminal panel, backup `-2026-11-22b`) → old table backed up equal, new table equal (742 files), replace exit 0 · [artifacts/main-lake-filing-text-replace-2.txt](artifacts/main-lake-filing-text-replace-2.txt)
    - Read-back → `_meta.json` equal, only `steps.filing_text` changed, 30,597 rows (30,506 with an MD&A), 21 `DataView` cases as expected · [artifacts/main-lake-filing-text-readback-followup3.txt](artifacts/main-lake-filing-text-readback-followup3.txt)
    - Rule review → 75 FLAGs of 30,506 MD&As, each read (59 own, 8 subsidiary, 8 another company's) · [artifacts/filing-text-review-followup3.txt](artifacts/filing-text-review-followup3.txt)
  - Mutation testing:
    - Stage 29 modules: 2,339 of 2,396 killed (97.6%), no untested edge left;
    - H1–H2 code: 853 of 889 (96.0%);
    - H6–H7 code: 237 of 240 (98.75%);
    - every survivor classified · [artifacts/mutation/mutation-summary.md](artifacts/mutation/mutation-summary.md)
  - H9 (the "Managment" heading): `test_a_heading_that_misspells_management_is_still_read` was seen to fail (2 failed) before the fix, then pass. The change is a module-level pattern, which mutmut does not mutate.
- Acceptance criteria: unchanged and still met.
  - The recorded-run replay is in the brief's Verify (29 passed). Its lake slice has no `filing_text`, so the evidence and the cached request are byte-identical.
  - The going-concern check now also reads 10-Q MD&As: `test_fundamental_going_concern.py`, `test_filings_view.py` and the main-lake read-back (BBBY FLAG on 2023-01-27, CLEAR on 2023-01-25).
- Waivers: none

- Merge into `main`: fast-forward 1e32337 → 4ebcd40 (owner's Terminal panel), trees equal; merged result `uv run pytest -q` → 5418 passed, 82 deselected, exit 0 · `make check` → exit 0 · raw: [artifacts/followup3-merged-main-check.txt](artifacts/followup3-merged-main-check.txt)

**Result:** ALL CRITERIA VERIFIED (re-run after follow-up 3)

#### Follow-up 4 — Stage 29 — 2026-11-23
Owner: "can you complete the still open ones and complete them?" The follow-up covered:
- Bank of New York Mellon's and Northern Trust's 10-Q MD&As;
- GE's 10-Q contents entries;
- Windstream's quote;
- ExxonMobil's CIK, which became 16 issuers with former CIKs plus Twenty-First Century Fox (Stages 09 and 12 code);
- the whole-table read (Stage 06 code);
- the CI record of `c579104`.

Graph wiring stays with Stage 35 (owner).

- Commands run (this chat, one after another, on the final code):
  - `uv run pytest tests/unit/agents/research/test_fundamental.py -q` (brief's Verify) → 29 passed, exit 0 · raw: [artifacts/followup4-verify-brief.txt](artifacts/followup4-verify-brief.txt)
  - `uv run pytest -q` → 5472 passed, 82 deselected, exit 0 · raw: [artifacts/followup4-pytest-full.txt](artifacts/followup4-pytest-full.txt)
  - `make check` → exit 0 · raw: [artifacts/followup4-make-check.txt](artifacts/followup4-make-check.txt)
    - `ruff check .` → All checks passed!
    - `ruff format --check .` → 510 files already formatted
    - `mypy src/atlas` → Success: no issues found in 188 source files
    - pytest: 5469 passed + 3 timing passed
  - `make test-integration` → 48 passed, 15 skipped, exit 0 · raw: [artifacts/followup4-test-integration.txt](artifacts/followup4-test-integration.txt)
  - Main lake:
    - Whole-table `filing_text` read (J1) → 30,597 rows, 2.14 GiB of text, 29 s, sorted by key · [artifacts/main-lake-whole-read-followup4.txt](artifacts/main-lake-whole-read-followup4.txt)
    - Former CIKs checked on EDGAR → 16 pairs and TFCF · [artifacts/former-ciks-followup4.txt](artifacts/former-ciks-followup4.txt)
    - Recheck of every row the rules could change plus 600 random rows → 2,513 re-read, 840 changed (3 controls, each an improvement), every kind reviewed · [artifacts/filing-text-recheck-followup4.txt](artifacts/filing-text-recheck-followup4.txt)
    - Scratch copy `cik`, `text`, `missing`, `check` → only the intended rows changed · [artifacts/main-lake-followup4.txt](artifacts/main-lake-followup4.txt), [artifacts/filing-text-compare-followup4.txt](artifacts/filing-text-compare-followup4.txt)
    - Rebuild → factors 142 month-ends, exit 0; risk model 2,471 sessions, 115 snapshots, exit 0 · [artifacts/main-lake-rebuild-followup4.txt](artifacts/main-lake-rebuild-followup4.txt)
    - Store comparison → raw exposures changed only for the 18 symbols · [artifacts/main-lake-stores-compare-followup4.txt](artifacts/main-lake-stores-compare-followup4.txt)
    - Swap (owner's Terminal panel) → 7 tables backed up and replaced, each equal to the scratch copy; swap exit 0 · [artifacts/main-lake-swap-followup4.txt](artifacts/main-lake-swap-followup4.txt)
    - Read-back → row counts equal, `_meta.json` tables equal, 11 `DataView` cases as expected, 30,927 of 30,927 filings with an MD&A · [artifacts/main-lake-readback-followup4.txt](artifacts/main-lake-readback-followup4.txt)
    - Rule review → 76 FLAGs of 30,927 MD&As: the 75 reviewed plus Enphase's (own doubt) · [artifacts/filing-text-review-followup4.txt](artifacts/filing-text-review-followup4.txt)
  - Mutation testing of the changed functions → 768 of 883 killed; new-line survivors equivalent (32), 83 on old lines (Backlog) · [artifacts/mutation/mutation-summary.md](artifacts/mutation/mutation-summary.md)
  - Each rule change was seen to fail before its fix, then pass (the new tests in `test_filings_mda.py`, `test_filings_ingest.py`, `test_storage_lake.py`, `test_fundamentals_{client,ingest}.py`, `test_events_earnings.py`, `test_fundamental_red_flags.py`).
- Acceptance criteria: unchanged and still met.
  - The recorded-run replay is in the brief's Verify (29 passed). Its fixture lake has no `filing_text`, and its symbols' CIKs did not change.
- Waivers: none

- Merge into `main`: fast-forward c579104 → 8611b27 (owner's Terminal panel), trees equal; merged result `uv run pytest -q` → 5472 passed, 82 deselected, exit 0 · `make check` → exit 0 · raw: [artifacts/followup4-merged-main-check.txt](artifacts/followup4-merged-main-check.txt)

**Result:** ALL CRITERIA VERIFIED (re-run after follow-up 4)

#### Follow-up 5 — Stage 29 — 2026-11-23
Task from the session that started this one, on the owner's behalf: "Close every gap that Stage 29 (fundamental analyst) still has open". The gaps:
- Emerson's FY2015 pointer page;
- MD&As running past their end (SolarEdge 2024);
- the 83 old-line surviving mutants;
- OKTA's and VYLR's fundamentals;
- the uncommitted CI record of `3d77679` (now [artifacts/ci-runs-3d77679.json](artifacts/ci-runs-3d77679.json): both runs success).

Graph wiring stays with Stage 35 (owner).

- Gate: `uv run pytest -q` → 5472 passed, 82 deselected, exit 0 · raw: [artifacts/followup5-gate-pytest.txt](artifacts/followup5-gate-pytest.txt)
- Commands run (this chat, one after another, on the final code):
  - `uv run pytest tests/unit/agents/research/test_fundamental.py -q` (brief's Verify) → 29 passed, exit 0 · raw: [artifacts/followup5-verify-brief.txt](artifacts/followup5-verify-brief.txt)
  - `uv run pytest -q` → 5563 passed, 82 deselected, exit 0 · raw: [artifacts/followup5-pytest-full.txt](artifacts/followup5-pytest-full.txt)
  - `make check` → exit 0 · raw: [artifacts/followup5-make-check.txt](artifacts/followup5-make-check.txt)
    - `ruff check .` → All checks passed!
    - `ruff format --check .` → 510 files already formatted
    - `mypy src/atlas` → Success: no issues found in 188 source files
    - pytest: 5560 passed + 3 timing passed
  - `make test-integration` → 48 passed, 15 skipped, exit 0 · raw: [artifacts/followup5-test-integration.txt](artifacts/followup5-test-integration.txt)
- Main lake:
  - Full re-extraction of all 30,927 stored filings (HEAD and final code, from the fetched documents' text) → 0 errors · [artifacts/filing-text-full-recheck-followup5.txt](artifacts/filing-text-full-recheck-followup5.txt)
    - 131 stale rows (stored by earlier code);
    - 533 rows changed by the final code, every kind reviewed;
    - over-run markers from 181 rows to 14 (all cross-references);
    - all 30,927 with an MD&A.
  - Scratch copy: `copy`, `before`, OKTA/VYLR syncs, `new`, `text`, `check` → only intended rows changed. Outside `filing_text`, only OKTA and VYLR. In `filing_text`, exactly the 533 · [artifacts/main-lake-followup5.txt](artifacts/main-lake-followup5.txt)
  - Rebuild → factors 142 month-ends, exit 0; risk model, exit 0 · [artifacts/main-lake-rebuild-followup5.txt](artifacts/main-lake-rebuild-followup5.txt)
  - Store comparison → raw exposures changed only for OKTA (12 values); all changes on 2026-10-30 · [artifacts/main-lake-stores-compare-followup5.txt](artifacts/main-lake-stores-compare-followup5.txt)
  - Swap (owner's Terminal panel) → 9 tables backed up in `cache/backup-stage29-followup5-2026-11-23` and replaced, each equal to the scratch copy; swap exit 0 · [artifacts/main-lake-swap-followup5.txt](artifacts/main-lake-swap-followup5.txt)
  - Read-back → row counts equal, `_meta.json` tables equal, 9 `DataView` cases as expected, 30,927 of 30,927 with an MD&A · [artifacts/main-lake-readback-followup5.txt](artifacts/main-lake-readback-followup5.txt)
  - Going-concern review → 76 FLAGs of 30,927, the same 76 as follow-up 4 (0 new, 0 gone) · [artifacts/filing-text-review-followup5.txt](artifacts/filing-text-review-followup5.txt)
- Mutation testing → [artifacts/mutation/mutation-summary.md](artifacts/mutation/mutation-summary.md)
  - old lines: 70 of 83 killed by new tests, 13 equivalent;
  - changed functions: 425 of 440 detected, 15 equivalent.
- Each gap, against evidence:
  - [x] Emerson FY2015 → EX-13, 56,539 characters from "Results of Operations" — `test_an_exhibit_whose_mda_sections_carry_no_mda_heading_is_read_by_their_titles`, the read-back's EMR case. Comerica's and Sempra's hidden regressions are fixed too (`test_a_10k_whose_item_7_names_sections_of_its_own_financial_section_reads_them`, `test_an_item_naming_annual_report_sections_reads_the_exhibit`).
  - [x] Run-ons:
    - found by a full re-extraction and a marker scan, with safe end rules (later items, Part II items, signatures, exhibit index, split "ITEM" lines), each with tests in `test_filings_mda.py`;
    - SolarEdge 2024: 242,676 → 75,160;
    - markers 181 → 14 (cross-references only).
  - [x] 83 old-line mutants: 70 killed by real tests, 13 equivalent in writing ([followup5-old-lines-classified.tsv](artifacts/mutation/followup5-old-lines-classified.tsv)). No code weakened. Decision logged under Stage 29.
  - [x] OKTA and VYLR synced on the main lake: prices, fundamentals, events, reactions, stores. Read-back: OKTA 21 values, revenue TTM $3.07B; VYLR 8 values.
  - [x] CI record `ci-runs-3d77679.json` committed with this work.
  - [x] After the MD&A change, the going-concern FLAG count is still 76 (same set).
- Acceptance criteria: unchanged and still met. The recorded-run replay is in the brief's Verify (29 passed); its fixture lake has no `filing_text`.
- Waivers: none

**Result:** ALL CRITERIA VERIFIED (re-run after follow-up 5)

- Merge into `main`: fast-forward 3d77679 → 4b2d460 (owner's Terminal panel), trees equal; merged result `uv run pytest -q` → 5563 passed, 82 deselected, exit 0 · `make check` → exit 0 · raw: [artifacts/followup5-merged-main-check.txt](artifacts/followup5-merged-main-check.txt)

#### Follow-up 6 — Stage 29 — 2026-11-25
Owner: "Sync prices to today". A routine data sync of the main lake; no code changed.
- Commands run (this chat):
  - `atlas data sync --datasets prices` (scratch copy) → 627 ok, 88 empty, 0 failed, exit 0. Then the same for DHI, DHR and DIS after their checkpoints were put back → 3 ok, exit 0 · [artifacts/main-lake-followup6.txt](artifacts/main-lake-followup6.txt)
  - `main_lake_followup6.py check` → prices +10,675 bars (2026-11-02 … 24), none removed, changed bars only in the 5-session overlap; corporate actions +155; instruments only `last_trade_date`; 503 of 503 current members with bars to 2026-11-24 · same file
  - Swap (owner's Terminal panel) → 3 tables backed up and replaced, each equal to the scratch copy, exit 0 · [artifacts/main-lake-swap-followup6.txt](artifacts/main-lake-swap-followup6.txt)
  - Read-back → counts and `_meta.json` equal; `DataView` bars to 2026-11-24 · [artifacts/main-lake-readback-followup6.txt](artifacts/main-lake-readback-followup6.txt)
  - Merged `main`: `uv run pytest -q` and `make check` → [artifacts/followup6-merged-main-check.txt](artifacts/followup6-merged-main-check.txt)
- Criterion: [x] the main lake's prices reach the last published session (2026-11-24) for every current member — evidence: `check` and read-back above.
- Waivers: none

#### Follow-up 7 — Stage 29 — 2026-11-25
Owner: "run the full data sync for the rest". A full `atlas data sync` of the main lake, plus 2 Stage 09 EDGAR client fixes it exposed (rewritten submissions history pages). Records: [worklog.md › Follow-up 7](worklog.md), [plan.md › Follow-up 7](plan.md), hand-offs [1](handoffs/handoff-1.md) and [2](handoffs/handoff-2.md).
- Commands run (this chat):
  - Gate: `uv run pytest -q` → 5563 passed, 82 deselected, exit 0 · [artifacts/followup7-gate-pytest.txt](artifacts/followup7-gate-pytest.txt)
  - After the client fixes: `uv run pytest -q` → 5571 passed, 82 deselected, exit 0 · [artifacts/followup7-gate2-pytest.txt](artifacts/followup7-gate2-pytest.txt)
  - `make test-integration` → 48 passed, 15 skipped, exit 0 · [artifacts/followup7-test-integration.txt](artifacts/followup7-test-integration.txt)
  - `make check` (lint, format, mypy, tests) → ruff all checks passed, 510 files formatted, mypy no issues in 188 files, 5568 + 3 timing tests passed, exit 0 · [artifacts/followup7-make-check.txt](artifacts/followup7-make-check.txt)
  - `atlas data sync` run 3 on a fresh scratch copy (runs 1 and 2 discarded) → 12 steps, 0 failed, exit 0 · [artifacts/main-lake-followup7.txt](artifacts/main-lake-followup7.txt)
  - `main_lake_followup7.py members` / `check` (run 3) → no rows removed; fundamentals +17,459, changed 123 (`accepted_at`/`available_at` NaT → value, JPM and MS only); filing_text +27; events +872; reactions +376; news and news_sentiment +8,120; macro observations +4,804; data quality +169 · [artifacts/main-lake-check-followup7.txt](artifacts/main-lake-check-followup7.txt)
  - Going-concern review → 76 FLAGs, the same 76; 30,954 MD&As · [artifacts/filing-text-review-followup7.txt](artifacts/filing-text-review-followup7.txt)
  - `atlas factors build --from 2026-07-01 --to 2026-10-31 --refresh` and `atlas risk-model build --from 2017 --to 2026 --cached-factors` → exit 0; store comparison shows only the intended changes · [artifacts/main-lake-stores-compare-followup7.txt](artifacts/main-lake-stores-compare-followup7.txt)
  - Swap (owner's Terminal panel) → 11 tables backed up and replaced, each equal to the scratch copy; raw 38 added, 2,427 replaced; exit 0 · [artifacts/main-lake-swap-followup7.txt](artifacts/main-lake-swap-followup7.txt)
  - Read-back → all 18 tables' counts and `_meta.json` equal to the scratch copy · [artifacts/main-lake-readback-followup7.txt](artifacts/main-lake-readback-followup7.txt)
- Criteria:
  - [x] Every dataset is synced to today, or the gap is recorded with its reason — evidence: run 3 step summaries (0 failed); `members`: 502 of 503 members with bars to 2026-11-24 (the last published session); SNDK stops at 2026-11-19 (Yahoo's bars after its 8:1 split of 2026-11-20 are missing; checkpoint kept at 2026-11-19 so the next sync retries; Backlog → 07); 24 members whose SEC companyfacts lag their filings (upstream; `docs/data-limitations.md` fundamentals item 1; Backlog → 09).
  - [x] No stored row is lost or silently changed — evidence: `check` run 3 (removed 0 in every table; the 123 changed facts only gain acceptance times).
  - [x] The main lake equals the checked scratch copy — evidence: swap and read-back above.
  - [x] The going-concern FLAG set is unchanged (76) — evidence: filing-text review.
  - [x] The EDGAR client fixes are test-first, each new test red on the old code — evidence: 8 new tests in `tests/unit/data/fundamentals/test_fundamentals_client.py`; gate 2 above.
- Waivers: none

**Result:** ALL CRITERIA VERIFIED (follow-up 7)

- Merge into `main`: fast-forward f800722 → bb43abf (owner's Terminal panel), trees equal; merged result `uv run pytest -q` → 5571 passed, 82 deselected, exit 0 · `make check` → exit 0 · raw: [artifacts/followup7-merged-main-check.txt](artifacts/followup7-merged-main-check.txt)
- Pushed `main` and the branch at bb43abf (level with `origin`); CI success on both runs (45693828359 main, 45693828271 branch) · [artifacts/ci-runs-bb43abf.json](artifacts/ci-runs-bb43abf.json)
