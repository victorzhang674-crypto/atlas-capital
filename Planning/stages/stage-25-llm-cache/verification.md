# Stage 25 — LLM record/replay cache · Verification evidence

Output of commands run in this stage's chat (Project.md Rule 1). Raw output is under `artifacts/`.

#### Verification evidence — Stage 25 — 2026-11-14
- Commands run (this chat):
  - Gate: `uv run pytest -q` → 4244 passed, 71 deselected, exit 0 · raw: [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt)
  - `uv run pytest -q` (serial, after all changes) → **4348 passed, 71 deselected** in 422 s, exit 0 (deselected: `live` / `llm` / `integration` markers only) · raw: [artifacts/pytest.txt](artifacts/pytest.txt)
  - `make check` → exit 0 · raw: [artifacts/make-check.txt](artifacts/make-check.txt)
    - `uv run ruff check .` → All checks passed!
    - `uv run ruff format --check .` → 401 files already formatted
    - `uv run mypy src/atlas` → Success: no issues found in 159 source files
    - `pytest` (parallel) → 4345 passed; timing budgets alone → 3 passed
  - Brief's Verify `uv run pytest tests/unit/llm -q` → **268 passed, 2 deselected** (the 2 are the `llm`-marked live DeepSeek test of Stage 24), exit 0 · raw: [artifacts/verify-pytest-unit-llm.txt](artifacts/verify-pytest-unit-llm.txt)
  - Coverage (`tests/unit/llm`, `tests/unit/cli/test_llm_cmd.py`, `test_storage_llm_cache.py`): `llm/cache.py` 100%, `llm/gateway.py` 100%, `llm/cost.py` 100%, `cli/llm_cmd.py` 100% · raw: [artifacts/coverage.txt](artifacts/coverage.txt)
  - Mutation check (mutmut 3.8.1, `src/atlas/llm/cache.py`): 211 / 245 killed before the audit tests, **224 / 245** after; the 21 survivors are equivalent (reviewed in [worklog.md](worklog.md)) · raw: [mutation-summary.txt](artifacts/mutation-summary.txt), [run 1](artifacts/mutation-survivors-run1.txt), [run 2](artifacts/mutation-survivors-run2.txt)
  - Secret scan: the `DEEPSEEK_API_KEY` value of the main checkout's `.env` occurs in no worktree file (`grep -rlF`, `.venv*` and `.git` excluded) → no match
- Acceptance criteria (brief: "Running the same scripted agent twice in `read_write` makes 1 provider call; `replay_only` with a miss fails loudly"):
  - [x] Same scripted agent twice in `read_write` → 1 provider call — evidence: `test_the_same_scripted_agent_twice_in_read_write_makes_one_provider_call` (`len(fake.calls) == 1`, second result `cache_hit`); also across sessions on DuckDB: `test_a_recording_in_duckdb_replays_in_a_later_session` · raw: [artifacts/acceptance-tests.txt](artifacts/acceptance-tests.txt)
  - [x] `replay_only` with a miss fails loudly — evidence: `test_replay_only_with_a_miss_fails_loudly_and_never_calls` (`CacheMiss`, an `LLMError`, carrying the key; the provider was never called; `llm.cache_miss` warning logged), `test_the_configured_mode_applies_when_a_cache_is_given`
- Brief In-scope items (each checked against code and tests):
  - [x] Key = SHA-256 of (provider, model, canonical messages, schema name+version, temperature, tools, prompt template version) — `test_llm_cache_key.py`: `test_dict_order_is_irrelevant`, `test_a_prompt_template_version_bump_changes_the_key`, `test_everything_the_model_sees_changes_the_key` (9 cases), `test_schema_fingerprint_follows_the_json_schema_not_the_name`, pinned digests; + thinking mode (Stage 24 note)
  - [x] DuckDB table `llm_cache(key, created_at, model, request_json, response_json, usage)` — `test_storage_llm_cache.py` (+ `provider` column). Optional compressed blobs: not built (brief: "optional") → Backlog
  - [x] Modes `off`, `read_write`, `replay_only` (`CacheMiss`), `record` — `test_llm_cache_gateway.py` (mode tests), config default and paper profile in `test_config.py`
  - [x] Cache-hit cost = 0, recorded as a hit in the ledger; hit-rate stats — `test_a_hit_is_free_and_says_so`, `test_a_hit_writes_a_cache_hit_row_with_cost_zero` (`cache_counts() == (1, 1)`), `test_stats` (CLI hit rate 66.7%)
  - [x] CLI `atlas llm cache stats|export|prune --older-than` — `tests/unit/cli/test_llm_cmd.py` (20 tests)
  - [x] Docs note: replay valid only while prompts and data are unchanged — [docs/llm-cache.md](../../../docs/llm-cache.md) § "Replay is only valid while prompts and data are unchanged"
- Waivers: none
- Merge into `main`: fast-forward 2eaf072 → **d1ef62d** (2026-11-14, run in the owner's Terminal panel: the sandbox cannot write the main checkout); trees equal. Merged result: `uv run pytest -q` → 4348 passed, 71 deselected, exit 0 · `make check` → exit 0 (ruff clean, 401 files formatted, mypy clean, 4345 passed + 3 timing passed) · `git branch --merged main` lists the stage branch · raw: [merged-main-check.txt](artifacts/merged-main-check.txt), script [merged-main-check.sh](artifacts/merged-main-check.sh). pushed (next line)
- Pushed 2026-11-14 on the owner's go-ahead ("Yes, push"): `main` 2eaf072..c50a541 and the stage branch. CI (GitHub Actions, `make check` + `make test-integration`) on c50a541: **`main` run 43593079634 success**, stage-branch run 43593079719 success · raw: [ci-runs-c50a541.json](artifacts/ci-runs-c50a541.json)

#### Post-DONE audit — 2026-11-14
Owner: "please fully audit this stage". Five defects fixed test-first (A1 replay fidelity under concurrency and in `record` mode, A2 token cap in the key, A3 int/float temperature, A4 CLI crash on an unreadable database, A5 `attempts`/`cache_hit` consistency) and one test gap closed (A6) — [audit report](artifacts/audit-2026-11-14-report.md).
- Probes: 9 failed before the fixes → 14 passed after (incl. both acceptance tests) · [red](artifacts/audit-probes-red.txt), [green](artifacts/audit-probes-green.txt)
- `make check` → exit 0 (ruff clean, 402 files formatted, mypy clean on 159 files, 4359 passed + 3 timing) · [audit-make-check.txt](artifacts/audit-make-check.txt)
- `uv run pytest -q` → **4362 passed, 71 deselected**, exit 0 · [audit-pytest.txt](artifacts/audit-pytest.txt)
- Verify `uv run pytest tests/unit/llm -q` → **281 passed, 2 deselected**, exit 0 · [audit-verify-pytest-unit-llm.txt](artifacts/audit-verify-pytest-unit-llm.txt)
- Coverage 100% on every touched module; mutation `cache.py` + `gateway.py` 910 / 967 killed, survivors reviewed · [audit-coverage.txt](artifacts/audit-coverage.txt), [audit-mutation-summary.txt](artifacts/audit-mutation-summary.txt)
- Audit merge into `main`: fast-forward 2c4bbd7 → **23696eb** (2026-11-14, owner's Terminal panel); trees equal; merged result `uv run pytest -q` → 4362 passed, 71 deselected, exit 0 · `make check` → exit 0 (4359 + 3 passed) · `git branch --merged main` lists the stage branch · raw: [audit-merged-main-check.txt](artifacts/audit-merged-main-check.txt)
- CI on the audit push 168b858: `main` run 43607690087 success, branch run 43607690088 success
- Acceptance criteria re-checked: both still hold (`test_the_same_scripted_agent_twice_in_read_write_makes_one_provider_call`, `test_replay_only_with_a_miss_fails_loudly_and_never_calls`)

#### Final audit — 2026-11-14
Owner: "please have a final audit and explicitly state whether it is completed or not". One more defect fixed test-first (A7: the A1 striped locks made unrelated requests wait for each other; now one lock per key) — [final audit](artifacts/audit-2026-11-14-report.md#final-audit-2026-11-14).
- `make check` → exit 0 (4361 passed + 3 timing) · [final-audit-make-check.txt](artifacts/final-audit-make-check.txt)
- `uv run pytest -q` → **4364 passed, 71 deselected**, exit 0 · [final-audit-pytest.txt](artifacts/final-audit-pytest.txt)
- Verify `uv run pytest tests/unit/llm -q` → **283 passed, 2 deselected**, exit 0 · [final-audit-verify-pytest-unit-llm.txt](artifacts/final-audit-verify-pytest-unit-llm.txt)
- Acceptance criteria re-checked on the final tree: both hold · [final-audit-acceptance.txt](artifacts/final-audit-acceptance.txt)
- Final-audit merge into `main`: fast-forward 168b858 → **5748d17**; trees equal; merged result `uv run pytest -q` → 4364 passed, 71 deselected, exit 0 · `make check` → exit 0 (4361 + 3 passed) · `git branch --merged main` lists the stage branch · raw: [final-audit-merged-main-check.txt](artifacts/final-audit-merged-main-check.txt)
- Verdict: **COMPLETE** — every acceptance criterion and In-scope item verified; no open defect

#### Backlog follow-up — 2026-11-14
Owner: "can you complete what is left in the backlog?" The three Stage 25 backlog items, done test-first as post-DONE follow-ups (the fourth, `prompt_version` in Stage 26's prompt registry, is Stage 26 scope and was not started, Rule 4):
- F1 concurrent recorders: probe showed DuckDB lets one process open `atlas.duckdb` at a time ([followup-duckdb-two-process-probe.txt](artifacts/followup-duckdb-two-process-probe.txt)), so the remaining race is two gateways sharing a cache in one process; new answers are now stored by compare-and-set (`LLMCache.swap`, `Database.swap_llm_cache`) and a losing writer returns the stored answer (`llm.cache_adopted`); `record` replaces an unreadable recording (`LLMCache.delete`)
- F2 the provider's endpoint (OpenAI-compatible base URL) is part of the key (`KEY_VERSION` 3; digests re-pinned)
- F3 request/response bodies of at least `llm.cache.compress_min_bytes` (4096) bytes are stored zlib-compressed in DuckDB; every reader unpacks; corrupt bodies raise `LLMError`
- Probes: 12 of 14 failed before the code ([followup-probes-red.txt](artifacts/followup-probes-red.txt)); after: follow-up + audit + acceptance tests 37 passed ([followup-tests-green.txt](artifacts/followup-tests-green.txt)), 20/20 stable runs ([followup-stability.txt](artifacts/followup-stability.txt))
- `make check` → exit 0 (ruff clean, 403 files formatted, mypy clean on 159 files, 4388 passed + 3 timing) · [followup-make-check.txt](artifacts/followup-make-check.txt)
- `uv run pytest -q` → **4391 passed, 71 deselected**, exit 0 · [followup-pytest.txt](artifacts/followup-pytest.txt)
- Verify `uv run pytest tests/unit/llm -q` → **305 passed, 2 deselected**, exit 0 · [followup-verify-pytest-unit-llm.txt](artifacts/followup-verify-pytest-unit-llm.txt)
- Coverage: `cache.py`, `gateway.py`, `openai_compat.py`, `cli/llm_cmd.py` 100%; every new `db.py` line covered · [followup-coverage.txt](artifacts/followup-coverage.txt)
- Mutation (`cache.py` + `gateway.py`, 1107 mutants): 1021 → **1047** killed after the survivor tests; the follow-up survivors left are equivalent (zlib level, `"ASCII"` spelling, key text in error messages) · [followup-mutation-summary.txt](artifacts/followup-mutation-summary.txt)
- Acceptance criteria re-checked: both still hold

**Result:** ALL CRITERIA VERIFIED
