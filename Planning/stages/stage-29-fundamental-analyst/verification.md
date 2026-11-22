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
