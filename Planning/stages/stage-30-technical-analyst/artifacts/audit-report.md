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

(filled in below)
