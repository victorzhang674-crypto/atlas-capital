# Stage 30 — Technical analyst agent · Verification evidence

Output of commands run in this stage's chat (Project.md Rule 1); raw files in [artifacts/](artifacts/). The final run is on commit `1137434` (after the audit's fixes); the first run (on `7c25857`, `make check` failing on mypy) is recorded in [worklog.md](worklog.md) and [artifacts/audit-report.md](artifacts/audit-report.md) (A1).

#### Verification evidence — Stage 30 — 2026-11-26
- Commands run (this chat):
  - Gate (before any work, on a clean extract of `a2e90f3`): `uv run pytest -q` → 5571 passed, 82 deselected, exit 0 · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
  - `uv run pytest tests/unit/agents/research/test_technical.py -q` (brief's Verify) → 32 passed, exit 0 · raw: [artifacts/verify-brief.txt](artifacts/verify-brief.txt)
  - `uv run pytest -q` → 5708 passed, 83 deselected (live/llm/integration), exit 0 · raw: [artifacts/pytest-full.txt](artifacts/pytest-full.txt)
  - `make check` → exit 0 · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
    - `uv run ruff check .` → All checks passed!
    - `uv run ruff format --check .` → 524 files already formatted
    - `uv run mypy src/atlas` → Success: no issues found in 191 source files
    - pytest (parallel) 5705 passed + timing 3 passed
  - `make test-integration` (what CI runs besides `make check`) → 48 passed, 15 skipped, exit 0 · raw: [artifacts/test-integration.txt](artifacts/test-integration.txt)
  - `uv run pytest tests/unit/agents/research/test_technical_live.py -m llm -s -q` (live DeepSeek, `DEEPSEEK_API_KEY` from the main checkout's `.env`, on the final code) → 1 passed, status ok, 2 calls (one repair turn), 10 ids cited, USD 0.002085 · raw: [artifacts/live-test.txt](artifacts/live-test.txt)
  - Recording (`artifacts/record_fixture.py`): main lake vs fixture slice, AAPL's 11 items → 11 of 11 equal (same regime) · raw: [artifacts/fixture-compare.txt](artifacts/fixture-compare.txt); live run → status ok, 1 call, 4,352 + 1,662 tokens, USD 0.0017 · raw: [artifacts/live-run.txt](artifacts/live-run.txt)
  - Main lake read-only check (NVDA split, GEV/KVUE young, JPM 2020-03-17, ATVI delisted, SNDK stale, AAPL today) → every case 11 items, exit 0 · raw: [artifacts/main-lake-check.txt](artifacts/main-lake-check.txt)
  - Coverage → `agents/tools/technical_tools.py` 99% (the one missed line is a defensive branch shown unreachable), `agents/research/technical.py` 100%, `agents/research/checks.py` 100%, `agents/research/fundamental.py` 100% (384 tests) · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
  - Mutation testing (mutmut 3.8.1 on `7d9f6da`, then the survivors against the audit's tests) → 997 of 1,048 killed (95.1%); 51 classified: 47 equivalent, 3 unreachable, 1 deleted code · [artifacts/mutation/mutation-summary.md](artifacts/mutation/mutation-summary.md)
- Acceptance criteria (brief: "Schema-valid output with evidence; FakeLLM tests; a recorded live fixture. Recorded as Stage 29's fixtures are (exported cache + lake slice under `tests/fixtures/recorded/`)"):
  - [x] Schema-valid output — evidence: `test_the_recorded_aapl_run_replays_offline` parses the recorded live answer into `TechnicalReport` (an `AnalystReport` whose signals carry `timing` and `suggested_stop_pct`) and compares it with `expected_report.json`; `check_output` passes (status `ok`, `problems == ()`): ticker, as_of, analyst, the four signals, the six sections, the stop and timing rules (`stop_problems` empty for every signal); contract tests `tests/unit/core/types/test_types_technical.py` (11); `test_a_schema_violation_is_repaired_by_the_gateway`
  - [x] With evidence — evidence: the replay test asserts `citations.ok`, every cited id was shown, and no sentence of the summary or a risk states a number without an `[E:<id>]` tag; every number the tools give has an id (`pxs:`, `tsnap:`, `regime:`, `gap:`); FakeLLM `test_a_report_problem_gets_a_repair_turn` (12 cases), `test_an_unknown_evidence_id_gets_the_citation_repair`
  - [x] FakeLLM tests — evidence: `test_technical.py` (32, incl. the golden prompt `golden/technical_analyst_prompt.txt`, the tools offered and versioned, the regime input, repairs, `low_quality`, failure modes, a peer tool call, memory), `test_technical_edges.py` (18), `test_technical_tools.py` (27), `test_technical_tools_pit.py` (6)
  - [x] A recorded live fixture (exported cache + lake slice under `tests/fixtures/recorded/`) — evidence: `tests/fixtures/recorded/stage-30-technical-aapl-2024-05-01/` (`lake/` from `atlas data slice`, `llm_cache.jsonl` from `atlas llm cache export`, `regime.json`, `expected_report.json`, `run.json`, `README.md`); the replay test removes `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY`, builds the gateway in `replay_only` (`OfflineProvider`) and asserts `llm_calls == cache_hits == 1`, cost 0 and the recorded report
  - [x] In scope items (the two tools, the prompt, the output fields) — evidence: [artifacts/audit-report.md](artifacts/audit-report.md) section 1, brief line by line
- Waivers: none

**Result:** ALL CRITERIA VERIFIED
