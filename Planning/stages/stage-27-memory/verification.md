# Stage 27 — Memory system (episodic + lessons, point-in-time) · Verification evidence

Every command below was run by the AI in this stage's chat (2026-11-15), after the audit fixes; raw output is under `artifacts/`.

#### Verification evidence — Stage 27 — 2026-11-15
- Commands run (this chat):
  - Gate (before any work): `uv run pytest -q` → 4560 passed, 73 deselected, exit 0 · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
  - `uv run pytest -q` → **4730 passed, 74 deselected, exit 0** (346 s) · raw: [artifacts/pytest.txt](artifacts/pytest.txt)
  - `make check` → **exit 0**: `ruff check .` → All checks passed! · `ruff format --check .` → 450 files already formatted · `mypy src/atlas` → Success: no issues found in 173 source files · pytest 4727 passed (parallel) + 3 timing passed · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
  - Brief's Verify `uv run pytest tests/unit/agents/memory -q` → **158 passed, 1 deselected (the `live` MiniLM test), exit 0** · raw: [artifacts/verify-brief.txt](artifacts/verify-brief.txt)
  - Live: `pytest -m live tests/unit/agents/memory/test_memory_live_minilm.py -s` (real all-MiniLM-L6-v2, model in `$TMPDIR/models` via `ATLAS__AGENTS__MEMORY__MODEL_DIR`) → 1 passed; top hit on-topic for 5/5 paraphrased queries · raw: [artifacts/live-minilm.txt](artifacts/live-minilm.txt)
  - Coverage of `src/atlas/agents/memory` → **100% (711 statements)** · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
  - Mutation (audit): 1198 / 1308 mutants killed; the 110 survivors reviewed (equivalent or float-exact) · [artifacts/audit-report.md](artifacts/audit-report.md), [artifacts/mutation-survivors.txt](artifacts/mutation-survivors.txt)
- Acceptance criteria (brief):
  - [x] **The PIT memory tests pass** — evidence: `tests/unit/agents/memory/test_memory_pit.py` (in the Verify run): `test_future_episodes_are_invisible`, `test_a_memory_created_exactly_at_the_cutoff_is_invisible`, `test_outcomes_are_hidden_until_settled_before_the_cutoff`, `test_unsettled_episodes_never_show_an_outcome`, `test_a_date_uses_the_configured_decision_time`, `test_future_lessons_are_invisible`, `test_a_superseded_lesson_is_visible_only_before_its_supersession`, `test_another_agents_memories_are_invisible_but_global_lessons_are_shared`, `test_an_index_returning_a_future_memory_raises` (+ hidden lesson, other agent), `test_a_lesson_citing_a_later_episode_raises`, `test_no_socket_is_opened_in_a_full_cycle`, and the hypothesis property `test_recall_matches_a_brute_force_point_in_time_reference`; audit additions `test_a_datetime_as_of_is_refused`, `test_a_lesson_citing_an_outcome_settled_after_it_raises`, `test_lesson_visibility_matches_a_point_in_time_reference`, `test_the_cap_is_point_in_time`, `test_context_recall_is_bound_to_the_views_as_of`
  - [x] **Retrieval returns relevant items on a synthetic corpus** — evidence: `test_recall_returns_relevant_items_first` (5 topics on the 11-episode corpus in `memory_world.py`, deterministic `HashEmbedder`, in the Verify run) and the live `test_minilm_recalls_relevant_memories_from_paraphrases` (real model, paraphrased queries: best on-topic 0.47–0.56 vs best off-topic 0.17–0.30)
  - [x] Brief tasks: 1 store CRUD with DuckDB — `test_memory_store.py` (DuckDB and in-memory), `test_storage_memory_tables.py`; 2 embedder interface + fake deterministic embedder — `test_memory_embedder.py`; 3 PIT retrieval tests — above; 4 dedupe + caps — `test_memory_curation.py`
- Waivers: none
- Merge into `main`: fast-forward 59b5e0b → **9c01904** (2026-11-15, run in the owner's Terminal panel: the sandbox cannot write the main checkout); trees equal (cc74f86). Merged result: `uv run pytest -q` → 4730 passed, 74 deselected, exit 0 · `make check` → exit 0 (ruff clean, 450 files formatted, mypy clean, 4727 passed + 3 timing passed) · `git branch --merged main` lists the stage branch · raw: [merged-main-check.txt](artifacts/merged-main-check.txt), script [merged-main-check.sh](artifacts/merged-main-check.sh). pushed (next line)
- Pushed 2026-11-15 on the owner's go-ahead ("Yes, push"): `main` 59b5e0b..38c5903 and the stage branch. CI (GitHub Actions, `make check` + `make test-integration`) on 38c5903: **`main` run 43716562748 success**, stage-branch run 43716562759 success · raw: [ci-runs-38c5903.json](artifacts/ci-runs-38c5903.json)
- Records commit 1a932c0 (`stage-27: push and CI records`) pushed 2026-11-15 (`main` 38c5903..1a932c0 and the branch). CI on 1a932c0: **`main` run 43718001568 success**, stage-branch run 43718001937 success · raw: [ci-runs-1a932c0.json](artifacts/ci-runs-1a932c0.json). The completeness pass that followed (worklog file list, this line, the status line) is in records commit 1a932c0's successor; its CI result is reported in the chat and on GitHub, since a commit cannot record its own CI run.

**Result:** ALL CRITERIA VERIFIED


#### Phase 3 checkpoint Tier 2 — 2026-11-17
- Item 1 (memory ids from the id source): `test_memory_seeded_ids.py` 4 passed; `tests/unit/agents/memory` 162 passed. Full block and raw output: [../stage-28-langgraph-skeleton/verification.md](../stage-28-langgraph-skeleton/verification.md) (Tier 2) — `uv run pytest -q` 5008 passed; `make check` exit 0.
- Acceptance criteria unchanged and still evidenced (`tests/unit/agents/memory` 162 passed).

**Result:** ALL CRITERIA VERIFIED
