# Stage 13 — Data quality checks & DataView facade
**Phase:** 1 · **Size:** M · **Depends on:** 07–12 · **Spec refs:** §3.2, §3.6

## Goal
One door to all data: `DataView(as_of)`, which guarantees point-in-time correctness and refuses data that failed quality checks.

## In scope
- Quality checks (`data/quality.py`), each returning `QualityIssue{table, symbol, date, check, severity, detail}`:
  missing sessions, stale price (unchanged ≥ 5 sessions with volume > 0), return outlier (|r| > 8σ with no corporate action), OHLC inconsistency, zero/negative prices, split mismatch (from Stage 07), fundamentals jumps (> 10× q/q), macro gaps
- `quality_report(as_of) -> DataFrame` + persisted `data_quality_issues`; severity CRITICAL blocks the symbol for that day
- `DataView` (read-only, as_of bound):
  `prices(symbols, lookback) / returns(...) / adv(...) / fundamentals(symbol) / fundamentals_panel(symbols) / macro(series, lookback) / news(symbol, lookback) / sentiment(symbol) / events(...) / universe(name) / tradable() / instrument(symbol)`
  Every method applies `pit_filter` and drops CRITICAL-flagged symbols; results are cached per (as_of, args) in an LRU
- `DataView.advance(new_as_of)` returns a new view (immutable)
- Exhaustive look-ahead test suite: inject future rows into every table and assert none leak

## Out of scope
Feature computation (Phase 2).

## Files
`src/atlas/data/quality.py`, `src/atlas/data/view.py`, `tests/unit/data/test_quality.py`, `tests/unit/data/test_view_pit.py`

## Tasks
1. Each check, with a synthetic failing case.
2. Persist issues; CRITICAL-block logic.
3. DataView methods delegating to the PIT queries of Stages 07–12.
4. Leak suite (parametrised over all methods).
5. Caching + immutability tests.

## Acceptance criteria
- The leak suite passes for every DataView method; blocked symbols are absent; strategy and agent code needs nothing else.

## Verify
`uv run pytest tests/unit/data -q`
