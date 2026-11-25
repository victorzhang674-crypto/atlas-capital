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
