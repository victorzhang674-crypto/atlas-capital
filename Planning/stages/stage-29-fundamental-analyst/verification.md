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
