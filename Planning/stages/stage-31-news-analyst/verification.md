# Stage 31 — News & event analyst agent · Verification evidence

Output of commands run in this stage's chat (Project.md Rule 1); raw files in [artifacts/](artifacts/). The final run is on the finished tree (the uncommitted working tree on `068775f`, committed unchanged as the stage commit); two earlier full runs are in the [worklog](worklog.md) (the first failed: `uv` could not use its sandbox-blocked cache, a script slip; the second failed collection on a test-module name clash with Stage 11, fixed by a rename).

#### Verification evidence — Stage 31 — 2026-11-28
- Commands run (this chat):
  - Gate (before any work, on a clean extract of `068775f`): `uv run pytest -q` → 5754 passed, 83 deselected, exit 0 · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
  - `uv run pytest tests/unit/agents/research/test_news.py -q` (brief's Verify) → 60 passed, exit 0 · raw: [artifacts/verify-brief.txt](artifacts/verify-brief.txt)
  - `uv run pytest -q` → 5928 passed, 84 deselected (live/llm/integration), exit 0 · raw: [artifacts/pytest-full.txt](artifacts/pytest-full.txt)
  - `make check` → exit 0 · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
    - `uv run ruff check .` → All checks passed!
    - `uv run ruff format --check .` → 544 files already formatted
    - `uv run mypy src/atlas` → Success: no issues found in 194 source files
    - pytest (parallel) 5925 passed + timing 3 passed
  - `make test-integration` (what CI runs besides `make check`) → 48 passed, 15 skipped, exit 0 · raw: [artifacts/test-integration.txt](artifacts/test-integration.txt)
  - `uv run pytest tests/unit/agents/research/test_news_analyst_live.py -m llm -s -q` (live DeepSeek, `DEEPSEEK_API_KEY` from the main checkout's `.env`) → 1 passed, status ok, 2 calls (one repair turn), 24 ids cited, USD 0.003059 · raw: [artifacts/live-test.txt](artifacts/live-test.txt)
  - Recording (`artifacts/record_fixture.py`): main lake vs fixture slice, AAPL's 47 items → 47 of 47 equal · raw: [artifacts/fixture-compare.txt](artifacts/fixture-compare.txt); live run → status ok, 1 call, 6,684 + 1,763 tokens, USD 0.0021 · [artifacts/live-run.txt](artifacts/live-run.txt)
  - Main lake read-only check (AAPL on 5 days incl. 2020-03-16 and 2015-01-05, KVUE young listing, SMCI, SIVB former member, GEHC before listing, MSFT holiday week) → 10 cases, 16–47 items in 0.6–2.7 s, no item timed at or after its cutoff, exit 0 · raw: [artifacts/main-lake-check.txt](artifacts/main-lake-check.txt)
  - Coverage → `agents/tools/news_tools.py` 100%, `agents/research/news.py` 100% (553 tests of `agents/research`, the two framework tests and the types test) · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
  - Mutation testing (mutmut 3.8.1 on `news_tools.py` and `research/news.py`) → 707 of 717 killed (98.6%); the 10 left are equivalent · [artifacts/mutation/mutation-summary.md](artifacts/mutation/mutation-summary.md)
- Acceptance criteria (brief: "Every catalyst has an evidence ID; the empty-news path is tested; offline replay works. Recorded as Stage 29's fixtures are (exported cache + lake slice under `tests/fixtures/recorded/`)"):
  - [x] Every catalyst has an evidence ID — evidence: `NewsCatalyst.evidence_id` is required and non-empty (`test_types_news.py`); it is a framework citation (`test_agents_single_evidence_id.py`: an unknown id is unknown, an empty one uncited); `test_every_catalyst_has_an_evidence_id_the_run_showed`, `test_an_unknown_catalyst_id_gets_the_citation_repair`, `test_a_schema_violation_is_repaired_by_the_gateway` (a catalyst without it), and the replay test (every recorded catalyst's id was shown and cited)
  - [x] The empty-news path is tested — evidence: `test_with_no_news_*` (a good report passes and keeps its event catalyst; the three guard problems are exact; only `news_flow` must be FLAT), `test_the_cap_holds_even_when_the_repair_fails` (confidence 0.2, below the 0.3 low-quality cap), `test_a_missing_news_table_is_the_empty_path_with_a_citable_gap`, `test_no_article_is_a_citable_zero`, `test_says_no_news*`
  - [x] Offline replay works — evidence: `test_the_recorded_aapl_run_replays_offline` (no key, `OfflineProvider`, `replay_only`, no lake but the slice: the recorded report equals `expected_report.json`, one call = one cache hit, cost 0, citations ok, no uncited number)
  - [x] Recorded as Stage 29's fixtures are — evidence: `tests/fixtures/recorded/stage-31-news-aapl-2024-05-01/` (`lake/` from `slice_lake`, `llm_cache.jsonl` from `atlas llm cache export`, `expected_report.json`, `run.json`, `README.md`); slice equal to the main lake on 47 of 47 items
  - [x] In-scope items (the four tools, the prompt, the output with catalysts, the guard) — evidence: [artifacts/audit-report.md](artifacts/audit-report.md), brief line by line
- Waivers: none

**Result:** ALL CRITERIA VERIFIED
