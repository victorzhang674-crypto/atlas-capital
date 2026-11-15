# Stage 27 — Audit report (2026-11-15)

Scope: the whole Stage 27 diff (`src/atlas/agents/memory/`, the `episodes` / `lessons` tables, config, ids, `AgentContext.recall`, the `_memory.j2` partial, docs), checked against the brief, spec §3 (principle 2: memory and lessons are point-in-time), §10, §11, and Project.md. Method: a line-by-line self-review (no subagent, as in Stages 25–26), then a generated mutation run. Every finding was fixed test-first; the tests are in `tests/unit/agents/memory/test_memory_audit.py`.

## Defects found and fixed

| # | Kind | Finding | Fix | Test |
|---|---|---|---|---|
| T6 | ranking | (found by the Task 6 tests) between equally similar lessons, recall-time dedupe kept whichever Chroma listed first, not the documented winner (newer, then id) | rank hits before deduping | `test_an_agent_lesson_and_a_global_duplicate_are_shown_once` |
| T7 | config (Stage 26) | `ATLAS__AGENTS__...` env variables were rejected: `agents` was missing from the env allow-list `_SECTIONS` | added; a test checks every section stays env-overridable | `test_every_config_section_can_be_set_from_the_environment` |
| T7b | privacy | onnxruntime's platform telemetry was left on | `onnxruntime.disable_telemetry_events()` before the model loads | `test_minilm_switches_onnxruntime_telemetry_off` |
| **A1** | **point in time** | `recall(as_of=<datetime>)`: a `datetime` is a `date`; an aware midnight passed pydantic's `date` check and silently used that day's 08:00 cutoff, so memories created between 00:00 and 08:00 were visible | `TypeError`, as `DataView` does | `test_a_datetime_as_of_is_refused` (RED: did not raise) |
| **A2** | **point in time** | a lesson could cite an episode whose outcome settled after the lesson's `created_at` (possible when a backtest is re-run and the outcome is already stored), so a lesson could carry a future outcome | `LookAheadError` | `test_a_lesson_citing_an_outcome_settled_after_it_raises` (RED: did not raise) |
| A5 | model | a `Memory` could hold an outcome without its `settled_at` (or the reverse) | validator: both or neither | `test_a_memory_outcome_comes_with_its_settled_at` (RED) |

## Test gaps closed (no defect behind them)

- **A3** hypothesis property for lesson visibility (supersession, decay, `min_quality`, cap per scope) against a brute-force reference: passed at once.
- **A4** coverage 99% → 100% (orphan index entries skipped, lesson early stop at `k`, vector-less hits, non-mapping JSON, rebuild of a new index).
- **A5–A8** from the mutation run: settled-episode row round trip; `_clip`'s exact boundary; unrated lesson renders `quality n/a`; `sync` embeds in batches; MiniLM's empty result is float32; persistent **and** ephemeral Chroma clients have telemetry off (only the ephemeral settings object was checked before); closing an ephemeral index frees its collections; stable persistent collection names; `dim=1`; exact `micros`; a tzinfo with no offset counts as naive; newer-first ties for episodes; `run_id` and `tags` stored; an explicit `quality_score` beats the default in dedupe; `similarity` reported on supersession; a superseded lesson is not the near-duplicate found; **pinned hash-embedder values** (an index named `hash-256-v1` must match later vectors); stop words ignored; most-similar lessons kept at an early stop; a duplicate does not stop the scan; lesson sources carried; inclusive similarity floor; hash buckets add up on collisions; strict recall-time dedupe threshold.

## Mutation run

mutmut 3.8.1 as an overlay on a scratch copy (`paths_to_mutate = src/atlas/agents/memory/`, tests `tests/unit/agents/memory/`).

- First pass: 1308 mutants: 430 killed, 23 survived, **855 "suspicious"**: the test process ended abnormally. They were concentrated in `index` and `retrieval`, the Chroma-backed code. mutmut forks a child per mutant, and chromadb's Rust runtime does not survive a fork. Each suspicious mutant was re-run in a fresh, non-forked pytest process (`MUTANT_UNDER_TEST`, mutants first on `PYTHONPATH`). The runner was validated first on a known killed mutant (fails) and a known survivor (passes). Result: 708 killed, 147 survived.
- After the audit tests: the 170 survivors re-run (A5–A7: 38 more killed; A8: 20 more; two last tests: 2 more).
- **Final: 1198 / 1308 killed (91.6%), 110 survived**, all reviewed (`mutation-survivors.txt`):

| Category | Count | Why equivalent / accepted |
|---|---|---|
| log calls | 40 | event names and fields of structlog calls |
| error / message text | 17 | the exception type is tested; the wording is not |
| SQL keyword, DuckDB parameter, pandas `orient` case | 17 | case-insensitive |
| dtype casts redone downstream | 12 | `normalise` / Chroma cast to float32 again |
| Chroma call details | 6 | ephemeral prefix case; default embedding function never used (we always pass vectors); extra `include`; `n_results` clamping Chroma also does |
| `zip(strict=…)` on equal lengths | 6 | lengths always equal |
| cosine clip bounds | 4 | Chroma's cosine is already in [-1, 1] |
| other | 8 | `render` `and`→`or` (the `Memory` validator added after the mutants were generated makes it unreachable); `found =` vs `+=` (episodes come first, `found` is empty); lessons without the early stop (trimmed to `k` after); `continue`→`break` on an oldest-first list (every later lesson is also invisible); float-exact ties and boundaries of decayed quality; `np.divide(out=None)` leaves zero rows uninitialised (the code passes `out=zeros`; not reliably detectable) |

## Checked and found sound

- Every visibility decision uses `AsOf.knows` (strict), for episodes, outcomes, lessons and supersession. The index pre-filters on `created_us`, and every hit is checked again against the records (`LookAheadError`). The cap is computed at recall time, so it is point-in-time by construction.
- No wall-clock time in the memory logic: all times come from the caller. Ids are random ULIDs, as for `agent_run_id` (Stage 26 A1).
- No network: Chroma always gets our vectors (`embedding_function=None`) and runs with telemetry off. The full write/recall cycle opens no socket (`test_no_socket_is_opened_in_a_full_cycle`). The MiniLM model is fetched only by the `live` test.
- Untrusted text: memories render inside `data_block(..., source="memory")`, and `</data>` is escaped (`test_memory_block_escapes_injected_data_tags`).
- `import atlas.agents` does not import chromadb (it is imported when a `VectorIndex` opens).

## Not done / limits

- The brief names sentence-transformers. The same model (all-MiniLM-L6-v2) runs here in ONNX through chromadb, without torch (D2). Its behaviour is tested only by the `live` test, on a small synthetic corpus (11 episodes, 5 paraphrased queries). `min_similarity` 0.3 is set from that run, and may need tuning on real agent text in Phase 4.
- Writing lessons (Stage 54) and scoring (Stage 53) are out of scope; this stage provides the API.
- Two processes writing one lake's Chroma index at once are not coordinated. DuckDB already allows one process per `atlas.duckdb` (Stage 25 F1), so this cannot happen through `AgentMemory` with a lake.
