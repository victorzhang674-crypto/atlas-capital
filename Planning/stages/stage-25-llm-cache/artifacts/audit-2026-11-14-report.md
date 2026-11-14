# Stage 25 — post-DONE audit report (2026-11-14)

Owner's words: "please fully audit this stage". Followed by a final audit, below. Self-audit in the same session, inline (no subagents). Scope: the whole Stage 25 diff (`2eaf072..d1ef62d`, 37 files) read against the brief, the roadmap row, spec §3 principle 3 ("A backtest re-run in replay mode makes zero API calls and gives identical results"), the Stage 24 backlog note and Project.md; then probe tests, fixes, a mutation run over `cache.py` + `gateway.py`, and full re-verification.

**Outcome:** the acceptance criteria still hold and the stage stays `DONE`. Five defects were found and fixed test-first (A1–A5); one test gap was closed (A6). Every finding had a failing test before its fix ([audit-probes-red.txt](audit-probes-red.txt): 9 failed) and passes after it ([audit-probes-green.txt](audit-probes-green.txt)).

## Findings

| # | Severity | Finding | Failure it caused | Fix | Test |
|---|---|---|---|---|---|
| A1 | **Medium** — breaks spec §3 principle 3 | (a) Two threads that missed the same key both called the provider, and each used its own answer while only the first was stored. (b) In `record` mode every repeat of a request called the provider again and replaced the stored answer. | A replay did not give the results the recording run saw: a `record` run that saw `one, two` replayed as `two, two`; a threaded `read_write` run could return `two` to a caller whose replay would return `one`. Stage 28's parallel fan-out would hit (a). | Calls with the same key are serialised (64 striped locks in the gateway); in `record` mode a gateway re-records each key once, then replays it. Collision recorded: the brief's literal "record (always call + store)" yields to the spec (Rule 3); `record` still calls and replaces for every distinct request. | `test_concurrent_identical_misses_call_once_and_agree` (30/30 stable runs), `test_a_record_run_replays_identically`, `test_record_re_records_each_key_once_per_gateway`; `test_record_calls_again_and_replaces_the_stored_answer` rewritten (my Stage 25 test encoded the old reading) |
| A2 | **Medium** | The output token cap was not in the key (my D1), although the provider receives it. | An answer truncated at a small cap (`finish_reason: length`) was replayed for a larger cap for ever. | `max_tokens` is in the key (`KEY_VERSION` 2). A change of `per_agent_call_max_tokens` is now a miss, which the replay rule ("valid only while prompts and data are unchanged") covers. | `test_a_truncated_answer_is_not_replayed_under_a_larger_cap`, `test_the_token_cap_changes_the_key`; `test_labels_and_caps_do_not_change_the_key` renamed `…_and_the_timeout_…` (the test itself was wrong on the cap) |
| A3 | Low | `temperature=1` and `temperature=1.0` gave different keys (JSON `1` vs `1.0`). | A spurious miss (a `replay_only` run would fail with `CacheMiss`). | The key holds `float(temperature)`. | `test_an_int_temperature_hits_the_float_recording`, `test_an_int_temperature_is_the_same_value_as_its_float` |
| A4 | Low | `atlas llm cache …` on a database DuckDB cannot open (corrupt, or held by another process) ended in a traceback; the handler only caught `AtlasError` and was marked `pragma: no cover`. | Unhelpful crash instead of exit 1 with a message. | `_open` also catches `duckdb.Error`; the pragma is gone. | `test_an_unreadable_database_exits_1_without_a_traceback` |
| A5 | Low | `LLMResult.attempts` was relaxed to `ge=0` for hits, so any result could claim 0 attempts. | A paid result with `attempts=0`, or a hit with `attempts>0`, validated. | `attempts == 0` exactly when `cache_hit` (model validator). | `test_attempts_and_cache_hit_agree` (3 cases) |
| A6 | Test gap | Mutation run 1: the `llm.cache_hit` / `llm.cache_miss` log lines, the agent/step in the `CacheMiss` message and unique hit `call_id`s were untested (a constant `call_id` would have broken the DuckDB primary key on the second hit). | — | Tests only. | `test_a_hit_is_logged_with_what_it_saved`, `test_a_replay_miss_is_logged_and_names_the_call`, `test_every_hit_gets_its_own_ledger_row` |

Efficiency (no behaviour change): `LLMCache.stats()` on DuckDB now aggregates in SQL instead of reading every stored JSON body into memory (the CLI `stats` would have loaded the whole cache). The memory and DuckDB stores still give identical stats (`test_stats`, parametrised over both).

## Checked and found sound (no change)
- Key covers everything the providers send that changes the answer: `openai_compat.py` sends model, messages, `max_tokens` (now keyed), `temperature`, `thinking`, tools, `response_format` + schema text; the Anthropic provider sends a subset. `metadata` is never sent, so leaving `agent`/`step` out of the key is safe.
- The data-policy text is inside the hashed messages: a wording change voids recordings and `replay_only` fails loudly (correct).
- Hits skip the budget but never bypass it for new spend: a request above the token cap was never recorded (it raised), so it misses and `_run` raises `BudgetExceeded`.
- Only validated answers are stored; failures, invalid output and corrupt or schema-drifted entries raise `LLMError` with no silent provider call.
- `CacheMiss` is an `LLMError`, so callers that fail safe on `LLMError` hold (spec principle 6).
- No `datetime.now()` outside `atlas.core.clock`; no network in tests; no secrets in files (scan clean).
- `paper` profile sets `off`; YAML's unquoted `off` → `False` is handled.

## Left open (Backlog)
- Two **processes** recording into one lake at once are not coordinated (the first stored answer wins; the other process used its own). One backtest per lake at a time is the expected use.
- The provider identity in the key is the provider's name (`openai_compatible`), not its `base_url`: pointing `base_url` at another OpenAI-compatible vendor that serves the same model id would share recordings. Model ids differ across vendors today.
- Compressed blobs (brief: optional) and `prompt_version` in Stage 26 stay in the Backlog as before.

## Evidence
- Probes red before the fixes: [audit-probes-red.txt](audit-probes-red.txt) (9 failed); green after: [audit-probes-green.txt](audit-probes-green.txt) (14 passed, incl. the two acceptance tests)
- Mutation (mutmut 3.8.1, `cache.py` + `gateway.py`, 967 mutants): run 1 **865** killed, run 2 after the A6 tests **910**; the 57 survivors are Stage 24 message/log text and equivalents, and in Stage 25 code only message-text casing and `astimezone(None)` on an aware UTC clock · [summary](audit-mutation-summary.txt), [run 1](audit-mutation-survivors-run1.txt), [run 1 new-code diffs](audit-mutation-survivors-run1-new-code.txt), [run 2](audit-mutation-survivors-run2.txt), [setup](audit-mutmut-setup.cfg)
- Coverage: `cache.py`, `gateway.py`, `cost.py`, `schemas.py`, `cli/llm_cmd.py` 100% · [audit-coverage.txt](audit-coverage.txt)
- `make check` → exit 0 (ruff clean, 402 files formatted, mypy clean on 159 files, 4359 passed + 3 timing) · [audit-make-check.txt](audit-make-check.txt)
- `uv run pytest -q` → 4362 passed, 71 deselected, exit 0 · [audit-pytest.txt](audit-pytest.txt)
- Verify `uv run pytest tests/unit/llm -q` → 281 passed, 2 deselected, exit 0 · [audit-verify-pytest-unit-llm.txt](audit-verify-pytest-unit-llm.txt)

## Final audit (2026-11-14)
Owner's words: "please have a final audit and explicitly state whether it is completed or not." The final code was re-read against the brief, every acceptance criterion and In-scope item was re-checked, the records were cross-checked (checkbox, status lines, `ALL CRITERIA VERIFIED`, every link in the stage records resolves, the Stage 26 folder holds only `brief.md`), and verification was re-run on the final tree.

| # | Severity | Finding | Fix | Test |
|---|---|---|---|---|
| A7 | Low (performance; answers were right) | The A1 fix serialised calls through 64 **striped** locks, so two *different* requests whose keys shared a stripe waited for each other: with 10 parallel agent calls (Stage 28's fan-out) that happens about half the time, silently turning parallel calls into sequential ones. | One lock per key, created on demand and removed when its last caller leaves (`LLMGateway._key_lock`); identical calls still wait for each other. | `test_different_requests_run_in_parallel` (two keys that share a stripe meet at a barrier: failed with `BrokenBarrierError` before the fix), `test_key_locks_are_released_after_use` · red: [final-audit-probe-red.txt](final-audit-probe-red.txt) |

Final-audit evidence (all on the final tree):
- `make check` → exit 0 (ruff clean, 402 files formatted, mypy clean on 159 files, 4361 passed + 3 timing) · [final-audit-make-check.txt](final-audit-make-check.txt)
- `uv run pytest -q` → **4364 passed, 71 deselected**, exit 0 · [final-audit-pytest.txt](final-audit-pytest.txt)
- Verify `uv run pytest tests/unit/llm -q` → **283 passed, 2 deselected**, exit 0 · [final-audit-verify-pytest-unit-llm.txt](final-audit-verify-pytest-unit-llm.txt)
- Acceptance and audit tests → 16 passed · [final-audit-acceptance.txt](final-audit-acceptance.txt); the audit tests 30/30 stable runs · [final-audit-stability.txt](final-audit-stability.txt)
- Coverage 100% on `cache.py`, `gateway.py`, `cost.py`, `schemas.py`, `cli/llm_cmd.py` · [final-audit-coverage.txt](final-audit-coverage.txt)
- Mutation (`cache.py` + `gateway.py`, 960 mutants) → 906 killed; none of the 54 survivors is in the lock code; the Stage 25 survivors are message-text casing and `astimezone(None)` on an aware UTC clock · [final-audit-mutation-summary.txt](final-audit-mutation-summary.txt), [survivors](final-audit-mutation-survivors.txt)
- CI on the audit push 168b858: `main` run 43607690087 success, branch run 43607690088 success

**Verdict: Stage 25 is COMPLETE (`DONE`).** Every acceptance criterion and In-scope item of the brief is met with evidence; no defect found by either audit remains open; the items left in the Backlog (two recording processes on one lake, base URL not in the key, optional compressed blobs, `prompt_version` in Stage 26) are outside the brief's scope or explicitly optional.
