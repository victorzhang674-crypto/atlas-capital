# Stage 27 — Memory system (episodic + lessons, point-in-time) · Work log

Append entries in order. Record failures and dead ends as honestly as successes.

## 2026-11-15 — Gate check, environment, probes
- Did: read Project.md, PROGRESS.md, brief, spec §10/§11/§12, roadmap row 27, Stage 26 plan and handoff. Stage 26 is DONE; its `verification.md` says ALL CRITERIA VERIFIED.
- Ran: `uv sync --group dev` (fresh `UV_CACHE_DIR`) → ok. Gate `uv run pytest -q` → 4560 passed, 73 deselected, exit 0 (403 s) · raw: `artifacts/gate-pytest.txt`.
- Probes (scratch env in `$TMPDIR`, not committed): chromadb 1.5.9. In-process ephemeral clients share one state (a second `EphemeralClient` saw the first one's collection); metadata values cannot be `None`; collection names need 3–512 chars; cosine distance = 1 − cosine similarity; filtered top-5 on 5,000 random 64-d vectors = brute force 250 / 250, 4 ms per query; `query(ids=...)` restricts the candidates. `ONNXMiniLM_L6_V2` (all-MiniLM-L6-v2, ONNX) downloads ~79 MB from `chroma-onnx-models.s3.amazonaws.com` on first call to the instance's `DOWNLOAD_PATH`; the first attempt failed mid-download (`peer closed connection`), the retry worked; 384-d vectors.
- `uv add "chromadb>=1.5"` → lock change purely additive (no existing pin moved; `git diff uv.lock` has no removed lines).
- Plan written: `plan.md` (D1–D10).

## 2026-11-15 — Task 1: config, ids, DuckDB tables
- Tests first: `tests/unit/agents/memory/test_memory_foundations.py`, `tests/unit/core/storage/test_storage_memory_tables.py`, edits to `test_types_ids.py` (EPISODE prefix, `EpisodeId`), `test_storage_db.py` (native table list), `test_storage_schema.py` (reserved names).
- Ran → collection errors (names missing): RED.
- Implemented: `MemoryConfig` / `LessonCurationConfig` / `MemoryRenderConfig` under `agents.memory` (`config.py`, `config/fund.yaml`), `IdPrefix.EPISODE = "epi"`, `EpisodeId`, native `episodes` / `lessons` tables with `insert_episodes`, `settle_episode` (once), `insert_lesson(row, supersedes=)` (one transaction), reserved names.
- Ran the touched suites → 1 failed: `test_every_yaml_key_has_a_comment` (the `memory:`, `lessons:`, `render:` section keys had no comment). Added comments → 820 passed.

## 2026-11-15 — Task 2: store CRUD (brief task 1)
- Tests first: `test_memory_store.py` (22 tests, each store test parametrised over DuckDB and in-memory). RED: module missing.
- Implemented `src/atlas/agents/memory/store.py`: `Episode`, `StoredLesson` (the Stage 04 `Lesson` + supersession), `MemoryStore`, row converters, `json_object`.
- Ran → 1 failed: my test expected `Z` for UTC in the decision round trip, `canonical_json` writes `+00:00` (documented): the test's expectation was wrong, fixed → `tests/unit/agents/memory` 41 passed.

## 2026-11-15 — Task 3: embedders (brief task 2)
- Tests first: `test_memory_embedder.py` → RED (module missing).
- Implemented `embedder.py`: `Embedder` protocol, `HashEmbedder` (BLAKE2b-hashed words, stop words, unit norm, deterministic across processes), `MiniLMEmbedder` (chromadb's bundled ONNX all-MiniLM-L6-v2, lazy, `model_dir`), `make_embedder`. → 12 passed. Simplified `normalise` afterwards (it handled shapes it never receives) → still 12 passed.

## 2026-11-15 — Task 4: ChromaDB index
- Wrote `test_memory_index.py` and `index.py` in the same step (not a separate RED run: I recorded RED afterwards by running the tests against `HEAD`'s source with `PYTHONPATH=<git archive HEAD>/src` → `ModuleNotFoundError: atlas.agents.memory`). → 14 passed.
- `VectorIndex`: one collection per kind, cosine, our embeddings, telemetry off, mandatory `created_before` filter (strict), `agents` / `symbol` / `ids` filters, ephemeral collections named per instance (probe finding), embedder/dim recorded in the collection metadata (`ConfigError` on mismatch, `rebuild=True` empties).

## 2026-11-15 — Task 5: AgentMemory and point-in-time recall (brief task 3)
- Tests first: `memory_world.py` (11-episode synthetic corpus over March 2024, two agents), `test_memory_pit.py` (incl. a hypothesis property vs a brute-force PIT reference, a corrupt-index `LookAheadError`, no socket opened), `test_memory_retrieval.py`. RED: `retrieval` missing.
- Implemented `retrieval.py`: `Memory`, `LessonWrite`, `effective_quality`, `AgentMemory` (writes, recall, `visible_lessons`, `sync`, rebuild). The curation logic of Task 6 was written here too.
- Ran → 3 failed, all test set-up errors on inspection: (1) the test expected memories at as_of 2024-03-04, but the first seed is created at noon that day, after the 08:00 cutoff (empty is right; test now checks 3/1 and 3/4 are empty); (2) a "hidden lesson from a corrupt index" test had no visible lesson, so the index was never queried (added one); (3) a supply-chain query shared one word with the second supply text, too little for the lexical hash embedder (query reworded; semantic recall is the live MiniLM test's job). → 97 passed.

## 2026-11-15 — Task 6: curation (brief task 4)
- `test_memory_curation.py` (13 tests): 12 passed at once (the logic came with Task 5), 1 failed: **defect** — between equally similar lessons, recall-time dedupe kept whichever Chroma listed first, not the documented winner (newer, then id). Fixed by ranking hits before deduping → 110 passed, three runs in a row.

## 2026-11-15 — Task 7: live MiniLM, `min_similarity`, env override
- Live test `test_memory_live_minilm.py` (marked `live`) failed first on config: **defect (Stage 26)** — `ATLAS__AGENTS__...` env variables were rejected because `agents` was missing from the env section allow-list `_SECTIONS`. Tests first (`test_memory_settings_can_be_set_from_the_environment`, `test_every_config_section_can_be_set_from_the_environment`) → RED → added `agents` → green.
- Live run (model from `$TMPDIR/models`, downloaded in the probe): top hit on-topic for all 5 paraphrased queries; best on-topic 0.47–0.56, best off-topic 0.17–0.30, weakest on-topic item 0.355 · raw: `artifacts/live-minilm-run1.txt`. Set `min_similarity` 0.2 → 0.3. The hash embedder's word-overlap scale is lower: the corpus helper uses `HASH_CONFIG` (floor 0.1); one retrieval test had failed under 0.3 for that reason. Live re-run at 0.3 passed · raw: `artifacts/live-minilm.txt` (the final run, same numbers).
- onnxruntime printed a telemetry line at import (no connection was reported by the sandbox). Test first (`test_minilm_switches_onnxruntime_telemetry_off`) → RED → `onnxruntime.disable_telemetry_events()` before the model loads → green.

## 2026-11-15 — Task 8: rendering partial
- Tests first: `test_memory_render.py` → RED. Implemented `render.py` (`render_memory`: 4 lines per episode, 2–3 per lesson, whitespace collapsed, lines cut at `max_chars`; `memory_block` in a `memory` data block), `prompts/_partials/_memory.j2` (`{# version: 1 #}`), template global `memory_block`.
- The Stage 26 test listing shipped partials failed (new partial) → list updated. chromadb is now imported only when a `VectorIndex` opens (`python -X importtime -c "import atlas.agents"` shows no chromadb). → 274 agent tests passed.

## 2026-11-15 — Task 9: public API, context door, docs
- Tests first: `test_memory_package.py` → RED. `atlas.agents.memory.__all__`; `AgentContext.memory` typed `AgentMemory | None` and `AgentContext.recall(...)` bound to the context's `as_of` (D11); docs section "Memory" in `docs/agents.md`. → 278 agent tests passed.
- Static checks: ruff fixed formatting and line lengths; `×` in docstrings → `*` (RUF002); `onnxruntime` added to the mypy missing-stubs list. `ruff check .` → All checks passed!; `ruff format --check .` → 449 files already formatted; `mypy src/atlas` → Success: no issues found in 173 source files.

## 2026-11-15 — Pre-audit full check
- Ran `make check` → exit 0: ruff clean, format clean, mypy clean, pytest 4697 passed (parallel) + 3 timing passed · raw: `artifacts/make-check.txt` (superseded by the final run after the audit).

## 2026-11-15 — Task 11: audit (self-review + mutation run)
- Self-review of the diff against the brief, spec §3/§10 and Project.md. Findings, each fixed test-first (`tests/unit/agents/memory/test_memory_audit.py`):
  - **A1 (PIT defect):** `recall(as_of=<datetime>)` — a `datetime` is a `date`; an aware midnight passed pydantic's `date` check and silently used that day's 08:00 cutoff, later than meant (memories from 00:00–08:00 visible). RED (`DID NOT RAISE TypeError`) → `TypeError`, as `DataView` does.
  - **A2 (PIT defect):** a lesson could cite an episode whose outcome settled after the lesson's `created_at` (possible in a re-run backtest, where the outcome is already stored). RED → `LookAheadError`.
  - **A3:** hypothesis property for lesson visibility (supersession, decay, `min_quality`, cap per scope) vs a brute-force reference → passed at once (no defect).
  - **A4:** coverage 99% → 100%: tests for the orphan-index skip, lesson early stop at `k`, vector-less hits, non-mapping JSON, rebuild of a new index.
- Mutation run (mutmut 3.8.1, overlay on a scratch copy, `src/atlas/agents/memory`, tests `tests/unit/agents/memory`): set-up failures first (a test-selection string passed as one argument; `rsync --exclude data` also dropped `src/atlas/data`; `docs/` missing from `also_copy`), then 1308 mutants: 430 killed, 23 survived, **855 "suspicious"** — the test process ended abnormally, concentrated in `index` / `retrieval` (Chroma-backed code): mutmut forks a child per mutant, and chromadb's Rust runtime does not survive the fork. Reclassified each suspicious mutant in a fresh, non-forked pytest process (`MUTANT_UNDER_TEST`, mutants first on `PYTHONPATH`), after validating the runner on a known killed mutant (fails) and a known survivor (passes).
  - **A5** (from the 23 survivors): tests for the settled-episode row round trip, `_clip`'s exact boundary, a lesson with no quality (`n/a`), `sync` batching (embed calls `[2, 2, 1]` with batch 2), MiniLM's empty-result dtype; plus a `Memory` validator (outcome and `settled_at` both or neither; RED first). Equivalent survivors: SQL keyword case, pandas `orient` case, `zip(strict=…)` on equal lengths, float32 casts redone by `normalise`.
  - **A6** (from reclassified survivors): the persistent index's client settings were never checked for telemetry (only the ephemeral settings object) — test on both clients; closing an ephemeral index frees its collections; persistent collection names stay `atlas-episodes` / `atlas-lessons`; `dim=1` works; `micros` exact values; a tzinfo with no offset counts as naive. All passed against the code (gaps, not defects).
  - **A7** (reclassified survivors in `retrieval`): newer-first ties for episodes, `run_id` / `tags` stored, an explicit `quality_score` used (not the default) in dedupe, `similarity` on supersession, a superseded lesson not found as the near-duplicate. All passed against the code.
  - **A8**: pinned hash-embedder values (an index stored as `hash-256-v1` must match later vectors), stop words ignored, default and minimum `dim`, most-similar lessons kept at an early stop, a duplicate does not stop the scan, lesson sources carried, inclusive similarity floor, orphan test reordered so the orphan ranks first; then hash-bucket collisions add up and the recall-time dedupe threshold is strict. All passed against the code.
  - Survivor re-runs (fresh processes, current tests; the one test that needs the new `Memory` validator deselected, since the mutants predate it): 170 → 132 (A5–A7) → 112 (A8) → 110 (last two tests; both target mutants verified killed).
  - **Final mutation score: 1198 / 1308 killed (91.6%)**; the 110 survivors reviewed one by one: log calls 40, message text 17, SQL/parameter/orient case 17, dtype casts redone downstream 12, Chroma call details 6, `zip(strict)` 6, clip bounds 4, other 8 (unreachable or float-exact ties) · `artifacts/mutation-survivors.txt`, `artifacts/audit-report.md`.
  - Time: the fresh-process reclassification took ~2.5 h (4 workers; each survivor runs the full memory suite incl. two hypothesis properties).

## 2026-11-15 — Task 10–12: verification, commit, merge
- Final runs after the audit: `make check` → exit 0 (4727 + 3 timing passed); `uv run pytest -q` → 4730 passed, 74 deselected, exit 0; Verify `uv run pytest tests/unit/agents/memory -q` → 158 passed, 1 deselected; live MiniLM → 1 passed; coverage 100% (711 statements). `verification.md` and PROGRESS.md written; stage set DONE.
- Staged diff reviewed (48 files: stage code, tests, config, docs, lock, stage records; no `.env`, lake, cache or Chroma files; secret values from the main checkout's `.env` not found in the diff). Commit **9c01904** `stage-27: memory system (episodic + lessons, point-in-time)` (pre-commit hooks passed); `git status` clean.
- Merge: the owner's Terminal panel ran `artifacts/merged-main-check.sh` from the main checkout: `git merge --ff-only` 59b5e0b → 9c01904 (merge exit 0, trees equal cc74f86); merged `uv run pytest -q` → 4730 passed, exit 0; merged `make check` → exit 0; `git branch --merged main` lists the branch · raw: `artifacts/merged-main-check.txt`.
- Push: not done; `main` is 1 ahead of `origin/main`. Waits for the owner's go-ahead (the remote may be public).
