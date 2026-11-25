# Stage 30 — Technical analyst agent · Work log

Dated, in order. Branch `claude/stage-30-planning-audit-d30ece` (worktree `.claude/worktrees/stage-30-planning-audit-d30ece`), from `a2e90f3` (= `main`).

## 2026-11-25

### Start
- Kick-off (owner): "please plan, execute and audit stage 30."
- Read Project.md, PROGRESS.md (Stage 29 DONE, ALL CRITERIA VERIFIED incl. follow-up 7), the brief, roadmap row 30, spec §6/§7, Stage 15 (`build_snapshots`), Stage 21 (`regime_at`, `RegimeState`), Stage 28 (`FundState`, checkpoint allow-list), Stage 29 (analyst, tools, tests, fixture recorder).
- Fresh worktree: `uv sync --group dev` (new cache `$TMPDIR/uv-cache-s30`), exit 0.
- Copied the template; wrote [plan.md](plan.md) (decisions D1–D8, tasks 1–11). PROGRESS.md: Stage 30 `IN PROGRESS`.

### Gate (Rule 4)
- The first gate run was started in the worktree and then stopped: I edited `config/fund.yaml` while it ran, and tests read that file at run time, so its result could not count. Re-run on a clean `git archive` of `a2e90f3` with `PYTHONPATH=<extract>/src` (the log's second line shows atlas imported from the extract): `uv run pytest -q` → **5571 passed, 82 deselected, exit 0** · [artifacts/gate-pytest.txt](artifacts/gate-pytest.txt).

### Task 1 — output contract (D1)
- `Timing`, `StopFraction`, `TechnicalSignal(Signal)`, `TechnicalReport(AnalystReport)` in `atlas.core.types.research`, exported from `atlas.core.types`.
- Tests: `tests/unit/core/types/test_types_technical.py` (placed with the other core-type tests rather than under `agents/research/` as the plan said; the layout rule mirrors `src/`). Red: ImportError. Green after the change; one test was wrong (a plain `Signal` instance fails with "instance of TechnicalSignal", not "timing"; a dict without timing fails with "timing"): fixed the test, both cases now asserted.
- `test_types_roundtrip.py::test_every_model_has_a_sample` failed as it should (new exported models without a sample): added `TECH_SIGNAL` and a `TechnicalReport` sample. `tests/unit/core/types` + checkpoint tests: 318 passed.

### Task 2 — config `agents.technical`
- `TechnicalAnalystConfig` (core/config.py; `reject_bool` on the floats, ascending/unique checks, swing ≤ range, min < max) and `config/fund.yaml` block, every key commented.
- `tests/unit/agents/research/test_technical_config.py`: red (ImportError) → green; config tests 347 passed.

### Task 3 — shared report checks (D6)
- `src/atlas/agents/research/checks.py` (`sentences`, `uncited_numbers`, `missing_sections(summary, sections)`, `quote`, `MAX_LISTED`, `QUOTE_CHARS`); `fundamental.py` keeps `missing_sections(summary)`, `sentences`, `uncited_numbers`, `MAX_LISTED`.
- `test_research_checks.py`: red (ImportError) → green. All research tests 281 passed, including Stage 29's golden prompt and recorded replay (unchanged request).

### Task 4 — test world
- `tests/unit/agents/research/technical_world.py`: 24 members (UPP, DWN, SPL with a 2:1 split on 2023-11-15, NEW listed 80 sessions ago, M01–M20) + SPY, 600 sessions to 2024-02-29; dirty copy with bars to 2024-03-15 and a 4:1 UPP split on 2024-03-08. Probe: snapshots and `regime_at` both compute on it (regime NORMAL / DOWN, 0.14 s).
- Found while writing the PIT test: my first dirty bars doubled UPP overnight, and the Stage 13 quality check (rightly) blocked UPP in a later view. The future walk now continues from each symbol's last close.

### Tasks 5–7 — tools and prefetch
- `src/atlas/agents/tools/technical_tools.py`: `price_summary`, `technical_snapshot`, `regime_item`, `given_regime`, `prefetch`, `fmt_feature`, `FEATURE_GROUPS`, the two `Tool`s.
- Tests `test_technical_tools.py` (25) and `test_technical_tools_pit.py` (6), expected numbers computed in the test from the raw bars (split adjusted by hand). **Honest note:** these tests were written before the code, but I did not run them on their own before writing the module, so their red step was only the missing module. Mutation testing (Task 11) checks that they bite.
- Read the evidence texts on the world and fixed two wordings: "SMA50 is below SMA200 by -3.87%" → "SMA50 is 3.87% below SMA200"; "inflation gap n/a points" → "n/a". Later: the prompt's data block puts each item on one line, so the OHLC rows are joined by "; " (they ran together).

### Task 8 — prompt and analyst
- `src/atlas/agents/prompts/technical_analyst.j2` v1, `src/atlas/agents/research/technical.py` (`TechnicalAnalyst`, `technical_registry`, `stop_problems`, `SIGNALS`, `SECTIONS`, `MEMORY_QUERY`), exported from `atlas.agents.research`; golden prompt `tests/unit/agents/research/golden/technical_analyst_prompt.txt`.
- `test_technical.py`: 30 tests; three test mistakes fixed on the first run (`ToolRegistry.names` is a property; a hand-made `RegimeState` with `data_date == as_of` is invalid; the stop problem text carries its unit hint). Agents + core tests: 1682 passed.

### Commit
- `dd90db7` "stage-30 (part 1): technical analyst contract, config, tools, analyst and tests" (staged diff reviewed: 25 files, no secrets or data; the pre-commit ruff hook re-sorted `__all__` in `core/types/__init__.py`, recommitted).

### Task 9 — recorded live fixture (D7)
- `artifacts/record_fixture.py` (steps `members`, `regime`, `fixture`, `compare`, `live`, `export`), read-only on the main lake.
- `members` → 502 members on 2024-05-01, 503 symbols with AAPL and SPY; none blocked · [fixture-members.txt](artifacts/fixture-members.txt).
- `regime` → `regime_at` on the main lake in 15.4 s: NORMAL / UP, HMM normal 0.999, Reflation, no stress, risk scalar 1.0 → `regime.json` · [fixture-regime.txt](artifacts/fixture-regime.txt).
- `fixture` → `atlas data slice` since 2021-10-01, 4 tables, 325,899 rows (320,863 bars), 10.05 MB · [fixture-slice.txt](artifacts/fixture-slice.txt).
- `compare` → AAPL's 11 items equal on the main lake and the slice (11 of 11); AAPL's last close 170.33 (2024-04-30) is the real close · [fixture-compare.txt](artifacts/fixture-compare.txt).
- `live` (first recording) → status ok in 2 calls: the first answer had a SHORT volatility signal without a stop and my D3 check sent the repair. The prompt said a FLAT signal has no stop but never that a LONG or SHORT one must have one, so the prompt now says "Every LONG or SHORT signal has one, whatever its timing" (golden updated), and the run was recorded again: status ok, **1 LLM call**, 4,352 + 1,662 tokens, USD 0.0017, no problems · [live-run.txt](artifacts/live-run.txt). `export` → 1 cache entry. Fixture grep for keys: none.
- Replay test `test_the_recorded_aapl_run_replays_offline` (no keys, `OfflineProvider`, `replay_only`): passes. `recorded.py` takes the fixture as an argument (Stage 29's callers unchanged); `recorded_technical.py` adds the Stage 30 constants and `recorded_regime()`.
- Live test `test_technical_live.py -m llm -s` → 1 passed, status ok (that run needed a repair turn: the model is not deterministic), USD 0.0025 · [live-test.txt](artifacts/live-test.txt).

### Task 10 — docs and real-lake check
- `docs/agents.md` "The technical analyst"; fixture `README.md`. Docs tests 4 passed.
- `artifacts/main_lake_check.py` on the main lake (regime computed, the default path), 11–34 s per case · [main-lake-check.txt](artifacts/main-lake-check.txt): NVDA after its 10:1 split (levels post-split, 52-week high 125.58 on 2024-06-06), GEV and KVUE (young: "only 46/60 sessions of history", no 63-session level), JPM on 2020-03-17 (CRISIS, risk scalar 0.25, vol_21d 110.6%), ATVI after delisting and SNDK (stale last bar named), AAPL today.
- **Defect found and fixed test-first:** for a stale symbol the 52-week window ended at the latest session, so ATVI read "only 248 sessions of history" (wrong: it had years of history). The window now ends at the symbol's last bar (re-read with `range_sessions + behind` sessions); fewer bars mean a short history (first bar after the window start: "only N sessions of history, first bar D") or sessions without a bar ("K sessions in the window without a bar"). Two new tests (`test_a_stale_last_bar_is_named` extended, `test_missing_bars_inside_the_window_are_counted`) were red (3 failures) before the fix.
- SNDK checked against its raw bars: its 8:1 split (ex 2026-11-20) is known at the as-of, so every bar, including the last (pre-split) one, is on the post-split basis; ratios and levels are consistent and its -68% from the high is real. The module docstring's "relative to the symbol's last bar" was inaccurate for that case and is corrected.
- Stale symbols: the Stage 15 snapshot is taken at the cross-section's latest session, so most of their features read n/a (fail-safe); documented in `docs/agents.md`.
