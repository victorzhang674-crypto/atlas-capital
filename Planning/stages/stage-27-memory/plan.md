# Stage 27 — Memory system (episodic + lessons, point-in-time) · Plan

- **Brief:** [brief.md](brief.md) · **Spec:** [00-design-spec.md](../../00-design-spec.md) §10 (learning loop), §3 principle 2 (point in time, "memory and lessons are point-in-time as well"), §11 (vector memory: ChromaDB, local), §12 (`agents/memory/`)
- **Status:** DONE 2026-11-15 (see [verification.md](verification.md))
- **Gate check:** Stage 26 is DONE (its `verification.md`: ALL CRITERIA VERIFIED); `uv run pytest -q` → 4560 passed, 73 deselected, exit 0 (2026-11-15) · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
- **Execution:** written and executed in one go, inline TDD, no subagents (kick-off: "Please plan, implement and audit stage 27"; the owner's standing preference: plan.md first, then execute, no review pause). `worklog.md` is the ledger.

**Goal:** agents recall similar past situations, their decisions and lessons, without ever seeing the future: a memory written at or after the decision cutoff is invisible, and an outcome is visible only once it settled before the cutoff.

**Architecture:** `atlas.agents.memory` has four layers. `store.py` keeps the records (`Episode`, stored lessons) in two native DuckDB tables, `episodes` and `lessons`, or in memory without a lake; it is the source of truth. `embedder.py` turns text into L2-normalised vectors (`Embedder` protocol, a deterministic `HashEmbedder` for tests and offline use, and `MiniLMEmbedder`, all-MiniLM-L6-v2 in ONNX). `index.py` is the ChromaDB vector index (persistent under the lake, ephemeral without one), always filtered by `created_at` before the cutoff; it can be rebuilt from the records. `retrieval.py` holds `AgentMemory`, the one entry point: write API (`record_episode`, `settle_episode`, `add_lesson` with near-duplicate curation) and `recall(agent, query_text, as_of, k=5, symbol=None) -> list[Memory]`, which re-checks every hit against the cutoff and masks unsettled outcomes. `render.py` turns memories into compact prompt lines inside a data block; a versioned Jinja partial exposes it to templates.

## Global constraints
- No network in unit tests: Chroma gets our embeddings (`embedding_function=None`, so it never loads a model) and runs with telemetry off; the real MiniLM model is used only by tests marked `live`.
- No wall clock in memory logic: `created_at` / `settled_at` are given by the caller (the simulated decision time in a backtest); quality decay is measured from `created_at` to the `as_of` cutoff, never to "now".
- Nothing hard-coded: thresholds, caps, half-life, model folder and index folder live in `config/fund.yaml` (`agents.memory.*`).
- Fail safe: an index that returns a memory created at or after the cutoff raises `LookAheadError` (never silently filtered); a lesson whose source episode was created after it raises `LookAheadError`; storage errors propagate.

## Collisions with the overall plan
None found (roadmap row 27: M, depends on 26; spec §10, §11 and §12 match the brief). Readings and decisions, also logged in PROGRESS.md › Decisions log:

- **D1 Vector index = ChromaDB** (spec §11), `chromadb>=1.5` (1.5.9; the lock change is purely additive: no existing pin moved). Persistent client under `<lake>/<agents.memory.index_dir>` (default `memory/chroma`), ephemeral without a lake. Probed 2026-11-15 in a scratch env: (a) in-process ephemeral clients **share one state**, so each ephemeral index uses its own collection-name prefix; (b) metadata values cannot be `None` (no symbol = `""`); (c) cosine distance = 1 − cosine similarity; (d) a filtered top-5 query on 5,000 random 64-d vectors returned exactly the brute-force answer 250 / 250 (pre-filtering), 4 ms per query; (e) `query(ids=...)` restricts the search to given ids. Collections record their embedder name and dimension; opening one with another embedder raises `ConfigError` unless `rebuild=True` (re-embeds every record).
- **D2 Embedder:** `Embedder` protocol (`name`, `dim`, `embed(texts) -> np.ndarray` of L2-normalised float32 rows). `HashEmbedder(dim=256)`: deterministic hashed bag of words (lower-cased word tokens, a small stop-word list, BLAKE2b bucket + sign), the "fake deterministic embedder" of brief task 2, good enough for lexical relevance in tests. `MiniLMEmbedder`: all-MiniLM-L6-v2 (the sentence-transformers model of the brief) run in ONNX through chromadb's bundled `ONNXMiniLM_L6_V2` (onnxruntime; no torch). The model (~79 MB, SHA-256 checked by chromadb) is downloaded on first use into `agents.memory.model_dir` (default `~/.cache/atlas/models`); construction never downloads. Probe: 384-d vectors, works in the sandbox with `chroma-onnx-models.s3.amazonaws.com` allowed.
- **D3 Records:** native DuckDB tables `episodes(id, agent, symbol, created_at, situation_text, decision_json, outcome_json, settled_at, run_id)` and `lessons(id, agent, created_at, text, tags, source_episode_ids, quality_score, superseded_by, superseded_at)` in `atlas.duckdb` (reserved names, like `agent_runs`). Extra columns vs the brief: `run_id` (which fund run made the episode) and the two supersession columns (D5). Without a database the records live in memory (as `TraceStore`). Lessons use the Stage 04 `Lesson` contract (`les_` ids); episodes get a new `IdPrefix.EPISODE = "epi"` and `EpisodeId`. Ids are random ULIDs (Stage 26 A1: seeded ids collide on re-runs).
- **D4 Point in time:** visibility uses `AsOf.knows` (strictly before the cutoff), the rule for every dataset: a memory is visible iff `created_at < cutoff`; its outcome and `settled_at` iff `settled_at < cutoff`. The brief's "`created_at <= as_of`" / "`settled_at <= as_of`" are read with `as_of` = the decision moment, as everywhere else; a memory written at the cutoff instant is not visible to that same decision. `recall` takes `as_of: AsOf | date` (a date uses `calendar.decision_time`). Chroma filters `created_us < cutoff_us`; every hit is re-checked against the records (`LookAheadError` if the index returned a future memory). An episode settles once, after it was created; settling again raises `DataError`.
- **D5 Lesson curation** (all point-in-time, nothing rewritten in the past):
  - *Decay:* effective quality at `as_of` = `quality_score × 0.5^(age_days / half_life_days)`, age from `created_at` to the cutoff (`half_life_days` 180; unrated lessons use `default_quality` 0.5).
  - *Dedupe at write:* a near-duplicate is a same-scope lesson with cosine > `dedupe_cosine` (0.92) created at or before the new one and not yet superseded at that time. If the new lesson's quality is not higher than the old one's effective quality at the new `created_at`, it is **not stored** (`LessonWrite(stored=False, duplicate_of=...)`); otherwise it is stored and the old one is marked `superseded_at = new.created_at` (still visible to decisions before that).
  - *Cap:* at recall, each scope (the agent, `global`) keeps only its `max_lessons_per_agent` (50) best lessons by effective quality at `as_of`; lessons below `min_quality` (0.05) are dropped. Computed at recall, so the cap is point-in-time by construction.
  - *Dedupe at recall:* an agent lesson and a global lesson closer than the threshold are shown once (the higher-ranked).
- **D6 Ranking:** `score` = cosine similarity for both kinds; hits below `min_similarity` (default set from the live MiniLM run, Task 7) are dropped; order = score, then newer, then id; `k` counts both kinds; `kinds=` limits to episodes or lessons. An agent sees its own episodes, its own lessons and `global` lessons, never another agent's. `symbol=` filters episodes only (lessons have no symbol).
- **D7 Rendering:** `render_memory(m)` gives 2–4 lines: a header (`[episode 2024-03-01 AAA sim 0.81]` / `[lesson 2024-02-01 global q 0.62 sim 0.74]`), the text, the decision (episodes), the outcome or `outcome: not settled as of <day>`; long text is cut at `render.max_chars`. `memory_block(memories)` wraps them in `data_block(..., source="memory")` (memories hold past model text: untrusted). Partial `prompts/_partials/_memory.j2` (`{# version: 1 #}`) and a `memory_block` template global.
- **D8 Write API:** `record_episode(agent, situation_text, decision, *, created_at, symbol=None, run_id=None) -> Episode`, `settle_episode(id, outcome, *, settled_at) -> Episode`, `add_lesson(agent, text, *, created_at, tags=(), source_episode_ids=(), quality_score=None) -> LessonWrite`. Writing lessons is Stage 54's job; this stage provides the API. A lesson's source episodes must exist and be created at or before it. `global` is a lesson scope, not an agent name for episodes. Writes are serialised by a lock (Stage 28 fans agents out on threads).
- **D9 Config:** `agents.memory`: `index_dir`, `model_dir`, `embedder` (`minilm` | `hash`), `default_k`, `min_similarity`, `lessons.{dedupe_cosine, max_per_agent, half_life_days, min_quality, default_quality}`, `render.max_chars`.
- **D11 The context door** (added during Task 9): `AgentContext.memory` is typed `AgentMemory | None` and `AgentContext.recall(agent, query_text, k, symbol, kinds=)` calls `memory.recall` at the context's `as_of` (`[]` without a memory), so agent code cannot ask for a later moment, as `DataView` is the only door to data.
- **D12 `ATLAS__AGENTS__...` env overrides** (found in Task 7): Stage 26 added the `agents` config section but not to the env allow-list `_SECTIONS`; added, with a test that every section stays env-overridable.
- **D13 `min_similarity` = 0.3** (Task 7): measured with the live MiniLM model on the synthetic corpus (off-topic ≤ 0.30, weakest on-topic 0.355). The hash embedder's scale is lower; tests on it set 0.1.
- **D10 Files beyond the brief's list:** `index.py` (the Chroma layer, kept apart from the records), `memory/__init__.py`, `_memory.j2`; docs section in `docs/agents.md`.

## File map
| File | Responsibility |
|---|---|
| `pyproject.toml`, `uv.lock` | + `chromadb` |
| `src/atlas/core/config.py`, `config/fund.yaml` | `MemoryConfig`, `LessonCurationConfig`, `MemoryRenderConfig` under `agents.memory` |
| `src/atlas/core/types/ids.py`, `types/__init__.py` | `IdPrefix.EPISODE`, `EpisodeId` |
| `src/atlas/core/storage/db.py`, `schema.py` | native `episodes`, `lessons`; `insert_episodes`, `settle_episode`, `insert_lessons`, `supersede_lesson`; reserved names |
| `src/atlas/agents/memory/store.py` | `Episode`, `StoredLesson`, `MemoryStore` (DuckDB or memory) |
| `src/atlas/agents/memory/embedder.py` | `Embedder`, `HashEmbedder`, `MiniLMEmbedder`, `make_embedder` |
| `src/atlas/agents/memory/index.py` | `VectorIndex` (ChromaDB) |
| `src/atlas/agents/memory/retrieval.py` | `Memory`, `LessonWrite`, `AgentMemory`, `effective_quality` |
| `src/atlas/agents/memory/render.py`, `prompts/_partials/_memory.j2`, `prompting.py` | `render_memory`, `memory_block`, partial + global |
| `tests/unit/agents/memory/…` | tests per task (`memory_world.py`: the synthetic corpus) |
| `docs/agents.md` | memory section |

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

Brief task mapping: brief 1 (store CRUD) → Tasks 1–2; brief 2 (embedder + fake) → Task 3; brief 3 (PIT retrieval) → Tasks 4–5; brief 4 (dedupe + caps) → Task 6. Task 7 covers the real model and the "relevant items" criterion; Task 8 rendering; Task 9 docs and API.

### Task 1 — Foundations: dependency, config, ids, DuckDB tables
- Files: `pyproject.toml`, `uv.lock`, `config.py`, `fund.yaml`, `ids.py`, `types/__init__.py`, `db.py`, `schema.py` · tests `tests/unit/agents/memory/test_memory_foundations.py`, `tests/unit/core/types/test_types_ids.py`, `tests/unit/core/storage/test_storage_memory_tables.py`, `test_storage_db.py`
- [x] Failing tests: config defaults / validation / yaml equals defaults; `chromadb` is a runtime dependency; `IdPrefix.EPISODE` and `EpisodeId`; new database has empty `episodes` / `lessons`; insert, duplicate id → `DataError`; settle once (second settle → `DataError`, unknown id → `DataError`); supersede once; reserved names; native table list
- [x] Run → FAIL · implement · run → PASS

### Task 2 — Store CRUD (brief task 1)
- Files: `store.py` · test `test_memory_store.py` (parametrised: DuckDB and in-memory)
- [x] Failing tests: `Episode` validation (aware times, `global` refused, decision a JSON object, settled after created); add/get/get-many; persistence across reopen; settle sets outcome; settle before creation → `ValueError`; lessons add / list by scope / supersede; JSON round trip of decision, outcome, tags, sources; thread-safe concurrent adds
- [x] Run → FAIL · implement · run → PASS

### Task 3 — Embedder interface and the fake deterministic embedder (brief task 2)
- Files: `embedder.py` · test `test_memory_embedder.py`
- [x] Failing tests: `HashEmbedder` deterministic across instances and processes (no `hash()` salt), unit norm, shape, empty text → zero vector, related texts closer than unrelated; `MiniLMEmbedder` constructs without download, names itself, uses `model_dir`; `make_embedder` from config; protocol check
- [x] Run → FAIL · implement · run → PASS

### Task 4 — Vector index (ChromaDB)
- Files: `index.py` · test `test_memory_index.py`
- [x] Failing tests: add + query returns ids and similarities (1 − distance); filters by agent / symbol / `created_us`; `ids=` restriction; empty collection and `n` above the count; two ephemeral indexes do not share data; persistent index survives reopen; embedder mismatch → `ConfigError`, `rebuild=True` resets; telemetry off; dimension check
- [x] Run → FAIL · implement · run → PASS

### Task 5 — `AgentMemory` and point-in-time recall (brief task 3)
- Files: `retrieval.py` · test `test_memory_pit.py`, `test_memory_retrieval.py`, `memory_world.py`
- [x] Failing tests: **future memories hidden** (created after, and exactly at, the cutoff); **outcomes hidden until settled** (settled after / at the cutoff → `outcome is None`, `settled_at is None`; before → visible); date `as_of` uses the decision time; lessons created later hidden; superseded lessons visible before their supersession only; another agent's episodes and lessons hidden, `global` lessons shown; an index returning a future id → `LookAheadError`; a lesson citing a later episode → `LookAheadError`; **hypothesis property**: on random corpora, recall equals a brute-force point-in-time reference and never shows a future memory or an unsettled outcome; relevance on the synthetic corpus (earnings / rates / supply-chain situations) with `HashEmbedder`; `symbol`, `k`, `kinds`, `min_similarity`; index rebuilt from records (`sync` adds missing, `rebuild`); no socket opened during a full write/recall cycle
- [x] Run → FAIL · implement · run → PASS

### Task 6 — Lesson curation: dedupe, caps, decay (brief task 4)
- Files: `retrieval.py` · test `test_memory_curation.py`
- [x] Failing tests: `effective_quality` (half-life math, unrated default, age from the cutoff, never negative age); near-duplicate not stored when not better; stored and old superseded when better (old still recalled before the new `created_at`); threshold is strict (> 0.92), other scopes not compared, later lessons not compared; cap per scope keeps the best by effective quality at `as_of` (point in time: a different `as_of` picks differently); `min_quality` drops decayed lessons; agent vs global duplicate shown once
- [x] Run → FAIL · implement · run → PASS

### Task 7 — The real model (live) and `min_similarity`
- Files: `test_memory_live_minilm.py` (marked `live`), `fund.yaml`
- [x] Run the synthetic corpus with `MiniLMEmbedder` (model downloaded to `$TMPDIR`): relevant items first; record the similarity spread; set `min_similarity` from it; raw output saved under `artifacts/`

### Task 8 — Rendering partial
- Files: `render.py`, `prompts/_partials/_memory.j2`, `prompting.py` · test `test_memory_render.py`
- [x] Failing tests: 2–4 lines per memory; unsettled outcome text; truncation at `render.max_chars`; inside a `memory` data block with `</data>` escaped; empty list → `(no memories)`; the partial renders through `PromptRegistry` and has a version line
- [x] Run → FAIL · implement · run → PASS

### Task 9 — Public API and docs
- Files: `memory/__init__.py`, `docs/agents.md`, `test_agents_package.py` · test `test_memory_package.py`
- [x] Failing tests: `atlas.agents.memory.__all__`; every module has a docstring; docs mention `recall`, `AsOf.knows`, `settled_at`, `dedupe_cosine`, `max_per_agent`, `half_life_days`, `memory_block`
- [x] Run → FAIL · implement · run → PASS

### Task 10 — Verification
- [x] `make check`; `uv run pytest -q`; brief's Verify `uv run pytest tests/unit/agents/memory -q`; coverage of `src/atlas/agents/memory`; live MiniLM test; raw output in `artifacts/`; `verification.md`, PROGRESS.md

### Task 11 — Audit (the owner asked for it in the kick-off)
Findings (report: [artifacts/audit-report.md](artifacts/audit-report.md)): A1 a `datetime` `as_of` passed as a day (midnight) moved the cutoff later → `TypeError`; A2 a lesson could cite an outcome settled after it → `LookAheadError`; A3 lesson visibility property test (supersession, decay, min quality, cap) vs a brute-force reference; A4 seven untested branches covered (100% coverage). Earlier, during the tasks: the tie-break defect in recall-time dedupe (Task 6), the `agents` env allow-list (D12).
- [x] Self-review of the stage diff against the brief, spec §3/§10 and Project.md: PIT holes, untested branches, error paths, docs; mutation run on `src/atlas/agents/memory` (mutmut overlay); findings fixed test-first and recorded in `artifacts/audit-report.md`

### Task 12 — Git (Rule 6A)
- [ ] Review the staged diff; commit `stage-27: memory system`; fast-forward `main`; merged-`main` `uv run pytest -q` and `make check`; push on the owner's go-ahead; record hashes
