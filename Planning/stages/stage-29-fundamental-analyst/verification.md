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
