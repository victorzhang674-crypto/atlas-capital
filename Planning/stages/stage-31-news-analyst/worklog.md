# Stage 31 — News & event analyst agent · Work log

Dated, in order. Branch `claude/stage-31-planning-audit-b487ce` (worktree `.claude/worktrees/stage-31-planning-audit-b487ce`), from `068775f` (= `main`).

## 2026-11-28

### Start
- Kick-off (owner): "please plan, execute and audit stage 31."
- Read Project.md, PROGRESS.md (Stage 30 DONE, ALL CRITERIA VERIFIED), the brief, Stage 30's plan, tools, analyst, checks, tests and fixture recorder, the agent framework (`BaseAgent`, `evidence.py`), the Stage 11/12 readers (`News`, `Events`, reactions) and `DataView`.
- Fresh worktree: `uv sync --group dev` (cache `$TMPDIR/uv-cache-s31`), exit 0.
- Gate: Stage 30 DONE; `uv run pytest -q` on a clean `git archive` extract of `068775f` → 5754 passed, 83 deselected, exit 0 ([artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)).
- Copied the template; wrote [plan.md](plan.md) (decisions D1–D9, tasks 1–11). PROGRESS.md: Stage 31 `IN PROGRESS`.

### Task 1 — output contract (D1)
- Did: `Impact`, `NewsCatalyst`, `NewsReport` in `atlas.core.types.research`, exported from `atlas.core.types`; `tests/unit/core/types/test_types_news.py` (23 tests); the round-trip suite's one-sample-per-model rule needed samples for both models.
- Ran: against the clean extract's `src` → collection error (red); in the worktree `tests/unit/core/types` → 324 passed. The tests were written first; the implementation went in with the same command that wrote them, so the red run was made against the old tree.

### Task 2 — `evidence_id` citations and `finalize` (D2, D6)
- Did: red first (9 of 10 new tests failed), then `check_citations` reads a string `evidence_id` as a citation (empty = uncited claim; `can_cite` knows the field) and `BaseAgent.finalize(ctx, output)` (identity by default) runs after the low-quality cap and inside the `try`, so a failing `finalize` is traced `failed`.
- Ran: `tests/unit/agents` → 722 passed (one of my own expectations was wrong: an empty `evidence_id` also reports `<root>`, as an empty `evidence_ids` does; test corrected, not the code).
- Deviation from the brief's file list: `agents/evidence.py` and `agents/base.py` (Stage 26) change.

### Task 3 — `agents.news` config (D8)
- Did: `NewsAnalystConfig` (9 keys, bounds, `sector_articles_per_peer ≤ sector_articles`), `config/fund.yaml`, tests incl. "every key of the block is commented". Red (import error) then green; `tests/unit/core` and `test_repo_docs.py` green (1067 passed).

### Task 4 — test world
- Did: `tests/unit/agents/research/news_world.py` (clean and dirty lakes, see its docstring) and `test_news_world.py` (6). Dead end: constant prices made every symbol `stale_price`-blocked (the data-quality rule), so the world's bars carry a repeating ±0.4% wiggle; a first correction row had a NaT timestamp (index-aligned assignment: `.to_numpy()`); the correction moved from AAA's first article to its second, because a corrected copy of the first made the deduplication keep the Yahoo copy instead (a data property of the world, not a defect).

### Tasks 5–7 — the four tools, `prefetch`, PIT (D4, D5)
- Did: `agents/tools/news_tools.py`, `test_news_tools.py` (34 incl. audit tests), `test_news_tools_pit.py` (8). Decisions while writing: sector news excludes the company's own articles *before* the per-peer cap (the cap applied first dropped a quiet peer's third article); per-peer ADV from `DataView.adv`; the `ncov` / `evt:…:none` / `rxs` items make an empty read a citable fact; estimated earnings say "estimated" in the item text, with the projected time of day.
- Ran: 46 passed. Honest note: the tests of Tasks 4–8 were written before the code, but I did not run a separate red phase for them (the modules did not exist: every test would have failed on import); the mutation run in Task 11 is the check that they bite.

### Task 8 — prompt and `NewsAnalyst` (D3, D6, D7)
- Did: `prompts/news_analyst.j2` (v1), `research/news.py`, `checks.grounding_problems` takes labelled extra texts (the catalyst descriptions), golden prompt, `test_news.py` (51). The code was written before its tests here (honest note as above).
- Ran: 117 → 119 passed with the audit tests; ruff clean, mypy clean (194 files).

### Task 9 — recorded fixture (D9)
- Did: `artifacts/record_fixture.py`: `members` (65 Information Technology members + SPY), `fixture` (three `slice_lake` parts with different `since`, merged: 1.15 MB, 2,269 articles), `compare` (**47 of 47** items equal on the main lake and on the slice), `live` (DeepSeek `deepseek-flash` quick tier, 1 call, 6,684 + 1,763 tokens, USD 0.0021, status `ok`, no problems), `export`. Reading the report: material news separated from noise by tag, the 2024-05-02 earnings date correctly said to be an estimate, the May 7 event dated, the reaction history cited. Replay test: no key, no lake, one cache hit, cost 0. `-m llm` live test: passed (status ok, 2 calls, USD 0.0031). No secret in the fixture or artifacts (grep).
- Dead end: the recorder's `fixture` step failed with an empty registry (the table schemas register when their readers are imported): added the imports.

### Task 10 — docs and real-lake check
- Did: `docs/agents.md` (framework: `evidence_id`, `finalize`; "The news analyst"), `docs/data-limitations.md` (news: stored text holds HTML entities). Real-lake prefetch ([artifacts/main-lake-check.txt](artifacts/main-lake-check.txt)): 10 symbol/day cases (AAPL 2024-05-01, 2020-03-16, 2015-01-05, 2025-05-01, 2026-11-24; KVUE young listing; SMCI; SIVB a former member; GEHC before its listing; MSFT holiday week): 16–47 items in 0.6–2.7 s, no item timed at or after its cutoff, "0 reactions" and "none" items where the history is missing, estimated dates labelled.
- Findings: stored article text holds HTML entities (Backlog); instruments' sectors are today's classification, so a 2023 view of SIVB reads "Financials peers" as today's GICS (Stage 29/14 limit, unchanged).

### Audit pass before the final runs
- `company_article_count` turned a missing news table into 0 without a trace: now logs `news.company_count_unavailable` (the `gap:` item says the same to the model); tested.
- `past_reactions` carried branches for a reaction without a return: the table schema forbids null returns, so they could never run: removed (no dead code); the summary uses every row.


### Task 11 — verification, mutation, audit
- First full run: every step exit 2 — `uv` could not open its sandbox-blocked cache (`verify.sh` lacked `UV_CACHE_DIR`): script fixed, not a code result. Second run: `pytest`, `make check`, `make test-integration` failed collection: my `test_news_live.py` has the same basename as Stage 11's `tests/unit/data/news/test_news_live.py` ("import file mismatch"); renamed to `test_news_analyst_live.py`, no other duplicate basename in `tests/`.
- Third run (all green) → coverage showed 4 uncovered lines of `news_tools.py` (a peer without an instrument row, an ex-dividend already in the bars, an unexpected event type): three tests added → 100%.
- Mutation testing ([artifacts/mutation/mutation-summary.md](artifacts/mutation/mutation-summary.md)): first run 719 mutants, 72 survivors after the plain re-run, read one by one; new tests closed real gaps (guard conditions, exact problem texts, blocked symbols in every tool, the limits passed to the view, NaN-volume ranking, recall arguments) and one unused parameter was removed; second run 717 mutants, 17 survivors; five more tests; **707 of 717 killed**, the last 10 equivalent. A first attempt failed because mutmut's own dependencies (`libcst`, …) were not in the project venv: installed `--target` into a scratch folder.
- Audit: [artifacts/audit-report.md](artifacts/audit-report.md) (A1–A9; A1–A5 fixed test-first, A6–A9 recorded and in the Backlog).
- Final run on the finished tree: brief Verify 60 passed; `uv run pytest -q` 5928 passed, 84 deselected; `make check` exit 0 (ruff clean, 544 files formatted, mypy clean on 194 files, 5925 + 3 timing passed); `make test-integration` 48 passed, 15 skipped; coverage 100% of both new modules. Raw output in `artifacts/`.
- Stray `.claude` folders: two of my `cd` calls into package or artifact folders left an empty harness folder (`tests/unit/agents/research/.claude`, Stage 30's `artifacts/mutation/.claude`): removed; I then stopped using `cd` into such folders.

### Git (Rule 6A)
- Stage commit `38c8203` "stage-31: News & event analyst agent" (staged diff reviewed: 142 files, no secret: the three keys' values grep to 0 hits in the diff; `.gitkeep` removed); hooks (ruff, format, whitespace) passed. Pushed the branch (`github.com` reachable from the sandbox).
- `main` had moved on: `origin/main` = `207243a` (two Stage 19 follow-up commits from another session: ETF ridge in `risk_model/etf_mapping.py`, `config.py`, `fund.yaml`); local `main` was also behind. Merged `origin/main` into the stage branch: **`e6dfcea`**, no conflict (both sides touched `config.py` and `config/fund.yaml` in different places).
- Checks on the merged branch (`artifacts/merged-check.sh`): `uv run pytest -q` → 5930 passed, 84 deselected, exit 0; `make check` → exit 0 (ruff clean, 544 files formatted, mypy clean on 194 files, 5927 + 3 timing passed); `make test-integration` → 48 passed, 15 skipped, exit 0 · raw: `artifacts/merged-pytest.txt`, `merged-make-check.txt`, `merged-integration.txt`.
