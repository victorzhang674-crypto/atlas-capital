# Stage 30 — self-audit (2026-11-25)

The owner asked to "plan, execute and audit stage 30". This is the audit, done by me in this
chat (no reviewer subagent, the owner's standing preference), after the first full
verification run and before the stage was marked DONE. Every finding below was either fixed
test-first in this chat or recorded with its reason.

## 1. The brief, line by line

| Brief line | Where | Evidence |
|---|---|---|
| Goal: interprets precomputed technical features, never computes them | `TechnicalAnalyst` (LLM, quick tier) reads the evidence of `technical_tools`; every number comes from Stage 15 features, the Stage 21 regime or the bars | prompt: "You interpret these numbers; you never compute new indicators"; "Use the numbers as shown; do not compute new ones" (golden file) |
| Tool `get_technical_snapshot` (Stage 15 values + percentiles + regime context) | `technical_snapshot` + `regime_item` | `test_snapshot_items_show_values_and_percentiles`, `test_every_registered_feature_is_in_exactly_one_group`, `test_the_regime_item_from_a_given_state`, `test_without_a_given_state_the_regime_is_computed` |
| Tool `get_price_summary` (recent OHLC table, 52w range, key MAs) | `price_summary` (plus levels and stop references, which the prompt's "levels" and "invalidation level" need) | `test_the_ohlc_table_is_split_adjusted`, `test_the_52_week_range`, `test_moving_averages_are_the_stage_15_features`, `test_levels_split_into_support_below_and_resistance_above`, `test_stop_references` |
| Prompt `technical_analyst.j2`: trend, momentum, volatility regime, levels, timing/entry, invalidation level | six summary sections (`SECTIONS`) enforced by `check_output` | golden prompt; `test_a_report_problem_gets_a_repair_turn[…Momentum…]` |
| Output `AnalystReport` + `suggested_stop_pct` and `timing` (now / wait for pullback / avoid) in signals | `TechnicalReport(AnalystReport)` with `TechnicalSignal(Signal)` (decision D1) | `tests/unit/core/types/test_types_technical.py` (11 tests) |
| Files: `agents/research/technical.py`, prompt, tests | as listed, plus `technical_tools.py` (Stage 29's pattern), `checks.py` (shared), config, core types, docs | worklog |
| AC: schema-valid output with evidence | replay test parses the recorded answer into `TechnicalReport`; citations ok | `test_the_recorded_aapl_run_replays_offline` |
| AC: FakeLLM tests | 31 tests in `test_technical.py` | verify-brief |
| AC: a recorded live fixture (exported cache + lake slice under `tests/fixtures/recorded/`) | `tests/fixtures/recorded/stage-30-technical-aapl-2024-05-01/` | `fixture-compare.txt` (11 of 11 items equal to the main lake), `live-run.txt` |
| Verify: `uv run pytest tests/unit/agents/research/test_technical.py -q` | — | `verify-brief.txt` |

## 2. Point in time

- Every read goes through `DataView` (prices, split factors, membership, the features, `regime_at`).
- `test_rows_known_later_never_show` (four symbols): a lake with bars to 2024-03-15 and a 4:1
  split known after the as-of gives exactly the clean lake's evidence;
  `test_a_later_view_sees_the_later_rows` shows the later rows are real (the split rescales the
  old bar when it is known).
- `test_no_item_is_timed_after_the_cutoff`: every item of every member is timed before the
  cutoff. Price items carry their bars' `available_at`; computed items the cutoff − 1 µs.
- A regime of another decision day is refused (`ConfigError`) before any LLM call.

## 3. Fail-safe paths

- Blocked symbol, symbol without bars → `DataError`, failed trace, no LLM call (tests).
- Snapshot or regime that cannot be computed → `gap:` item, citable (tests).
- Undefined values → "n/a" (short history, stale symbol), never a guess.
- Stop rules (D3): a directional signal without a stop, a stop outside [0.01, 0.25], a FLAT
  signal with a stop or a timing other than AVOID → repair turn, then `low_quality`
  (confidence capped). A stop written as a percentage (8) fails the schema and the gateway
  repairs it (`test_a_schema_violation_is_repaired_by_the_gateway`).

## 4. Findings

| # | Finding | Kind | Resolution |
|---|---|---|---|
| A1 | `make check` failed on the first verification run: 13 mypy errors in `technical_tools.py` (pandas label indexing typed `Hashable`; `float \| None` not narrowed) | defect (type check) | Fixed: positional indexing (`_at`), narrowing with `_value`; no `type: ignore` left except one on `pd.Timestamp(...)`. mypy clean on 191 files |
| A2 | The 52-week window of a stale symbol ended at the latest session, so ATVI after delisting read "only 248 sessions of history" | defect (found on the main lake) | Fixed test-first: the window ends at the symbol's last bar; short history vs missing bars told apart |
| A3 | `get_technical_snapshot` for a symbol with no bars returned all n/a instead of an error | defect (found by mutation testing) | Fixed: `DataError`; the test fails on the previous commit |
| A4 | A symbol whose bars are older than the window failed with "no bars known for X on D", which reads as if it never traded | message | Now "no bars known for X in the 252 sessions to D" |
| A5 | `_feature_values` took an argument it never used | dead code | Deleted |
| A6 | A peer `get_technical_snapshot` call returns the regime item again (same id) | checked | The evidence ledger ignores an identical repeat; test `test_a_peer_snapshot_repeats_the_regime_item_without_conflict` |
| A7 | Without `inputs["regime"]`, each run computes `regime_at` (15 s on the main lake) and so does each peer snapshot call | performance | Kept (correct, deterministic); DataView has no public memo and reaching into its private cache would be worse. Backlog → 35: pass `FundState.regime` |
| A8 | The checkpoint allow-list (`state_types()`) is built from annotations naming `AnalystReport` only | latent (Stage 35) | Backlog → 35 with the fix and the test to add |
| A9 | The first live recording needed a repair turn (a SHORT signal without a stop) | prompt | The prompt now says every LONG/SHORT signal carries a stop; re-recorded in one call. A later live test run still needed one repair: the model is not deterministic, and the repair turn exists for that |
| A10 | For a stale symbol the Stage 15 snapshot reads the latest session, so most features are n/a | limitation (Stage 15) | Fail-safe; documented in `docs/agents.md`; Backlog → 15 |
| A11 | Mutation testing: 111 survivors after the first runs | test gaps | 11 edges closed by new tests; 997 of 1,048 killed (95.1%), the other 51 classified (47 equivalent, 3 unreachable, 1 deleted code): [mutation-summary.md](mutation/mutation-summary.md) |
| A12 | The first gate run read `config/fund.yaml` while I edited it | process | Discarded; gate re-run on a clean extract of `a2e90f3` (5571 passed) |
| A13 | Tasks 5–7's tests were written before the code but not run red on their own | process (recorded honestly) | Mutation testing (A11) shows they bite |

## 5. Real data

On the main lake (read-only, [main-lake-check.txt](main-lake-check.txt)): NVDA after its 10:1
split (levels on the post-split basis), GEV and KVUE (young), JPM in the March 2020 crash
(CRISIS, risk scalar 0.25), ATVI after delisting and SNDK (stale last bar named), AAPL today.
AAPL on 2024-05-01: the fixture slice and the main lake give the same 11 items.

## 6. Verdict

Every acceptance criterion is met with evidence (section 1); the defects found are fixed with
tests; what is left is recorded in the Backlog with its expected impact. The final verification
run on the audited code is in [verification.md](../verification.md).
