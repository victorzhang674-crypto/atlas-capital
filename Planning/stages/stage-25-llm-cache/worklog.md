# Stage 25 — LLM record/replay cache · Work log

Append entries in order. Record failures and dead ends as honestly as successes.

## 2026-11-14 — Start, gate check
- Did: read Project.md, PROGRESS.md (current stage 25, `NOT STARTED`; Stage 24 `DONE`, its `verification.md` says ALL CRITERIA VERIFIED), the brief, spec §3.3 and the Stage 24 gateway code and backlog notes ("(Stage 24 → 25) the cache key needs the schema, tools, temperature and `thinking` …; hash the request after the data-policy step; a hit writes an `llm_usage` row with `cache_hit = true` and cost 0").
- Ran: `uv sync --group dev` in the fresh worktree (`.venv.nosync` + `.venv` symlink) → ok.
- Ran: gate `uv run pytest -q` → **4244 passed, 71 deselected** in 380 s, exit 0 · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
- Copied the template; wrote [plan.md](plan.md) (decisions D1–D7). Kick-off: "please plan, implement, execute and audit stage 25" (owner preference: plan then execute, no review pause).

## 2026-11-14 — Task 1: error, config, storage table
- Tests first: `test_cache_miss_is_an_llm_error_with_its_key` (`test_errors.py`), three config tests (`test_config.py`), `test_storage_llm_cache.py` (10 tests), reserved name `llm_cache` (`test_storage_schema.py`). Red: `ImportError` for `CacheMiss` and `LLM_CACHE_COLUMNS`.
- Implemented: `CacheMiss(LLMError)` with `key`; `LLMCacheConfig` / `LLMCacheMode` and `llm.cache` in `LLMConfig`; `llm.cache.mode: read_write` in `config/fund.yaml`, `off` in `config/profiles/paper.yaml`; native table `llm_cache` (DDL in `Database.__init__`), `Database.put_llm_cache(rows, *, replace)` (`INSERT OR IGNORE` / `OR REPLACE`, one transaction), `Database.prune_llm_cache(before)`; `RESERVED_TABLES` += `llm_cache`.
- Problems / fixes:
  - `_count` with `fetchone()` left an unfinished DuckDB result ("connection has an unfinished result") → `fetchall()`.
  - `test_storage_db.py` listed the native tables exactly → added `llm_cache` (a new table, not a weakened assertion).
  - **YAML `mode: off` is read as the boolean `False`** (YAML 1.1) and the paper profile failed to load → a `before` validator maps `False` to `"off"`; `test_llm_cache_mode_accepts_an_unquoted_yaml_off` (and `True` is still refused).
  - My first config test read `Path("config")`, but the autouse fixture chdirs to `tmp_path` → used the existing `_repo_config_copy` helper.
- Ran: `uv run pytest tests/unit/core -q` → 954 passed.

## 2026-11-14 — Task 2: key and store (`src/atlas/llm/cache.py`)
- Tests first: `test_llm_cache_key.py` (key canonicalisation: dict order irrelevant, a `prompt_version` bump changes the key, every input the model sees changes it, labels / token cap / timeout / tier label do not, pinned digest), `test_llm_cache_store.py` (memory and DuckDB, parametrised).
- Implemented: `key_payload`, `cache_key`, `request_json`, `schema_fingerprint`, `StoredResult`, `CacheEntry` (`create`, `result()` → `LLMError` when corrupt), `CacheStats`, `LLMCache` (`get`, `put`, `entries`, `stats`, `prune`).
- Problems / fixes: the store tests asked for a `lake` fixture that only exists under `tests/unit/core/storage` → used the shared `tmp_lake`. The pinned digest was taken from the first green run (`fbbf2c4b…`), after the other key tests passed.
- Ran: the two files → 35 passed.

## 2026-11-14 — Task 3: gateway modes and ledger
- Tests first: `test_llm_cache_gateway.py` (28 tests: the acceptance pair, every mode, hits are free and logged, budget skipped on hits only, structured output and repair retries, tool calls, failures never stored, corrupt entry and schema drift fail safe, DuckDB replay in a later session, `build_gateway` wiring). Red: `LLMGateway.__init__() got an unexpected keyword argument 'cache'`.
- Implemented: `LLMGateway(..., cache=, cache_mode=)` (+ `cache`, `cache_mode` properties; `ConfigError` for a mode without a cache), `_cached` (lookup / `CacheMiss` / store with `replace` in `record`), `_replay` (re-parses the schema, writes the `cache_hit` ledger row, logs `llm.cache_hit`); `build_gateway` builds a DuckDB cache with a lake, in-memory without, none when `off`; `CostLedger.cache_counts()`; `LLMResult.attempts` now `ge=0` (0 = a hit); `atlas.llm` exports.
- Problems / fixes: `build_gateway` test opened a second `Database` on the file the process-wide one holds ("Cannot change configuration option TimeZone") → read through `get_database` as the Stage 24 ledger test does.
- Ran: `uv run pytest tests/unit/llm tests/unit/core -q` → 1215 passed, 2 deselected.

## 2026-11-14 — Task 4: CLI and docs
- Tests first: `tests/unit/cli/test_llm_cmd.py` (help, stats incl. hit rate and an empty cache, default store from `data.lake_path`, missing store exits 1 and creates nothing, export JSON lines oldest first, `--force`, prune with `--dry-run`, bad `--older-than` exits 2 for 8 bad values, `parse_older_than`). Red: `atlas.cli.llm_cmd` missing.
- Implemented: `src/atlas/cli/llm_cmd.py` (`atlas llm cache stats|export|prune`), registered in `app.py`; `docs/llm-cache.md` (modes, key, the replay-validity note, commands).
- Problems / fixes: the stats regexes did not allow rich's box characters → the test's `_plain` strips them. The fixtures needed "now" for ages: `LiveClock().now()` (no `datetime.now()` outside `atlas.core.clock`).
- Ran: `test_llm_cmd.py` + `test_cli_startup.py` → 21 passed.

## 2026-11-14 — Lint, coverage, audit
- `ruff format` (6 files), `ruff check` (3 long docstring lines, an unused import, an en dash, `RUF015`) and `mypy` (`dict[Hashable, Any]` into `_entry_from_row` → `Mapping[Any, Any]`) fixed; all clean.
- Coverage: `cache.py` 100%, `gateway.py` 100%, `cost.py` 100%, `cli/llm_cmd.py` 100% after two added tests (naive prune cutoff in memory, bad `--config-dir`) · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
- Mutation (mutmut 3.8.1 on `src/atlas/llm/cache.py`, scratch copy): run 1 **211 / 245** killed. Real gaps among the survivors: `request_json` was never checked to be the key's preimage; an absent `prompt_version` hashing as `""` (vs `null`) was not pinned; the split of the answer and the meter into two JSON columns was not asserted; stored timestamps could come back as pandas `Timestamp`. Added `test_the_key_without_a_prompt_version_is_pinned`, `test_request_json_is_the_preimage_of_the_key`, `test_answer_and_meter_go_to_their_own_columns`, `test_stored_timestamps_come_back_as_plain_aware_datetimes` and a preimage check in the gateway test. Run 2 **224 / 245**; the 21 survivors are equivalent (`model_dump` mode spelling: `canonical_json` encodes both modes alike for these types; SQL keyword case; `orient="RECORDS"`; `or`→`and` on `tzinfo is None` / `utcoffset() is None`; the memory path never reads `_entry_from_row`) · raw: [mutation-summary.txt](artifacts/mutation-summary.txt), [run 1](artifacts/mutation-survivors-run1.txt), [run 2](artifacts/mutation-survivors-run2.txt), [setup](artifacts/mutmut-setup.cfg)
- Self-review notes (no change made): two threads that miss the same key at once both call the provider (first writer wins the store) — acceptable, no lock across a network call; a hit is returned before the token-cap check (a hit spends nothing).

## 2026-11-14 — Verify, commit, merge
- Ran: `make check` → exit 0 (4345 passed + 3 timing); `uv run pytest -q` → 4348 passed, 71 deselected, exit 0; Verify `uv run pytest tests/unit/llm -q` → 268 passed, 2 deselected; secret scan → no match.
- Reviewed the staged diff (37 files: stage folder, `docs/llm-cache.md`, `config/`, `src/`, `tests/`; no `.env`, lake or cache files). Commit **d1ef62d** `stage-25: LLM record/replay cache` (pre-commit hooks passed); `git status --short` clean.
- Merge: fast-forward `main` 2eaf072 → d1ef62d in the owner's Terminal panel ([merged-main-check.sh](artifacts/merged-main-check.sh)); trees equal; merged result `uv run pytest -q` → 4348 passed, 71 deselected, exit 0; `make check` → exit 0; `git branch --merged main` lists the stage branch · raw: [artifacts/merged-main-check.txt](artifacts/merged-main-check.txt). Records commit follows (fast-forwarded onto `main`). Not pushed: waits for the owner's go-ahead.
- Pushed on the owner's "Yes, push": `git push origin main <branch>` from the sandbox (main 2eaf072..c50a541, new branch). CI on c50a541: `main` run 43593079634 success, branch run 43593079719 success (read from the GitHub API).

## Code files created or changed
- Created: `src/atlas/llm/cache.py`, `src/atlas/cli/llm_cmd.py`, `docs/llm-cache.md`, `tests/unit/llm/test_llm_cache_key.py`, `tests/unit/llm/test_llm_cache_store.py`, `tests/unit/llm/test_llm_cache_gateway.py`, `tests/unit/cli/test_llm_cmd.py`, `tests/unit/core/storage/test_storage_llm_cache.py`
- Changed: `src/atlas/llm/gateway.py`, `src/atlas/llm/schemas.py`, `src/atlas/llm/cost.py`, `src/atlas/llm/__init__.py`, `src/atlas/core/errors.py`, `src/atlas/core/config.py`, `src/atlas/core/storage/db.py`, `src/atlas/core/storage/schema.py`, `src/atlas/cli/app.py`, `config/fund.yaml`, `config/profiles/paper.yaml`, `tests/unit/core/test_errors.py`, `tests/unit/core/test_config.py`, `tests/unit/core/storage/test_storage_db.py`, `tests/unit/core/storage/test_storage_schema.py`
