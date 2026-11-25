# Stage 30 — Technical analyst agent · Plan

- **Brief:** [brief.md](brief.md)
- **Status:** IN PROGRESS
- **Kick-off (owner's words):** "please plan, execute and audit stage 30." Planned, executed and audited in one go (inline TDD, no subagents), the owner's standing preference.
- **Gate check:** Stage 29 is DONE ([verification.md](../stage-29-fundamental-analyst/verification.md) says ALL CRITERIA VERIFIED, follow-up 7 too); `uv run pytest -q` → see [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt) (2026-11-25)

## Collisions with the overall plan
None. Roadmap row 30 (S, depends on 15, 28); spec §6 "Technical Analyst | LLM (reads computed features) | quick | `AnalystReport`"; spec §7 `AnalystReport{…, signals[{name, direction, strength, evidence_ids}], …}`.

The brief adds `suggested_stop_pct` and `timing` "fields in signals". The spec's contract is kept unchanged: the analyst's output is `TechnicalReport`, a subclass of `AnalystReport` whose signals are `TechnicalSignal`, a subclass of `Signal` with the two extra fields (decision D1). Every consumer that takes an `AnalystReport` takes it. Not a collision: nothing the spec states changes.

## Decisions (recorded in PROGRESS.md › Decisions log)

- **D1 Output contract.** `atlas.core.types.research` gains `Timing` (StrEnum `NOW` / `WAIT_FOR_PULLBACK` / `AVOID`), `TechnicalSignal(Signal)` (+ `timing: Timing`, + `suggested_stop_pct: float | None`, a fraction of the last close in (0, 1): 0.08 = a stop 8% below the last close for a LONG view, 8% above for a SHORT) and `TechnicalReport(AnalystReport)` (`signals: tuple[TechnicalSignal, ...]`). Rejected: optional fields on `Signal` itself. That would change every analyst's output schema, so the Stage 29 recording (whose request holds the schema) would stop replaying, and it would offer fundamental signals fields that mean nothing for them. The graph's checkpoint allow-list (`state_types()`, Stage 28) is built from the state's annotations and will not list the subclasses; wiring the analysts in is Stage 35's (owner, Stage 29), so this goes to the Backlog → 35 with the fix (allow-list the subclasses, test a checkpoint round trip).
- **D2 Signals and sections.** Signals exactly `trend`, `momentum`, `volatility`, `levels`. Summary sections, in order: "Trend:", "Momentum:", "Volatility regime:", "Levels:", "Timing and entry:", "Invalidation:". The brief's prompt items map one to one (trend state, momentum, volatility regime, levels, timing/entry considerations, invalidation level).
- **D3 Timing and stop rules (`check_output`, repairable).** A LONG or SHORT signal needs a `suggested_stop_pct` within [`min_stop_pct`, `max_stop_pct`] (config); a FLAT signal has no stop and its timing is `AVOID` (there is nothing to enter). The schema itself only bounds the stop to (0, 1), so "8" for 8% fails the schema and the gateway's schema repair asks again.
- **D4 Regime context.** The snapshot carries the fund's market regime (Stage 21 `RegimeState`: vol and trend regimes, HMM P(turbulent), macro quadrant, stress flag, risk scalar and the readings), evidence `regime:<day>:market`. Source: `ctx.inputs["regime"]` when the caller has one (the fund graph keeps one per day in `FundState.regime`, Stage 28); otherwise `regime_at(ctx.view)` (point in time, through `DataView`). A regime for another day is a `ConfigError` (look-ahead guard). A regime that cannot be computed becomes a `gap:` item, never a guess. The recorded fixture stores the regime computed on the main lake (`regime.json`): the inputs of `regime_at` (every member's prices since 2009 and FRED vintages) are far too large for a test fixture.
- **D5 Tools read precomputed features only.** `get_technical_snapshot` = Stage 15 `build_snapshots` (values, own-history and universe percentiles) grouped into `trend`, `momentum`, `volatility`, `levels`, `participation` (every registered feature in exactly one group, tested), plus the regime item. `get_price_summary` = recent adjusted OHLC table, 52-week (252-session) range with dates and position, the key moving averages (Stage 15 `sma_20/50/200` values), support/resistance candidates (20- and 63-session swing lows/highs, 52-week high/low, the averages) and stop references (ATR(14) from Stage 15 and k×ATR, distances to the swing lows/highs), each as a percentage of the last close. Prices are split- and dividend-adjusted (Stage 15 `PricePanel` convention) so a level is comparable with today's close; a range with fewer sessions than asked says so.
- **D6 Shared report checks.** `sentences`, `uncited_numbers`, `missing_sections` and the problem quoting move from `fundamental.py` into `atlas.agents.research.checks` (sections passed in); `fundamental.py` keeps its public names as thin wrappers, so its tests, prompt and recording are unchanged. Stages 31–34 reuse the module.
- **D7 Recorded fixture.** AAPL as of 2024-05-01 (Stage 29's day, so the two reports can be compared): slice of the S&P 500 members on the day + AAPL + SPY (the snapshot's cross-section and the beta benchmark), tables `prices_daily`, `corporate_actions`, `index_membership`, `instruments`, since a date early enough that every feature of the last 252 sessions is defined (checked by the main-lake vs slice comparison, item by item), plus `regime.json`.
- **D8 Evidence ids and times.** `tsnap:<SYM>:<session>:<group>`, `regime:<day>:market`, `pxs:<SYM>:<session>:<ohlc|range|ma|levels|stops>`, `gap:<SYM>:<day>:<tool>`. Price-summary items carry the latest `available_at` of the bars they use; snapshot, regime and gap items are computed at the decision moment (cutoff − 1 µs, Stage 29's convention).

## Tasks (execute in order; tick only after the task's tests have run and passed in this chat)

### Task 1 — Output contract (D1)
- Files: modify `src/atlas/core/types/research.py`, `src/atlas/core/types/__init__.py` · test `tests/unit/agents/research/test_technical_contracts.py`
- [ ] Failing tests: `Timing` values; `TechnicalSignal` needs a timing, stop in (0, 1) or None, rejects extra keys, bool, NaN; `TechnicalReport` is an `AnalystReport`, holds only `TechnicalSignal`s, JSON round trip; `cap_confidence` keeps the subclass; the core types package exports them
- [ ] Run → FAIL (import error) · implement · run → PASS

### Task 2 — Config `agents.technical`
- Files: modify `src/atlas/core/config.py`, `config/fund.yaml` · test `tests/unit/agents/research/test_technical_config.py`
- [ ] Failing tests: defaults (`ohlc_sessions` 10, `range_sessions` 252, `swing_sessions` (20, 63), `atr_stop_multiples` (2.0, 3.0), `min_stop_pct` 0.01, `max_stop_pct` 0.25); bounds (positive, ascending, unique, `min_stop_pct < max_stop_pct < 1`, swing windows ≤ range); `fund.yaml` values equal the defaults; every key commented in `fund.yaml`
- [ ] Run → FAIL · implement · run → PASS; config docs tests green

### Task 3 — Shared report checks (D6)
- Files: create `src/atlas/agents/research/checks.py`; modify `src/atlas/agents/research/fundamental.py` · test `tests/unit/agents/research/test_research_checks.py`
- [ ] Failing tests: `missing_sections(summary, sections)`, `quote_sentences` (5 listed, 120 characters, "and N more"), `sentences`, `uncited_numbers` behave as Stage 29's
- [ ] Run → FAIL · move the code · run → PASS; every Stage 29 test still passes (golden prompt and recorded replay unchanged)

### Task 4 — Test world
- Files: create `tests/unit/agents/research/technical_world.py`, extend `conftest.py`
- [ ] A clean lake: 24 S&P 500 members with OHLC random walks of distinct shapes (UPP steady uptrend, DWN downtrend, SPL a 2:1 split inside the 52 weeks, NEW listed 80 sessions ago), SPY (benchmark, not a member), 600 sessions to 2024-02-29; a dirty copy adds bars to 2024-03-15 and a UPP 4:1 split known only after the as-of
- [ ] A smoke test that the world builds and `build_snapshots` reads it

### Task 5 — `get_price_summary`
- Files: create `src/atlas/agents/tools/technical_tools.py` · test `tests/unit/agents/research/test_technical_tools.py`
- [ ] Failing tests (hand-computed on the world): OHLC table rows and adjustment (SPL's pre-split bars halved); 52-week high/low with dates and position; MA levels equal Stage 15's `sma_*` values; swing levels; ATR stops (k×ATR / close) and swing-stop distances; a short history says "only N sessions"; a stale last bar is named; blocked symbol and no bars → `DataError`; ids, sources and times (D8)
- [ ] Run → FAIL · implement · run → PASS

### Task 6 — `get_technical_snapshot` and the regime item
- Files: modify `technical_tools.py` · test `test_technical_tools.py`
- [ ] Failing tests: every registered feature is in exactly one group; each line shows value in its unit, own and universe percentiles (or "n/a"); USD features say "no universe percentile"; the regime item from a given `RegimeState`, from `regime_at` when absent, `ConfigError` for another day's regime, a `gap:` item when `regime_at` fails
- [ ] Run → FAIL · implement · run → PASS

### Task 7 — `prefetch` and the tools
- Files: modify `technical_tools.py`, `src/atlas/agents/tools/__init__.py` docstring · tests `test_technical_tools.py`, `test_technical_tools_pit.py`
- [ ] Failing tests: prefetch = price summary + snapshot + regime; a snapshot or regime `DataError` becomes a gap; a price-summary `DataError` propagates; the two `Tool`s (names, args, descriptions, version 1) return the same evidence for a peer; point in time: the dirty lake gives exactly the clean lake's evidence, and every item's time is at or before the cutoff
- [ ] Run → FAIL · implement · run → PASS

### Task 8 — The prompt and `TechnicalAnalyst`
- Files: create `src/atlas/agents/prompts/technical_analyst.j2`, `src/atlas/agents/research/technical.py`; modify `src/atlas/agents/research/__init__.py` · tests `tests/unit/agents/research/test_technical.py` (the brief's Verify file), golden `golden/technical_analyst_prompt.txt`
- [ ] Failing FakeLLM tests: golden prompt; every prefetched item in the prompt; the two tools offered, prompt version string; a cited report validates (status ok, evidence returned, `TechnicalReport` JSON in the trace); symbol normalised and required; regime input checked; each rule of D2/D3 and Stage 29's (ticker, as_of, analyst, signals, sections, uncited numbers) gives a problem and the repair turn; problems left → `low_quality`; unknown evidence id → citation repair; blocked symbol → failed trace; gap shown and citable; the model may look at a peer; memories reach the prompt point in time
- [ ] Run → FAIL · implement · run → PASS

### Task 9 — Recorded live fixture (D7)
- Files: create `Planning/stages/stage-30-technical-analyst/artifacts/record_fixture.py`, `tests/fixtures/recorded/stage-30-technical-aapl-2024-05-01/` (`lake/`, `regime.json`, `llm_cache.jsonl`, `expected_report.json`, `run.json`, `README.md`), `tests/unit/agents/research/recorded_technical.py`, `tests/unit/agents/research/test_technical_live.py` (marker `llm`)
- [ ] Steps: `members` → `regime` (main lake, `regime_at`) → `slice` (main lake → fixture) → `compare` (AAPL prefetch on the main lake vs the slice, item by item, equal) → `live` (DeepSeek quick tier on a copy of the slice, cache `read_write`) → `export`
- [ ] Failing replay test in `test_technical.py`: no keys, `OfflineProvider`, `replay_only`, the recorded report equals `expected_report.json`, citations ok, no uncited number, signals exact, stops and timings obey D3, cost 0
- [ ] Live test run once with the key (`-m llm -s`), output saved

### Task 10 — Docs and real-lake check
- Files: modify `docs/agents.md` ("The technical analyst"), `docs/data-limitations.md` if a limit is found
- [ ] Docs tests green
- [ ] Prefetch on the main lake (read-only) for several symbols and days (AAPL, a split name, a young listing, a 2020 crash day), output saved and read

### Task 11 — Verification, audit, records, git
- [ ] `uv run pytest tests/unit/agents/research/test_technical.py -q`, `uv run pytest -q`, `make check`, `make test-integration`, coverage of the new modules, mutation testing of the new modules (survivors classified)
- [ ] Self-audit (`artifacts/audit-report.md`): every brief line against evidence, point in time, fail-safe paths, units, Rule 8 corners; fix findings test-first
- [ ] `verification.md`, `worklog.md`, `PROGRESS.md` (status, handoff, Decisions log, Backlog)
- [ ] Commit `stage-30: Technical analyst agent`, merge into `main`, re-run on the merged result, push, CI on the pushed commit
